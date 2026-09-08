"""Vehicle catalogue and stateless scoring — Doc 06 §6.5, §6.7."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.schemas.catalog import (
    EmiOut,
    EmiRequest,
    FinalScoringRequest,
    ScoreOut,
    VehicleCreate,
    VehicleModelCreate,
    VehicleModelOut,
    VehicleOut,
)
from app.api.schemas.common import ERROR_RESPONSES, Page
from app.core.dependencies import (
    CatalogServiceDep,
    PaginationParams,
    ScoringServiceDep,
)
from app.core.permissions import CurrentUser, require_permission

router = APIRouter(tags=["Catalogue"], responses=ERROR_RESPONSES)


def _user_id(user: CurrentUser) -> int | None:
    try:
        return int(user.id)
    except (TypeError, ValueError):
        return None


def _score_out(result: Any) -> ScoreOut:
    """Reshape an engine result. Presentation only — nothing is recomputed."""
    from app.api.mappers import factors, score_components

    return ScoreOut(
        total_score=result.total_score,
        grade=result.grade,
        grade_label=result.grade_label,
        risk_level=result.risk_level,
        components=score_components(result),
        risk_factors=factors(result.risk_factors),
        positive_factors=factors(result.positive_factors),
        explanation=result.explanation,
        extras={
            k: v for k, v in (result.extras or {}).items() if not k.startswith("_")
        },
        engine_version=result.engine_version,
        config_version=result.config_version_no,
    )


# ---------------------------------------------------------------------------
# Vehicle models
# ---------------------------------------------------------------------------
@router.get(
    "/vehicle-models",
    response_model=Page[VehicleModelOut],
    summary="List vehicle models",
    description=(
        "The catalogue an officer picks from. The economics inputs the engine uses — "
        "battery, real-world range, maintenance cost, warranty — live here rather than "
        "being retyped per application, so two applications for the same vehicle cannot "
        "be scored on different assumptions."
    ),
)
def list_vehicle_models(
    pagination: PaginationParams,
    service: CatalogServiceDep,
    _user: CurrentUser = Depends(require_permission("route:read")),
    category: str | None = None,
    brand: str | None = None,
    is_active: bool | None = True,
    q: Annotated[str | None, Query(description="Brand or model")] = None,
    sort: str | None = "brand",
) -> Page[VehicleModelOut]:
    rows, total = service.list_models(
        offset=pagination.offset,
        limit=pagination.limit,
        category=category,
        brand=brand,
        is_active=is_active,
        q=q,
        sort=sort,
    )
    return Page.build(
        [VehicleModelOut.model_validate(r) for r in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get(
    "/vehicle-models/{model_id}",
    response_model=VehicleModelOut,
    summary="Get a vehicle model",
)
def get_vehicle_model(
    model_id: int,
    service: CatalogServiceDep,
    _user: CurrentUser = Depends(require_permission("route:read")),
) -> VehicleModelOut:
    return VehicleModelOut.model_validate(service.get_model(model_id))


@router.post(
    "/vehicle-models",
    response_model=VehicleModelOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a vehicle model",
    description=(
        "Reference data. A duplicate brand/model/variant is refused: two rows for one "
        "vehicle would let two applications score it from different economics."
    ),
)
def create_vehicle_model(
    payload: VehicleModelCreate,
    service: CatalogServiceDep,
    user: CurrentUser = Depends(require_permission("settings:update")),
) -> VehicleModelOut:
    return VehicleModelOut.model_validate(
        service.create_model(payload.model_dump(), created_by=_user_id(user))
    )


# ---------------------------------------------------------------------------
# Vehicles
# ---------------------------------------------------------------------------
@router.get(
    "/vehicles",
    response_model=Page[VehicleOut],
    summary="List vehicles",
)
def list_vehicles(
    pagination: PaginationParams,
    service: CatalogServiceDep,
    _user: CurrentUser = Depends(require_permission("route:read")),
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    vehicle_model_id: int | None = None,
    q: Annotated[str | None, Query(description="Registration number")] = None,
) -> Page[VehicleOut]:
    rows, total = service.list_vehicles(
        offset=pagination.offset,
        limit=pagination.limit,
        status=status_filter,
        vehicle_model_id=vehicle_model_id,
        q=q,
    )
    return Page.build(
        [_vehicle_out(r) for r in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/vehicles/{vehicle_id}", response_model=VehicleOut, summary="Get a vehicle")
def get_vehicle(
    vehicle_id: UUID,
    service: CatalogServiceDep,
    _user: CurrentUser = Depends(require_permission("route:read")),
) -> VehicleOut:
    return _vehicle_out(service.get_vehicle(vehicle_id))


@router.post(
    "/vehicles",
    response_model=VehicleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a vehicle",
    description=(
        "The individual unit a loan will be secured on. Defaults its purchase price from "
        "the model's on-road price. A duplicate registration number is refused."
    ),
)
def create_vehicle(
    payload: VehicleCreate,
    service: CatalogServiceDep,
    user: CurrentUser = Depends(require_permission("application:create")),
) -> VehicleOut:
    return _vehicle_out(
        service.create_vehicle(
            payload.model_dump(exclude_none=True), created_by=_user_id(user)
        )
    )


def _vehicle_out(row: Any) -> VehicleOut:
    model = getattr(row, "model", None)
    return VehicleOut(
        id=row.uuid,
        vehicle_model_id=row.vehicle_model_id,
        model_name=f"{model.brand} {model.model}" if model else None,
        registration_number=row.registration_number,
        chassis_number=row.chassis_number,
        manufacture_year=row.manufacture_year,
        purchase_price=row.purchase_price,
        is_new=row.is_new,
        odometer_km=row.odometer_km,
        telematics_device_id=row.telematics_device_id,
        telematics_status=row.telematics_status,
        status=str(row.status),
        created_at=row.created_at,
    )


# ---------------------------------------------------------------------------
# Stateless scoring — Doc 06 §6.7
# ---------------------------------------------------------------------------
scoring_router = APIRouter(
    prefix="/scoring", tags=["Scoring"], responses=ERROR_RESPONSES
)


@scoring_router.post(
    "/route",
    response_model=ScoreOut,
    summary="Score a route without saving",
    description=(
        "The same engine and the same active configuration as `/routes/{id}/assess`, with "
        "the result returned instead of stored. Used by the what-if controls, so a figure "
        "shown to an officer can never differ from the one that would be persisted."
    ),
)
def score_route(
    payload: dict,
    service: ScoringServiceDep,
    _user: CurrentUser = Depends(require_permission("route:assess")),
) -> ScoreOut:
    return _score_out(service.score_route(payload))


@scoring_router.post(
    "/customer",
    response_model=ScoreOut,
    summary="Score a borrower without saving",
    description=(
        "Knock-outs run first, exactly as in the persisted path: a blacklisted or defaulted "
        "applicant returns score 0 and grade E with no component breakdown."
    ),
)
def score_customer(
    payload: dict,
    service: ScoringServiceDep,
    _user: CurrentUser = Depends(require_permission("application:assess")),
) -> ScoreOut:
    return _score_out(service.score_customer(payload))


@scoring_router.post(
    "/final",
    summary="Combined score without saving",
    description=(
        "What powers the amount and tenure sliders: change the structure and watch the "
        "final score, DSCR, EMI, FOIR and LTV move. Returns the same shape as the `final` "
        "block of `/applications/{id}/assess`."
    ),
)
def score_final(
    payload: FinalScoringRequest,
    service: ScoringServiceDep,
    _user: CurrentUser = Depends(require_permission("application:assess")),
) -> dict:
    return service.score_final(payload.model_dump()).to_dict()


@scoring_router.post(
    "/emi",
    response_model=EmiOut,
    summary="EMI calculator",
    description="The same reducing-balance function the amortisation schedule is built from.",
)
def score_emi(
    payload: EmiRequest,
    service: ScoringServiceDep,
    _user: CurrentUser = Depends(require_permission("application:assess")),
) -> EmiOut:
    result = service.emi(
        Decimal(payload.principal), Decimal(payload.annual_rate), payload.tenure_months
    )
    return EmiOut(
        emi=result.emi,
        total_interest=result.total_interest,
        total_payable=result.total_payable,
    )
