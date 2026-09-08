"""Loan, schedule and repayment schemas — Doc 06 §6.8."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from app.api.schemas.common import ApiModel, PublicId

PaymentMode = Literal[
    "CASH", "BANK_TRANSFER", "CHEQUE", "MOBILE_WALLET", "DIRECT_DEBIT", "OTHER"
]


class BookLoanRequest(ApiModel):
    """`POST /applications/{id}/book`. The commercial terms come from the approval
    decision, not from this body — a client cannot book different terms from the ones
    that were approved."""

    disbursement_date: date | None = Field(
        default=None, description="Defaults to today (UTC)."
    )
    first_emi_date: date | None = Field(
        default=None,
        description="Defaults to one month after disbursement; at most 45 days after it.",
    )
    assigned_officer_id: int | None = None


class ScheduleRowOut(ApiModel):
    installment_no: int
    due_date: date
    opening_balance: Decimal
    principal_due: Decimal
    interest_due: Decimal
    total_due: Decimal
    closing_balance: Decimal
    principal_paid: Decimal
    interest_paid: Decimal
    penalty_due: Decimal
    penalty_paid: Decimal
    total_paid: Decimal
    paid_date: date | None = None
    days_past_due: int
    status: str


class ScheduleSummaryOut(ApiModel):
    installment_count: int
    first_due: date | None = None
    last_due: date | None = None
    total_principal: Decimal
    total_interest: Decimal
    total_payable: Decimal


class LoanSummaryOut(ApiModel):
    id: PublicId
    loan_account_number: str
    principal_amount: Decimal
    interest_rate: Decimal
    tenure_months: int
    emi_amount: Decimal
    outstanding_principal: Decimal
    overdue_amount: Decimal
    total_paid: Decimal
    days_past_due: int
    installments_paid: int
    installments_overdue: int
    classification: str
    risk_status: str
    risk_grade: str
    final_risk_score: Decimal
    status: str
    disbursement_date: date
    maturity_date: date


class LoanDetailOut(LoanSummaryOut):
    application_id: UUID | None = None
    applicant_id: UUID | None = None
    route_id: UUID | None = None
    ltv_percent: Decimal
    down_payment: Decimal
    total_interest: Decimal
    total_payable: Decimal
    first_emi_date: date
    assigned_officer_id: int | None = None
    branch_code: str | None = None
    schedule_summary: ScheduleSummaryOut | None = None
    created_at: datetime


class BookedLoanOut(ApiModel):
    loan: LoanDetailOut
    schedule_summary: ScheduleSummaryOut


class RepaymentRequest(ApiModel):
    payment_date: date
    amount_paid: Annotated[Decimal, Field(gt=0, le=100_000_000)]
    payment_mode: PaymentMode
    reference_number: Annotated[str, Field(max_length=60)] | None = None
    remarks: Annotated[str, Field(max_length=500)] | None = None


class AllocationLineOut(ApiModel):
    installment_no: int
    penalty: Decimal
    interest: Decimal
    principal: Decimal
    total: Decimal


class AllocationOut(ApiModel):
    """Doc 06 §6.8 — the split a client shows as the allocation preview."""

    penalty: Decimal
    interest: Decimal
    principal: Decimal
    allocated: Decimal
    unallocated: Decimal = Field(
        description="Received beyond every outstanding instalment; recorded as an advance"
    )
    settled_installments: list[int] = Field(default_factory=list)
    lines: list[AllocationLineOut] = Field(default_factory=list)


class LoanPositionOut(ApiModel):
    days_past_due: int
    overdue_amount: Decimal
    outstanding_principal: Decimal
    outstanding_interest: Decimal
    total_paid: Decimal
    installments_paid: int
    installments_overdue: int
    classification: str
    risk_status: str


class RepaymentOut(ApiModel):
    id: PublicId
    receipt_number: str
    payment_date: date
    amount_paid: Decimal
    principal_component: Decimal
    interest_component: Decimal
    penalty_component: Decimal
    payment_mode: str
    reference_number: str | None = None
    days_late: int
    is_advance: bool
    created_at: datetime


class RepaymentPostedOut(ApiModel):
    repayment: RepaymentOut
    allocation: AllocationOut
    loan: LoanPositionOut
    affected_installments: list[ScheduleRowOut] = Field(default_factory=list)


class JobRunOut(ApiModel):
    job_name: str
    as_of: date
    status: str
    rows_processed: int
    error: str | None = None


class ServicingRunOut(ApiModel):
    """`POST /admin/jobs/{job_name}/run` — TRD §2.10."""

    runs: list[JobRunOut] = Field(default_factory=list)
