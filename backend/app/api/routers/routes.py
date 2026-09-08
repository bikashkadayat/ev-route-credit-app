"""Route endpoints — Doc 06 §6.3.

Every handler is: authorise (via the dependency), call the service, map the result. The
assessment endpoint delegates to ``RouteAssessmentService``, which owns the single call to
``RouteScoringEngine``. Nothing here touches the engine.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.mappers import route_assessment_from_result, route_assessment_from_row
from app.api.schemas.common import ERROR_RESPONSES, Page
from app.api.schemas.route import (
    AssessRouteRequest,
    ChargingStationOut,
    RouteAssessmentOut,
    RouteCreate,
    RouteDetail,
    RouteSummary,
    RouteUpdate,
)
from app.core.dependencies import PaginationParams, RouteServiceDep
from app.core.errors import NotFoundError
from app.core.permissions import CurrentUser, require_permission

router = APIRouter(prefix="/routes", tags=["Routes"], responses=ERROR_RESPONSES)


def _resolve(service, route_uuid: UUID):
    """Resolve the public UUID to the internal row.

    The API exposes UUIDs, never the BIGSERIAL, so ids cannot be enumerated
    (Doc 05 §5.1 principle 4).
    """
    route = service.routes.get_by(uuid=route_uuid)
    if route is None or route.deleted_at is not None:
        raise NotFoundError("route", route_uuid)
    return route


@router.get(
    "",
    response_model=Page[RouteSummary],
    summary="List routes",
    description=(
        "Paginated, filterable list of operating corridors. Sorting is restricted to an "
        "allow-list; the default is highest score first."
    ),
)
def list_routes(
    pagination: PaginationParams,
    service: RouteServiceDep,
    _user: CurrentUser = Depends(require_permission("route:read")),
    q: Annotated[str | None, Query(description="Name, code, origin or destination")] = None,
    grade: Annotated[list[str] | None, Query(description="A, B or C")] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    province: str | None = None,
    district: str | None = None,
    min_score: Annotated[Decimal | None, Query(ge=0, le=100)] = None,
    max_score: Annotated[Decimal | None, Query(ge=0, le=100)] = None,
    sort: Annotated[str | None, Query(description="e.g. -latest_score")] = "-latest_score",
) -> Page[RouteSummary]:
    rows, total = service.list_routes(
        offset=pagination.offset,
        limit=pagination.limit,
        q=q,
        grade=grade,
        status=status_filter,
        province=province,
        district=district,
        min_score=min_score,
        max_score=max_score,
        sort=sort,
    )
    return Page.build(
        [RouteSummary.model_validate(r) for r in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.post(
    "",
    response_model=RouteDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a route",
    description=(
        "Registers an operating corridor. Charging density and the derived daily revenue "
        "and energy cost are computed server-side and cannot be supplied by the client."
    ),
)
def create_route(
    payload: RouteCreate,
    service: RouteServiceDep,
    user: CurrentUser = Depends(require_permission("route:create")),
) -> RouteDetail:
    route = service.create_route(
        payload.model_dump(), created_by=_user_id(user)
    )
    return RouteDetail.model_validate(route)


@router.get(
    "/{route_id}",
    response_model=RouteDetail,
    summary="Get a route",
)
def get_route(
    route_id: UUID,
    service: RouteServiceDep,
    _user: CurrentUser = Depends(require_permission("route:read")),
) -> RouteDetail:
    return RouteDetail.model_validate(_resolve(service, route_id))


@router.patch(
    "/{route_id}",
    response_model=RouteDetail,
    summary="Update a route",
    description=(
        "The documented verb (Doc 06 §6.14). Editing a scoring-relevant field makes the "
        "latest assessment stale; re-assess to refresh the score."
    ),
)
@router.put(
    "/{route_id}",
    response_model=RouteDetail,
    summary="Replace a route",
    description=(
        "Retained alongside PATCH for the clients written against the earlier "
        "implementation. Both verbs call the same service and validate identically; PATCH "
        "is canonical."
    ),
    deprecated=True,
)
def update_route(
    route_id: UUID,
    payload: RouteUpdate,
    service: RouteServiceDep,
    user: CurrentUser = Depends(require_permission("route:update")),
) -> RouteDetail:
    route = _resolve(service, route_id)
    updated = service.update_route(
        route.id, payload.model_dump(), updated_by=_user_id(user)
    )
    return RouteDetail.model_validate(updated)


@router.post(
    "/{route_id}/assess",
    response_model=RouteAssessmentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Assess a route",
    description=(
        "Runs the route scoring engine against the active ROUTE configuration and stores "
        "an immutable assessment stamped with the config and engine versions that "
        "produced it. Returns 422 listing every missing field if the route is incomplete, "
        "and 409 if no configuration has been published."
    ),
)
def assess_route(
    route_id: UUID,
    service: RouteServiceDep,
    payload: AssessRouteRequest | None = None,
    user: CurrentUser = Depends(require_permission("route:assess")),
) -> RouteAssessmentOut:
    route = _resolve(service, route_id)
    request = payload or AssessRouteRequest()
    result = service.assess(
        route.id,
        assessed_by=_user_id(user) or 1,
        reference_vehicle_model_id=request.reference_vehicle_model_id,
    )
    return route_assessment_from_result(result)


@router.get(
    "/{route_id}/assessments",
    response_model=Page[RouteAssessmentOut],
    summary="List a route's assessment history",
    description="Newest first. Assessments are immutable, so this is a complete audit trail.",
)
def list_assessments(
    route_id: UUID,
    pagination: PaginationParams,
    service: RouteServiceDep,
    _user: CurrentUser = Depends(require_permission("route:read")),
) -> Page[RouteAssessmentOut]:
    route = _resolve(service, route_id)
    rows, total = service.list_assessments(
        route.id, offset=pagination.offset, limit=pagination.limit
    )
    return Page.build(
        [route_assessment_from_row(r) for r in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get(
    "/{route_id}/charging-stations",
    response_model=list[ChargingStationOut],
    summary="List the charging stations on a corridor",
)
def list_charging_stations(
    route_id: UUID,
    service: RouteServiceDep,
    _user: CurrentUser = Depends(require_permission("route:read")),
) -> list[ChargingStationOut]:
    route = _resolve(service, route_id)
    return [
        ChargingStationOut(
            station_name=s.station_name,
            operator=s.operator,
            charger_type=s.charger_type,
            power_kw=s.power_kw,
            port_count=s.port_count,
            is_operational=s.is_operational,
            distance_from_origin_km=s.distance_from_origin_km,
            verified_at=s.verified_at.isoformat() if s.verified_at else None,
        )
        for s in service.routes.stations_for(route.id)
    ]


def _user_id(user: CurrentUser) -> int | None:
    """Token subjects are UUIDs in production and integers in tests; both are accepted."""
    try:
        return int(user.id)
    except (TypeError, ValueError):
        return None


@router.delete(
    "/{route_id}",
    response_model=RouteDetail,
    summary="Retire a route",
    description=(
        "Soft delete (Doc 05 §5.1 principle 6). The corridor stops appearing in every "
        "query, but the row survives because assessments and loans reference it and must "
        "stay explainable. 409 if live loans still run on it."
    ),
)
def delete_route(
    route_id: UUID,
    service: RouteServiceDep,
    user: CurrentUser = Depends(require_permission("route:delete")),
) -> RouteDetail:
    route = _resolve(service, route_id)
    return RouteDetail.model_validate(
        service.delete_route(route.id, deleted_by=_user_id(user))
    )
