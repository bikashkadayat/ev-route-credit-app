"""Vehicle catalogue queries — Doc 05 §5.3.9, §5.3.10."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.models.catalog import Vehicle, VehicleModel
from app.repositories.base import BaseRepository, apply_soft_delete_filter, apply_sort


class VehicleModelRepository(BaseRepository[VehicleModel]):
    """The product catalogue — the models a lender is willing to finance."""

    model = VehicleModel

    SORTABLE = frozenset({"brand", "model", "on_road_price", "real_world_range_km"})

    def list_models(
        self,
        *,
        offset: int,
        limit: int,
        category: str | None = None,
        brand: str | None = None,
        is_active: bool | None = True,
        q: str | None = None,
        sort: str | None = "brand",
    ) -> tuple[Sequence[VehicleModel], int]:
        stmt = select(VehicleModel)
        if category:
            stmt = stmt.where(VehicleModel.category == category)
        if brand:
            stmt = stmt.where(VehicleModel.brand.ilike(f"%{brand}%"))
        if is_active is not None:
            stmt = stmt.where(VehicleModel.is_active.is_(is_active))
        if q:
            stmt = stmt.where(
                VehicleModel.brand.ilike(f"%{q}%") | VehicleModel.model.ilike(f"%{q}%")
            )
        stmt = apply_sort(stmt, VehicleModel, sort, self.SORTABLE)
        return self.paginate(stmt, offset=offset, limit=limit)

    def create(self, **values: Any) -> VehicleModel:
        return self.add(VehicleModel(**values))

    def by_brand_model_variant(
        self, brand: str, model: str, variant: str
    ) -> VehicleModel | None:
        stmt = select(VehicleModel).where(
            VehicleModel.brand == brand,
            VehicleModel.model == model,
            VehicleModel.variant == variant,
        ).limit(1)
        return self.session.execute(stmt).scalar_one_or_none()


class VehicleRepository(BaseRepository[Vehicle]):
    """Individual financed units."""

    model = Vehicle

    SORTABLE = frozenset({"created_at", "manufacture_year", "purchase_price"})

    def by_uuid(self, vehicle_uuid: UUID) -> Vehicle | None:
        stmt = (
            apply_soft_delete_filter(
                select(Vehicle).options(joinedload(Vehicle.model)), Vehicle
            )
            .where(Vehicle.uuid == vehicle_uuid)
            .limit(1)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def get_active(self, vehicle_id: int) -> Vehicle | None:
        stmt = apply_soft_delete_filter(
            select(Vehicle).options(joinedload(Vehicle.model)).where(Vehicle.id == vehicle_id),
            Vehicle,
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_vehicles(
        self,
        *,
        offset: int,
        limit: int,
        status: str | None = None,
        vehicle_model_id: int | None = None,
        q: str | None = None,
        sort: str | None = "-created_at",
    ) -> tuple[Sequence[Vehicle], int]:
        stmt = apply_soft_delete_filter(
            select(Vehicle).options(joinedload(Vehicle.model)), Vehicle
        )
        if status:
            stmt = stmt.where(Vehicle.status == status)
        if vehicle_model_id is not None:
            stmt = stmt.where(Vehicle.vehicle_model_id == vehicle_model_id)
        if q:
            stmt = stmt.where(Vehicle.registration_number.ilike(f"%{q}%"))
        stmt = apply_sort(stmt, Vehicle, sort, self.SORTABLE)
        return self.paginate(stmt, offset=offset, limit=limit)

    def create(self, **values: Any) -> Vehicle:
        return self.add(Vehicle(**values))

    def by_registration(self, registration_number: str) -> Vehicle | None:
        stmt = select(Vehicle).where(
            Vehicle.registration_number == registration_number
        ).limit(1)
        return self.session.execute(stmt).scalar_one_or_none()
