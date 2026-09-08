"""LoanService — booking, amortisation, repayment and servicing — Doc 06 §6.8.

The service owns orchestration and persistence. Every number it writes comes from a pure
function: ``app.engines.emi.build_schedule`` for the amortisation, and
``app.engines.servicing`` for allocation, days past due, outstanding and classification.
Nothing below decides how much interest is owed or when a loan becomes substandard.

Booking is one unit of work. The loan row, its full schedule, the vehicle's status and the
application's status either all commit or none do — a loan with half a schedule is worse
than no loan at all, because the servicing jobs would treat it as real.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import (
    ConflictError,
    DuplicateError,
    IncompleteDataError,
    NotFoundError,
    ValidationError,
)
from app.engines.emi import add_months, build_schedule, calculate_emi
from app.engines.servicing import (
    Allocation,
    InstallmentState,
    LoanPosition,
    allocate_payment,
    installment_status,
    loan_position,
)
from app.models.catalog import Vehicle
from app.models.portfolio import Loan, Repayment, RepaymentSchedule
from app.repositories.credit import LoanApplicationRepository, LoanAssessmentRepository
from app.repositories.loan import (
    LoanBookRepository,
    RepaymentRepository,
    RepaymentScheduleRepository,
    UnderwritingDecisionRepository,
)
from app.services.audit import AuditService

#: Doc 06 §6.8 — the first instalment must fall within 45 days of disbursement.
MAX_FIRST_EMI_OFFSET_DAYS = 45

CLOSED_STATUSES = frozenset({"CLOSED", "WRITTEN_OFF", "FORECLOSED"})


@dataclass(frozen=True, slots=True)
class BookedLoan:
    loan: Loan
    schedule: tuple[RepaymentSchedule, ...]
    total_interest: Decimal
    total_payable: Decimal


@dataclass(frozen=True, slots=True)
class PostedRepayment:
    repayment: Repayment
    allocation: Allocation
    position: LoanPosition
    affected: tuple[RepaymentSchedule, ...]
    is_replay: bool = False


class LoanService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.loans = LoanBookRepository(session)
        self.schedules = RepaymentScheduleRepository(session)
        self.repayments = RepaymentRepository(session)
        self.applications = LoanApplicationRepository(session)
        self.assessments = LoanAssessmentRepository(session)
        self.decisions = UnderwritingDecisionRepository(session)
        self.audit = AuditService(session)

    # -- reads -------------------------------------------------------------
    def get(self, loan_id: int) -> Loan:
        loan = self.loans.get(loan_id)
        if loan is None:
            raise NotFoundError("loan", loan_id)
        return loan

    def by_uuid(self, loan_uuid: Any) -> Loan:
        loan = self.loans.by_uuid(loan_uuid)
        if loan is None:
            raise NotFoundError("loan", loan_uuid)
        return loan

    def list_loans(self, **filters: Any) -> tuple[Any, int]:
        return self.loans.list_loans(**filters)

    def schedule_for(self, loan_id: int, *, offset: int, limit: int) -> tuple[Any, int]:
        self.get(loan_id)
        return self.schedules.paginated_for_loan(loan_id, offset=offset, limit=limit)

    def repayments_for(self, loan_id: int, *, offset: int, limit: int) -> tuple[Any, int]:
        self.get(loan_id)
        return self.repayments.for_loan(loan_id, offset=offset, limit=limit)

    # -- booking -----------------------------------------------------------
    def book_loan(
        self,
        application_id: int,
        *,
        booked_by: int,
        disbursement_date: date | None = None,
        first_emi_date: date | None = None,
        assigned_officer_id: int | None = None,
        now: datetime | None = None,
    ) -> BookedLoan:
        """Doc 06 §6.8 — turn an approved application into a live loan.

        Every precondition is checked before anything is written, and the whole booking is
        one transaction: loan, schedule, vehicle status and application status commit
        together or not at all.
        """
        moment = now or datetime.now(UTC)
        disbursed = disbursement_date or moment.date()

        application = self.applications.get_active(application_id)
        if application is None:
            raise NotFoundError("loan application", application_id)

        # The duplicate check comes first because it is the more specific fact: a
        # re-booking attempt should be told the loan already exists, not that the
        # application is no longer APPROVED — which is only true *because* it was booked.
        if self.loans.for_application(application.id) is not None:
            raise DuplicateError(
                "This application has already been disbursed",
                details={"application_id": str(application.uuid)},
            )

        if str(application.status) != "APPROVED":
            raise ConflictError(
                f"Only an APPROVED application can be booked; this one is "
                f"{application.status}",
                details={"status": str(application.status)},
            )

        decision = self.decisions.latest_for_application(application.id)
        if decision is None or str(decision.final_decision) != "APPROVE":
            raise IncompleteDataError(
                "This application has no approval decision to book against",
                ["decision_id"],
            )

        assessment = self.assessments.latest_for_application(application.id)
        if assessment is None:
            raise IncompleteDataError(
                "This application has no assessment to book against", ["assessment_id"]
            )

        principal = Decimal(decision.approved_amount or 0)
        tenure = int(decision.approved_tenure_months or 0)
        rate = Decimal(decision.approved_interest_rate or 0)
        self._validate_terms(principal, tenure, rate, application)

        first_due = first_emi_date or add_months(disbursed, 1)
        if first_due < disbursed:
            raise ValidationError(
                "The first instalment cannot fall before disbursement",
                details=[{"field": "first_emi_date", "code": "BEFORE_DISBURSEMENT",
                          "message": f"Must be on or after {disbursed.isoformat()}"}],
            )
        if (first_due - disbursed).days > MAX_FIRST_EMI_OFFSET_DAYS:
            raise ValidationError(
                f"The first instalment must fall within {MAX_FIRST_EMI_OFFSET_DAYS} days "
                f"of disbursement",
                details=[{"field": "first_emi_date", "code": "TOO_LATE",
                          "message": f"At most {MAX_FIRST_EMI_OFFSET_DAYS} days after "
                                     f"{disbursed.isoformat()}"}],
            )

        vehicle = self.session.get(Vehicle, application.vehicle_id)
        if vehicle is None:
            raise NotFoundError("vehicle", application.vehicle_id)

        # -- the pure engine produces every figure below ---------------------
        emi = calculate_emi(principal, rate, tenure)
        rows = build_schedule(principal, rate, tenure, first_due, emi=emi)
        total_interest = sum((r.interest_due for r in rows), start=Decimal("0"))
        total_payable = sum((r.total_due for r in rows), start=Decimal("0"))

        ltv = (
            principal * Decimal("100") / Decimal(application.vehicle_on_road_price)
        ).quantize(Decimal("0.01"))

        loan = self.loans.create(
            loan_account_number=self.loans.next_account_number(disbursed.year),
            application_id=application.id,
            applicant_id=application.applicant_id,
            vehicle_id=application.vehicle_id,
            route_id=application.route_id,
            principal_amount=principal,
            interest_rate=rate,
            tenure_months=tenure,
            emi_amount=emi,
            down_payment=application.down_payment_amount,
            ltv_percent=ltv,
            disbursement_date=disbursed,
            first_emi_date=first_due,
            maturity_date=rows[-1].due_date,
            total_interest=total_interest,
            total_payable=total_payable,
            outstanding_principal=principal,
            total_paid=Decimal("0"),
            overdue_amount=Decimal("0"),
            days_past_due=0,
            installments_paid=0,
            installments_overdue=0,
            classification="PERFORMING",
            risk_status="GREEN",
            final_risk_score=assessment.final_score,
            risk_grade=assessment.risk_grade,
            status="ACTIVE",
            assigned_officer_id=assigned_officer_id,
            branch_code=application.branch_code,
            created_by=booked_by,
        )

        stored = self.schedules.bulk_create([
            {
                "loan_id": loan.id,
                "installment_no": row.installment_no,
                "due_date": row.due_date,
                "opening_balance": row.opening_balance,
                "principal_due": row.principal_due,
                "interest_due": row.interest_due,
                "total_due": row.total_due,
                "closing_balance": row.closing_balance,
                "status": "PENDING",
            }
            for row in rows
        ])

        vehicle.status = "FINANCED"
        application.status = "DISBURSED"
        self.session.flush()

        self.audit.record(
            action="LOAN_BOOKED",
            user_id=booked_by,
            entity_type="loan",
            entity_id=loan.id,
            entity_uuid=loan.uuid,
            after={
                "loan_account_number": loan.loan_account_number,
                "principal_amount": str(principal),
                "tenure_months": tenure,
                "emi_amount": str(emi),
                "installments": len(rows),
            },
            event_time=moment,
        )

        return BookedLoan(
            loan=loan,
            schedule=tuple(stored),
            total_interest=Decimal(total_interest),
            total_payable=Decimal(total_payable),
        )

    def _validate_terms(
        self, principal: Decimal, tenure: int, rate: Decimal, application: Any
    ) -> None:
        problems: list[dict[str, str]] = []
        if principal <= 0:
            problems.append({"field": "approved_amount", "code": "NOT_POSITIVE",
                             "message": "Must be greater than zero"})
        if principal >= Decimal(application.vehicle_on_road_price):
            problems.append({"field": "approved_amount", "code": "EXCEEDS_PRICE",
                             "message": "Cannot finance more than the vehicle costs"})
        if not 6 <= tenure <= 120:
            problems.append({"field": "approved_tenure_months", "code": "OUT_OF_RANGE",
                             "message": "Between 6 and 120 months"})
        if not Decimal("0") < rate < Decimal("40"):
            problems.append({"field": "approved_interest_rate", "code": "OUT_OF_RANGE",
                             "message": "Between 0 and 40 percent"})
        if problems:
            raise ValidationError(
                "The approved terms cannot be booked", details=problems
            )

    # -- repayment ---------------------------------------------------------
    def record_repayment(
        self,
        loan_id: int,
        *,
        recorded_by: int,
        amount_paid: Decimal,
        payment_date: date,
        payment_mode: str,
        reference_number: str | None = None,
        remarks: str | None = None,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> PostedRepayment:
        """Doc 06 §6.8 — post a payment, allocate it and recompute the loan in one go.

        Allocation, DPD and classification all come from ``app.engines.servicing``; this
        method reads the schedule, hands it over, and writes back what comes out.
        """
        moment = now or datetime.now(UTC)
        loan = self.get(loan_id)

        if str(loan.status) in CLOSED_STATUSES:
            raise ConflictError(
                f"No payment can be posted to a {loan.status} loan",
                details={"status": str(loan.status)},
            )
        if Decimal(amount_paid) <= 0:
            raise ValidationError(
                "The payment amount must be positive",
                details=[{"field": "amount_paid", "code": "NOT_POSITIVE",
                          "message": "Must be greater than zero"}],
            )

        if idempotency_key:
            existing = self.repayments.by_idempotency_key(loan.id, idempotency_key)
            if existing is not None:
                # A retried request must not post the payment twice. The stored receipt is
                # returned unchanged, alongside the loan's current position.
                return PostedRepayment(
                    repayment=existing,
                    allocation=Allocation((), Decimal("0"), Decimal("0"), Decimal("0"),
                                          Decimal("0"), ()),
                    position=self.position(loan, payment_date),
                    affected=(),
                    is_replay=True,
                )

        rows = list(self.schedules.for_loan(loan.id))
        if not rows:
            raise IncompleteDataError(
                "This loan has no repayment schedule", ["schedule"]
            )

        allocation = allocate_payment(Decimal(amount_paid), _states(rows))
        by_number = {row.installment_no: row for row in rows}
        affected: list[RepaymentSchedule] = []

        for line in allocation.lines:
            row = by_number[line.installment_no]
            row.penalty_paid = Decimal(row.penalty_paid) + line.penalty
            row.interest_paid = Decimal(row.interest_paid) + line.interest
            row.principal_paid = Decimal(row.principal_paid) + line.principal
            row.total_paid = Decimal(row.total_paid) + line.total
            if line.installment_no in allocation.settled_installments:
                row.paid_date = payment_date
            affected.append(row)
        self.session.flush()

        first_line = allocation.lines[0] if allocation.lines else None
        schedule_id = by_number[first_line.installment_no].id if first_line else None
        days_late = 0
        if first_line is not None:
            due = by_number[first_line.installment_no].due_date
            days_late = max((payment_date - due).days, 0)

        repayment = self.repayments.create(
            loan_id=loan.id,
            schedule_id=schedule_id,
            receipt_number=self.repayments.next_receipt_number(payment_date),
            payment_date=payment_date,
            amount_paid=Decimal(amount_paid),
            principal_component=allocation.principal_component,
            interest_component=allocation.interest_component,
            penalty_component=allocation.penalty_component,
            payment_mode=payment_mode,
            reference_number=reference_number,
            days_late=days_late,
            is_advance=allocation.is_advance,
            idempotency_key=idempotency_key,
            remarks=remarks,
            recorded_by=recorded_by,
        )

        position = self.refresh_position(loan, payment_date)

        self.audit.record(
            action="REPAYMENT_RECORDED",
            user_id=recorded_by,
            entity_type="repayment",
            entity_id=repayment.id,
            entity_uuid=repayment.uuid,
            after={
                "receipt_number": repayment.receipt_number,
                "amount_paid": str(amount_paid),
                "allocation": allocation.to_dict(),
                "days_past_due": position.days_past_due,
                "classification": position.classification,
            },
            event_time=moment,
        )

        return PostedRepayment(
            repayment=repayment,
            allocation=allocation,
            position=position,
            affected=tuple(affected),
        )

    # -- servicing ---------------------------------------------------------
    def position(self, loan: Loan, as_of: date) -> LoanPosition:
        """The loan's servicing position, computed but not written."""
        return loan_position(_states(self.schedules.for_loan(loan.id)), as_of)

    def refresh_position(self, loan: Loan, as_of: date) -> LoanPosition:
        """Recompute DPD, outstanding and classification and write them back.

        Idempotent by construction: it is a pure function of the schedule and the business
        date, so running it twice for the same date writes the same values.
        """
        rows = list(self.schedules.for_loan(loan.id))
        states = _states(rows)
        position = loan_position(states, as_of)

        for row, state in zip(rows, states, strict=True):
            row.days_past_due = max((as_of - row.due_date).days, 0) if not state.is_settled else 0
            row.status = installment_status(state, as_of)

        loan.days_past_due = position.days_past_due
        loan.overdue_amount = position.overdue_amount
        loan.outstanding_principal = position.outstanding_principal
        loan.total_paid = position.total_paid
        loan.installments_paid = position.installments_paid
        loan.installments_overdue = position.installments_overdue
        loan.classification = position.classification
        loan.risk_status = position.risk_status

        if position.is_fully_repaid and str(loan.status) == "ACTIVE":
            loan.status = "CLOSED"
            loan.closed_at = datetime.now(UTC)

        self.session.flush()
        return position


def _states(rows: Any) -> list[InstallmentState]:
    """Adapt ORM schedule rows into the engine's frozen input. The only translation
    between the table and the arithmetic."""
    return [
        InstallmentState(
            installment_no=int(row.installment_no),
            due_date=row.due_date,
            principal_due=Decimal(row.principal_due),
            interest_due=Decimal(row.interest_due),
            penalty_due=Decimal(row.penalty_due or 0),
            principal_paid=Decimal(row.principal_paid or 0),
            interest_paid=Decimal(row.interest_paid or 0),
            penalty_paid=Decimal(row.penalty_paid or 0),
        )
        for row in rows
    ]
