"""Alert and monitoring-snapshot persistence."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.portfolio import (
    AlertActivity,
    Loan,
    LoanMonitoringSnapshot,
    RiskAlert,
)
from app.repositories.base import BaseRepository, apply_sort

SORTABLE = frozenset({"trigger_date", "severity", "status", "created_at", "alert_number"})

#: Statuses that count as a live alert. Mirrors the partial unique index in the schema,
#: which is what actually enforces one live alert per (loan, rule).
LIVE_STATUSES = ("OPEN", "ACKNOWLEDGED", "IN_PROGRESS")


class AlertRepository(BaseRepository[RiskAlert]):
    model = RiskAlert

    def get_with_activities(self, alert_id: int) -> RiskAlert | None:
        stmt = (
            select(RiskAlert)
            .options(selectinload(RiskAlert.activities))
            .where(RiskAlert.id == alert_id)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def live_for_loan(self, loan_id: int) -> Sequence[RiskAlert]:
        stmt = select(RiskAlert).where(
            RiskAlert.loan_id == loan_id,
            RiskAlert.status.in_(LIVE_STATUSES),
        )
        return self.session.execute(stmt).scalars().all()

    def live_for_loan_and_rule(self, loan_id: int, rule_id: int) -> RiskAlert | None:
        stmt = select(RiskAlert).where(
            RiskAlert.loan_id == loan_id,
            RiskAlert.rule_id == rule_id,
            RiskAlert.status.in_(LIVE_STATUSES),
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_alerts(
        self,
        *,
        offset: int,
        limit: int,
        status: str | None = None,
        severity: str | None = None,
        category: str | None = None,
        loan_id: int | None = None,
        assigned_to: int | None = None,
        unassigned: bool | None = None,
        sort: str | None = None,
    ) -> tuple[Sequence[RiskAlert], int]:
        stmt = select(RiskAlert)
        if status:
            stmt = stmt.where(RiskAlert.status == status)
        if severity:
            stmt = stmt.where(RiskAlert.severity == severity)
        if category:
            stmt = stmt.where(RiskAlert.alert_type == category)
        if loan_id is not None:
            stmt = stmt.where(RiskAlert.loan_id == loan_id)
        if assigned_to is not None:
            stmt = stmt.where(RiskAlert.assigned_to == assigned_to)
        if unassigned:
            stmt = stmt.where(RiskAlert.assigned_to.is_(None))

        if sort:
            stmt = apply_sort(stmt, RiskAlert, sort, SORTABLE)
        else:
            # Doc 03 Screen 21 — the working order: most severe first, then oldest.
            stmt = stmt.order_by(
                RiskAlert.severity.desc(), RiskAlert.trigger_date.asc(), RiskAlert.id.asc()
            )
        return self.paginate(stmt, offset=offset, limit=limit)

    def next_alert_number(self, year: int) -> str:
        prefix = f"AL-{year}-"
        stmt = (
            select(RiskAlert.alert_number)
            .where(RiskAlert.alert_number.like(f"{prefix}%"))
            .order_by(RiskAlert.alert_number.desc())
            .limit(1)
        )
        latest = self.session.execute(stmt).scalar_one_or_none()
        sequence = int(latest.rsplit("-", 1)[1]) + 1 if latest else 1
        return f"{prefix}{sequence:06d}"

    def create(self, **values: Any) -> RiskAlert:
        return self.add(RiskAlert(**values))

    def activities_for(self, alert_id: int) -> Sequence[AlertActivity]:
        """Oldest first - the investigation timeline reads as a story."""
        stmt = (
            select(AlertActivity)
            .where(AlertActivity.alert_id == alert_id)
            .order_by(AlertActivity.created_at.asc(), AlertActivity.id.asc())
        )
        return self.session.execute(stmt).scalars().all()

    def add_activity(self, **values: Any) -> AlertActivity:
        activity = AlertActivity(**values)
        self.session.add(activity)
        self.session.flush()
        return activity


class MonitoringSnapshotRepository(BaseRepository[LoanMonitoringSnapshot]):
    model = LoanMonitoringSnapshot

    def latest_for_loan(
        self, loan_id: int, as_of: date | None = None
    ) -> LoanMonitoringSnapshot | None:
        stmt = select(LoanMonitoringSnapshot).where(
            LoanMonitoringSnapshot.loan_id == loan_id
        )
        if as_of is not None:
            stmt = stmt.where(LoanMonitoringSnapshot.snapshot_date <= as_of)
        stmt = stmt.order_by(LoanMonitoringSnapshot.snapshot_date.desc()).limit(1)
        return self.session.execute(stmt).scalar_one_or_none()

    def upsert(self, **values: Any) -> LoanMonitoringSnapshot:
        existing = self.session.get(
            LoanMonitoringSnapshot, (values["loan_id"], values["snapshot_date"])
        )
        if existing is None:
            return self.add(LoanMonitoringSnapshot(**values))
        for key, value in values.items():
            setattr(existing, key, value)
        self.session.flush()
        return existing


class LoanRepository(BaseRepository[Loan]):
    model = Loan

    def active_loans(self) -> Sequence[Loan]:
        stmt = select(Loan).where(Loan.status == "ACTIVE").order_by(Loan.id)
        return self.session.execute(stmt).scalars().all()

    def list_portfolio(
        self,
        *,
        offset: int,
        limit: int,
        risk_status: str | None = None,
        risk_grade: str | None = None,
        classification: str | None = None,
        min_dpd: int | None = None,
        assigned_officer_id: int | None = None,
        status: str | None = "ACTIVE",
    ) -> tuple[Sequence[Loan], int]:
        stmt = select(Loan)
        if status:
            stmt = stmt.where(Loan.status == status)
        if risk_status:
            stmt = stmt.where(Loan.risk_status == risk_status)
        if risk_grade:
            stmt = stmt.where(Loan.risk_grade == risk_grade)
        if classification:
            stmt = stmt.where(Loan.classification == classification)
        if min_dpd is not None:
            stmt = stmt.where(Loan.days_past_due >= min_dpd)
        if assigned_officer_id is not None:
            stmt = stmt.where(Loan.assigned_officer_id == assigned_officer_id)
        stmt = stmt.order_by(Loan.days_past_due.desc(), Loan.id)
        return self.paginate(stmt, offset=offset, limit=limit)

    def set_risk_status(self, loan: Loan, risk_status: str) -> None:
        loan.risk_status = risk_status
        self.session.flush()
