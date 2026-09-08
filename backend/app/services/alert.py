"""AlertService — Doc 04 §4.10, Doc 08 §8.6.

Orchestration only. Rule conditions are never restated here: the service builds a
``MetricSnapshot`` from persisted monitoring data, hands it and the catalogue to
``evaluate_rules``, and turns the outcomes into alert rows using the dedup, supersession
and auto-resolve semantics the engine already decided.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.core.observability import alerts_generated_total
from app.models.portfolio import Loan, RiskAlert
from app.repositories.alert import (
    AlertRepository,
    LoanRepository,
    MonitoringSnapshotRepository,
)
from app.repositories.config import RiskRuleRepository
from app.risk.engine import ENGINE_VERSION, evaluate_rules, loan_risk_status
from app.risk.metrics import LoanSnapshotRow, build_metric_snapshot
from app.risk.models import MetricSnapshot, Rule, RuleOutcome
from app.services.audit import AuditService
from app.services.config import ConfigService

LIVE_STATUSES = ("OPEN", "ACKNOWLEDGED", "IN_PROGRESS")


@dataclass(frozen=True, slots=True)
class LoanEvaluation:
    loan_id: int
    risk_status: str
    created: tuple[str, ...] = ()
    refreshed: tuple[str, ...] = ()
    superseded: tuple[str, ...] = ()
    auto_resolved: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvaluationRun:
    as_of: date
    engine_version: str
    loans_evaluated: int
    alerts_created: int
    alerts_refreshed: int
    alerts_superseded: int
    alerts_auto_resolved: int
    results: tuple[LoanEvaluation, ...] = field(default_factory=tuple)


#: Terminal states. Nothing may be worked, reassigned or reopened from here.
CLOSED_ALERT_STATUSES = frozenset({"RESOLVED", "FALSE_POSITIVE", "SUPERSEDED"})


class AlertService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.alerts = AlertRepository(session)
        self.loans = LoanRepository(session)
        self.snapshots = MonitoringSnapshotRepository(session)
        self.rules_repo = RiskRuleRepository(session)
        self.config_service = ConfigService(session)
        self.audit = AuditService(session)

    # -- reads -------------------------------------------------------------
    def get_alert(self, alert_id: int) -> RiskAlert:
        alert = self.alerts.get_with_activities(alert_id)
        if alert is None:
            raise NotFoundError("alert", alert_id)
        return alert

    def list_alerts(self, **filters: Any) -> tuple[Sequence[RiskAlert], int]:
        return self.alerts.list_alerts(**filters)

    # -- snapshot ----------------------------------------------------------
    def build_snapshot(self, loan: Loan, as_of: date) -> MetricSnapshot:
        """Assemble the engine's input from persisted monitoring data.

        Every field is optional: a loan in its first month has no usage baseline, and a
        vehicle with no device has no telemetry. The engine treats a missing metric as
        "condition does not fire", which is why nothing is defaulted to zero here — a
        zero would look like a stationary vehicle rather than an absent signal.
        """
        snap = self.snapshots.latest_for_loan(loan.id, as_of)
        row = LoanSnapshotRow(
            loan_id=loan.id,
            as_of=as_of,
            days_past_due=loan.days_past_due,
            consecutive_missed_emi=loan.installments_overdue,
            overdue_amount=loan.overdue_amount,
            emi_amount=loan.emi_amount,
            partial_payment_count_90d=(
                getattr(snap, "partial_payment_count_90d", None) if snap else None
            ),
            avg_daily_km_7d=snap.avg_daily_km_7d if snap else None,
            avg_daily_km_30d=snap.avg_daily_km_30d if snap else None,
            baseline_daily_km=snap.baseline_daily_km if snap else None,
            active_days_30d=snap.active_days_30d if snap else None,
            zero_km_streak_days=snap.zero_km_streak_days if snap else None,
            estimated_revenue_30d=snap.estimated_revenue_30d if snap else None,
            projected_revenue_30d=(
                self._projected_revenue(loan, snap) if snap else None
            ),
            monthly_net_contribution=snap.estimated_revenue_30d if snap else None,
            charging_sessions_30d=snap.charging_sessions_30d if snap else None,
            baseline_charging_sessions_30d=self._charging_baseline(snap),
            latest_state_of_health=snap.latest_state_of_health if snap else None,
            avg_route_deviation_percent=(
                snap.avg_route_deviation_percent if snap else None
            ),
            days_since_last_telemetry=(
                snap.days_since_last_telemetry if snap else None
            ),
        )
        return build_metric_snapshot(row)

    @staticmethod
    def _projected_revenue(loan: Loan, snap: Any) -> Any:
        """The underwriting projection the actual 30-day revenue is compared against."""
        return getattr(snap, "projected_revenue_30d", None)

    @staticmethod
    def _charging_baseline(snap: Any) -> Any:
        """Derive the charging baseline from the stored change percentage when the
        baseline itself is not persisted, so CHARGING_CHANGE_PCT stays computable."""
        if snap is None or snap.charging_sessions_30d is None:
            return None
        change = getattr(snap, "charging_change_percent", None)
        if change is None:
            return None
        from decimal import Decimal

        factor = Decimal("1") + (Decimal(change) / Decimal("100"))
        if factor <= 0:
            return None
        return Decimal(snap.charging_sessions_30d) / factor

    # -- evaluation --------------------------------------------------------
    def evaluate_loan(
        self, loan: Loan, *, as_of: date, rules: Sequence[Rule] | None = None
    ) -> LoanEvaluation:
        catalogue = tuple(rules) if rules is not None else self.config_service.active_rules()
        snapshot = self.build_snapshot(loan, as_of)

        outcomes = evaluate_rules(catalogue, snapshot)
        rules_by_code = {r.rule_code: r for r in catalogue}

        created: list[str] = []
        refreshed: list[str] = []
        superseded: list[str] = []
        resolved: list[str] = []

        for outcome in outcomes:
            rule = rules_by_code.get(outcome.rule_code)
            if rule is None:
                continue
            rule_row = self.rules_repo.by_code(outcome.rule_code)
            if rule_row is None:
                # The catalogue came from the shipped defaults on an unseeded database;
                # there is no rule row to reference, so nothing can be persisted.
                continue

            live = self.alerts.live_for_loan_and_rule(loan.id, rule_row.id)

            if outcome.is_actionable:
                if live is not None:
                    self._refresh(live, outcome, as_of)
                    refreshed.append(outcome.rule_code)
                else:
                    self._create(loan, rule_row, outcome, as_of)
                    # TRD 2.11 — rule noise is only visible if every raise is counted.
                    alerts_generated_total.labels(
                        outcome.severity, outcome.rule_code
                    ).inc()
                    created.append(outcome.rule_code)
            elif outcome.fired and outcome.superseded_by is not None:
                if live is not None:
                    self._close(live, "SUPERSEDED", "SUPERSEDED_BY_HIGHER_SEVERITY",
                                f"Superseded by {outcome.superseded_by}")
                    superseded.append(outcome.rule_code)
            elif not outcome.fired and live is not None and rule.auto_resolve:
                self._close(live, "RESOLVED", "AUTO_CONDITION_CLEARED",
                            "Triggering condition no longer met")
                resolved.append(outcome.rule_code)

        status = loan_risk_status(outcomes)
        self.loans.set_risk_status(loan, status)

        return LoanEvaluation(
            loan_id=loan.id,
            risk_status=status,
            created=tuple(created),
            refreshed=tuple(refreshed),
            superseded=tuple(superseded),
            auto_resolved=tuple(resolved),
        )

    def evaluate(
        self, *, as_of: date | None = None, loan_id: int | None = None
    ) -> EvaluationRun:
        """Doc 06 §6.10 ``POST /alerts/evaluate``.

        Idempotent for a given business date: dedup is enforced by the partial unique
        index on (loan, rule) while live, so a re-run refreshes rather than duplicates.
        """
        business_date = as_of or datetime.now(UTC).date()
        catalogue = self.config_service.active_rules()

        if loan_id is not None:
            loan = self.session.get(Loan, loan_id)
            if loan is None:
                raise NotFoundError("loan", loan_id)
            targets: Sequence[Loan] = [loan]
        else:
            targets = self.loans.active_loans()

        results = [
            self.evaluate_loan(loan, as_of=business_date, rules=catalogue)
            for loan in targets
        ]
        return EvaluationRun(
            as_of=business_date,
            engine_version=ENGINE_VERSION,
            loans_evaluated=len(results),
            alerts_created=sum(len(r.created) for r in results),
            alerts_refreshed=sum(len(r.refreshed) for r in results),
            alerts_superseded=sum(len(r.superseded) for r in results),
            alerts_auto_resolved=sum(len(r.auto_resolved) for r in results),
            results=tuple(results),
        )

    # -- alert row lifecycle ----------------------------------------------
    def _create(self, loan: Loan, rule_row: Any, outcome: RuleOutcome, as_of: date) -> RiskAlert:
        now = datetime.now(UTC)
        return self.alerts.create(
            alert_number=self.alerts.next_alert_number(as_of.year),
            loan_id=loan.id,
            applicant_id=loan.applicant_id,
            vehicle_id=loan.vehicle_id,
            rule_id=rule_row.id,
            alert_type=str(rule_row.category),
            severity=outcome.severity,
            trigger_condition=outcome.trigger_condition,
            trigger_metrics=outcome.evidence,
            trigger_date=as_of,
            current_metric_value=self._primary_value(outcome),
            occurrence_count=1,
            status="OPEN",
            acknowledge_due_at=now + timedelta(hours=rule_row.sla_hours_acknowledge),
            resolve_due_at=now + timedelta(hours=rule_row.sla_hours_resolve),
            last_evaluated_at=now,
        )

    def _refresh(self, alert: RiskAlert, outcome: RuleOutcome, as_of: date) -> None:
        alert.occurrence_count = int(alert.occurrence_count) + 1
        alert.current_metric_value = self._primary_value(outcome)
        alert.trigger_metrics = outcome.evidence
        alert.trigger_condition = outcome.trigger_condition
        alert.last_evaluated_at = datetime.now(UTC)
        self.session.flush()

    def _close(self, alert: RiskAlert, status: str, code: str, note: str) -> None:
        alert.status = status
        alert.resolution_code = code
        alert.resolution_notes = note
        alert.resolution_date = datetime.now(UTC)
        self.session.flush()

    @staticmethod
    def _primary_value(outcome: RuleOutcome) -> Any:
        for condition in outcome.conditions:
            if condition.passed and condition.value is not None:
                return condition.value
        return None

    # -- workflow ----------------------------------------------------------
    #: FR-7.7. An alert moves forward through the queue; it never moves back, because the
    #: history is the record of how it was worked. SUPERSEDED is set by the engine, not by
    #: a person, so it is not a target here.
    ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
        "OPEN": frozenset({"ACKNOWLEDGED", "IN_PROGRESS", "RESOLVED", "FALSE_POSITIVE",
                           "ESCALATED"}),
        "ACKNOWLEDGED": frozenset({"IN_PROGRESS", "RESOLVED", "FALSE_POSITIVE",
                                   "ESCALATED"}),
        "IN_PROGRESS": frozenset({"RESOLVED", "FALSE_POSITIVE", "ESCALATED"}),
        "ESCALATED": frozenset({"IN_PROGRESS", "RESOLVED", "FALSE_POSITIVE"}),
        "RESOLVED": frozenset(),
        "FALSE_POSITIVE": frozenset(),
        "SUPERSEDED": frozenset(),
    }

    def _require_transition(self, alert: RiskAlert, target: str) -> None:
        current = str(alert.status)
        if target not in self.ALLOWED_TRANSITIONS.get(current, frozenset()):
            raise ConflictError(
                f"An alert in {current} cannot move to {target}",
                code="INVALID_STATE_TRANSITION",
                details={
                    "status": current,
                    "attempted": target,
                    "allowed": sorted(self.ALLOWED_TRANSITIONS.get(current, frozenset())),
                },
            )

    def assign(
        self, alert_id: int, *, assignee_id: int, user_id: int, note: str | None = None
    ) -> RiskAlert:
        """FR-7.8 - put a named officer on it.

        Assignment is not a state change: an OPEN alert stays OPEN when it is handed to
        someone, because the SLA clock is about the work, not about the ownership.
        """
        alert = self.get_alert(alert_id)
        if str(alert.status) in CLOSED_ALERT_STATUSES:
            raise ConflictError(
                f"Alert is {alert.status} and cannot be reassigned",
                code="INVALID_STATE_TRANSITION",
            )

        previous = alert.assigned_to
        alert.assigned_to = assignee_id
        self.alerts.add_activity(
            alert_id=alert.id,
            activity_type="ASSIGNMENT",
            description=note or f"Assigned to user {assignee_id}",
            performed_by=user_id,
        )
        self.session.flush()
        self.audit.record(
            action="ALERT_ASSIGNED",
            user_id=user_id,
            entity_type="risk_alert",
            entity_id=alert.id,
            entity_uuid=alert.uuid,
            before={"assigned_to": previous},
            after={"assigned_to": assignee_id},
        )
        return alert

    def start_work(
        self, alert_id: int, *, user_id: int, note: str | None = None
    ) -> RiskAlert:
        """FR-7.7 - OPEN or ACKNOWLEDGED becomes IN_PROGRESS."""
        alert = self.get_alert(alert_id)
        self._require_transition(alert, "IN_PROGRESS")
        alert.status = "IN_PROGRESS"
        self.alerts.add_activity(
            alert_id=alert.id,
            activity_type="STATUS_CHANGE",
            description=note or "Investigation started",
            performed_by=user_id,
        )
        self.session.flush()
        self.audit.record(
            action="ALERT_IN_PROGRESS", user_id=user_id, entity_type="risk_alert",
            entity_id=alert.id, entity_uuid=alert.uuid, after={"status": "IN_PROGRESS"},
        )
        return alert

    def escalate(
        self, alert_id: int, *, user_id: int, reason: str, assignee_id: int | None = None
    ) -> RiskAlert:
        """FR-7.7 and BR-5 - hand it up when the SLA is at risk or the officer is stuck."""
        alert = self.get_alert(alert_id)
        self._require_transition(alert, "ESCALATED")
        alert.status = "ESCALATED"
        if assignee_id is not None:
            alert.assigned_to = assignee_id
        self.alerts.add_activity(
            alert_id=alert.id,
            activity_type="ESCALATION",
            description=reason,
            performed_by=user_id,
        )
        self.session.flush()
        self.audit.record(
            action="ALERT_ESCALATED", user_id=user_id, entity_type="risk_alert",
            entity_id=alert.id, entity_uuid=alert.uuid,
            after={"status": "ESCALATED", "reason": reason, "assigned_to": assignee_id},
        )
        return alert

    def add_activity(
        self,
        alert_id: int,
        *,
        user_id: int,
        activity_type: str,
        description: str,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """FR-7.7 - the investigation trail. Doc 12 step 17 adds a CALL_LOG here.

        Permitted on a closed alert too: a note recording what was learned afterwards is
        exactly the kind of thing that should not be lost.
        """
        alert = self.get_alert(alert_id)
        row = self.alerts.add_activity(
            alert_id=alert.id,
            activity_type=activity_type,
            description=description,
            performed_by=user_id,
            activity_metadata=metadata,
        )
        self.session.flush()
        return row

    def activities(self, alert_id: int) -> Sequence[Any]:
        self.get_alert(alert_id)
        return self.alerts.activities_for(alert_id)

    def sla_state(self, alert: RiskAlert, now: datetime) -> dict[str, Any]:
        """BR-5 - where this alert stands against its acknowledge and resolve deadlines."""

        def remaining(due: datetime | None) -> int | None:
            if due is None:
                return None
            deadline = due if due.tzinfo else due.replace(tzinfo=UTC)
            return int((deadline - now).total_seconds())

        acknowledge_left = (
            None if alert.acknowledged_at else remaining(alert.acknowledge_due_at)
        )
        resolve_left = (
            None if str(alert.status) in CLOSED_ALERT_STATUSES
            else remaining(alert.resolve_due_at)
        )
        return {
            "acknowledge_seconds_remaining": acknowledge_left,
            "resolve_seconds_remaining": resolve_left,
            "acknowledge_breached": acknowledge_left is not None and acknowledge_left < 0,
            "resolve_breached": resolve_left is not None and resolve_left < 0,
        }

    def acknowledge(self, alert_id: int, *, user_id: int) -> RiskAlert:
        alert = self.get_alert(alert_id)
        self._require_transition(alert, "ACKNOWLEDGED")
        alert.status = "ACKNOWLEDGED"
        alert.acknowledged_at = datetime.now(UTC)
        self.alerts.add_activity(
            alert_id=alert.id, activity_type="STATUS_CHANGE",
            description="Alert acknowledged", performed_by=user_id,
        )
        self.session.flush()
        self.audit.record(
            action="ALERT_ACKNOWLEDGED", user_id=user_id, entity_type="risk_alert",
            entity_id=alert.id, entity_uuid=alert.uuid, after={"status": "ACKNOWLEDGED"},
        )
        return alert

    def resolve(
        self, alert_id: int, *, user_id: int, resolution_code: str, notes: str
    ) -> RiskAlert:
        alert = self.get_alert(alert_id)
        status = "FALSE_POSITIVE" if resolution_code == "FALSE_POSITIVE" else "RESOLVED"
        self._require_transition(alert, status)
        alert.status = status
        alert.resolution_code = resolution_code
        alert.resolution_notes = notes
        alert.resolved_by = user_id
        alert.resolution_date = datetime.now(UTC)
        self.alerts.add_activity(
            alert_id=alert.id, activity_type="STATUS_CHANGE",
            description=f"Resolved as {resolution_code}: {notes}", performed_by=user_id,
        )
        self.session.flush()
        self.audit.record(
            action="ALERT_RESOLVED", user_id=user_id, entity_type="risk_alert",
            entity_id=alert.id, entity_uuid=alert.uuid,
            after={"status": status, "resolution_code": resolution_code},
        )
        return alert
