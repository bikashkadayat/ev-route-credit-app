"""Loan servicing endpoints — Doc 06 §6.8.

Booking lives on the application (`POST /applications/{id}/book`) because that is where
the approval it draws its terms from lives. Everything after disbursement is here.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status

from app.api.mappers_loan import (
    loan_detail_out,
    loan_summary_out,
    repayment_out,
    repayment_posted_out,
    schedule_row_out,
)
from app.api.schemas.common import ERROR_RESPONSES, Page
from app.api.schemas.loan import (
    JobRunOut,
    LoanDetailOut,
    LoanSummaryOut,
    RepaymentOut,
    RepaymentPostedOut,
    RepaymentRequest,
    ScheduleRowOut,
    ServicingRunOut,
)
from app.core.dependencies import (
    LoanServiceDep,
    MonitoringServiceDep,
    PaginationParams,
    ServicingJobServiceDep,
)
from app.core.permissions import CurrentUser, require_permission

router = APIRouter(prefix="/loans", tags=["Loans"], responses=ERROR_RESPONSES)


def _user_id(user: CurrentUser) -> int:
    try:
        return int(user.id)
    except (TypeError, ValueError):
        return 1


@router.get(
    "",
    response_model=Page[LoanSummaryOut],
    summary="List the loan book",
    description="Worst first by default: the officer's queue is ordered by days past due.",
)
def list_loans(
    pagination: PaginationParams,
    service: LoanServiceDep,
    _user: CurrentUser = Depends(require_permission("portfolio:read")),
    status_filter: Annotated[str | None, Query(alias="status")] = "ACTIVE",
    risk_status: Annotated[str | None, Query(description="GREEN, YELLOW or RED")] = None,
    classification: str | None = None,
    risk_grade: str | None = None,
    min_dpd: Annotated[int | None, Query(ge=0)] = None,
    assigned_officer_id: int | None = None,
    branch_code: str | None = None,
    sort: str | None = "-days_past_due",
) -> Page[LoanSummaryOut]:
    rows, total = service.list_loans(
        offset=pagination.offset,
        limit=pagination.limit,
        status=status_filter,
        risk_status=risk_status,
        classification=classification,
        risk_grade=risk_grade,
        min_dpd=min_dpd,
        assigned_officer_id=assigned_officer_id,
        branch_code=branch_code,
        sort=sort,
    )
    return Page.build(
        [loan_summary_out(r) for r in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get(
    "/{loan_id}",
    response_model=LoanDetailOut,
    summary="Get a loan",
    description="The loan with its schedule summary and current servicing position.",
)
def get_loan(
    loan_id: UUID,
    service: LoanServiceDep,
    _user: CurrentUser = Depends(require_permission("portfolio:read")),
) -> LoanDetailOut:
    loan = service.by_uuid(loan_id)
    schedule = service.schedules.for_loan(loan.id)
    return loan_detail_out(loan, schedule=schedule)


@router.get(
    "/{loan_id}/schedule",
    response_model=Page[ScheduleRowOut],
    summary="Amortisation schedule",
    description=(
        "Every instalment with what is due, what has been paid and its current status. "
        "Ordered by instalment number."
    ),
)
def get_schedule(
    loan_id: UUID,
    pagination: PaginationParams,
    service: LoanServiceDep,
    _user: CurrentUser = Depends(require_permission("portfolio:read")),
) -> Page[ScheduleRowOut]:
    loan = service.by_uuid(loan_id)
    rows, total = service.schedule_for(
        loan.id, offset=pagination.offset, limit=pagination.limit
    )
    return Page.build(
        [schedule_row_out(r) for r in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get(
    "/{loan_id}/repayments",
    response_model=Page[RepaymentOut],
    summary="Repayment history",
    description="Newest first. Repayments are immutable, so this is a complete record.",
)
def list_repayments(
    loan_id: UUID,
    pagination: PaginationParams,
    service: LoanServiceDep,
    _user: CurrentUser = Depends(require_permission("portfolio:read")),
) -> Page[RepaymentOut]:
    loan = service.by_uuid(loan_id)
    rows, total = service.repayments_for(
        loan.id, offset=pagination.offset, limit=pagination.limit
    )
    return Page.build(
        [repayment_out(r) for r in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.post(
    "/{loan_id}/repayments",
    response_model=RepaymentPostedOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record a repayment",
    description=(
        "Allocates the payment penalty → interest → principal against the oldest unpaid "
        "instalment first, then recomputes days past due, outstanding and classification "
        "in the same transaction.\n\n"
        "An excess beyond every outstanding instalment is reported as `unallocated` and "
        "flagged as an advance. Send an `Idempotency-Key` header to make a retry safe: a "
        "repeated key returns the original receipt without posting again. 409 if the loan "
        "is closed or written off."
    ),
)
def record_repayment(
    loan_id: UUID,
    payload: RepaymentRequest,
    service: LoanServiceDep,
    user: CurrentUser = Depends(require_permission("repayment:create")),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RepaymentPostedOut:
    loan = service.by_uuid(loan_id)
    posted = service.record_repayment(
        loan.id,
        recorded_by=_user_id(user),
        amount_paid=payload.amount_paid,
        payment_date=payload.payment_date,
        payment_mode=payload.payment_mode,
        reference_number=payload.reference_number,
        remarks=payload.remarks,
        idempotency_key=idempotency_key,
    )
    return repayment_posted_out(posted)


@router.get(
    "/{loan_id}/monitoring",
    summary="Telemetry series and derived trends",
    description=(
        "FR-6.5. The daily aggregates for the window plus the derived picture: 7- and "
        "30-day averages against the 30-90 day baseline, percentage change, active days, "
        "zero-kilometre streak, charging change, state of health and the behaviour score. "
        "The behaviour score is the one the rule engine evaluated, not a second reading of "
        "the same data."
    ),
)
def loan_monitoring(
    loan_id: UUID,
    loans: LoanServiceDep,
    monitoring: MonitoringServiceDep,
    _user: CurrentUser = Depends(require_permission("portfolio:read")),
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
) -> dict:
    loan = loans.by_uuid(loan_id)
    result = monitoring.loan_monitoring(
        loan.id, date_from=date_from, date_to=date_to
    )
    return {
        "loan_id": str(result["loan_id"]),
        "from": result["from"].isoformat(),
        "to": result["to"].isoformat(),
        "baseline_daily_km": _num(result["baseline_daily_km"]),
        "current": {
            key: _num(value) for key, value in asdict(result["current"]).items()
        },
        "series": [
            {k: (v.isoformat() if isinstance(v, date) else _num(v)) for k, v in row.items()}
            for row in result["series"]
        ],
    }


def _num(value: object) -> object:
    """Decimals are rendered as strings elsewhere in the API; a chart wants numbers."""
    return float(value) if isinstance(value, Decimal) else value


# ---------------------------------------------------------------------------
# Servicing jobs — TRD §2.10
# ---------------------------------------------------------------------------
jobs_router = APIRouter(prefix="/admin/jobs", tags=["Admin"], responses=ERROR_RESPONSES)


@jobs_router.post(
    "/run",
    response_model=ServicingRunOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run the nightly servicing pass",
    description=(
        "Recomputes arrears, reclassifies the book and evaluates the risk rules, in that "
        "order — the rule engine reads days past due, so evaluating before recomputing "
        "would raise alerts against yesterday's position.\n\n"
        "Idempotent for a business date: every step is a pure function of the stored "
        "schedule, so a re-run writes the same values. Each execution records a `job_runs` "
        "row; nothing is recorded for a job that did not run."
    ),
)
def run_servicing(
    service: ServicingJobServiceDep,
    _user: CurrentUser = Depends(require_permission("settings:update")),
    as_of: Annotated[str | None, Query(description="Business date, YYYY-MM-DD")] = None,
) -> ServicingRunOut:
    from datetime import date as date_type

    business_date = date_type.fromisoformat(as_of) if as_of else None
    results = service.run_nightly(as_of=business_date)
    return ServicingRunOut(
        runs=[
            JobRunOut(
                job_name=r.job_name,
                as_of=r.as_of,
                status=r.status,
                rows_processed=r.rows_processed,
                error=r.error,
            )
            for r in results
        ]
    )
