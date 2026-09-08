"""Route schemas — Doc 06 §6.3.

Validation mirrors the documented rules exactly; the cross-field checks (fast chargers
within total, gap within distance, fare required when the corridor carries passengers)
are model validators so the API rejects an impossible route before any service runs.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.api.schemas.common import ApiModel, PublicId

RoadType = Literal["PITCH", "GRAVEL", "OFF_ROAD", "MIXED"]
RoadCondition = Literal["EXCELLENT", "GOOD", "FAIR", "POOR", "VERY_POOR"]
Gradient = Literal["FLAT", "ROLLING", "HILLY", "STEEP"]
RouteType = Literal["URBAN", "SUBURBAN", "INTERCITY", "RURAL", "HIGHWAY"]
Traffic = Literal["LOW", "MODERATE", "HIGH", "SEVERE"]
Competition = Literal["LOW", "MODERATE", "HIGH", "SATURATED"]
RiskLevel = Literal["NONE", "LOW", "MODERATE", "HIGH", "SEVERE"]
RouteStatus = Literal["DRAFT", "ASSESSED", "ACTIVE", "SUSPENDED"]


class RouteBase(ApiModel):
    route_name: Annotated[str, Field(min_length=3, max_length=150)]
    origin: Annotated[str, Field(min_length=2, max_length=100)]
    destination: Annotated[str, Field(min_length=2, max_length=100)]
    province: Annotated[str, Field(max_length=40)]
    district: Annotated[str, Field(max_length=60)]
    route_type: RouteType

    total_distance_km: Annotated[Decimal, Field(gt=0, le=1000)]
    road_type: RoadType
    road_condition: RoadCondition
    gradient_profile: Gradient = "FLAT"
    pitch_road_percent: Annotated[Decimal, Field(ge=0, le=100)] = Decimal("100")

    charging_station_count: Annotated[int, Field(ge=0, le=200)] = 0
    fast_charger_count: Annotated[int, Field(ge=0, le=200)] = 0
    avg_charging_distance_km: Annotated[Decimal, Field(ge=0)] = Decimal("0")
    max_charging_gap_km: Annotated[Decimal, Field(ge=0)] | None = None

    passenger_volume_daily: Annotated[int, Field(ge=0)] = 0
    freight_volume_daily_tons: Annotated[Decimal, Field(ge=0)] = Decimal("0")
    estimated_daily_trips: Annotated[Decimal, Field(gt=0, le=60)]
    avg_fare_per_trip: Annotated[Decimal, Field(ge=0)] = Decimal("0")
    avg_freight_revenue_per_trip: Annotated[Decimal, Field(ge=0)] = Decimal("0")
    traffic_density: Traffic
    competition_level: Competition
    operator_count: Annotated[int, Field(ge=0)] | None = None

    seasonal_risk: RiskLevel = "LOW"
    monsoon_disruption_days: Annotated[int, Field(ge=0, le=180)] = 0
    flood_landslide_risk: RiskLevel = "LOW"
    security_risk: Literal["LOW", "MODERATE", "HIGH"] = "LOW"

    electricity_tariff_per_kwh: Annotated[Decimal, Field(ge=1, le=50)] = Decimal("12")
    estimated_daily_operating_cost: Annotated[Decimal, Field(ge=0)] = Decimal("0")
    permit_required: bool = False

    @model_validator(mode="after")
    def _cross_field_rules(self) -> RouteBase:
        if self.fast_charger_count > self.charging_station_count:
            raise ValueError(
                "fast_charger_count cannot exceed charging_station_count"
            )
        if self.max_charging_gap_km is not None:
            if self.max_charging_gap_km > self.total_distance_km:
                raise ValueError(
                    "max_charging_gap_km cannot exceed total_distance_km"
                )
        elif self.charging_station_count >= 1:
            raise ValueError(
                "max_charging_gap_km is required when the corridor has charging stations"
            )
        if self.passenger_volume_daily > 0 and self.avg_fare_per_trip <= 0:
            raise ValueError(
                "avg_fare_per_trip is required when passenger_volume_daily is above zero"
            )
        if self.freight_volume_daily_tons > 0 and self.avg_freight_revenue_per_trip <= 0:
            raise ValueError(
                "avg_freight_revenue_per_trip is required when the corridor carries freight"
            )
        return self


class RouteCreate(RouteBase):
    """POST /routes"""


class RouteUpdate(RouteBase):
    """PUT /routes/{id} — a full replacement, validated by the same rules."""


class RouteSummary(ApiModel):
    """List row — deliberately narrower than the detail response."""

    id: PublicId
    route_code: str
    route_name: str
    origin: str
    destination: str
    province: str
    district: str
    route_type: str
    total_distance_km: Decimal
    charging_station_count: int
    fast_charger_count: int
    latest_score: Decimal | None = None
    latest_grade: str | None = None
    latest_assessed_at: datetime | None = None
    status: str


class RouteDetail(RouteSummary):
    road_type: str
    road_condition: str
    gradient_profile: str
    pitch_road_percent: Decimal
    charging_station_density: Decimal
    avg_charging_distance_km: Decimal
    max_charging_gap_km: Decimal | None = None
    passenger_volume_daily: int
    freight_volume_daily_tons: Decimal
    estimated_daily_trips: Decimal
    avg_fare_per_trip: Decimal
    avg_freight_revenue_per_trip: Decimal
    traffic_density: str
    competition_level: str
    operator_count: int | None = None
    seasonal_risk: str
    monsoon_disruption_days: int
    flood_landslide_risk: str
    security_risk: str
    electricity_tariff_per_kwh: Decimal
    estimated_daily_revenue: Decimal
    estimated_daily_operating_cost: Decimal
    estimated_daily_energy_cost: Decimal
    permit_required: bool
    created_at: datetime
    updated_at: datetime


class AssessRouteRequest(ApiModel):
    reference_vehicle_model_id: int | None = Field(
        default=None,
        description=(
            "Uses this model's real-world range for the charging gap analysis. "
            "Defaults to the configured reference vehicle (24 kWh / 140 km)."
        ),
    )
    notes: str | None = Field(default=None, max_length=500)


class SubFactorOut(ApiModel):
    code: str
    value: str | bool | None = None
    score: Decimal
    weight: Decimal
    contribution: Decimal


class ScoreComponentOut(ApiModel):
    code: str
    label: str
    normalized_score: Decimal
    weight: Decimal
    weighted_score: Decimal
    raw_inputs: dict = Field(default_factory=dict)
    explanation: str
    sub_factors: list[SubFactorOut] = Field(default_factory=list)


class FactorOut(ApiModel):
    code: str
    type: str
    severity: str | None = None
    message: str


class ConfigVersionOut(ApiModel):
    config_type: str
    version_no: int
    name: str | None = None


class RouteAssessmentOut(ApiModel):
    id: PublicId
    route_id: UUID | None = None
    route_name: str | None = None
    total_score: Decimal
    grade: str
    risk_level: str
    charging_adequacy: str
    revenue_potential: str
    recommendation: str
    components: list[ScoreComponentOut] = Field(default_factory=list)
    risk_factors: list[FactorOut] = Field(default_factory=list)
    positive_factors: list[FactorOut] = Field(default_factory=list)
    explanation: str
    estimated_monthly_revenue: Decimal | None = None
    estimated_monthly_profit: Decimal | None = None
    engine_version: str
    config_version: int | None = None
    assessed_at: datetime


class ChargingStationOut(ApiModel):
    station_name: str
    operator: str | None = None
    charger_type: str
    power_kw: Decimal
    port_count: int
    is_operational: bool
    distance_from_origin_km: Decimal | None = None
    verified_at: str | None = None
