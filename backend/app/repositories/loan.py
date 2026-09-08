"""Loan booking, schedule and repayment queries — Doc 05 §5.3.24-5.3.26.

Queries only. Allocation, DPD and classification are arithmetic and live in
``app.engines.servicing``; nothing in this module decides anything.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.credit import UnderwritingDecision
from app.models.portfolio import Loan, Repayment, RepaymentSchedule
from app.models.system import JobRun
from app.repositories.base import BaseRepository, apply_sort


class UnderwritingDecisionRepository(BaseRepository[UnderwritingDecision]):
    """Append-only in practice: an override adds a decision, it never edits one, so the
    escalation path stays readable (Doc 05 §5.3.23)."""

    model = UnderwritingDecision

    def create(self, **values: Any) -> UnderwritingDecision:
        return self.add(UnderwritingDecision(**values))

    def latest_for_application(self, application_id: int) -> UnderwritingDecision | None:
        stmt = (
            select(UnderwritingDecision)
            .where(UnderwritingDecision.application_id == application_id)
            .order_by(UnderwritingDecision.decided_at.desc(), UnderwritingDecision.id.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def history_for_application(
        self, application_id: int
    ) -> Sequence[UnderwritingDecision]:
        """Every decision, oldest first — the override trail."""
        stmt = (
            select(UnderwritingDecision)
            .where(UnderwritingDecision.application_id == application_id)
            .order_by(UnderwritingDecision.decided_at.asc(), UnderwritingDecision.id.asc())
        )
        return self.session.execute(stmt).scalars().all()


class LoanBookRepository(BaseRepository[Loan]):
    """Loan writes and the booking-side reads.

    ``app.repositories.alert.LoanRepository`` owns the monitoring-side reads; this one owns
    creation and servicing, so the alert pipeline and the servicing pipeline do not have to
    share a growing class.
    """

    model = Loan

    def create(self, **values: Any) -> Loan:
        return self.add(Loan(**values))

    def by_uuid(self, loan_uuid: Any) -> Loan | None:
        stmt = select(Loan).where(Loan.uuid == loan_uuid).limit(1)
        return self.session.execute(stmt).scalar_one_or_none()

    def for_application(self, application_id: int) -> Loan | None:
        """One loan per application — the uniqueness is also a database constraint."""
        stmt = select(Loan).where(Loan.application_id == application_id).limit(1)
        return self.session.execute(stmt).scalar_one_or_none()

    def with_schedule(self, loan_id: int) -> Loan | None:
        stmt = (
            select(Loan)
            .options(selectinload(Loan.schedule))
            .where(Loan.id == loan_id)
            .limit(1)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def next_account_number(self, year: int) -> str:
        prefix = f"LN-{year}-"
        stmt = (
            select(Loan.loan_account_number)
            .where(Loan.loan_account_number.like(f"{prefix}%"))
            .order_by(Loan.loan_account_number.desc())
            .limit(1)
        )
        latest = self.session.execute(stmt).scalar_one_or_none()
        sequence = int(latest.rsplit("-", 1)[1]) + 1 if latest else 1
        return f"{prefix}{sequence:06d}"

    SORTABLE = frozenset({
        "days_past_due", "outstanding_principal", "final_risk_score",
        "disbursement_date", "created_at",
    })

    def list_loans(
        self,
        *,
        offset: int,
        limit: int,
        status: str | None = None,
        risk_status: str | None = None,
        classification: str | None = None,
        risk_grade: str | None = None,
        min_dpd: int | None = None,
        assigned_officer_id: int | None = None,
        route_id: int | None = None,
        branch_code: str | None = None,
        sort: str | None = "-days_past_due",
    ) -> tuple[Sequence[Loan], int]:
        stmt = select(Loan)
        if status:
            stmt = stmt.where(Loan.status == status)
        if risk_status:
            stmt = stmt.where(Loan.risk_status == risk_status)
        if classification:
            stmt = stmt.where(Loan.classification == classification)
        if risk_grade:
            stmt = stmt.where(Loan.risk_grade == risk_grade)
        if min_dpd is not None:
            stmt = stmt.where(Loan.days_past_due >= min_dpd)
        if assigned_officer_id is not None:
            stmt = stmt.where(Loan.assigned_officer_id == assigned_officer_id)
        if route_id is not None:
            stmt = stmt.where(Loan.route_id == route_id)
        if branch_code:
            stmt = stmt.where(Loan.branch_code == branch_code)
        stmt = apply_sort(stmt, Loan, sort, self.SORTABLE)
        return self.paginate(stmt, offset=offset, limit=limit)

    def active_loan_ids(self) -> Sequence[int]:
        """Used by the nightly servicing jobs, which page over ids rather than rows."""
        stmt = select(Loan.id).where(Loan.status == "ACTIVE").order_by(Loan.id)
        return list(self.session.execute(stmt).scalars().all())


class RepaymentScheduleRepository(BaseRepository[RepaymentSchedule]):
    model = RepaymentSchedule

    def for_loan(self, loan_id: int) -> Sequence[RepaymentSchedule]:
        stmt = (
            select(RepaymentSchedule)
            .where(RepaymentSchedule.loan_id == loan_id)
            .order_by(RepaymentSchedule.installment_no)
        )
        return self.session.execute(stmt).scalars().all()

    def paginated_for_loan(
        self, loan_id: int, *, offset: int, limit: int
    ) -> tuple[Sequence[RepaymentSchedule], int]:
        stmt = (
            select(RepaymentSchedule)
            .where(RepaymentSchedule.loan_id == loan_id)
            .order_by(RepaymentSchedule.installment_no)
        )
        return self.paginate(stmt, offset=offset, limit=limit)

    def bulk_create(self, rows: Sequence[dict[str, Any]]) -> list[RepaymentSchedule]:
        """One flush for the whole schedule; a 60-month loan is 60 rows."""
        entities = [RepaymentSchedule(**row) for row in rows]
        self.session.add_all(entities)
        self.session.flush()
        return entities

    def by_installment(self, loan_id: int, installment_no: int) -> RepaymentSchedule | None:
        stmt = select(RepaymentSchedule).where(
            RepaymentSchedule.loan_id == loan_id,
            RepaymentSchedule.installment_no == installment_no,
        )
        return self.session.execute(stmt).scalar_one_or_none()


class RepaymentRepository(BaseRepository[Repayment]):
    """Repayments are immutable (Doc 05 §5.3.26) — no update method exists here."""

    model = Repayment

    def create(self, **values: Any) -> Repayment:
        return self.add(Repayment(**values))

    def for_loan(
        self, loan_id: int, *, offset: int, limit: int
    ) -> tuple[Sequence[Repayment], int]:
        stmt = (
            select(Repayment)
            .where(Repayment.loan_id == loan_id)
            .order_by(Repayment.payment_date.desc(), Repayment.id.desc())
        )
        return self.paginate(stmt, offset=offset, limit=limit)

    def by_idempotency_key(self, loan_id: int, key: str) -> Repayment | None:
        """Doc 06 §6.8 — a retried POST must not post the payment twice."""
        stmt = select(Repayment).where(
            Repayment.loan_id == loan_id, Repayment.idempotency_key == key
        ).limit(1)
        return self.session.execute(stmt).scalar_one_or_none()

    def next_receipt_number(self, on: date) -> str:
        prefix = f"RC-{on.year}-"
        stmt = (
            select(Repayment.receipt_number)
            .where(Repayment.receipt_number.like(f"{prefix}%"))
            .order_by(Repayment.receipt_number.desc())
            .limit(1)
        )
        latest = self.session.execute(stmt).scalar_one_or_none()
        sequence = int(latest.rsplit("-", 1)[1]) + 1 if latest else 1
        return f"{prefix}{sequence:06d}"


class JobRunRepository(BaseRepository[JobRun]):
    """Batch-job execution records — TRD §2.10."""

    model = JobRun

    def successful_run(self, job_name: str, as_of: date) -> JobRun | None:
        """The row ``uq_job_success`` already holds for this job and business date."""
        stmt = (
            select(JobRun)
            .where(JobRun.job_name == job_name)
            .where(JobRun.as_of_date == as_of)
            .where(JobRun.status == "SUCCESS")
            .limit(1)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def recent(self, *, limit: int = 20) -> Sequence[JobRun]:
        stmt = select(JobRun).order_by(JobRun.started_at.desc()).limit(limit)
        return self.session.execute(stmt).scalars().all()

    def start(self, **values: Any) -> JobRun:
        run = JobRun(**values)
        self.session.add(run)
        self.session.flush()
        return run
