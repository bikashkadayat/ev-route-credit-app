"""RouteAssessmentService — Doc 04 §4.7.

Orchestration only. The scoring itself belongs to ``RouteScoringEngine`` and is not
reproduced, wrapped or adjusted here: this service loads the route, resolves the active
configuration, marshals the engine input, calls the engine once, and persists the result
with the config and engine versions that produced it.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import (
    ConflictError,
    DuplicateError,
    IncompleteDataError,
    NotFoundError,
)
from app.core.observability import scoring_duration_seconds, scoring_runs_total
from app.engines.base import ScoreResult
from app.engines.route_engine import (
    DEFAULT_REFERENCE_BATTERY_KWH,
    DEFAULT_REFERENCE_RANGE_KM,
    IncompleteRouteData,
    RouteScoringEngine,
    RouteScoringInput,
)
from app.models.catalog import Route, RouteAssessment, VehicleModel
from app.repositories.route import RouteAssessmentRepository, RouteRepository
from app.services.config import ConfigService

CONFIG_TYPE = "ROUTE"


@dataclass(frozen=True, slots=True)
class RouteAssessmentResult:
    """What the router turns into a response. Carries the persisted row *and* the pure
    engine result, so nothing has to be recomputed for presentation."""

    assessment: RouteAssessment
    score: ScoreResult
    route: Route
    reused: bool = False


class RouteAssessmentService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.routes = RouteRepository(session)
        self.assessments = RouteAssessmentRepository(session)
        self.config_service = ConfigService(session)
        self.engine = RouteScoringEngine()

    # -- CRUD --------------------------------------------------------------
    def get_route(self, route_id: int) -> Route:
        route = self.routes.get_active(route_id)
        if route is None:
            raise NotFoundError("route", route_id)
        return route

    def list_routes(self, **filters: Any) -> tuple[Sequence[Route], int]:
        return self.routes.list_routes(**filters)

    def create_route(self, payload: dict[str, Any], *, created_by: int | None) -> Route:
        duplicate = self.routes.find_duplicate(
            payload["origin"], payload["destination"], payload["route_name"]
        )
        if duplicate is not None:
            raise DuplicateError(
                f"A route already exists for {payload['origin']} to "
                f"{payload['destination']}",
                details={"existing_route_id": str(duplicate.uuid),
                         "route_code": duplicate.route_code},
            )
        values = dict(payload)
        values.setdefault("route_code", self._next_route_code(payload))
        values["charging_station_density"] = self._density(
            payload["charging_station_count"], payload["total_distance_km"]
        )
        values.update(self._derive_economics(payload))
        values["created_by"] = created_by
        return self.routes.create(**values)

    def update_route(
        self, route_id: int, payload: dict[str, Any], *, updated_by: int | None
    ) -> Route:
        route = self.get_route(route_id)
        values = dict(payload)
        merged = {**self._route_as_dict(route), **values}
        values["charging_station_density"] = self._density(
            merged["charging_station_count"], merged["total_distance_km"]
        )
        values.update(self._derive_economics(merged))
        values["updated_by"] = updated_by
        return self.routes.update(route, **values)

    def delete_route(
        self, route_id: int, *, deleted_by: int | None, now: datetime | None = None
    ) -> Route:
        """Doc 06 §6.14 — soft delete only.

        A corridor is referenced by every assessment and every loan booked against it, so
        it is retired rather than removed: hard-deleting it would orphan credit files that
        must stay explainable. Doc 05 §5.1 principle 6 makes the row invisible to every
        query thereafter.
        """
        route = self.get_route(route_id)
        if self.routes.has_active_loans(route.id):
            raise ConflictError(
                "This corridor has active loans booked against it and cannot be retired",
                details={"route_id": str(route.uuid)},
            )
        route.deleted_at = now or datetime.now(UTC)
        route.updated_by = deleted_by
        route.status = "SUSPENDED"
        self.session.flush()
        return route

    # -- assessment --------------------------------------------------------
    def assess(
        self,
        route_id: int,
        *,
        assessed_by: int,
        reference_vehicle_model_id: int | None = None,
        as_of: datetime | None = None,
    ) -> RouteAssessmentResult:
        """Run the engine and persist the result. The engine is called exactly once."""
        route = self.get_route(route_id)
        config = self.config_service.active_config(CONFIG_TYPE)
        payload = self.build_engine_input(route, reference_vehicle_model_id)

        started = time.perf_counter()
        try:
            score = self.engine.score(payload, config)
        except IncompleteRouteData as exc:
            # Translate the engine's domain error into the API contract (422 listing
            # every missing field) without the engine knowing HTTP exists.
            raise IncompleteDataError(
                "Route is missing data required for assessment", exc.fields
            ) from exc

        # TRD 2.11 — instrumentation lives in the service, never in the engine: a
        # counter is a side effect, and the engine has to stay a pure function.
        scoring_duration_seconds.labels("route").observe(time.perf_counter() - started)
        scoring_runs_total.labels("route", score.grade).inc()

        assessment = self._persist(route, score, payload, assessed_by, as_of)
        return RouteAssessmentResult(assessment=assessment, score=score, route=route)

    def list_assessments(
        self, route_id: int, *, offset: int, limit: int
    ) -> tuple[Sequence[RouteAssessment], int]:
        self.get_route(route_id)  # 404 for an unknown route rather than an empty page
        return self.assessments.list_for_route(route_id, offset=offset, limit=limit)

    def latest_assessment(self, route_id: int) -> RouteAssessment | None:
        return self.assessments.latest_for_route(route_id)

    # -- engine input ------------------------------------------------------
    def build_engine_input(
        self, route: Route, reference_vehicle_model_id: int | None = None
    ) -> RouteScoringInput:
        """Marshal an ORM row into the engine's frozen input dataclass.

        This is the whole adapter between persistence and the pure layer, and it is the
        only place the two shapes meet.
        """
        reference_range = DEFAULT_REFERENCE_RANGE_KM
        reference_battery = DEFAULT_REFERENCE_BATTERY_KWH
        if reference_vehicle_model_id is not None:
            model = self.session.get(VehicleModel, reference_vehicle_model_id)
            if model is None:
                raise NotFoundError("vehicle model", reference_vehicle_model_id)
            reference_range = Decimal(model.real_world_range_km)
            reference_battery = Decimal(model.battery_capacity_kwh)

        return RouteScoringInput(
            route_name=route.route_name,
            total_distance_km=Decimal(route.total_distance_km),
            road_type=str(route.road_type),
            road_condition=str(route.road_condition),
            gradient_profile=str(route.gradient_profile),
            pitch_road_percent=Decimal(route.pitch_road_percent),
            charging_station_count=int(route.charging_station_count),
            fast_charger_count=int(route.fast_charger_count),
            avg_charging_distance_km=Decimal(route.avg_charging_distance_km),
            max_charging_gap_km=(
                Decimal(route.max_charging_gap_km)
                if route.max_charging_gap_km is not None
                else None
            ),
            passenger_volume_daily=int(route.passenger_volume_daily),
            freight_volume_daily_tons=Decimal(route.freight_volume_daily_tons),
            estimated_daily_trips=Decimal(route.estimated_daily_trips),
            avg_fare_per_trip=Decimal(route.avg_fare_per_trip),
            avg_freight_revenue_per_trip=Decimal(route.avg_freight_revenue_per_trip),
            traffic_density=str(route.traffic_density),
            competition_level=str(route.competition_level),
            seasonal_risk=str(route.seasonal_risk),
            monsoon_disruption_days=int(route.monsoon_disruption_days),
            flood_landslide_risk=str(route.flood_landslide_risk),
            security_risk=str(route.security_risk),
            electricity_tariff_per_kwh=Decimal(route.electricity_tariff_per_kwh),
            estimated_daily_operating_cost=Decimal(route.estimated_daily_operating_cost),
            reference_range_km=reference_range,
            reference_battery_kwh=reference_battery,
        )

    # -- persistence -------------------------------------------------------
    def _persist(
        self,
        route: Route,
        score: ScoreResult,
        payload: RouteScoringInput,
        assessed_by: int,
        as_of: datetime | None,
    ) -> RouteAssessment:
        config_row = self.config_service.configs.active(CONFIG_TYPE)
        assessment = self.assessments.create(
            route_id=route.id,
            config_id=config_row.id if config_row else None,
            engine_version=score.engine_version,
            total_score=score.total_score,
            grade=score.grade,
            risk_level=score.risk_level,
            charging_adequacy=str(score.extras.get("charging_adequacy", "")),
            revenue_potential=str(score.extras.get("revenue_potential", "")),
            recommendation=str(score.extras.get("recommendation", "")),
            risk_factors=[f.to_dict() for f in score.risk_factors],
            positive_factors=[f.to_dict() for f in score.positive_factors],
            explanation=score.explanation,
            input_snapshot=self._snapshot(payload, score),
            estimated_monthly_revenue=score.extras.get("estimated_monthly_revenue"),
            estimated_monthly_profit=score.extras.get("estimated_monthly_profit"),
            assessed_by=assessed_by,
            assessed_at=as_of or datetime.now(UTC),
        )
        for component in score.components:
            self.assessments.add_component(
                assessment_id=assessment.id,
                component_code=component.code,
                label=component.label,
                raw_inputs=component.raw_inputs,
                normalized_score=component.normalized_score,
                weight=component.weight,
                weighted_score=component.weighted_score,
                explanation=component.explanation,
                display_order=component.display_order,
            )
        self.session.flush()
        self.routes.set_latest_assessment(route, assessment)
        return assessment

    @staticmethod
    def _snapshot(payload: RouteScoringInput, score: ScoreResult) -> dict[str, Any]:
        """Everything needed to replay this exact score (Doc 05 §5.3.17)."""
        import dataclasses

        raw = dataclasses.asdict(payload)
        return {
            "inputs": {k: (str(v) if isinstance(v, Decimal) else v)
                       for k, v in raw.items() if k != "extras"},
            "engine_version": score.engine_version,
            "config_version": score.config_version_no,
        }

    # -- derivations shared by create and update ---------------------------
    @staticmethod
    def _density(station_count: int, distance_km: Decimal) -> Decimal:
        distance = Decimal(distance_km)
        if distance <= 0:
            return Decimal("0")
        return (Decimal(station_count) / (distance / Decimal("10"))).quantize(
            Decimal("0.001")
        )

    @staticmethod
    def _derive_economics(payload: dict[str, Any]) -> dict[str, Any]:
        """Derived revenue and energy cost stored on the route for list/report use.

        The engine recomputes these itself from the same formulas; these columns are a
        convenience for querying, never an input the engine trusts.
        """
        from app.engines.route_engine import TERRAIN_FACTOR

        trips = Decimal(payload.get("estimated_daily_trips") or 0)
        fare = Decimal(payload.get("avg_fare_per_trip") or 0)
        freight = Decimal(payload.get("avg_freight_revenue_per_trip") or 0)
        distance = Decimal(payload.get("total_distance_km") or 0)
        tariff = Decimal(payload.get("electricity_tariff_per_kwh") or 0)
        terrain = TERRAIN_FACTOR.get(str(payload.get("gradient_profile", "FLAT")),
                                     Decimal("0"))

        energy_per_km = (
            DEFAULT_REFERENCE_BATTERY_KWH / DEFAULT_REFERENCE_RANGE_KM
        ) * tariff * (Decimal("1") + terrain)
        daily_km = trips * distance
        return {
            "estimated_daily_revenue": (trips * (fare + freight)).quantize(Decimal("0.01")),
            "estimated_daily_energy_cost": (daily_km * energy_per_km).quantize(
                Decimal("0.01")
            ),
        }

    @staticmethod
    def _route_as_dict(route: Route) -> dict[str, Any]:
        return {
            "total_distance_km": route.total_distance_km,
            "charging_station_count": route.charging_station_count,
            "estimated_daily_trips": route.estimated_daily_trips,
            "avg_fare_per_trip": route.avg_fare_per_trip,
            "avg_freight_revenue_per_trip": route.avg_freight_revenue_per_trip,
            "electricity_tariff_per_kwh": route.electricity_tariff_per_kwh,
            "gradient_profile": route.gradient_profile,
        }

    def _next_route_code(self, payload: dict[str, Any]) -> str:
        def initials(name: str) -> str:
            return "".join(c for c in name.upper() if c.isalpha())[:3] or "XXX"

        prefix = f"RT-{initials(payload['origin'])}-{initials(payload['destination'])}"
        return f"{prefix}-{self.routes.count_with_code_prefix(prefix) + 1:03d}"
