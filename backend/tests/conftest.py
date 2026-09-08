"""Shared fixtures.

The reference payloads here are the Doc 07 worked examples. They are used by the
example-based tests, the property-based tests and the API integration tests, so there is
exactly one definition of "the Kathmandu-Dhulikhel case" in the codebase.
"""

from __future__ import annotations

from decimal import Decimal as Dc

import pytest

from app.engines.config_loader import default_config
from app.engines.customer_engine import CustomerScoringInput
from app.engines.emi import calculate_emi
from app.engines.route_engine import RouteScoringInput
from app.engines.vehicle_economics import (
    VehicleEconomics,
    VehicleEconomicsInput,
    calculate_vehicle_economics,
)


@pytest.fixture(scope="session")
def route_config():
    return default_config("ROUTE")


@pytest.fixture(scope="session")
def customer_config():
    return default_config("CUSTOMER")


@pytest.fixture(scope="session")
def final_config():
    return default_config("FINAL")


# ---------------------------------------------------------------------------
# Doc 07 §7.3.9 — Kathmandu-Dhulikhel
# ---------------------------------------------------------------------------
@pytest.fixture
def ktm_dhulikhel() -> RouteScoringInput:
    return RouteScoringInput(
        route_name="Kathmandu-Dhulikhel",
        total_distance_km=Dc("30"),
        road_type="PITCH",
        road_condition="GOOD",
        gradient_profile="ROLLING",
        pitch_road_percent=Dc("100"),
        charging_station_count=6,
        fast_charger_count=3,
        avg_charging_distance_km=Dc("5"),
        max_charging_gap_km=Dc("12"),
        passenger_volume_daily=1200,
        freight_volume_daily_tons=Dc("0"),
        estimated_daily_trips=Dc("8"),
        avg_fare_per_trip=Dc("950"),
        traffic_density="HIGH",
        competition_level="MODERATE",
        seasonal_risk="LOW",
        monsoon_disruption_days=6,
        flood_landslide_risk="LOW",
        security_risk="LOW",
        electricity_tariff_per_kwh=Dc("12"),
        estimated_daily_operating_cost=Dc("1800"),
        reference_range_km=Dc("140"),
        reference_battery_kwh=Dc("24"),
    )


@pytest.fixture
def ktm_jiri() -> RouteScoringInput:
    """Doc 16 §16.3 — the deliberately unfinanceable Class C corridor."""
    return RouteScoringInput(
        route_name="Kathmandu-Jiri",
        total_distance_km=Dc("187"),
        road_type="MIXED",
        road_condition="POOR",
        gradient_profile="STEEP",
        pitch_road_percent=Dc("55"),
        charging_station_count=1,
        fast_charger_count=0,
        avg_charging_distance_km=Dc("91"),
        max_charging_gap_km=Dc("96"),
        passenger_volume_daily=300,
        freight_volume_daily_tons=Dc("8"),
        estimated_daily_trips=Dc("1.5"),
        avg_fare_per_trip=Dc("1400"),
        avg_freight_revenue_per_trip=Dc("900"),
        traffic_density="LOW",
        competition_level="LOW",
        seasonal_risk="HIGH",
        monsoon_disruption_days=45,
        flood_landslide_risk="HIGH",
        security_risk="MODERATE",
        electricity_tariff_per_kwh=Dc("12.5"),
        estimated_daily_operating_cost=Dc("2400"),
        reference_range_km=Dc("140"),
        reference_battery_kwh=Dc("24"),
    )


# ---------------------------------------------------------------------------
# Doc 07 §7.5.7 — Ram Bahadur Tamang / BYD e6
# ---------------------------------------------------------------------------
@pytest.fixture
def byd_e6_economics() -> VehicleEconomics:
    return calculate_vehicle_economics(
        VehicleEconomicsInput(
            battery_capacity_kwh=Dc("71.7"),
            real_world_range_km=Dc("380"),
            on_road_price=Dc("4600000"),
            maintenance_cost_per_km=Dc("0.55"),
            battery_warranty_years=8,
            daily_km=Dc("240"),
            operating_days_per_month=26,
            daily_trips=Dc("8"),
            avg_revenue_per_trip=Dc("950"),
            electricity_tariff_per_kwh=Dc("12"),
            gradient_profile="ROLLING",
            monsoon_disruption_days=6,
            driver_monthly_wage=Dc("0"),
            annual_insurance=Dc("85000"),
            annual_permit_tax=Dc("12000"),
            down_payment=Dc("1400000"),
            tenure_months=60,
        )
    )


@pytest.fixture
def requested_emi() -> Dc:
    """EMI on the requested structure: NPR 3,200,000 @ 13% over 60 months."""
    return calculate_emi(Dc("3200000"), Dc("13.0"), 60)


@pytest.fixture
def ram_bahadur(byd_e6_economics, requested_emi) -> CustomerScoringInput:
    return CustomerScoringInput(
        applicant_type="OWNER_DRIVER",
        age_years=Dc("34"),
        age_at_maturity=Dc("39"),
        bureau_score=Dc("742"),
        credit_history_months=54,
        previous_default_count=0,
        max_dpd_last_24m=12,
        enquiries_last_6m=1,
        is_blacklisted=False,
        total_monthly_income=Dc("145000"),
        total_monthly_expenses=Dc("60000"),
        total_existing_emi=Dc("12400"),
        existing_loan_count=1,
        overdue_obligation_count=0,
        avg_bank_balance_6m=Dc("83000"),
        income_proof_type="BANK_STATEMENT",
        income_verified=True,
        requested_amount=Dc("3200000"),
        requested_tenure_months=60,
        down_payment=Dc("1400000"),
        vehicle_on_road_price=Dc("4600000"),
        proposed_emi=requested_emi,
        driving_experience_years=Dc("9"),
        commercial_driving_years=Dc("6"),
        has_previous_ev_experience=False,
        licence_category="B",
        licence_months_remaining=Dc("30"),
        economics=byd_e6_economics,
        route_grade="A",
        route_score=Dc("90.12"),
        extras={"proposed_interest_rate": "13.0"},
    )
