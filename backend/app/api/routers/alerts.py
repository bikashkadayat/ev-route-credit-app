"""Alert endpoints — Doc 06 §6.10.

``/evaluate`` delegates to ``AlertService``, which owns the single call to
``evaluate_rules``. No rule condition, threshold or supersession decision appears here.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.schemas.common import ERROR_RESPONSES, Page
from app.api.schemas.credit import (
    AlertActivityOut,
    AlertActivityRequest,
    AlertAssignRequest,
    AlertDetail,
    AlertEscalateRequest,
    AlertEvaluateRequest,
    AlertEvaluationOut,
    AlertResolveRequest,
    AlertStartWorkRequest,
    AlertSummary,
    SlaStateOut,
)
from app.core.dependencies import AlertServiceDep, PaginationParams
from app.core.errors import NotFoundError, ValidationError
from app.core.permissions import (
    CurrentUser,
    require_any_permission,
    require_permission,
)

router = APIRouter(prefix="/alerts", tags=["Alerts"], responses=ERROR_RESPONSES)


def _resolve(service, alert_uuid: UUID):
    alert = service.alerts.get_by(uuid=alert_uuid)
    if alert is None:
        raise NotFoundError("alert", alert_uuid)
    return alert


def _user_id(user: CurrentUser) -> int:
    try:
        return int(user.id)
    except (TypeError, ValueError):
        return 1


@router.get(
    "",
    response_model=Page[AlertSummary],
    summary="List risk alerts",
    description=(
        "The working queue. Default order is the one an officer needs: most severe "
        "first, then oldest."
    ),
)
def list_alerts(
    pagination: PaginationParams,
    service: AlertServiceDep,
    _user: CurrentUser = Depends(require_permission("alert:read")),
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    severity: Annotated[str | None, Query(description="YELLOW or RED")] = None,
    category: str | None = None,
    loan_id: int | None = None,
    assigned_to: int | None = None,
    unassigned: bool | None = None,
    sort: str | None = None,
) -> Page[AlertSummary]:
    rows, total = service.list_alerts(
        offset=pagination.offset,
        limit=pagination.limit,
        status=status_filter,
        severity=severity,
        category=category,
        loan_id=loan_id,
        assigned_to=assigned_to,
        unassigned=unassigned,
        sort=sort,
    )
    return Page.build(
        [AlertSummary.model_validate(r) for r in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get(
    "/{alert_id}",
    response_model=AlertDetail,
    summary="Get an alert",
    description=(
        "Includes the metric evidence that fired the rule — value, operator and threshold "
        "per condition — plus the activity timeline."
    ),
)
def get_alert(
    alert_id: UUID,
    service: AlertServiceDep,
    _user: CurrentUser = Depends(require_permission("alert:read")),
) -> AlertDetail:
    alert = _resolve(service, alert_id)
    return AlertDetail.model_validate(service.get_alert(alert.id))


@router.post(
    "/evaluate",
    response_model=AlertEvaluationOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Evaluate the risk rules",
    description=(
        "Runs the 22-rule catalogue against the active book, or one loan when `loan_id` "
        "is given. Idempotent for a business date: a live alert is refreshed rather than "
        "duplicated, a cleared condition auto-resolves, and a higher-severity sibling "
        "supersedes a lower one."
    ),
)
def evaluate_alerts(
    service: AlertServiceDep,
    payload: AlertEvaluateRequest | None = None,
    _user: CurrentUser = Depends(require_permission("risk:config:publish")),
) -> AlertEvaluationOut:
    request = payload or AlertEvaluateRequest()
    run = service.evaluate(as_of=request.as_of, loan_id=request.loan_id)
    return AlertEvaluationOut(
        as_of=run.as_of,
        engine_version=run.engine_version,
        loans_evaluated=run.loans_evaluated,
        alerts_created=run.alerts_created,
        alerts_refreshed=run.alerts_refreshed,
        alerts_superseded=run.alerts_superseded,
        alerts_auto_resolved=run.alerts_auto_resolved,
        results=[
            {
                "loan_id": r.loan_id,
                "risk_status": r.risk_status,
                "created": list(r.created),
                "refreshed": list(r.refreshed),
                "superseded": list(r.superseded),
                "auto_resolved": list(r.auto_resolved),
            }
            for r in run.results
        ],
    )


@router.post(
    "/{alert_id}/acknowledge",
    response_model=AlertDetail,
    summary="Acknowledge an alert",
    description="Stops the acknowledge SLA clock and records an activity entry.",
)
def acknowledge_alert(
    alert_id: UUID,
    service: AlertServiceDep,
    user: CurrentUser = Depends(require_permission("alert:assign")),
) -> AlertDetail:
    alert = _resolve(service, alert_id)
    return AlertDetail.model_validate(
        service.acknowledge(alert.id, user_id=_user_id(user))
    )


@router.post(
    "/{alert_id}/resolve",
    response_model=AlertDetail,
    summary="Resolve an alert",
    description=(
        "Requires a resolution code and notes of at least 10 characters, or 20 for "
        "FALSE_POSITIVE. An already-closed alert returns 409."
    ),
)
def resolve_alert(
    alert_id: UUID,
    payload: AlertResolveRequest,
    service: AlertServiceDep,
    user: CurrentUser = Depends(require_permission("alert:resolve")),
) -> AlertDetail:
    if payload.resolution_code == "FALSE_POSITIVE" and len(payload.resolution_notes) < 20:
        raise ValidationError(
            "A false positive needs at least 20 characters of explanation",
            details=[{"field": "resolution_notes", "code": "TOO_SHORT",
                      "message": "At least 20 characters for FALSE_POSITIVE"}],
        )
    alert = _resolve(service, alert_id)
    return AlertDetail.model_validate(
        service.resolve(
            alert.id,
            user_id=_user_id(user),
            resolution_code=payload.resolution_code,
            notes=payload.resolution_notes,
        )
    )

@router.post(
    "/{alert_id}/assign",
    response_model=AlertDetail,
    summary="Assign an alert",
    description=(
        "FR-7.8. Assignment is not a state change: an OPEN alert stays OPEN when it is "
        "handed to someone, because the SLA clock measures the work, not the ownership. "
        "409 if the alert is already closed."
    ),
)
def assign_alert(
    alert_id: UUID,
    payload: AlertAssignRequest,
    service: AlertServiceDep,
    user: CurrentUser = Depends(require_permission("alert:assign")),
) -> AlertDetail:
    alert = _resolve(service, alert_id)
    return AlertDetail.model_validate(
        service.assign(
            alert.id,
            assignee_id=payload.assignee_id,
            user_id=_user_id(user),
            note=payload.note,
        )
    )


@router.post(
    "/{alert_id}/start",
    response_model=AlertDetail,
    summary="Begin the investigation",
    description="FR-7.7 — OPEN or ACKNOWLEDGED becomes IN_PROGRESS.",
)
def start_alert(
    alert_id: UUID,
    service: AlertServiceDep,
    payload: AlertStartWorkRequest | None = None,
    user: CurrentUser = Depends(require_permission("alert:assign")),
) -> AlertDetail:
    alert = _resolve(service, alert_id)
    request = payload or AlertStartWorkRequest()
    return AlertDetail.model_validate(
        service.start_work(alert.id, user_id=_user_id(user), note=request.note)
    )


@router.post(
    "/{alert_id}/escalate",
    response_model=AlertDetail,
    summary="Escalate an alert",
    description=(
        "FR-7.7, BR-5. Hands the alert up, optionally reassigning it. The reason is "
        "mandatory: an escalation with no stated cause tells the next officer nothing."
    ),
)
def escalate_alert(
    alert_id: UUID,
    payload: AlertEscalateRequest,
    service: AlertServiceDep,
    user: CurrentUser = Depends(require_permission("alert:assign")),
) -> AlertDetail:
    alert = _resolve(service, alert_id)
    return AlertDetail.model_validate(
        service.escalate(
            alert.id,
            user_id=_user_id(user),
            reason=payload.reason,
            assignee_id=payload.assignee_id,
        )
    )


@router.post(
    "/{alert_id}/activities",
    response_model=AlertActivityOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record an investigation note",
    description=(
        "FR-7.7. Permitted on a closed alert too — a note recording what was learned "
        "afterwards is exactly the kind of thing that should not be lost."
    ),
)
def add_alert_activity(
    alert_id: UUID,
    payload: AlertActivityRequest,
    service: AlertServiceDep,
    # Recording a note is a write, so reading the queue is not enough. A field officer who
    # may resolve an alert may certainly annotate it, and so may whoever it is assigned to.
    user: CurrentUser = Depends(
        require_any_permission("alert:assign", "alert:resolve")
    ),
) -> AlertActivityOut:
    alert = _resolve(service, alert_id)
    row = service.add_activity(
        alert.id,
        user_id=_user_id(user),
        activity_type=payload.activity_type,
        description=payload.description,
        metadata=payload.metadata,
    )
    return AlertActivityOut.model_validate(row)


@router.get(
    "/{alert_id}/activities",
    response_model=list[AlertActivityOut],
    summary="Investigation timeline",
    description="Oldest first, so the history reads as a story.",
)
def list_alert_activities(
    alert_id: UUID,
    service: AlertServiceDep,
    _user: CurrentUser = Depends(require_permission("alert:read")),
) -> list[AlertActivityOut]:
    alert = _resolve(service, alert_id)
    return [AlertActivityOut.model_validate(a) for a in service.activities(alert.id)]


@router.get(
    "/{alert_id}/sla",
    response_model=SlaStateOut,
    summary="SLA countdown",
    description=(
        "BR-5. Seconds remaining against the acknowledge and resolve deadlines, and "
        "whether either has been breached. An acknowledged alert stops its acknowledge "
        "clock; a closed one stops both."
    ),
)
def alert_sla(
    alert_id: UUID,
    service: AlertServiceDep,
    _user: CurrentUser = Depends(require_permission("alert:read")),
) -> SlaStateOut:
    from datetime import UTC, datetime

    alert = _resolve(service, alert_id)
    return SlaStateOut(**service.sla_state(alert, datetime.now(UTC)))
