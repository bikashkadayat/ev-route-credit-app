"""Compute the demo-pack route scores with the real engine.

The engine is the source of truth for every number that appears in the demo seed and in
the documentation's route table. Run this after any scorecard change:

    PYTHONPATH=. python scripts/compute_demo_routes.py

It writes scripts/demo_route_scores.json, which the seed generator and the docs
consistency check both read.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal as Dc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engines.config_loader import default_config
from app.engines.route_engine import RouteScoringEngine, RouteScoringInput


def R(**kw) -> RouteScoringInput:
    return RouteScoringInput(
        reference_range_km=Dc("140"), reference_battery_kwh=Dc("24"), **kw
    )


ROUTES: dict[str, RouteScoringInput] = {
    "RT-KTM-DHU-001": R(
        route_name="Kathmandu-Dhulikhel", total_distance_km=Dc("30"), road_type="PITCH",
        road_condition="GOOD", gradient_profile="ROLLING", charging_station_count=6,
        fast_charger_count=3, avg_charging_distance_km=Dc("5"), max_charging_gap_km=Dc("12"),
        passenger_volume_daily=1200, estimated_daily_trips=Dc("8"), avg_fare_per_trip=Dc("950"),
        traffic_density="HIGH", competition_level="MODERATE", seasonal_risk="LOW",
        monsoon_disruption_days=6, flood_landslide_risk="LOW", security_risk="LOW",
        electricity_tariff_per_kwh=Dc("12"), estimated_daily_operating_cost=Dc("1800"),
    ),
    "RT-KTM-RING-002": R(
        route_name="Kathmandu Ring Road Circuit", total_distance_km=Dc("27.5"), road_type="PITCH",
        road_condition="GOOD", gradient_profile="FLAT", charging_station_count=9,
        fast_charger_count=4, avg_charging_distance_km=Dc("3.1"), max_charging_gap_km=Dc("6"),
        passenger_volume_daily=3500, estimated_daily_trips=Dc("12"), avg_fare_per_trip=Dc("420"),
        traffic_density="SEVERE", competition_level="SATURATED", seasonal_risk="LOW",
        monsoon_disruption_days=3, flood_landslide_risk="LOW", security_risk="LOW",
        electricity_tariff_per_kwh=Dc("12"), estimated_daily_operating_cost=Dc("1500"),
    ),
    "RT-LAL-CHA-003": R(
        route_name="Lalitpur-Chapagaun", total_distance_km=Dc("12"), road_type="PITCH",
        road_condition="FAIR", gradient_profile="ROLLING", charging_station_count=3,
        fast_charger_count=1, avg_charging_distance_km=Dc("4"), max_charging_gap_km=Dc("7"),
        passenger_volume_daily=900, estimated_daily_trips=Dc("10"), avg_fare_per_trip=Dc("260"),
        traffic_density="HIGH", competition_level="HIGH", seasonal_risk="LOW",
        monsoon_disruption_days=4, flood_landslide_risk="LOW", security_risk="LOW",
        electricity_tariff_per_kwh=Dc("12"), estimated_daily_operating_cost=Dc("700"),
    ),
    "RT-BKT-BAN-004": R(
        route_name="Bhaktapur-Banepa", total_distance_km=Dc("18"), road_type="PITCH",
        road_condition="GOOD", gradient_profile="ROLLING", charging_station_count=4,
        fast_charger_count=2, avg_charging_distance_km=Dc("4.5"), max_charging_gap_km=Dc("9"),
        passenger_volume_daily=1100, estimated_daily_trips=Dc("9"), avg_fare_per_trip=Dc("380"),
        traffic_density="HIGH", competition_level="HIGH", seasonal_risk="LOW",
        monsoon_disruption_days=8, flood_landslide_risk="LOW", security_risk="LOW",
        electricity_tariff_per_kwh=Dc("12"), estimated_daily_operating_cost=Dc("900"),
    ),
    "RT-BRT-BHD-005": R(
        route_name="Birtamod-Bhadrapur", total_distance_km=Dc("22"), road_type="PITCH",
        road_condition="GOOD", gradient_profile="FLAT", charging_station_count=3,
        fast_charger_count=1, avg_charging_distance_km=Dc("7.3"), max_charging_gap_km=Dc("14"),
        passenger_volume_daily=700, estimated_daily_trips=Dc("7"), avg_fare_per_trip=Dc("320"),
        traffic_density="MODERATE", competition_level="MODERATE", seasonal_risk="MODERATE",
        monsoon_disruption_days=12, flood_landslide_risk="MODERATE", security_risk="LOW",
        electricity_tariff_per_kwh=Dc("11.5"), estimated_daily_operating_cost=Dc("650"),
    ),
    "RT-BUT-BHW-006": R(
        route_name="Butwal-Bhairahawa", total_distance_km=Dc("24"), road_type="PITCH",
        road_condition="EXCELLENT", gradient_profile="FLAT", charging_station_count=5,
        fast_charger_count=2, avg_charging_distance_km=Dc("4.8"), max_charging_gap_km=Dc("10"),
        passenger_volume_daily=1600, freight_volume_daily_tons=Dc("40"),
        estimated_daily_trips=Dc("8"), avg_fare_per_trip=Dc("300"),
        avg_freight_revenue_per_trip=Dc("1200"), traffic_density="HIGH",
        competition_level="MODERATE", seasonal_risk="LOW", monsoon_disruption_days=10,
        flood_landslide_risk="LOW", security_risk="LOW",
        electricity_tariff_per_kwh=Dc("11.5"), estimated_daily_operating_cost=Dc("2600"),
    ),
    "RT-PKR-LEK-007": R(
        route_name="Pokhara-Lekhnath", total_distance_km=Dc("16"), road_type="MIXED",
        road_condition="FAIR", gradient_profile="HILLY", pitch_road_percent=Dc("70"),
        charging_station_count=2, fast_charger_count=0, avg_charging_distance_km=Dc("8"),
        max_charging_gap_km=Dc("16"), passenger_volume_daily=500,
        estimated_daily_trips=Dc("6"), avg_fare_per_trip=Dc("280"),
        traffic_density="MODERATE", competition_level="MODERATE", seasonal_risk="MODERATE",
        monsoon_disruption_days=22, flood_landslide_risk="MODERATE", security_risk="LOW",
        electricity_tariff_per_kwh=Dc("12"), estimated_daily_operating_cost=Dc("600"),
    ),
    "RT-NPJ-KOH-008": R(
        route_name="Nepalgunj-Kohalpur", total_distance_km=Dc("15"), road_type="PITCH",
        road_condition="FAIR", gradient_profile="FLAT", charging_station_count=2,
        fast_charger_count=1, avg_charging_distance_km=Dc("7.5"), max_charging_gap_km=Dc("11"),
        passenger_volume_daily=800, estimated_daily_trips=Dc("9"), avg_fare_per_trip=Dc("240"),
        traffic_density="MODERATE", competition_level="HIGH", seasonal_risk="HIGH",
        monsoon_disruption_days=14, flood_landslide_risk="HIGH", security_risk="MODERATE",
        electricity_tariff_per_kwh=Dc("11.5"), estimated_daily_operating_cost=Dc("620"),
    ),
    "RT-KTM-JIR-009": R(
        route_name="Kathmandu-Jiri", total_distance_km=Dc("187"), road_type="MIXED",
        road_condition="POOR", gradient_profile="STEEP", pitch_road_percent=Dc("55"),
        charging_station_count=1, fast_charger_count=0, avg_charging_distance_km=Dc("91"),
        max_charging_gap_km=Dc("96"), passenger_volume_daily=300,
        freight_volume_daily_tons=Dc("8"), estimated_daily_trips=Dc("1.5"),
        avg_fare_per_trip=Dc("1400"), avg_freight_revenue_per_trip=Dc("900"),
        traffic_density="LOW", competition_level="LOW", seasonal_risk="HIGH",
        monsoon_disruption_days=45, flood_landslide_risk="HIGH", security_risk="MODERATE",
        electricity_tariff_per_kwh=Dc("12.5"), estimated_daily_operating_cost=Dc("2400"),
    ),
    "RT-ITA-DHR-010": R(
        route_name="Itahari-Dharan", total_distance_km=Dc("16"), road_type="PITCH",
        road_condition="GOOD", gradient_profile="FLAT", charging_station_count=4,
        fast_charger_count=2, avg_charging_distance_km=Dc("4"), max_charging_gap_km=Dc("8"),
        passenger_volume_daily=1400, estimated_daily_trips=Dc("10"), avg_fare_per_trip=Dc("260"),
        traffic_density="HIGH", competition_level="HIGH", seasonal_risk="MODERATE",
        monsoon_disruption_days=9, flood_landslide_risk="MODERATE", security_risk="LOW",
        electricity_tariff_per_kwh=Dc("11.5"), estimated_daily_operating_cost=Dc("700"),
    ),
}


def main() -> None:
    config = default_config("ROUTE")
    engine = RouteScoringEngine()
    results: dict[str, dict] = {}

    header = (
        f"{'code':18} {'score':>6} {'gr':>3} {'chg':>6} {'road':>6} {'dem':>6} "
        f"{'rev':>6} {'risk':>6}  {'adequacy':<11} rev-pot"
    )
    print(header)
    print("-" * len(header))

    for code, payload in ROUTES.items():
        r = engine.score(payload, config)
        c = {x.code: x.normalized_score for x in r.components}
        results[code] = {
            "route_name": payload.route_name,
            "score": str(r.total_score),
            "grade": r.grade,
            "risk_level": r.risk_level,
            "components": {k: str(v) for k, v in c.items()},
            "charging_adequacy": r.extras["charging_adequacy"],
            "revenue_potential": r.extras["revenue_potential"],
            "recommendation": r.extras["recommendation"],
            "estimated_monthly_revenue": r.extras["estimated_monthly_revenue"],
            "estimated_monthly_profit": r.extras["estimated_monthly_profit"],
            "risk_factors": [
                {"code": f.code, "severity": f.severity, "message": f.message}
                for f in r.risk_factors
            ],
            "positive_factors": [
                {"code": f.code, "message": f.message} for f in r.positive_factors
            ],
            "explanation": r.explanation,
        }
        print(
            f"{code:18} {r.total_score:>6} {r.grade:>3} "
            f"{c['CHARGING_INFRASTRUCTURE']:>6} {c['ROAD_QUALITY']:>6} {c['DEMAND']:>6} "
            f"{c['REVENUE_POTENTIAL']:>6} {c['ROUTE_RISK']:>6}  "
            f"{r.extras['charging_adequacy']:<11} {r.extras['revenue_potential']}"
        )

    out = Path(__file__).parent / "demo_route_scores.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
