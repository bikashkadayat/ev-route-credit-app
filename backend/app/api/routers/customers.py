"""Customer and credit-assessment endpoints — Doc 06 §6.4, §6.6.

The credit-assess endpoint delegates to ``CreditService``, which owns the single call to
``CustomerScoringEngine``. No scoring, knock-out or affordability logic appears here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.mappers import (
    credit_assessment_from_result,
    credit_assessment_from_row,
    customer_detail,
)
from app.api.schemas.catalog import BureauFetchRequest, BureauReportOut
from app.api.schemas.common import ERROR_RESPONSES, Page
from app.api.schemas.credit import CreditAssessmentOut, CreditAssessRequest
from app.api.schemas.customer import (
    CustomerCreate,
    CustomerDetail,
    CustomerSummary,
    CustomerUpdate,
    FinancialProfileCreate,
    FinancialProfileOut,
)
from app.core.dependencies import (
    BureauServiceDep,
    CreditServiceDep,
    CustomerServiceDep,
    PaginationParams,
)
from app.core.errors import NotFoundError
from app.core.permissions import CurrentUser, require_permission

#: Declared without a prefix and mounted twice below, so ``/applicants`` and
#: ``/customers`` are two addresses for one implementation rather than two copies.
router = APIRouter(tags=["Customers"], responses=ERROR_RESPONSES)


def _resolve(service, customer_uuid: UUID):
    applicant = service.customers.get_by(uuid=customer_uuid)
    if applicant is None or applicant.deleted_at is not None:
        raise NotFoundError("customer", customer_uuid)
    return applicant


def _user_id(user: CurrentUser) -> int | None:
    try:
        return int(user.id)
    except (TypeError, ValueError):
        return None


@router.get(
    "",
    response_model=Page[CustomerSummary],
    summary="List customers",
    description="Search by name, applicant code or phone; filter by type, status or location.",
)
def list_customers(
    pagination: PaginationParams,
    service: CustomerServiceDep,
    _user: CurrentUser = Depends(require_permission("applicant:read")),
    q: Annotated[str | None, Query(description="Name, code or phone")] = None,
    applicant_type: str | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    province: str | None = None,
    district: str | None = None,
    sort: Annotated[str | None, Query(description="e.g. -created_at")] = "-created_at",
) -> Page[CustomerSummary]:
    rows, total = service.list_customers(
        offset=pagination.offset,
        limit=pagination.limit,
        q=q,
        applicant_type=applicant_type,
        status=status_filter,
        province=province,
        district=district,
        sort=sort,
    )
    return Page.build(
        [CustomerSummary.model_validate(r) for r in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.post(
    "",
    response_model=CustomerDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Register a customer",
    description=(
        "The national identifier is stored encrypted and never returned; responses carry "
        "a masked form. A duplicate identifier returns 409 with the existing record."
    ),
)
def create_customer(
    payload: CustomerCreate,
    service: CustomerServiceDep,
    user: CurrentUser = Depends(require_permission("applicant:create")),
) -> CustomerDetail:
    applicant = service.create(payload.model_dump(), created_by=_user_id(user))
    return customer_detail(applicant, None)


@router.get(
    "/{customer_id}",
    response_model=CustomerDetail,
    summary="Get a customer",
    description="Includes the current financial profile version and existing obligations.",
)
def get_customer(
    customer_id: UUID,
    service: CustomerServiceDep,
    _user: CurrentUser = Depends(require_permission("applicant:read")),
) -> CustomerDetail:
    applicant = _resolve(service, customer_id)
    full = service.get(applicant.id)
    return customer_detail(full, service.current_financial_profile(applicant.id))


@router.patch(
    "/{customer_id}",
    response_model=CustomerDetail,
    summary="Update an applicant",
    description=(
        "The documented verb (Doc 06 §6.14). The national identifier and applicant code "
        "are immutable and are ignored if supplied: changing them would orphan every "
        "decision already made."
    ),
)
@router.put(
    "/{customer_id}",
    response_model=CustomerDetail,
    summary="Update an applicant (legacy verb)",
    description=(
        "Retained for clients written against the earlier implementation. Same handler, "
        "same validation; PATCH is canonical."
    ),
    deprecated=True,
)
def update_customer(
    customer_id: UUID,
    payload: CustomerUpdate,
    service: CustomerServiceDep,
    user: CurrentUser = Depends(require_permission("applicant:update")),
) -> CustomerDetail:
    applicant = _resolve(service, customer_id)
    updated = service.update(
        applicant.id, payload.model_dump(exclude_unset=True), updated_by=_user_id(user)
    )
    return customer_detail(updated, service.current_financial_profile(applicant.id))


@router.post(
    "/{customer_id}/financials",
    response_model=FinancialProfileOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record a financial profile",
    description=(
        "Creates a new **version** and marks it current. Totals are derived from the "
        "obligation rows and cannot be supplied directly."
    ),
)
def create_financial_profile(
    customer_id: UUID,
    payload: FinancialProfileCreate,
    service: CustomerServiceDep,
    user: CurrentUser = Depends(require_permission("applicant:update")),
) -> FinancialProfileOut:
    applicant = _resolve(service, customer_id)
    data = payload.model_dump()
    data["obligations"] = list(data.get("obligations", []))
    profile = service.replace_financial_profile(
        applicant.id, data, created_by=_user_id(user)
    )
    return FinancialProfileOut.model_validate(profile)


# ---------------------------------------------------------------------------
# Credit assessment
# ---------------------------------------------------------------------------
@router.post(
    "/{customer_id}/credit-assess",
    response_model=CreditAssessmentOut,
    status_code=status.HTTP_201_CREATED,
    tags=["Credit"],
    summary="Run a credit assessment",
    description=(
        "Delegates to CreditService, which resolves the active CUSTOMER configuration, "
        "assembles the engine input from the applicant, the current financial profile, "
        "the bureau report and the vehicle economics, and calls the customer scoring "
        "engine once. Knock-outs short-circuit inside the engine: a blacklisted or "
        "defaulted applicant returns score 0, grade E and no component breakdown."
    ),
)
def credit_assess(
    customer_id: UUID,
    customers: CustomerServiceDep,
    credit: CreditServiceDep,
    payload: CreditAssessRequest | None = None,
    user: CurrentUser = Depends(require_permission("application:assess")),
) -> CreditAssessmentOut:
    applicant = _resolve(customers, customer_id)
    request = payload or CreditAssessRequest()
    result = credit.assess(
        applicant.id,
        scored_by=_user_id(user) or 1,
        application_id=request.application_id,
        as_of=request.as_of,
    )
    return credit_assessment_from_result(result)


@router.get(
    "/{customer_id}/credit-assessments",
    response_model=Page[CreditAssessmentOut],
    tags=["Credit"],
    summary="List a customer's credit assessments",
    description="Newest first, across every application. Credit scores are immutable.",
)
def list_credit_assessments(
    customer_id: UUID,
    pagination: PaginationParams,
    customers: CustomerServiceDep,
    credit: CreditServiceDep,
    _user: CurrentUser = Depends(require_permission("application:read")),
) -> Page[CreditAssessmentOut]:
    applicant = _resolve(customers, customer_id)
    rows, total = credit.list_assessments(
        applicant.id, offset=pagination.offset, limit=pagination.limit
    )
    return Page.build(
        [credit_assessment_from_row(r) for r in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.post(
    "/{customer_id}/credit-report",
    response_model=BureauReportOut,
    status_code=status.HTTP_201_CREATED,
    tags=["Credit"],
    summary="Fetch a credit bureau report",
    description=(
        "FR-3.5. Pulls through the configured provider (CIB_PROVIDER) - the mock adapter "
        "in the MVP, a live bureau behind the same interface later - and persists the raw "
        "response. An enquiry is billed per call, so a report still inside its validity "
        "window is reused and flagged is_cached; pass force_refresh to re-purchase. The "
        "raw payload is stored but never returned, and the applicant identifier never "
        "appears in the response."
    ),
)
def fetch_credit_report(
    customer_id: UUID,
    customers: CustomerServiceDep,
    bureau: BureauServiceDep,
    payload: BureauFetchRequest | None = None,
    user: CurrentUser = Depends(require_permission("applicant:credit_check")),
) -> BureauReportOut:
    applicant = _resolve(customers, customer_id)
    request = payload or BureauFetchRequest()
    result = bureau.fetch(
        applicant.id,
        fetched_by=_user_id(user) or 1,
        force_refresh=request.force_refresh,
    )
    return _bureau_out_row(result.report, is_cached=result.is_cached)


@router.get(
    "/{customer_id}/credit-report",
    response_model=BureauReportOut,
    tags=["Credit"],
    summary="Latest valid bureau report",
    description="Returns the cached report if one is still inside its validity window.",
)
def latest_credit_report(
    customer_id: UUID,
    customers: CustomerServiceDep,
    bureau: BureauServiceDep,
    _user: CurrentUser = Depends(require_permission("applicant:read")),
) -> BureauReportOut:
    applicant = _resolve(customers, customer_id)
    row = bureau.latest_for(applicant.id, datetime.now(UTC).date())
    if row is None:
        raise NotFoundError("credit bureau report", customer_id)
    return _bureau_out_row(row, is_cached=True)


def _bureau_out_row(row, *, is_cached: bool) -> BureauReportOut:
    """Presentation only. raw_response is deliberately not mapped: it is retained for
    audit and dispute handling, not for the API surface."""
    return BureauReportOut(
        provider=row.provider,
        enquiry_id=row.enquiry_id,
        enquiry_date=row.enquiry_date,
        valid_until=row.valid_until,
        bureau_score=row.bureau_score,
        bureau_grade=row.bureau_grade,
        credit_history_months=row.credit_history_months,
        active_loan_count=row.active_loan_count,
        total_outstanding=row.total_outstanding,
        total_monthly_emi=row.total_monthly_emi,
        previous_default_count=row.previous_default_count,
        current_overdue_amount=row.current_overdue_amount,
        max_dpd_last_24m=row.max_dpd_last_24m,
        is_blacklisted=row.is_blacklisted,
        enquiries_last_6m=row.enquiries_last_6m,
        repayment_history=list(row.repayment_history or []),
        is_cached=is_cached,
        created_at=row.created_at,
    )


# ---------------------------------------------------------------------------
# Mounting — Doc 06 §6.14 names this resource ``/applicants``
# ---------------------------------------------------------------------------
# The first implementation shipped it as ``/customers``. The documented path is canonical;
# the old one is retained, deprecated, for clients already written against it. Both mounts
# include the *same* router, so there is one set of handlers, one service call per endpoint
# and one place to change. Removing the alias later is deleting one include.
applicants_router = APIRouter()
applicants_router.include_router(router, prefix="/applicants")

legacy_router = APIRouter()
legacy_router.include_router(router, prefix="/customers", deprecated=True)
