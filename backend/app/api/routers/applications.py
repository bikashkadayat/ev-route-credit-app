"""Loan application endpoints — Doc 06 §6.6.

Thin throughout. The state machine, the maker-checker rule, the override rules and the
whole assessment orchestration live in ``ApplicationService``; every handler below parses,
authorises, calls one service method and maps the result.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.mappers import application_detail, application_summary, decision_out
from app.api.mappers_loan import application_assessment_out, loan_detail_out
from app.api.schemas.application import (
    ApplicationAssessmentOut,
    ApplicationCreate,
    ApplicationDetail,
    ApplicationSummary,
    AssessRequest,
    DecisionOut,
    DecisionRequest,
    WithdrawRequest,
)
from app.api.schemas.common import ERROR_RESPONSES, Page
from app.api.schemas.loan import BookedLoanOut, BookLoanRequest, ScheduleSummaryOut
from app.core.dependencies import (
    ApplicationServiceDep,
    LoanServiceDep,
    PaginationParams,
)
from app.core.permissions import CurrentUser, require_permission

router = APIRouter(
    prefix="/applications", tags=["Applications"], responses=ERROR_RESPONSES
)


def _detail(service, application) -> ApplicationDetail:
    """Assemble the detail response from the rows the service resolves."""
    context = service.detail_context(application)
    return application_detail(
        application,
        applicant=context.applicant,
        route=context.route,
        vehicle=context.vehicle,
        financial_version=context.financial_version,
        latest_assessment=context.latest_assessment,
        decisions=context.decisions,
    )


def _user_id(user: CurrentUser) -> int:
    """Token subjects are UUIDs in production and integers in tests; both are accepted."""
    try:
        return int(user.id)
    except (TypeError, ValueError):
        return 1


@router.get(
    "",
    response_model=Page[ApplicationSummary],
    summary="List applications",
    description="Filterable, paginated pipeline view. Newest first by default.",
)
def list_applications(
    pagination: PaginationParams,
    service: ApplicationServiceDep,
    _user: CurrentUser = Depends(require_permission("application:read")),
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    applicant_id: UUID | None = None,
    route_id: UUID | None = None,
    branch_code: str | None = None,
    q: Annotated[str | None, Query(description="Application number")] = None,
    sort: str | None = "-created_at",
) -> Page[ApplicationSummary]:
    rows, total = service.list_applications(
        offset=pagination.offset,
        limit=pagination.limit,
        status=status_filter,
        applicant_id=service.resolve_applicant_id(applicant_id) if applicant_id else None,
        route_id=service.resolve_route_id(route_id) if route_id else None,
        branch_code=branch_code,
        q=q,
        sort=sort,
    )
    return Page.build(
        [application_summary(r) for r in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.post(
    "",
    response_model=ApplicationDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create an application",
    description=(
        "Creates a DRAFT. The applicant's current financial profile and the vehicle's "
        "on-road price are snapshotted onto the application, so a later edit to either "
        "cannot silently rewrite a decision already taken. 422 when the applicant has no "
        "current financial profile."
    ),
)
def create_application(
    payload: ApplicationCreate,
    service: ApplicationServiceDep,
    user: CurrentUser = Depends(require_permission("application:create")),
) -> ApplicationDetail:
    application = service.create_from_uuids(
        payload.model_dump(), created_by=_user_id(user)
    )
    return _detail(service, application)


@router.get(
    "/{application_id}",
    response_model=ApplicationDetail,
    summary="Get an application",
    description="Includes the latest assessment summary and the full decision history.",
)
def get_application(
    application_id: UUID,
    service: ApplicationServiceDep,
    _user: CurrentUser = Depends(require_permission("application:read")),
) -> ApplicationDetail:
    return _detail(service, service.by_uuid(application_id))


@router.patch(
    "/{application_id}",
    response_model=ApplicationDetail,
    summary="Edit a draft application",
    description=(
        "Partial update, permitted only while the application is a DRAFT. Once submitted "
        "the commercial terms are what the assessment runs against, so they are frozen: "
        "409 otherwise."
    ),
)
def update_application(
    application_id: UUID,
    payload: dict,
    service: ApplicationServiceDep,
    user: CurrentUser = Depends(require_permission("application:create")),
) -> ApplicationDetail:
    application = service.by_uuid(application_id)
    updated = service.update_application(
        application.id, payload, updated_by=_user_id(user)
    )
    return _detail(service, updated)


@router.post(
    "/{application_id}/submit",
    response_model=ApplicationDetail,
    summary="Submit for assessment",
    description="DRAFT → SUBMITTED. Any other starting status is a 409.",
)
def submit_application(
    application_id: UUID,
    service: ApplicationServiceDep,
    user: CurrentUser = Depends(require_permission("application:create")),
) -> ApplicationDetail:
    application = service.by_uuid(application_id)
    updated = service.submit_application(application.id, submitted_by=_user_id(user))
    return _detail(service, updated)


@router.post(
    "/{application_id}/assess",
    response_model=ApplicationAssessmentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Run the full assessment",
    description=(
        "Runs, in order: knock-outs, route assessment (reusing a recent one unless asked "
        "to re-score), customer score, vehicle economics, combined score, structuring and "
        "the decision matrix. Persists the result and moves the application to "
        "PENDING_DECISION.\n\n"
        "A knock-out is **not** an error: it returns 201 with `recommendation: REJECT` and "
        "the triggered knock-outs listed."
    ),
)
def assess_application(
    application_id: UUID,
    service: ApplicationServiceDep,
    payload: AssessRequest | None = None,
    user: CurrentUser = Depends(require_permission("application:assess")),
) -> ApplicationAssessmentOut:
    application = service.by_uuid(application_id)
    request = payload or AssessRequest()
    result = service.assess_application(
        application.id,
        assessed_by=_user_id(user),
        force_route_reassessment=request.force_route_reassessment,
    )
    return application_assessment_out(result)


@router.post(
    "/{application_id}/decision",
    response_model=DecisionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record the underwriting decision",
    description=(
        "Requires the application to be PENDING_DECISION with a completed assessment.\n\n"
        "**Maker-checker:** the officer who raised or submitted the application cannot "
        "decide it (409 `FOUR_EYES_REQUIRED`).\n\n"
        "**Override:** a decision differing from the system recommendation needs the "
        "`application:override` permission and a justification of at least 20 characters. "
        "The original recommendation is preserved on the decision record, so the override "
        "trail stays readable."
    ),
)
def make_decision(
    application_id: UUID,
    payload: DecisionRequest,
    service: ApplicationServiceDep,
    user: CurrentUser = Depends(require_permission("application:approve")),
) -> DecisionOut:
    application = service.by_uuid(application_id)
    decision = service.make_decision(
        application.id,
        decided_by=_user_id(user),
        final_decision=payload.final_decision,
        approved_amount=payload.approved_amount,
        approved_tenure_months=payload.approved_tenure_months,
        approved_interest_rate=payload.approved_interest_rate,
        conditions=payload.conditions,
        override_justification=payload.override_justification,
        can_override=user.has("application:override"),
    )
    return decision_out(decision)


@router.post(
    "/{application_id}/withdraw",
    response_model=ApplicationDetail,
    summary="Withdraw an application",
    description=(
        "Permitted from DRAFT, SUBMITTED or UNDER_ASSESSMENT. A withdrawn application is "
        "terminal — it cannot be resubmitted."
    ),
)
def withdraw_application(
    application_id: UUID,
    service: ApplicationServiceDep,
    payload: WithdrawRequest | None = None,
    user: CurrentUser = Depends(require_permission("application:create")),
) -> ApplicationDetail:
    application = service.by_uuid(application_id)
    request = payload or WithdrawRequest()
    updated = service.withdraw_application(
        application.id, withdrawn_by=_user_id(user), reason=request.reason
    )
    return _detail(service, updated)


@router.post(
    "/{application_id}/book",
    response_model=BookedLoanOut,
    status_code=status.HTTP_201_CREATED,
    tags=["Loans"],
    summary="Book the approved loan",
    description=(
        "Turns an APPROVED application into a live loan and generates the full "
        "amortisation schedule atomically. The commercial terms come from the approval "
        "decision, not from the request body. Sets the vehicle to FINANCED and the "
        "application to DISBURSED. 409 if the application is not approved or has already "
        "been disbursed."
    ),
)
def book_loan(
    application_id: UUID,
    applications: ApplicationServiceDep,
    loans: LoanServiceDep,
    payload: BookLoanRequest | None = None,
    user: CurrentUser = Depends(require_permission("loan:create")),
) -> BookedLoanOut:
    application = applications.by_uuid(application_id)
    request = payload or BookLoanRequest()
    booked = loans.book_loan(
        application.id,
        booked_by=_user_id(user),
        disbursement_date=request.disbursement_date,
        first_emi_date=request.first_emi_date,
        assigned_officer_id=request.assigned_officer_id,
    )
    return BookedLoanOut(
        loan=loan_detail_out(booked.loan, application=application, schedule=booked.schedule),
        schedule_summary=ScheduleSummaryOut(
            installment_count=len(booked.schedule),
            first_due=booked.schedule[0].due_date if booked.schedule else None,
            last_due=booked.schedule[-1].due_date if booked.schedule else None,
            total_principal=booked.loan.principal_amount,
            total_interest=booked.total_interest,
            total_payable=booked.total_payable,
        ),
    )


@router.get(
    "/{application_id}/decisions",
    response_model=list[DecisionOut],
    summary="Decision history",
    description=(
        "Oldest first. An override adds a decision rather than editing one, so this is the "
        "complete escalation trail."
    ),
)
def decision_history(
    application_id: UUID,
    service: ApplicationServiceDep,
    _user: CurrentUser = Depends(require_permission("application:read")),
) -> list[DecisionOut]:
    application = service.by_uuid(application_id)
    return [decision_out(d) for d in service.decision_history(application.id)]
