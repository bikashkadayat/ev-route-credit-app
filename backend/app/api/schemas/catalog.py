"""Vehicle catalogue, bureau and stateless-scoring schemas — Doc 06 §6.4, §6.5, §6.7."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import Field

from app.api.schemas.common import ApiModel, PublicId
from app.api.schemas.route import FactorOut, ScoreComponentOut

VehicleCategory = Literal[
    "TWO_WHEELER", "THREE_WHEELER", "CAR_TAXI", "SUV", "VAN", "MINI_BUS", "BUS",
    "CARGO_PICKUP", "TRUCK",
]
VehicleStatus = Literal["PROPOSED", "FINANCED", "REPOSSESSED", "SOLD", "WRITTEN_OFF"]


# ---------------------------------------------------------------------------
# Vehicle models
# ---------------------------------------------------------------------------
class VehicleModelOut(ApiModel):
    id: int
    brand: str
    model: str
    variant: str
    category: str
    battery_capacity_kwh: Decimal
    certified_range_km: int
    real_world_range_km: int
    motor_power_kw: Decimal | None = None
    seating_capacity: int | None = None
    payload_capacity_kg: int | None = None
    ex_showroom_price: Decimal
    on_road_price: Decimal
    battery_warranty_years: int
    battery_warranty_km: int | None = None
    vehicle_warranty_years: int
    charging_type: str | None = None
    fast_charge_minutes: int | None = None
    maintenance_cost_per_km: Decimal
    expected_life_years: int
    is_active: bool


class VehicleModelCreate(ApiModel):
    brand: Annotated[str, Field(min_length=1, max_length=60)]
    model: Annotated[str, Field(min_length=1, max_length=80)]
    variant: Annotated[str, Field(max_length=60)] = "STANDARD"
    category: VehicleCategory
    battery_capacity_kwh: Annotated[Decimal, Field(gt=0, le=1000)]
    certified_range_km: Annotated[int, Field(gt=0, le=2000)]
    real_world_range_km: Annotated[int, Field(gt=0, le=2000)]
    motor_power_kw: Annotated[Decimal, Field(gt=0)] | None = None
    seating_capacity: Annotated[int, Field(ge=1, le=100)] | None = None
    payload_capacity_kg: Annotated[int, Field(ge=0)] | None = None
    ex_showroom_price: Annotated[Decimal, Field(gt=0)]
    on_road_price: Annotated[Decimal, Field(gt=0)]
    battery_warranty_years: Annotated[int, Field(ge=0, le=20)] = 0
    battery_warranty_km: Annotated[int, Field(ge=0)] | None = None
    vehicle_warranty_years: Annotated[int, Field(ge=0, le=20)] = 0
    charging_type: Annotated[str, Field(max_length=30)] | None = None
    fast_charge_minutes: Annotated[int, Field(ge=0)] | None = None
    maintenance_cost_per_km: Annotated[Decimal, Field(ge=0, le=100)] = Decimal("0.5")
    expected_life_years: Annotated[int, Field(ge=1, le=40)] = 10


# ---------------------------------------------------------------------------
# Vehicles
# ---------------------------------------------------------------------------
class VehicleOut(ApiModel):
    id: PublicId
    vehicle_model_id: int
    model_name: str | None = None
    registration_number: str | None = None
    chassis_number: str | None = None
    manufacture_year: int | None = None
    purchase_price: Decimal
    is_new: bool
    odometer_km: int
    telematics_device_id: str | None = None
    telematics_status: str
    status: str
    created_at: datetime


class VehicleCreate(ApiModel):
    vehicle_model_id: int
    registration_number: Annotated[str, Field(max_length=30)] | None = None
    chassis_number: Annotated[str, Field(max_length=40)] | None = None
    engine_motor_number: Annotated[str, Field(max_length=40)] | None = None
    manufacture_year: Annotated[int, Field(ge=2010, le=2100)] | None = None
    purchase_price: Annotated[Decimal, Field(gt=0)] | None = None
    is_new: bool = True
    odometer_km: Annotated[int, Field(ge=0)] = 0
    telematics_device_id: Annotated[str, Field(max_length=60)] | None = None


# ---------------------------------------------------------------------------
# Bureau
# ---------------------------------------------------------------------------
class BureauFetchRequest(ApiModel):
    force_refresh: bool = Field(
        default=False,
        description=(
            "Re-purchase the report even if a valid one is cached. An enquiry is billed "
            "per call, so the default reuses a report inside its validity window."
        ),
    )


class BureauReportOut(ApiModel):
    """The applicant's bureau position. The raw payload is stored but never returned."""

    provider: str
    enquiry_id: str
    enquiry_date: date
    valid_until: date
    bureau_score: int | None = None
    bureau_grade: str | None = None
    credit_history_months: int
    active_loan_count: int
    total_outstanding: Decimal
    total_monthly_emi: Decimal
    previous_default_count: int
    current_overdue_amount: Decimal
    max_dpd_last_24m: int
    is_blacklisted: bool
    enquiries_last_6m: int
    repayment_history: list[dict[str, Any]] = Field(default_factory=list)
    is_cached: bool = Field(
        description="True when a report inside its validity window was reused"
    )
    created_at: datetime


# ---------------------------------------------------------------------------
# Stateless scoring — Doc 06 §6.7
# ---------------------------------------------------------------------------
class ScoreOut(ApiModel):
    """The engine result without any persistence fields."""

    total_score: Decimal
    grade: str
    grade_label: str | None = None
    risk_level: str
    components: list[ScoreComponentOut] = Field(default_factory=list)
    risk_factors: list[FactorOut] = Field(default_factory=list)
    positive_factors: list[FactorOut] = Field(default_factory=list)
    explanation: str
    extras: dict[str, Any] = Field(default_factory=dict)
    engine_version: str
    config_version: int | None = None


class EmiRequest(ApiModel):
    principal: Annotated[Decimal, Field(gt=0, le=100_000_000)]
    annual_rate: Annotated[Decimal, Field(gt=0, lt=40)]
    tenure_months: Annotated[int, Field(ge=1, le=480)]


class EmiOut(ApiModel):
    emi: Decimal
    total_interest: Decimal
    total_payable: Decimal


class FinalScoringRequest(ApiModel):
    """Doc 06 §6.7 — what the what-if sliders send."""

    route_score: Annotated[Decimal, Field(ge=0, le=100)]
    customer_score: Annotated[Decimal, Field(ge=0, le=100)]
    vehicle_economics_score: Annotated[Decimal, Field(ge=0, le=100)] | None = None
    vehicle: dict[str, Any] = Field(default_factory=dict)
    operation: dict[str, Any] = Field(default_factory=dict)
    loan: dict[str, Any] = Field(default_factory=dict)
    borrower: dict[str, Any] = Field(default_factory=dict)
    grades: dict[str, str] = Field(default_factory=dict)
