"""Vehicle economics calculator — Doc 07 §7.5.2.

Answers the question a traditional auto-loan template never asks: does the asset,
operating on this route, generate enough net contribution to service the instalment?
Pure.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.core.types import ZERO, D, q2, q3, safe_div
from app.engines.route_engine import TERRAIN_FACTOR

DAYS_PER_YEAR = Decimal("365")


@dataclass(frozen=True, slots=True)
class VehicleEconomicsInput:
    # vehicle
    battery_capacity_kwh: Decimal
    real_world_range_km: Decimal
    on_road_price: Decimal
    maintenance_cost_per_km: Decimal = Decimal("0.55")
    battery_warranty_years: int = 0

    # operation
    daily_km: Decimal = Decimal("0")
    operating_days_per_month: int = 26
    daily_trips: Decimal = Decimal("0")
    avg_revenue_per_trip: Decimal = Decimal("0")
    electricity_tariff_per_kwh: Decimal = Decimal("12")
    gradient_profile: str = "FLAT"
    monsoon_disruption_days: int = 0
    driver_monthly_wage: Decimal = Decimal("0")
    annual_insurance: Decimal = Decimal("0")
    annual_permit_tax: Decimal = Decimal("0")

    # loan
    down_payment: Decimal = Decimal("0")
    tenure_months: int = 60


@dataclass(frozen=True, slots=True)
class VehicleEconomics:
    energy_per_km_kwh: Decimal
    energy_cost_per_km: Decimal
    daily_energy_cost: Decimal
    daily_maintenance: Decimal
    daily_driver_cost: Decimal
    daily_fixed_accrual: Decimal
    daily_gross_revenue: Decimal
    daily_net_contribution: Decimal
    seasonal_factor: Decimal
    monthly_net_contribution: Decimal
    payback_months: Decimal | None
    warranty_to_tenure_ratio: Decimal | None
    terrain_factor: Decimal

    def dscr(self, emi: Decimal) -> Decimal | None:
        return safe_div(self.monthly_net_contribution, D(emi))

    def to_dict(self) -> dict[str, Any]:
        return {
            "energy_cost_per_km": float(q3(self.energy_cost_per_km)),
            "daily_energy_cost": float(q2(self.daily_energy_cost)),
            "daily_maintenance": float(q2(self.daily_maintenance)),
            "daily_driver_cost": float(q2(self.daily_driver_cost)),
            "daily_fixed_accrual": float(q2(self.daily_fixed_accrual)),
            "daily_gross_revenue": float(q2(self.daily_gross_revenue)),
            "daily_net_contribution": float(q2(self.daily_net_contribution)),
            "seasonal_factor": float(self.seasonal_factor.quantize(Decimal("0.00001"))),
            "monthly_net_contribution": float(q2(self.monthly_net_contribution)),
            "payback_months": (
                float(q2(self.payback_months)) if self.payback_months is not None else None
            ),
        }


def calculate_vehicle_economics(p: VehicleEconomicsInput) -> VehicleEconomics:
    terrain = TERRAIN_FACTOR.get(p.gradient_profile, ZERO)

    energy_per_km = safe_div(p.battery_capacity_kwh, p.real_world_range_km) or ZERO
    energy_cost_per_km = energy_per_km * p.electricity_tariff_per_kwh * (Decimal("1") + terrain)

    operating_days = Decimal(int(p.operating_days_per_month))

    daily_energy = p.daily_km * energy_cost_per_km
    daily_maintenance = p.daily_km * p.maintenance_cost_per_km
    daily_driver = (
        safe_div(p.driver_monthly_wage, operating_days) or ZERO
        if p.driver_monthly_wage > ZERO
        else ZERO
    )
    annual_fixed = p.annual_insurance + p.annual_permit_tax
    daily_fixed = safe_div(annual_fixed, Decimal("12") * operating_days) or ZERO

    daily_gross = p.daily_trips * p.avg_revenue_per_trip
    daily_net = daily_gross - daily_energy - daily_maintenance - daily_driver - daily_fixed

    seasonal = Decimal("1") - (D(p.monsoon_disruption_days) / DAYS_PER_YEAR)
    monthly_net = daily_net * operating_days * seasonal

    financed_cost = p.on_road_price - p.down_payment
    payback = (
        safe_div(financed_cost, monthly_net) if monthly_net > ZERO else None
    )

    warranty_ratio = (
        safe_div(D(p.battery_warranty_years) * Decimal("12"), D(p.tenure_months))
        if p.tenure_months
        else None
    )

    return VehicleEconomics(
        energy_per_km_kwh=energy_per_km,
        energy_cost_per_km=energy_cost_per_km,
        daily_energy_cost=daily_energy,
        daily_maintenance=daily_maintenance,
        daily_driver_cost=daily_driver,
        daily_fixed_accrual=daily_fixed,
        daily_gross_revenue=daily_gross,
        daily_net_contribution=daily_net,
        seasonal_factor=seasonal,
        monthly_net_contribution=monthly_net,
        payback_months=payback,
        warranty_to_tenure_ratio=warranty_ratio,
        terrain_factor=terrain,
    )
