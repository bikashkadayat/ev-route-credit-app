"""Route, charging station and route-assessment persistence."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models.catalog import ChargingStation, Route, RouteAssessment, RouteScoreComponent
from app.repositories.base import BaseRepository, apply_soft_delete_filter, apply_sort

SORTABLE = frozenset(
    {"route_name", "route_code", "latest_score", "latest_assessed_at", "created_at",
     "total_distance_km"}
)


class RouteRepository(BaseRepository[Route]):
    model = Route

    def get_active(self, route_id: int) -> Route | None:
        stmt = apply_soft_delete_filter(
            select(Route).where(Route.id == route_id), Route
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def get_by_code(self, route_code: str) -> Route | None:
        stmt = apply_soft_delete_filter(
            select(Route).where(Route.route_code == route_code), Route
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def find_duplicate(self, origin: str, destination: str, route_name: str) -> Route | None:
        """Doc 06 §6.3 — (origin, destination, name) is unique among live routes."""
        stmt = apply_soft_delete_filter(
            select(Route).where(
                Route.origin == origin,
                Route.destination == destination,
                Route.route_name == route_name,
            ),
            Route,
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_routes(
        self,
        *,
        offset: int,
        limit: int,
        q: str | None = None,
        grade: Sequence[str] | None = None,
        status: str | None = None,
        province: str | None = None,
        district: str | None = None,
        min_score: Decimal | None = None,
        max_score: Decimal | None = None,
        sort: str | None = "-latest_score",
    ) -> tuple[Sequence[Route], int]:
        stmt = apply_soft_delete_filter(select(Route), Route)
        if q:
            pattern = f"%{q.lower()}%"
            stmt = stmt.where(
                Route.route_name.ilike(pattern)
                | Route.route_code.ilike(pattern)
                | Route.origin.ilike(pattern)
                | Route.destination.ilike(pattern)
            )
        if grade:
            stmt = stmt.where(Route.latest_grade.in_(list(grade)))
        if status:
            stmt = stmt.where(Route.status == status)
        if province:
            stmt = stmt.where(Route.province == province)
        if district:
            stmt = stmt.where(Route.district == district)
        if min_score is not None:
            stmt = stmt.where(Route.latest_score >= min_score)
        if max_score is not None:
            stmt = stmt.where(Route.latest_score <= max_score)

        stmt = apply_sort(stmt, Route, sort, SORTABLE)
        return self.paginate(stmt, offset=offset, limit=limit)

    def create(self, **values: Any) -> Route:
        return self.add(Route(**values))

    def update(self, route: Route, **values: Any) -> Route:
        for key, value in values.items():
            setattr(route, key, value)
        self.session.flush()
        self.session.refresh(route)
        return route

    def set_latest_assessment(self, route: Route, assessment: RouteAssessment) -> None:
        """Denormalised pointers that make the route list sortable without a join."""
        route.latest_assessment_id = assessment.id
        route.latest_score = assessment.total_score
        route.latest_grade = assessment.grade
        route.latest_assessed_at = assessment.assessed_at
        route.status = "ACTIVE"
        self.session.flush()

    def stations_for(self, route_id: int) -> Sequence[ChargingStation]:
        stmt = (
            select(ChargingStation)
            .where(ChargingStation.route_id == route_id)
            .order_by(ChargingStation.distance_from_origin_km.asc().nullslast())
        )
        return self.session.execute(stmt).scalars().all()


    def count_with_code_prefix(self, prefix: str) -> int:
        """How many corridors already carry this origin/destination prefix."""
        stmt = (
            select(func.count())
            .select_from(Route)
            .where(Route.route_code.like(f"{prefix}-%"))
        )
        return int(self.session.execute(stmt).scalar_one())

    def has_active_loans(self, route_id: int) -> bool:
        """Whether any live loan runs on this corridor."""
        from app.models.portfolio import Loan

        stmt = (
            select(func.count())
            .select_from(Loan)
            .where(Loan.route_id == route_id)
            .where(Loan.status == "ACTIVE")
        )
        return bool(self.session.execute(stmt).scalar_one())


class RouteAssessmentRepository(BaseRepository[RouteAssessment]):
    model = RouteAssessment

    def get_with_components(self, assessment_id: int) -> RouteAssessment | None:
        stmt = (
            select(RouteAssessment)
            .options(selectinload(RouteAssessment.components))
            .where(RouteAssessment.id == assessment_id)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def latest_for_route(self, route_id: int) -> RouteAssessment | None:
        stmt = (
            select(RouteAssessment)
            .options(selectinload(RouteAssessment.components))
            .where(RouteAssessment.route_id == route_id)
            .order_by(RouteAssessment.assessed_at.desc(), RouteAssessment.id.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_for_route(
        self, route_id: int, *, offset: int, limit: int
    ) -> tuple[Sequence[RouteAssessment], int]:
        stmt = (
            select(RouteAssessment)
            .options(selectinload(RouteAssessment.components))
            .where(RouteAssessment.route_id == route_id)
            .order_by(RouteAssessment.assessed_at.desc(), RouteAssessment.id.desc())
        )
        return self.paginate(stmt, offset=offset, limit=limit)

    def create(self, **values: Any) -> RouteAssessment:
        """Insert-only: these rows are immutable (Doc 05 §5.3.17)."""
        return self.add(RouteAssessment(**values))

    def add_component(self, **values: Any) -> RouteScoreComponent:
        component = RouteScoreComponent(**values)
        self.session.add(component)
        return component
