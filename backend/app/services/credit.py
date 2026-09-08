"""CreditService — the orchestration around the customer scoring engine.

Loads the applicant, the current financial profile, the bureau report and the vehicle
economics; hands them to ``CustomerScoringEngine``; persists the immutable result.

The knock-out rules, the thin-file rule, the six component weights and the grade bands all
live in the engine and its configuration. None of them is restated here.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.errors import IncompleteDataError, NotFoundError
from app.core.observability import scoring_duration_seconds, scoring_runs_total
from app.engines.base import ScoreResult
from app.engines.customer_engine import CustomerScoringEngine, CustomerScoringInput
from app.engines.emi import calculate_emi
from app.engines.knockouts import KnockOut
from app.engines.vehicle_economics import (
    VehicleEconomics,
    VehicleEconomicsInput,
    calculate_vehicle_economics,
)
from app.models.catalog import Route, Vehicle
from app.models.credit import Applicant, CreditScore, LoanApplication
from app.repositories.credit import CreditScoreRepository, LoanApplicationRepository
from app.repositories.customer import (
    BureauReportRepository,
    CustomerRepository,
    FinancialProfileRepository,
    ObligationRepository,
)
from app.services.config import ConfigService

CONFIG_TYPE = "CUSTOMER"
INDIVIDUAL_TYPES = {"INDIVIDUAL_DRIVER", "OWNER_DRIVER"}


@dataclass(frozen=True, slots=True)
class CreditAssessmentResult:
    credit_score: CreditScore
    score: ScoreResult
    application: LoanApplication
    economics: VehicleEconomics | None
    #: The exact input the engine scored. Carried so the combined assessment can feed the
    #: final engine without re-deriving it and risking a different set of numbers.
    payload: CustomerScoringInput | None = None
    #: Knock-outs the engine triggered, rebuilt from the result it returned. The final
    #: engine short-circuits on these, so they have to survive the hand-off.
    knockouts: tuple[KnockOut, ...] = ()


class CreditService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.customers = CustomerRepository(session)
        self.financials = FinancialProfileRepository(session)
        self.obligations = ObligationRepository(session)
        self.bureau = BureauReportRepository(session)
        self.scores = CreditScoreRepository(session)
        self.applications = LoanApplicationRepository(session)
        self.config_service = ConfigService(session)
        self.engine = CustomerScoringEngine()

    # -- reads -------------------------------------------------------------
    def list_assessments(
        self, applicant_id: int, *, offset: int, limit: int
    ) -> tuple[Sequence[CreditScore], int]:
        if self.customers.get_active(applicant_id) is None:
            raise NotFoundError("customer", applicant_id)
        return self.scores.list_for_applicant(applicant_id, offset=offset, limit=limit)

    # -- assessment --------------------------------------------------------
    def assess(
        self,
        applicant_id: int,
        *,
        scored_by: int,
        application_id: int | None = None,
        as_of: date | None = None,
    ) -> CreditAssessmentResult:
        business_date = as_of or datetime.now(UTC).date()
        applicant = self.customers.get_active(applicant_id)
        if applicant is None:
            raise NotFoundError("customer", applicant_id)

        application = self._resolve_application(applicant, application_id)
        payload = self.build_engine_input(applicant, application, business_date)
        config = self.config_service.active_config(CONFIG_TYPE)

        started = time.perf_counter()
        score = self.engine.score(payload, config)
        # TRD 2.11 — the engine stays pure; the service records that it ran.
        scoring_duration_seconds.labels("customer").observe(time.perf_counter() - started)
        scoring_runs_total.labels("customer", score.grade).inc()

        credit_score = self._persist(application, score, payload, scored_by)
        return CreditAssessmentResult(
            credit_score=credit_score,
            score=score,
            application=application,
            payload=payload,
            knockouts=_knockouts_from(score),
            economics=payload.economics,
        )

    def _resolve_application(
        self, applicant: Applicant, application_id: int | None
    ) -> LoanApplication:
        if application_id is not None:
            application = self.applications.get_active(application_id)
            if application is None or application.applicant_id != applicant.id:
                raise NotFoundError("loan application", application_id)
            return application

        application = self.applications.latest_for_applicant(applicant.id)
        if application is None:
            raise IncompleteDataError(
                "This customer has no loan application to assess. Create one first, or "
                "pass application_id explicitly.",
                ["application_id"],
            )
        return application

    # -- engine input ------------------------------------------------------
    def build_engine_input(
        self, applicant: Applicant, application: LoanApplication, as_of: date
    ) -> CustomerScoringInput:
        """Marshal persisted rows into the engine's frozen input dataclass."""
        financial = self.financials.get(application.financial_profile_id)
        if financial is None:
            raise IncompleteDataError(
                "The application references a financial profile that no longer exists",
                ["financial_profile_id"],
            )

        report = self.bureau.latest_valid(applicant.id, as_of) or self.bureau.latest_for(
            applicant.id
        )
        obligations = self.obligations.for_applicant(applicant.id)
        overdue_count = sum(1 for o in obligations if o.is_overdue)

        vehicle = self.session.get(Vehicle, application.vehicle_id)
        route = self.session.get(Route, application.route_id)
        economics = self._economics(application, vehicle, route)

        proposed_emi = calculate_emi(
            Decimal(application.requested_amount),
            Decimal(application.proposed_interest_rate),
            int(application.requested_tenure_months),
        )

        age_years, age_at_maturity = self._ages(applicant, application, as_of)
        licence_months = self._licence_months_remaining(applicant, as_of)

        return CustomerScoringInput(
            applicant_type=str(applicant.applicant_type),
            age_years=age_years,
            age_at_maturity=age_at_maturity,
            bureau_score=Decimal(report.bureau_score) if report and report.bureau_score else None,
            credit_history_months=report.credit_history_months if report else 0,
            previous_default_count=report.previous_default_count if report else 0,
            max_dpd_last_24m=report.max_dpd_last_24m if report else 0,
            enquiries_last_6m=report.enquiries_last_6m if report else 0,
            current_overdue_amount=(
                Decimal(report.current_overdue_amount) if report else Decimal("0")
            ),
            is_blacklisted=bool(report.is_blacklisted) if report else False,
            total_monthly_income=Decimal(financial.total_monthly_income),
            total_monthly_expenses=Decimal(financial.total_monthly_expenses),
            total_existing_emi=Decimal(financial.total_existing_emi),
            existing_loan_count=int(financial.existing_loan_count),
            overdue_obligation_count=overdue_count,
            avg_bank_balance_6m=Decimal(financial.avg_bank_balance_6m),
            income_proof_type=str(financial.income_proof_type or "SELF_DECLARED"),
            income_verified=bool(financial.income_verified),
            requested_amount=Decimal(application.requested_amount),
            requested_tenure_months=int(application.requested_tenure_months),
            down_payment=Decimal(application.down_payment_amount),
            vehicle_on_road_price=Decimal(application.vehicle_on_road_price),
            proposed_emi=proposed_emi,
            driving_experience_years=Decimal(applicant.driving_experience_years or 0),
            commercial_driving_years=Decimal(applicant.commercial_driving_years or 0),
            business_experience_years=Decimal(applicant.business_experience_years or 0),
            fleet_size=int(applicant.fleet_size or 0),
            has_previous_ev_experience=bool(applicant.has_previous_ev_experience),
            licence_category=applicant.licence_category,
            licence_months_remaining=licence_months,
            is_driver_operated=str(applicant.applicant_type) in INDIVIDUAL_TYPES,
            vehicle_is_new=bool(vehicle.is_new) if vehicle else True,
            route_grade=route.latest_grade if route else None,
            route_score=Decimal(route.latest_score) if route and route.latest_score else None,
            economics=economics,
            extras={"proposed_interest_rate": str(application.proposed_interest_rate)},
        )

    def _economics(
        self, application: LoanApplication, vehicle: Vehicle | None, route: Route | None
    ) -> VehicleEconomics | None:
        if vehicle is None or vehicle.model is None:
            return None
        model = vehicle.model
        return calculate_vehicle_economics(
            VehicleEconomicsInput(
                battery_capacity_kwh=Decimal(model.battery_capacity_kwh),
                real_world_range_km=Decimal(model.real_world_range_km),
                on_road_price=Decimal(application.vehicle_on_road_price),
                maintenance_cost_per_km=Decimal(model.maintenance_cost_per_km),
                battery_warranty_years=int(model.battery_warranty_years),
                daily_km=Decimal(application.expected_daily_km),
                operating_days_per_month=int(application.expected_operating_days_month),
                daily_trips=Decimal(route.estimated_daily_trips) if route else Decimal("0"),
                avg_revenue_per_trip=(
                    Decimal(route.avg_fare_per_trip) + Decimal(route.avg_freight_revenue_per_trip)
                    if route
                    else Decimal("0")
                ),
                electricity_tariff_per_kwh=(
                    Decimal(route.electricity_tariff_per_kwh) if route else Decimal("12")
                ),
                gradient_profile=str(route.gradient_profile) if route else "FLAT",
                monsoon_disruption_days=int(route.monsoon_disruption_days) if route else 0,
                down_payment=Decimal(application.down_payment_amount),
                tenure_months=int(application.requested_tenure_months),
            )
        )

    @staticmethod
    def _ages(
        applicant: Applicant, application: LoanApplication, as_of: date
    ) -> tuple[Decimal | None, Decimal | None]:
        if applicant.date_of_birth is None:
            return None, None
        years = Decimal((as_of - applicant.date_of_birth).days) / Decimal("365.25")
        at_maturity = years + (
            Decimal(application.requested_tenure_months) / Decimal("12")
        )
        return years.quantize(Decimal("0.1")), at_maturity.quantize(Decimal("0.1"))

    @staticmethod
    def _licence_months_remaining(applicant: Applicant, as_of: date) -> Decimal | None:
        if applicant.licence_expiry is None:
            return None
        days = (applicant.licence_expiry - as_of).days
        return (Decimal(days) / Decimal("30.44")).quantize(Decimal("0.1"))

    # -- persistence -------------------------------------------------------
    def _persist(
        self,
        application: LoanApplication,
        score: ScoreResult,
        payload: CustomerScoringInput,
        scored_by: int,
    ) -> CreditScore:
        import dataclasses

        config_row = self.config_service.configs.active(CONFIG_TYPE)
        self.scores.supersede_latest(application.id)

        extras = score.extras
        credit_score = self.scores.create(
            application_id=application.id,
            config_id=config_row.id if config_row else None,
            engine_version=score.engine_version,
            total_score=score.total_score,
            grade=score.grade,
            risk_level=score.risk_level,
            dti_ratio=Decimal(str(extras.get("dti_ratio") or 0)),
            foir_ratio=Decimal(str(extras.get("foir_ratio") or 0)),
            disposable_income=Decimal(str(extras.get("disposable_income") or 0)),
            knockouts_triggered=extras.get("knockouts_triggered", []),
            risk_factors=[f.to_dict() for f in score.risk_factors],
            positive_factors=[f.to_dict() for f in score.positive_factors],
            explanation=score.explanation,
            input_snapshot={
                "inputs": {
                    k: (str(v) if isinstance(v, Decimal) else v)
                    for k, v in dataclasses.asdict(payload).items()
                    if k not in ("extras", "economics")
                },
                "engine_version": score.engine_version,
                "config_version": score.config_version_no,
            },
            is_latest=True,
            scored_by=scored_by,
            scored_at=datetime.now(UTC),
        )
        for component in score.components:
            self.scores.add_component(
                credit_score_id=credit_score.id,
                component_code=component.code,
                label=component.label,
                raw_inputs=component.raw_inputs,
                normalized_score=component.normalized_score,
                weight=component.weight,
                weighted_score=component.weighted_score,
                explanation=component.explanation,
                display_order=component.display_order,
            )
        self.session.flush()
        return credit_score


def _knockouts_from(score: ScoreResult) -> tuple[KnockOut, ...]:
    """Rebuild the engine's knock-outs from the result it returned.

    The customer engine short-circuits on a knock-out and records them in ``extras`` as
    plain dicts for storage. The final engine needs the objects again, and re-running the
    knock-out rules to get them would be a second evaluation that could disagree with the
    first.
    """
    return tuple(
        KnockOut(
            code=str(item.get("code", "")),
            message=str(item.get("message", "")),
            value=item.get("value"),
            threshold=item.get("threshold"),
        )
        for item in (score.extras.get("knockouts_triggered") or ())
    )
