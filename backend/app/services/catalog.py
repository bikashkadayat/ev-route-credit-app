"""CatalogService — vehicle models and financed vehicles — Doc 06 §6.14.

The model catalogue is reference data an officer picks from; a vehicle is the individual
unit a loan is secured on. Both are read constantly during underwriting, which is why the
engine's economics inputs (battery, range, maintenance cost, warranty) live on the model
rather than being retyped per application.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.errors import DuplicateError, NotFoundError, ValidationError
from app.models.catalog import Vehicle, VehicleModel
from app.repositories.catalog import VehicleModelRepository, VehicleRepository
from app.services.audit import AuditService


class CatalogService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.models = VehicleModelRepository(session)
        self.vehicles = VehicleRepository(session)
        self.audit = AuditService(session)

    # -- vehicle models ----------------------------------------------------
    def list_models(self, **filters: Any) -> tuple[Any, int]:
        return self.models.list_models(**filters)

    def get_model(self, model_id: int) -> VehicleModel:
        row = self.models.get(model_id)
        if row is None:
            raise NotFoundError("vehicle model", model_id)
        return row

    def create_model(
        self, payload: dict[str, Any], *, created_by: int | None
    ) -> VehicleModel:
        """Reference data, so duplicates matter: two rows for one variant would let two
        applications score the same vehicle from different economics."""
        existing = self.models.by_brand_model_variant(
            payload["brand"], payload["model"], payload.get("variant") or "STANDARD"
        )
        if existing is not None:
            raise DuplicateError(
                "This brand, model and variant is already in the catalogue",
                details={"vehicle_model_id": existing.id},
            )

        self._validate_model(payload)
        row = self.models.create(**payload)
        self.audit.record(
            action="VEHICLE_MODEL_CREATED",
            user_id=created_by,
            entity_type="vehicle_model",
            entity_id=row.id,
            after={"brand": row.brand, "model": row.model, "variant": row.variant},
        )
        return row

    @staticmethod
    def _validate_model(payload: dict[str, Any]) -> None:
        problems: list[dict[str, str]] = []
        real = payload.get("real_world_range_km")
        certified = payload.get("certified_range_km")
        if real and certified and int(real) > int(certified):
            # Real-world range above the certified figure would make the charging-gap
            # analysis optimistic in exactly the direction that strands a vehicle.
            problems.append({
                "field": "real_world_range_km", "code": "ABOVE_CERTIFIED",
                "message": "Real-world range cannot exceed the certified range",
            })
        on_road = payload.get("on_road_price")
        ex_showroom = payload.get("ex_showroom_price")
        if on_road and ex_showroom and Decimal(on_road) < Decimal(ex_showroom):
            problems.append({
                "field": "on_road_price", "code": "BELOW_EX_SHOWROOM",
                "message": "On-road price includes taxes and cannot be below ex-showroom",
            })
        if problems:
            raise ValidationError("The vehicle model is inconsistent", details=problems)

    # -- vehicles ----------------------------------------------------------
    def list_vehicles(self, **filters: Any) -> tuple[Any, int]:
        return self.vehicles.list_vehicles(**filters)

    def get_vehicle(self, vehicle_uuid: UUID) -> Vehicle:
        row = self.vehicles.by_uuid(vehicle_uuid)
        if row is None:
            raise NotFoundError("vehicle", vehicle_uuid)
        return row

    def create_vehicle(
        self, payload: dict[str, Any], *, created_by: int | None
    ) -> Vehicle:
        values = dict(payload)
        model_uuid_or_id = values.pop("vehicle_model_id")
        model = self.models.get(int(model_uuid_or_id))
        if model is None:
            raise NotFoundError("vehicle model", model_uuid_or_id)

        registration = values.get("registration_number")
        if registration and self.vehicles.by_registration(registration) is not None:
            raise DuplicateError(
                "A vehicle with this registration number already exists",
                details={"registration_number": registration},
            )

        values["vehicle_model_id"] = model.id
        values.setdefault("purchase_price", model.on_road_price)
        row = self.vehicles.create(**values)
        self.audit.record(
            action="VEHICLE_CREATED",
            user_id=created_by,
            entity_type="vehicle",
            entity_id=row.id,
            entity_uuid=row.uuid,
            after={"vehicle_model_id": model.id, "status": str(row.status)},
        )
        return row
