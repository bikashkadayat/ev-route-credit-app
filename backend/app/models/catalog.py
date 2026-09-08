"""Vehicles, routes and versioned scoring configuration — Doc 05 §5.3.11-5.3.18, 5.3.30."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Index, Numeric, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    ActorMixin,
    Base,
    BigInteger,
    Boolean,
    CreatedAtMixin,
    Date,
    DateTime,
    Integer,
    OptimisticLockMixin,
    SmallInteger,
    SoftDeleteMixin,
    String,
    TimestampMixin,
    enum_column,
    external_uuid,
    money,
    ratio,
    score,
    small_money,
)


class VehicleModel(Base, TimestampMixin):
    __tablename__ = "vehicle_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brand: Mapped[str] = mapped_column(String(60), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    variant: Mapped[str] = mapped_column(String(60), nullable=False, server_default="STANDARD")
    category: Mapped[str] = enum_column(
        "vehicle_category_enum", "TWO_WHEELER", "THREE_WHEELER", "CAR_TAXI", "SUV", "VAN",
        "MINI_BUS", "BUS", "CARGO_PICKUP", "TRUCK", nullable=False,
    )
    battery_capacity_kwh: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    certified_range_km: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    real_world_range_km: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    motor_power_kw: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    seating_capacity: Mapped[int | None] = mapped_column(SmallInteger)
    payload_capacity_kg: Mapped[int | None] = mapped_column(Integer)
    ex_showroom_price: Mapped[Decimal] = money(nullable=False)
    on_road_price: Mapped[Decimal] = money(nullable=False)
    battery_warranty_years: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    battery_warranty_km: Mapped[int | None] = mapped_column(Integer)
    vehicle_warranty_years: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    charging_type: Mapped[str | None] = mapped_column(String(30))
    fast_charge_minutes: Mapped[int | None] = mapped_column(SmallInteger)
    maintenance_cost_per_km: Mapped[Decimal] = mapped_column(
        Numeric(6, 3), nullable=False, server_default="0.500")
    expected_life_years: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="10")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    vehicles: Mapped[list[Vehicle]] = relationship(back_populates="model")

    __table_args__ = (
        UniqueConstraint("brand", "model", "variant", name="uq_vehicle_model"),
        Index("idx_vm_category", "category", "is_active"),
    )


class Vehicle(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "vehicles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[UUID] = external_uuid()
    vehicle_model_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vehicle_models.id", ondelete="RESTRICT"), nullable=False)
    registration_number: Mapped[str | None] = mapped_column(String(30))
    chassis_number: Mapped[str | None] = mapped_column(String(40))
    engine_motor_number: Mapped[str | None] = mapped_column(String(40))
    manufacture_year: Mapped[int | None] = mapped_column(SmallInteger)
    purchase_price: Mapped[Decimal] = money(nullable=False)
    is_new: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    odometer_km: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    telematics_device_id: Mapped[str | None] = mapped_column(String(60))
    telematics_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="NOT_INSTALLED")
    insurance_expiry: Mapped[date | None] = mapped_column(Date)
    permit_number: Mapped[str | None] = mapped_column(String(40))
    permit_expiry: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = enum_column(
        "vehicle_status_enum", "PROPOSED", "FINANCED", "REPOSSESSED", "SOLD", "WRITTEN_OFF",
        nullable=False, server_default="PROPOSED")

    model: Mapped[VehicleModel] = relationship(back_populates="vehicles")

    __table_args__ = (
        UniqueConstraint("uuid", name="uq_vehicles_uuid"),
        Index("idx_vehicles_model", "vehicle_model_id"),
        Index("idx_vehicles_status", "status"),
        Index("idx_vehicles_telstat", "telematics_status"),
    )


class MaintenanceEvent(Base, CreatedAtMixin):
    __tablename__ = "maintenance_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    vehicle_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    cost: Mapped[Decimal] = small_money(nullable=False, server_default="0")
    downtime_days: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    odometer_km: Mapped[int | None] = mapped_column(Integer)
    is_warranty_claim: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false")
    recorded_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))

    __table_args__ = (
        Index("idx_maint_vehicle", "vehicle_id", "event_date"),
        Index("idx_maint_type", "event_type"),
    )


class Route(Base, TimestampMixin, SoftDeleteMixin, ActorMixin):
    """Doc 05 §5.3.13 — the corridor, scored once and reused across applications."""

    __tablename__ = "routes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[UUID] = external_uuid()
    route_code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    route_name: Mapped[str] = mapped_column(String(150), nullable=False)
    origin: Mapped[str] = mapped_column(String(100), nullable=False)
    destination: Mapped[str] = mapped_column(String(100), nullable=False)
    province: Mapped[str] = mapped_column(String(40), nullable=False)
    district: Mapped[str] = mapped_column(String(60), nullable=False)
    route_type: Mapped[str] = enum_column(
        "route_type_enum", "URBAN", "SUBURBAN", "INTERCITY", "RURAL", "HIGHWAY", nullable=False)
    total_distance_km: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    road_type: Mapped[str] = enum_column(
        "road_type_enum", "PITCH", "GRAVEL", "OFF_ROAD", "MIXED", nullable=False)
    road_condition: Mapped[str] = enum_column(
        "road_condition_enum", "EXCELLENT", "GOOD", "FAIR", "POOR", "VERY_POOR", nullable=False)
    gradient_profile: Mapped[str] = enum_column(
        "gradient_enum", "FLAT", "ROLLING", "HILLY", "STEEP",
        nullable=False, server_default="FLAT")
    pitch_road_percent: Mapped[Decimal] = score(nullable=False, server_default="100")
    charging_station_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    fast_charger_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    charging_station_density: Mapped[Decimal] = mapped_column(
        Numeric(6, 3), nullable=False, server_default="0")
    avg_charging_distance_km: Mapped[Decimal] = mapped_column(
        Numeric(7, 2), nullable=False, server_default="0")
    max_charging_gap_km: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    passenger_volume_daily: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0")
    freight_volume_daily_tons: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), nullable=False, server_default="0")
    estimated_daily_trips: Mapped[Decimal] = score(nullable=False)
    avg_fare_per_trip: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, server_default="0")
    avg_freight_revenue_per_trip: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, server_default="0")
    traffic_density: Mapped[str] = enum_column(
        "traffic_enum", "LOW", "MODERATE", "HIGH", "SEVERE", nullable=False)
    competition_level: Mapped[str] = enum_column(
        "competition_enum", "LOW", "MODERATE", "HIGH", "SATURATED", nullable=False)
    operator_count: Mapped[int | None] = mapped_column(SmallInteger)
    seasonal_risk: Mapped[str] = enum_column(
        "risk_level_enum", "NONE", "LOW", "MODERATE", "HIGH", "SEVERE",
        nullable=False, server_default="LOW")
    monsoon_disruption_days: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    flood_landslide_risk: Mapped[str] = enum_column(
        "risk_level_enum", "NONE", "LOW", "MODERATE", "HIGH", "SEVERE",
        nullable=False, server_default="LOW")
    security_risk: Mapped[str] = enum_column(
        "risk_level_enum", "NONE", "LOW", "MODERATE", "HIGH", "SEVERE",
        nullable=False, server_default="LOW")
    electricity_tariff_per_kwh: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), nullable=False, server_default="12.00")
    estimated_daily_revenue: Mapped[Decimal] = small_money(nullable=False, server_default="0")
    estimated_daily_operating_cost: Mapped[Decimal] = small_money(
        nullable=False, server_default="0")
    estimated_daily_energy_cost: Mapped[Decimal] = small_money(
        nullable=False, server_default="0")
    permit_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false")
    latest_assessment_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("route_assessments.id", ondelete="SET NULL"))
    latest_score: Mapped[Decimal | None] = score()
    latest_grade: Mapped[str | None] = mapped_column(String(2))
    latest_assessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = enum_column(
        "route_status_enum", "DRAFT", "ASSESSED", "ACTIVE", "SUSPENDED",
        nullable=False, server_default="DRAFT")

    assessments: Mapped[list[RouteAssessment]] = relationship(
        back_populates="route", foreign_keys="RouteAssessment.route_id")
    stations: Mapped[list[ChargingStation]] = relationship(back_populates="route")

    __table_args__ = (
        UniqueConstraint("uuid", name="uq_routes_uuid"),
        Index("idx_routes_grade_score", "latest_grade", "latest_score"),
        Index("idx_routes_geo", "province", "district"),
        Index("idx_routes_status", "status"),
        Index("idx_routes_assessed_at", "latest_assessed_at"),
    )


class ChargingStation(Base, TimestampMixin):
    __tablename__ = "charging_stations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    station_name: Mapped[str] = mapped_column(String(150), nullable=False)
    operator: Mapped[str | None] = mapped_column(String(80))
    province: Mapped[str] = mapped_column(String(40), nullable=False)
    district: Mapped[str] = mapped_column(String(60), nullable=False)
    municipality: Mapped[str | None] = mapped_column(String(80))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    charger_type: Mapped[str] = mapped_column(String(20), nullable=False)
    power_kw: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    connector_types: Mapped[str | None] = mapped_column(String(80))
    port_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="1")
    is_operational: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true")
    is_24x7: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    tariff_per_kwh: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    route_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("routes.id", ondelete="SET NULL"))
    distance_from_origin_km: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    verified_at: Mapped[date | None] = mapped_column(Date)

    route: Mapped[Route | None] = relationship(back_populates="stations")

    __table_args__ = (
        Index("idx_cs_route", "route_id", "distance_from_origin_km"),
        Index("idx_cs_district", "district", "is_operational"),
        Index("idx_cs_type", "charger_type"),
    )


class ScoringConfiguration(Base, TimestampMixin, OptimisticLockMixin):
    """Doc 05 §5.3.15 — versioned scorecard. Exactly one ACTIVE per type."""

    __tablename__ = "scoring_configurations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    config_type: Mapped[str] = enum_column(
        "config_type_enum", "ROUTE", "CUSTOMER", "FINAL", nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = enum_column(
        "config_status_enum", "DRAFT", "ACTIVE", "ARCHIVED", "DISCARDED",
        nullable=False, server_default="DRAFT")
    grade_thresholds: Mapped[dict] = mapped_column(JSONB, nullable=False)
    decision_rules: Mapped[dict | None] = mapped_column(JSONB)
    notes: Mapped[str | None] = mapped_column(Text)
    published_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False)

    components: Mapped[list[ScoringConfigComponent]] = relationship(
        back_populates="config", cascade="all, delete-orphan",
        order_by="ScoringConfigComponent.display_order")

    __table_args__ = (
        UniqueConstraint("config_type", "version_no", name="uq_config_type_version"),
        Index("idx_config_status", "status"),
    )


class ScoringConfigComponent(Base, TimestampMixin):
    __tablename__ = "scoring_config_components"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    config_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("scoring_configurations.id", ondelete="CASCADE"),
        nullable=False)
    component_code: Mapped[str] = mapped_column(String(50), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    weight: Mapped[Decimal] = ratio(nullable=False)
    display_order: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    scoring_rules: Mapped[dict] = mapped_column(JSONB, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    config: Mapped[ScoringConfiguration] = relationship(back_populates="components")

    __table_args__ = (
        UniqueConstraint("config_id", "component_code", name="uq_config_component"),
        Index("idx_config_components", "config_id", "display_order"),
    )


class RouteAssessment(Base):
    """Immutable — Doc 05 §5.3.17. Insert only; a re-run creates a new row."""

    __tablename__ = "route_assessments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[UUID] = external_uuid()
    route_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("routes.id", ondelete="RESTRICT"), nullable=False)
    config_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("scoring_configurations.id", ondelete="RESTRICT"),
        nullable=False)
    engine_version: Mapped[str] = mapped_column(String(40), nullable=False)
    total_score: Mapped[Decimal] = score(nullable=False)
    grade: Mapped[str] = mapped_column(String(2), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    charging_adequacy: Mapped[str] = mapped_column(String(20), nullable=False)
    revenue_potential: Mapped[str] = mapped_column(String(20), nullable=False)
    recommendation: Mapped[str] = mapped_column(String(40), nullable=False)
    risk_factors: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="'[]'")
    positive_factors: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="'[]'")
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    input_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    estimated_monthly_revenue: Mapped[Decimal | None] = small_money()
    estimated_monthly_profit: Mapped[Decimal | None] = small_money()
    assessed_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False)
    assessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()")

    route: Mapped[Route] = relationship(
        back_populates="assessments", foreign_keys=[route_id])
    components: Mapped[list[RouteScoreComponent]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan",
        order_by="RouteScoreComponent.display_order")

    __table_args__ = (
        UniqueConstraint("uuid", name="uq_ra_uuid"),
        Index("idx_ra_route", "route_id", "assessed_at"),
        Index("idx_ra_grade", "grade"),
        Index("idx_ra_config", "config_id"),
    )


class RouteScoreComponent(Base):
    __tablename__ = "route_score_components"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    assessment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("route_assessments.id", ondelete="CASCADE"), nullable=False)
    component_code: Mapped[str] = mapped_column(String(50), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    raw_inputs: Mapped[dict] = mapped_column(JSONB, nullable=False)
    normalized_score: Mapped[Decimal] = score(nullable=False)
    weight: Mapped[Decimal] = ratio(nullable=False)
    weighted_score: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    display_order: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")

    assessment: Mapped[RouteAssessment] = relationship(back_populates="components")

    __table_args__ = (
        UniqueConstraint("assessment_id", "component_code", name="uq_rsc"),
        Index("idx_rsc_assessment", "assessment_id"),
    )
