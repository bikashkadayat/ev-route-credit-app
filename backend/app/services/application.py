"""ApplicationService — the underwriting workflow — Doc 06 §6.6, PRD FR-4.x.

Two things live here and nowhere else:

1. **The state machine.** Every transition is checked against ``ALLOWED_TRANSITIONS``
   before anything is written. An application that has been withdrawn or declined cannot
   be walked back into approval by an ordinary update, because the transition table says
   so rather than because each call site remembered to check.
2. **Orchestration of the assessment.** Route score, credit score, vehicle economics,
   combined score, structuring and the decision matrix are all computed by the pure
   engines; this service resolves configuration, feeds them in the right order and
   persists the result. No formula appears below.

Maker-checker (Doc 09 §9.3.3) and the override rules (Doc 06 §6.6) are enforced here too,
at the domain level, so a client cannot bypass them by calling the API directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import (
    ConflictError,
    IncompleteDataError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.engines.final_engine import FinalAssessment, FinalRiskEngine, FinalScoringInput
from app.models.catalog import Route, Vehicle
from app.models.credit import Applicant, LoanApplication, UnderwritingDecision
from app.repositories.credit import (
    CreditScoreRepository,
    LoanApplicationRepository,
    LoanAssessmentRepository,
)
from app.repositories.customer import CustomerRepository, FinancialProfileRepository
from app.repositories.loan import UnderwritingDecisionRepository
from app.repositories.route import RouteAssessmentRepository, RouteRepository
from app.services import audit as audit_events
from app.services.audit import AuditService
from app.services.config import ConfigService
from app.services.credit import CreditService
from app.services.route_assessment import RouteAssessmentService

CONFIG_TYPE = "FINAL"

#: Doc 06 §6.6 and the ``application_status_enum`` in db/schema.sql. The enum is the
#: authority for the names; this table is the authority for the moves between them.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "DRAFT": frozenset({"SUBMITTED", "WITHDRAWN", "EXPIRED"}),
    "SUBMITTED": frozenset({"UNDER_ASSESSMENT", "WITHDRAWN", "EXPIRED"}),
    "UNDER_ASSESSMENT": frozenset({"PENDING_DECISION", "WITHDRAWN", "EXPIRED"}),
    "PENDING_DECISION": frozenset({"APPROVED", "REJECTED", "WITHDRAWN", "EXPIRED"}),
    "APPROVED": frozenset({"DISBURSED", "EXPIRED"}),
    "REJECTED": frozenset(),
    "WITHDRAWN": frozenset(),
    "DISBURSED": frozenset(),
    "EXPIRED": frozenset(),
}

#: Fields a client may change while an application is still a draft. Anything else — the
#: applicant, the financial profile snapshot, the status — is set by the workflow, not by
#: the request body.
EDITABLE_FIELDS = frozenset({
    "requested_amount", "requested_tenure_months", "proposed_interest_rate",
    "down_payment_amount", "expected_daily_km", "expected_operating_days_month",
    "purpose", "vehicle_id", "route_id",
})

MIN_OVERRIDE_JUSTIFICATION = 20


@dataclass(frozen=True, slots=True)
class ApplicationContext:
    """The rows a detail response needs alongside the application itself."""

    applicant: Any
    route: Any
    vehicle: Any
    financial_version: int | None
    latest_assessment: Any
    decisions: list[Any]


@dataclass(frozen=True, slots=True)
class ApplicationAssessmentResult:
    """What one full assessment produced, before it is shaped for the API."""

    application: LoanApplication
    assessment: Any
    final: FinalAssessment
    route_assessment: Any
    route_reused: bool
    credit_score: Any
    credit_result: Any
    config_versions: dict[str, int]
    engine_versions: dict[str, str]


class ApplicationService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.applications = LoanApplicationRepository(session)
        self.assessments = LoanAssessmentRepository(session)
        self.decisions = UnderwritingDecisionRepository(session)
        self.credit_scores = CreditScoreRepository(session)
        self.customers = CustomerRepository(session)
        self.financials = FinancialProfileRepository(session)
        self.routes = RouteRepository(session)
        self.route_assessments = RouteAssessmentRepository(session)
        self.config_service = ConfigService(session)
        self.audit = AuditService(session)
        self.route_service = RouteAssessmentService(session)
        self.credit_service = CreditService(session)
        self.engine = FinalRiskEngine()

    # -- reads -------------------------------------------------------------
    def get(self, application_id: int) -> LoanApplication:
        application = self.applications.get_active(application_id)
        if application is None:
            raise NotFoundError("loan application", application_id)
        return application

    def by_uuid(self, application_uuid: Any) -> LoanApplication:
        application = self.applications.get_by(uuid=application_uuid)
        if application is None or application.deleted_at is not None:
            raise NotFoundError("loan application", application_uuid)
        return application

    def list_applications(self, **filters: Any) -> tuple[Any, int]:
        return self.applications.list_applications(**filters)

    def decision_history(self, application_id: int) -> Any:
        return self.decisions.history_for_application(application_id)

    def latest_assessment(self, application_id: int) -> Any:
        return self.assessments.latest_for_application(application_id)

    def resolve_applicant_id(self, applicant_uuid: Any) -> int:
        applicant = self.customers.get_by(uuid=applicant_uuid)
        if applicant is None or applicant.deleted_at is not None:
            raise NotFoundError("applicant", applicant_uuid)
        return int(applicant.id)

    def resolve_route_id(self, route_uuid: Any) -> int:
        route = self.routes.get_by(uuid=route_uuid)
        if route is None or route.deleted_at is not None:
            raise NotFoundError("route", route_uuid)
        return int(route.id)

    def resolve_vehicle_id(self, vehicle_uuid: Any) -> int:
        vehicle = self.session.query(Vehicle).filter(Vehicle.uuid == vehicle_uuid).first()
        if vehicle is None or vehicle.deleted_at is not None:
            raise NotFoundError("vehicle", vehicle_uuid)
        return int(vehicle.id)

    def create_from_uuids(
        self, payload: dict[str, Any], *, created_by: int | None
    ) -> LoanApplication:
        """Translate the public UUIDs a client sends into the internal row ids.

        The API never exposes a BIGSERIAL, so this translation has to happen somewhere; it
        belongs here rather than in the router, where it would mean the router touching
        three repositories.
        """
        resolved = dict(payload)
        resolved["applicant_id"] = self.resolve_applicant_id(payload["applicant_id"])
        resolved["route_id"] = self.resolve_route_id(payload["route_id"])
        resolved["vehicle_id"] = self.resolve_vehicle_id(payload["vehicle_id"])
        return self.create_application(resolved, created_by=created_by)

    def detail_context(self, application: LoanApplication) -> ApplicationContext:
        """The related rows a detail response needs, fetched once."""
        financial = self.financials.get(application.financial_profile_id)
        return ApplicationContext(
            applicant=self.customers.get(application.applicant_id),
            route=self.routes.get(application.route_id),
            vehicle=self.session.get(Vehicle, application.vehicle_id),
            financial_version=int(financial.version_no) if financial else None,
            latest_assessment=self.assessments.latest_for_application(application.id),
            decisions=list(self.decisions.history_for_application(application.id)),
        )

    # -- state machine -----------------------------------------------------
    @staticmethod
    def can_transition(current: str, target: str) -> bool:
        return target in ALLOWED_TRANSITIONS.get(current, frozenset())

    def _transition(self, application: LoanApplication, target: str) -> None:
        current = str(application.status)
        if current == target:
            raise ConflictError(
                f"This application is already {target}",
                details={"status": current, "attempted": target},
            )
        if not self.can_transition(current, target):
            raise ConflictError(
                f"An application in {current} cannot move to {target}",
                details={
                    "status": current,
                    "attempted": target,
                    "allowed": sorted(ALLOWED_TRANSITIONS.get(current, frozenset())),
                },
            )
        application.status = target

    # -- create and edit ---------------------------------------------------
    def create_application(
        self, payload: dict[str, Any], *, created_by: int | None, as_of: date | None = None
    ) -> LoanApplication:
        """Doc 06 §6.6 — snapshots the financial profile and the on-road price.

        Both are snapshotted rather than referenced live: an assessment has to stay
        explainable against the figures it actually saw, and a later edit to the
        applicant's finances must not silently rewrite a decision already taken.
        """
        business_date = as_of or datetime.now(UTC).date()
        applicant = self.customers.get_active(int(payload["applicant_id"]))
        if applicant is None:
            raise NotFoundError("applicant", payload["applicant_id"])

        financial = self.financials.current_for(applicant.id)
        if financial is None:
            raise IncompleteDataError(
                "This applicant has no current financial profile. Record one before "
                "creating an application.",
                ["financial_profile_id"],
            )

        vehicle = self.session.get(Vehicle, int(payload["vehicle_id"]))
        if vehicle is None:
            raise NotFoundError("vehicle", payload["vehicle_id"])
        route = self.session.get(Route, int(payload["route_id"]))
        if route is None or route.deleted_at is not None:
            raise NotFoundError("route", payload["route_id"])

        on_road_price = Decimal(payload.get("vehicle_on_road_price") or vehicle.purchase_price)
        down_payment = Decimal(payload["down_payment_amount"])
        if down_payment >= on_road_price:
            raise ValidationError(
                "The down payment must be less than the vehicle's on-road price",
                details=[{"field": "down_payment_amount", "code": "EXCEEDS_PRICE",
                          "message": f"Must be below {on_road_price}"}],
            )

        application = self.applications.create(
            application_number=self.applications.next_application_number(
                business_date.year
            ),
            applicant_id=applicant.id,
            financial_profile_id=financial.id,
            vehicle_id=vehicle.id,
            route_id=route.id,
            requested_amount=Decimal(payload["requested_amount"]),
            requested_tenure_months=int(payload["requested_tenure_months"]),
            proposed_interest_rate=Decimal(payload["proposed_interest_rate"]),
            down_payment_amount=down_payment,
            vehicle_on_road_price=on_road_price,
            expected_daily_km=Decimal(payload["expected_daily_km"]),
            expected_operating_days_month=int(
                payload.get("expected_operating_days_month") or 26
            ),
            purpose=payload.get("purpose"),
            status="DRAFT",
            branch_code=payload.get("branch_code"),
            created_by=created_by,
        )
        self.audit.record(
            action="APPLICATION_CREATED",
            user_id=created_by,
            entity_type="loan_application",
            entity_id=application.id,
            entity_uuid=application.uuid,
            after={"application_number": application.application_number,
                   "status": "DRAFT",
                   "requested_amount": str(application.requested_amount)},
        )
        return application

    def update_application(
        self, application_id: int, payload: dict[str, Any], *, updated_by: int | None
    ) -> LoanApplication:
        """Only a DRAFT is editable, and only its commercial terms.

        Once submitted, the figures are what the assessment will be run against; letting
        them change underneath a submitted application is how a file gets assessed on one
        set of numbers and approved on another.
        """
        application = self.get(application_id)
        if str(application.status) != "DRAFT":
            raise ConflictError(
                f"Only a DRAFT application can be edited; this one is "
                f"{application.status}",
                details={"status": str(application.status)},
            )

        before = self._snapshot(application)
        rejected = sorted(set(payload) - EDITABLE_FIELDS)
        if rejected:
            raise ValidationError(
                "These fields cannot be changed on an application",
                details=[{"field": f, "code": "IMMUTABLE",
                          "message": "Set at creation and fixed thereafter"}
                         for f in rejected],
            )

        for field, value in payload.items():
            setattr(application, field, value)
        application.updated_by = updated_by
        self.session.flush()

        self.audit.record(
            action="APPLICATION_UPDATED",
            user_id=updated_by,
            entity_type="loan_application",
            entity_id=application.id,
            entity_uuid=application.uuid,
            before=before,
            after=self._snapshot(application),
        )
        return application

    # -- transitions -------------------------------------------------------
    def submit_application(
        self, application_id: int, *, submitted_by: int | None, now: datetime | None = None
    ) -> LoanApplication:
        moment = now or datetime.now(UTC)
        application = self.get(application_id)
        self._transition(application, "SUBMITTED")
        application.submitted_at = moment
        application.updated_by = submitted_by
        self.session.flush()

        self.audit.record(
            action="APPLICATION_SUBMITTED",
            user_id=submitted_by,
            entity_type="loan_application",
            entity_id=application.id,
            entity_uuid=application.uuid,
            after={"status": "SUBMITTED"},
            event_time=moment,
        )
        return application

    def withdraw_application(
        self,
        application_id: int,
        *,
        withdrawn_by: int | None,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> LoanApplication:
        moment = now or datetime.now(UTC)
        application = self.get(application_id)
        self._transition(application, "WITHDRAWN")
        application.updated_by = withdrawn_by
        self.session.flush()

        self.audit.record(
            action="APPLICATION_WITHDRAWN",
            user_id=withdrawn_by,
            entity_type="loan_application",
            entity_id=application.id,
            entity_uuid=application.uuid,
            after={"status": "WITHDRAWN", "reason": reason},
            event_time=moment,
        )
        return application

    # -- assessment --------------------------------------------------------
    def assess_application(
        self,
        application_id: int,
        *,
        assessed_by: int,
        force_route_reassessment: bool = False,
        as_of: date | None = None,
    ) -> ApplicationAssessmentResult:
        """Doc 06 §6.6 — the "Run Full Assessment" orchestration.

        Order matters and is the document's: route, then customer (which itself runs the
        knock-outs and the vehicle economics), then the combined score, structuring and the
        decision matrix. Each step is a call into a pure engine; nothing is recomputed here.
        """
        business_date = as_of or datetime.now(UTC).date()
        application = self.get(application_id)

        status = str(application.status)
        if status in {"WITHDRAWN", "REJECTED", "DISBURSED", "EXPIRED"}:
            raise ConflictError(
                f"An application in {status} cannot be assessed",
                details={"status": status},
            )
        if status == "SUBMITTED":
            self._transition(application, "UNDER_ASSESSMENT")
            self.session.flush()
        elif status == "DRAFT":
            raise ConflictError(
                "Submit the application before assessing it",
                details={"status": status, "allowed": ["SUBMITTED"]},
            )

        route_assessment, reused = self._resolve_route_assessment(
            application, assessed_by, force_route_reassessment
        )
        credit = self.credit_service.assess(
            application.applicant_id,
            scored_by=assessed_by,
            application_id=application.id,
            as_of=business_date,
        )

        config = self.config_service.active_config(CONFIG_TYPE)
        payload = self.build_engine_input(credit, route_assessment)
        final = self.engine.assess(payload, config)

        assessment = self._persist(
            application, final, route_assessment, credit, assessed_by, business_date
        )

        if str(application.status) == "UNDER_ASSESSMENT":
            self._transition(application, "PENDING_DECISION")
        self.session.flush()

        self.audit.record(
            action="APPLICATION_ASSESSED",
            user_id=assessed_by,
            entity_type="loan_assessment",
            entity_id=assessment.id,
            entity_uuid=assessment.uuid,
            after={
                "final_score": str(final.score.total_score),
                "risk_grade": final.score.grade,
                "recommendation": final.outcome.decision,
            },
        )

        return ApplicationAssessmentResult(
            application=application,
            assessment=assessment,
            final=final,
            route_assessment=route_assessment,
            route_reused=reused,
            credit_score=credit.credit_score,
            credit_result=credit,
            config_versions=self.config_service.active_versions(),
            engine_versions=self.config_service.engine_versions(),
        )

    def _resolve_route_assessment(
        self, application: LoanApplication, assessed_by: int, force: bool
    ) -> tuple[Any, bool]:
        """Reuse a fresh assessment; re-score when asked or when none exists.

        A route is scored once and shared by every application that runs on it, so reusing
        is the normal path — re-scoring per application would produce a pile of identical
        assessments and hide the one that actually informed the decision.
        """
        existing = self.route_assessments.latest_for_route(application.route_id)
        if existing is not None and not force:
            return existing, True

        result = self.route_service.assess(application.route_id, assessed_by=assessed_by)
        return result.assessment, False

    def build_engine_input(
        self, credit: Any, route_assessment: Any
    ) -> FinalScoringInput:
        """Marshal two already-computed results into the final engine's frozen input.

        The engine consumes finished route and customer scores rather than reaching for
        them, which is what keeps it a pure function of its arguments.
        """
        score = credit.score
        economics = credit.economics
        if economics is None:
            raise IncompleteDataError(
                "The application's vehicle has no model data, so vehicle economics "
                "cannot be computed",
                ["vehicle_id"],
            )

        vehicle_component = next(
            (c for c in score.components if c.code == "VEHICLE_ECONOMICS"), None
        )
        payload = credit.score.extras or {}
        return FinalScoringInput(
            route_score=Decimal(route_assessment.total_score),
            route_grade=str(route_assessment.grade),
            customer_score=Decimal(score.total_score),
            customer_grade=str(score.grade),
            vehicle_economics_score=(
                Decimal(vehicle_component.normalized_score)
                if vehicle_component is not None
                else Decimal(payload.get("vehicle_economics_score") or 0)
            ),
            economics=economics,
            customer_inputs=credit.payload,
            is_thin_file=bool(payload.get("is_thin_file", False)),
            knockouts=tuple(credit.knockouts),
            battery_warranty_months=int(payload.get("battery_warranty_months") or 0),
        )

    def _persist(
        self,
        application: LoanApplication,
        final: FinalAssessment,
        route_assessment: Any,
        credit: Any,
        assessed_by: int,
        as_of: date,
    ) -> Any:
        config_row = self.config_service.configs.active(CONFIG_TYPE)
        previous = self.assessments.latest_for_application(application.id)
        if previous is not None:
            previous.is_latest = False
            self.session.flush()

        structure = final.structure
        economics = final.economics
        weights = final.score.extras.get("weights", {})

        return self.assessments.create(
            application_id=application.id,
            route_assessment_id=route_assessment.id,
            credit_score_id=credit.credit_score.id,
            config_id=config_row.id if config_row else None,
            engine_version=final.score.engine_version,
            route_score=Decimal(route_assessment.total_score),
            customer_score=Decimal(credit.score.total_score),
            vehicle_economics_score=_component_score(final),
            route_weight=Decimal(str(weights.get("route", 0))),
            customer_weight=Decimal(str(weights.get("customer", 0))),
            vehicle_weight=Decimal(str(weights.get("vehicle", 0))),
            final_score=final.score.total_score,
            risk_grade=final.score.grade,
            risk_level=final.score.risk_level,
            energy_cost_per_km=economics.energy_cost_per_km,
            daily_net_contribution=economics.daily_net_contribution,
            monthly_net_contribution=economics.monthly_net_contribution,
            recommended_amount=structure.recommended_amount,
            max_ltv_percent=structure.max_ltv_percent,
            applied_ltv_percent=structure.applied_ltv_percent,
            recommended_tenure_months=structure.recommended_tenure_months,
            recommended_interest_rate=structure.recommended_interest_rate,
            estimated_emi=structure.estimated_emi,
            dscr=structure.dscr if structure.dscr is not None else Decimal("0"),
            foir_post_loan=(
                structure.foir_post_loan
                if structure.foir_post_loan is not None
                else Decimal("0")
            ),
            payback_months=economics.payback_months,
            recommendation=final.outcome.decision,
            reasons=[r.to_dict() for r in final.score.extras.get("_reasons", ())],
            input_snapshot={
                "as_of": as_of.isoformat(),
                "route_assessment_id": route_assessment.id,
                "credit_score_id": credit.credit_score.id,
                "requested": final.to_dict().get("requested", {}),
            },
            is_latest=True,
            assessed_by=assessed_by,
        )

    # -- decision ----------------------------------------------------------
    def make_decision(
        self,
        application_id: int,
        *,
        decided_by: int,
        final_decision: str,
        approved_amount: Decimal | None = None,
        approved_tenure_months: int | None = None,
        approved_interest_rate: Decimal | None = None,
        conditions: list[str] | None = None,
        override_justification: str | None = None,
        can_override: bool = False,
        now: datetime | None = None,
    ) -> UnderwritingDecision:
        """Doc 06 §6.6. The rules enforced here, in order:

        * the application must be in PENDING_DECISION with a latest assessment;
        * the decider must not be the person who submitted it (Doc 09 §9.3.3 maker-checker);
        * a decision that differs from the system recommendation is an **override** and
          needs the ``application:override`` permission and a justification of at least 20
          characters;
        * the approved amount may not exceed the policy-capped LTV ceiling.
        """
        moment = now or datetime.now(UTC)
        application = self.get(application_id)

        if str(application.status) != "PENDING_DECISION":
            raise ConflictError(
                f"A decision requires the application to be PENDING_DECISION; this one is "
                f"{application.status}",
                details={"status": str(application.status)},
            )

        assessment = self.assessments.latest_for_application(application.id)
        if assessment is None:
            raise IncompleteDataError(
                "This application has not been assessed, so there is nothing to decide on",
                ["assessment_id"],
            )

        self._enforce_maker_checker(application, decided_by)

        recommendation = str(assessment.recommendation)
        is_override = final_decision != recommendation
        if is_override:
            if not can_override:
                raise PermissionDeniedError("application:override")
            if not override_justification or len(
                override_justification.strip()
            ) < MIN_OVERRIDE_JUSTIFICATION:
                raise ValidationError(
                    "Overriding the system recommendation requires a written justification "
                    f"of at least {MIN_OVERRIDE_JUSTIFICATION} characters",
                    code="OVERRIDE_JUSTIFICATION_REQUIRED",
                    details=[{"field": "override_justification", "code": "TOO_SHORT",
                              "message": f"At least {MIN_OVERRIDE_JUSTIFICATION} characters"}],
                )

        if final_decision == "APPROVE":
            approved_amount = self._validated_amount(
                assessment, application, approved_amount
            )
            approved_tenure_months = (
                approved_tenure_months or assessment.recommended_tenure_months
            )
            approved_interest_rate = (
                approved_interest_rate
                if approved_interest_rate is not None
                else assessment.recommended_interest_rate
            )

        decision = self.decisions.create(
            application_id=application.id,
            loan_assessment_id=assessment.id,
            system_recommendation=recommendation,
            final_decision=final_decision,
            is_override=is_override,
            override_justification=override_justification,
            approved_amount=approved_amount,
            approved_tenure_months=approved_tenure_months,
            approved_interest_rate=approved_interest_rate,
            conditions=conditions or [],
            reasons=assessment.reasons or [],
            decided_by=decided_by,
            decided_at=moment,
        )

        self._transition(
            application, "APPROVED" if final_decision == "APPROVE" else "REJECTED"
        )
        application.decided_at = moment
        self.session.flush()

        self.audit.record(
            action=(
                audit_events.DECISION_OVERRIDE if is_override else "APPLICATION_DECISION"
            ),
            user_id=decided_by,
            entity_type="underwriting_decision",
            entity_id=decision.id,
            entity_uuid=decision.uuid,
            after={
                "system_recommendation": recommendation,
                "final_decision": final_decision,
                "is_override": is_override,
                "override_justification": override_justification,
                "approved_amount": str(approved_amount) if approved_amount else None,
                "application_status": str(application.status),
            },
            event_time=moment,
        )
        return decision

    def _enforce_maker_checker(
        self, application: LoanApplication, decided_by: int
    ) -> None:
        """Doc 09 §9.3.3 — the person who put the file forward cannot be the one who
        approves it. Enforced in the domain, so it holds however the API is called."""
        maker = application.created_by
        submitter = application.updated_by if application.submitted_at else None
        if decided_by in {m for m in (maker, submitter) if m is not None}:
            raise ConflictError(
                "The person who raised or submitted an application cannot decide it. "
                "A second officer must review it.",
                code="FOUR_EYES_REQUIRED",
                details={"application_id": str(application.uuid), "user_id": decided_by},
            )

    def _validated_amount(
        self,
        assessment: Any,
        application: LoanApplication,
        approved_amount: Decimal | None,
    ) -> Decimal:
        """Doc 06 §6.6 — an approval may not exceed the policy-capped LTV ceiling.

        The cap comes from the assessment's own ``max_ltv_percent``, which structuring
        derived from the grade and the configured policy; recomputing it here would be a
        second implementation of the same rule.
        """
        amount = (
            Decimal(approved_amount)
            if approved_amount is not None
            else Decimal(assessment.recommended_amount)
        )
        if amount <= 0:
            raise ValidationError(
                "The approved amount must be positive",
                details=[{"field": "approved_amount", "code": "NOT_POSITIVE",
                          "message": "Must be greater than zero"}],
            )

        ceiling = (
            Decimal(assessment.max_ltv_percent)
            * Decimal(application.vehicle_on_road_price)
            / Decimal("100")
        ).quantize(Decimal("0.01"))
        if amount > ceiling:
            raise ValidationError(
                f"The approved amount exceeds the policy ceiling of {ceiling}",
                code="POLICY_LIMIT_EXCEEDED",
                details=[{"field": "approved_amount", "code": "ABOVE_LTV_CAP",
                          "message": f"Maximum {ceiling} at "
                                     f"{assessment.max_ltv_percent}% LTV"}],
            )
        return amount

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _snapshot(application: LoanApplication) -> dict[str, Any]:
        return {
            "status": str(application.status),
            "requested_amount": str(application.requested_amount),
            "requested_tenure_months": application.requested_tenure_months,
            "proposed_interest_rate": str(application.proposed_interest_rate),
            "down_payment_amount": str(application.down_payment_amount),
            "expected_daily_km": str(application.expected_daily_km),
            "purpose": application.purpose,
        }

    def applicant_for(self, application: LoanApplication) -> Applicant:
        applicant = self.customers.get_active(application.applicant_id)
        if applicant is None:
            raise NotFoundError("applicant", application.applicant_id)
        return applicant


def _component_score(final: FinalAssessment) -> Decimal:
    component = next(
        (c for c in final.score.components if c.code == "VEHICLE_ECONOMICS_SCORE"), None
    )
    if component is not None:
        return Decimal(component.normalized_score)
    return Decimal("0")
