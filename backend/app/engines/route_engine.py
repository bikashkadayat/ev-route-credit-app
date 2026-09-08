"""Route Assessment Engine — Doc 07 §7.3. `route-engine@1.0.0`

Pure. No I/O, no clock, no randomness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.core.types import ZERO, D, q2, safe_div
from app.engines.base import ComponentResult, ScoreResult, ScoringConfig, ScoringEngine
from app.engines.factors import ROUTE_FACTOR_RULES, evaluate_factors
from app.engines.normalizer import composite_breakdown, normalize

# Doc 07 §7.3.3 — gradient also drives the energy multiplier
TERRAIN_FACTOR: dict[str, Decimal] = {
    "FLAT": Decimal("0.00"),
    "ROLLING": Decimal("0.08"),
    "HILLY": Decimal("0.18"),
    "STEEP": Decimal("0.30"),
}

# Doc 16 §16.2 — the canonical reference vehicle used when a route is assessed without a
# specific financed vehicle. Overridable per assessment and via system_settings.
DEFAULT_REFERENCE_RANGE_KM = Decimal("140")
DEFAULT_REFERENCE_BATTERY_KWH = Decimal("24")

CHARGING_COMPONENT = "CHARGING_INFRASTRUCTURE"


@dataclass(frozen=True, slots=True)
class RouteScoringInput:
    """Everything the route engine is allowed to see. Doc 01 §1.10.1."""

    # identity (not scored, used in narrative)
    route_name: str = "Route"

    # physical
    total_distance_km: Decimal = Decimal("0")
    road_type: str = "PITCH"
    road_condition: str = "GOOD"
    gradient_profile: str = "FLAT"
    pitch_road_percent: Decimal = Decimal("100")

    # charging
    charging_station_count: int = 0
    fast_charger_count: int = 0
    avg_charging_distance_km: Decimal = Decimal("0")
    max_charging_gap_km: Decimal | None = None

    # demand
    passenger_volume_daily: int = 0
    freight_volume_daily_tons: Decimal = Decimal("0")
    estimated_daily_trips: Decimal = Decimal("0")
    avg_fare_per_trip: Decimal = Decimal("0")
    avg_freight_revenue_per_trip: Decimal = Decimal("0")
    traffic_density: str = "MODERATE"
    competition_level: str = "MODERATE"

    # risk
    seasonal_risk: str = "LOW"
    monsoon_disruption_days: int = 0
    flood_landslide_risk: str = "LOW"
    security_risk: str = "LOW"

    # economics
    electricity_tariff_per_kwh: Decimal = Decimal("12")
    estimated_daily_operating_cost: Decimal = Decimal("0")
    estimated_daily_revenue_override: Decimal | None = None
    estimated_daily_energy_cost_override: Decimal | None = None

    # reference vehicle for range-dependent maths
    reference_range_km: Decimal = DEFAULT_REFERENCE_RANGE_KM
    reference_battery_kwh: Decimal = DEFAULT_REFERENCE_BATTERY_KWH

    extras: dict[str, Any] = field(default_factory=dict)

    def require_complete(self) -> None:
        """Doc 01 FR-2.3 / Doc 06 §6.3 — 422 lists every missing field, and nothing is written."""
        missing: list[str] = []
        if self.total_distance_km <= ZERO:
            missing.append("total_distance_km")
        if self.estimated_daily_trips <= ZERO:
            missing.append("estimated_daily_trips")
        if self.charging_station_count > 0 and self.max_charging_gap_km is None:
            missing.append("max_charging_gap_km")
        if self.passenger_volume_daily > 0 and self.avg_fare_per_trip <= ZERO:
            missing.append("avg_fare_per_trip")
        if self.freight_volume_daily_tons > ZERO and self.avg_freight_revenue_per_trip <= ZERO:
            missing.append("avg_freight_revenue_per_trip")
        if self.reference_range_km <= ZERO:
            missing.append("reference_range_km")
        if missing:
            raise IncompleteRouteData(missing)


class IncompleteRouteData(ValueError):
    def __init__(self, fields: list[str]) -> None:
        self.fields = fields
        super().__init__(f"Route is missing required fields: {', '.join(fields)}")


def derive_route_inputs(p: RouteScoringInput) -> dict[str, Any]:
    """Everything the configured rules and factor rules read. Pure arithmetic."""
    distance = p.total_distance_km
    trips = p.estimated_daily_trips

    density = safe_div(D(p.charging_station_count), distance / Decimal("10")) or ZERO
    max_gap = p.max_charging_gap_km if p.max_charging_gap_km is not None else distance
    gap_ratio = safe_div(max_gap, p.reference_range_km) or ZERO
    fast_share = safe_div(D(p.fast_charger_count), D(p.charging_station_count)) or ZERO

    # MIXED road surface: 40 + 0.6 x pitch% (Doc 07 §7.3.3)
    mixed_score = Decimal("40") + Decimal("0.6") * p.pitch_road_percent

    terrain = TERRAIN_FACTOR.get(p.gradient_profile, ZERO)
    energy_per_km_kwh = safe_div(p.reference_battery_kwh, p.reference_range_km) or ZERO
    energy_cost_per_km = energy_per_km_kwh * p.electricity_tariff_per_kwh * (Decimal("1") + terrain)

    daily_km = trips * distance
    daily_energy_cost = (
        p.estimated_daily_energy_cost_override
        if p.estimated_daily_energy_cost_override is not None
        else daily_km * energy_cost_per_km
    )
    daily_revenue = (
        p.estimated_daily_revenue_override
        if p.estimated_daily_revenue_override is not None
        else trips * (p.avg_fare_per_trip + p.avg_freight_revenue_per_trip)
    )
    daily_margin = daily_revenue - p.estimated_daily_operating_cost - daily_energy_cost
    margin_ratio = safe_div(daily_margin, daily_revenue) or ZERO
    revenue_per_km = safe_div(daily_revenue, daily_km) or ZERO

    demand_index = D(p.passenger_volume_daily) + p.freight_volume_daily_tons * Decimal("10")

    return {
        # charging
        "charging_station_count": p.charging_station_count,
        "fast_charger_count": p.fast_charger_count,
        "charging_station_density": density,
        "avg_charging_distance_km": p.avg_charging_distance_km,
        "max_charging_gap_km": max_gap,
        "gap_to_range_ratio": gap_ratio,
        "gap_pct": q2(gap_ratio * Decimal("100")),
        "fast_charger_share": fast_share,
        "reference_range_km": p.reference_range_km,
        # road
        "road_condition": p.road_condition,
        "road_type": p.road_type,
        "road_type_effective": p.road_type,
        "road_type_effective_computed_score": mixed_score,
        "gradient_profile": p.gradient_profile,
        "pitch_road_percent": p.pitch_road_percent,
        "terrain_factor": terrain,
        # demand
        "estimated_daily_trips": trips,
        "demand_index": demand_index,
        "passenger_volume_daily": p.passenger_volume_daily,
        "freight_volume_daily_tons": p.freight_volume_daily_tons,
        "traffic_density": p.traffic_density,
        "competition_level": p.competition_level,
        # revenue
        "total_distance_km": distance,
        "daily_km": daily_km,
        "energy_per_km_kwh": energy_per_km_kwh,
        "energy_cost_per_km": energy_cost_per_km,
        "estimated_daily_revenue": daily_revenue,
        "estimated_daily_energy_cost": daily_energy_cost,
        "estimated_daily_operating_cost": p.estimated_daily_operating_cost,
        "daily_margin": daily_margin,
        "margin_ratio": margin_ratio,
        "margin_pct": q2(margin_ratio * Decimal("100")),
        "revenue_per_km": revenue_per_km,
        # risk
        "flood_landslide_risk": p.flood_landslide_risk,
        "monsoon_disruption_days": p.monsoon_disruption_days,
        "seasonal_risk": p.seasonal_risk,
        "security_risk": p.security_risk,
        **p.extras,
    }


def charging_adequacy(inputs: dict[str, Any]) -> str:
    """Doc 07 §7.3.2 — a gate, not a slider."""
    stations = inputs["charging_station_count"]
    ratio = D(inputs["gap_to_range_ratio"])
    if stations == 0 or ratio > Decimal("0.85"):
        return "INADEQUATE"
    if stations == 1 or ratio > Decimal("0.6"):
        return "MARGINAL"
    return "ADEQUATE"


def revenue_potential(component_score: Decimal) -> str:
    """Doc 07 §7.3.5."""
    if component_score >= Decimal("75"):
        return "HIGH"
    if component_score >= Decimal("50"):
        return "MODERATE"
    return "LOW"


class RouteScoringEngine(ScoringEngine):
    engine_version = "route-engine@1.0.0"
    config_type = "ROUTE"

    def __init__(self, inadequate_charging_cap: Decimal = Decimal("25")) -> None:
        # Doc 07 §7.3.2 / setting route.charging_inadequate_cap
        self.inadequate_charging_cap = inadequate_charging_cap

    def score(self, payload: RouteScoringInput, config: ScoringConfig) -> ScoreResult:
        payload.require_complete()
        inputs = derive_route_inputs(payload)
        adequacy = charging_adequacy(inputs)

        components: list[ComponentResult] = []
        for cfg in config.active_components:
            normalized = normalize(cfg.scoring_rules, inputs)
            # A charging verdict of INADEQUATE caps the component regardless of the curve.
            if cfg.code == CHARGING_COMPONENT and adequacy == "INADEQUATE":
                normalized = min(normalized, self.inadequate_charging_cap)
            components.append(
                self.make_component(
                    cfg,
                    normalized,
                    raw_inputs=_raw_for(cfg.code, inputs),
                    explanation=_component_explanation(cfg.code, inputs, adequacy),
                    sub_factors=composite_breakdown(cfg.scoring_rules, inputs),
                )
            )

        total = self.aggregate(components)
        band = config.band_for(total)
        risks, positives = evaluate_factors(ROUTE_FACTOR_RULES, inputs)

        by_code = {c.code: c for c in components}
        rev_component = by_code.get("REVENUE_POTENTIAL")
        rev_label = revenue_potential(
            rev_component.normalized_score if rev_component else ZERO
        )

        operating_days = Decimal("26")
        monthly_revenue = q2(D(inputs["estimated_daily_revenue"]) * operating_days)
        monthly_profit = q2(D(inputs["daily_margin"]) * operating_days)

        return ScoreResult(
            total_score=total,
            grade=band.grade,
            grade_label=band.label,
            risk_level=band.risk_level,
            components=tuple(components),
            risk_factors=risks,
            positive_factors=positives,
            explanation=build_route_explanation(
                payload, inputs, total, band.label, adequacy, components
            ),
            config_version_id=config.id,
            config_version_no=config.version_no,
            engine_version=self.engine_version,
            extras={
                "charging_adequacy": adequacy,
                "revenue_potential": rev_label,
                "recommendation": band.recommendation or "",
                "estimated_monthly_revenue": float(monthly_revenue),
                "estimated_monthly_profit": float(monthly_profit),
                "energy_cost_per_km": float(q2(D(inputs["energy_cost_per_km"]))),
            },
        )


# ---------------------------------------------------------------------------
# narrative helpers
# ---------------------------------------------------------------------------
_RAW_KEYS: dict[str, tuple[str, ...]] = {
    "CHARGING_INFRASTRUCTURE": (
        "charging_station_count", "fast_charger_count", "charging_station_density",
        "max_charging_gap_km", "gap_to_range_ratio", "fast_charger_share",
    ),
    "ROAD_QUALITY": ("road_condition", "road_type", "gradient_profile", "pitch_road_percent"),
    "DEMAND": (
        "estimated_daily_trips", "demand_index", "traffic_density", "competition_level",
    ),
    "REVENUE_POTENTIAL": (
        "daily_margin", "margin_ratio", "revenue_per_km", "estimated_daily_revenue",
        "estimated_daily_energy_cost", "energy_cost_per_km",
    ),
    "ROUTE_RISK": (
        "flood_landslide_risk", "monsoon_disruption_days", "seasonal_risk", "security_risk",
    ),
}


def _raw_for(code: str, inputs: dict[str, Any]) -> dict[str, Any]:
    keys = _RAW_KEYS.get(code, ())
    out: dict[str, Any] = {}
    for k in keys:
        v = inputs.get(k)
        out[k] = float(v) if isinstance(v, Decimal) else v
    return out


def _component_explanation(code: str, i: dict[str, Any], adequacy: str) -> str:
    if code == "CHARGING_INFRASTRUCTURE":
        return (
            f"{i['charging_station_count']} stations over {_num(i['total_distance_km'])} km "
            f"({_num(q2(D(i['charging_station_density'])))} per 10 km) with a maximum gap of "
            f"{_num(i['max_charging_gap_km'])} km; {i['fast_charger_count']} DC fast chargers. "
            f"Charging adequacy: {adequacy.lower()}."
        )
    if code == "ROAD_QUALITY":
        surface = (
            f"{_num(i['pitch_road_percent'])}% pitched mixed surface"
            if i["road_type"] == "MIXED"
            else i["road_type"].lower().replace("_", "-")
        )
        return (
            f"{surface} road in {i['road_condition'].lower().replace('_', ' ')} condition "
            f"over {i['gradient_profile'].lower()} terrain."
        )
    if code == "DEMAND":
        return (
            f"{_num(i['estimated_daily_trips'])} projected trips/day against a demand index of "
            f"{_num(i['demand_index'])}; {i['traffic_density'].lower()} traffic and "
            f"{i['competition_level'].lower()} operator competition."
        )
    if code == "REVENUE_POTENTIAL":
        return (
            f"NPR {_money(i['daily_margin'])} daily margin on NPR "
            f"{_money(i['estimated_daily_revenue'])} revenue ({_num(i['margin_pct'])}%), "
            f"with energy cost of NPR {_num(q2(D(i['energy_cost_per_km'])))}/km."
        )
    if code == "ROUTE_RISK":
        return (
            f"{i['flood_landslide_risk'].lower()} flood/landslide risk, "
            f"{i['monsoon_disruption_days']} monsoon disruption days per year, "
            f"{i['security_risk'].lower()} security risk."
        )
    return ""


def build_route_explanation(
    p: RouteScoringInput,
    i: dict[str, Any],
    total: Decimal,
    grade_label: str,
    adequacy: str,
    components: list[ComponentResult],
) -> str:
    top = max(components, key=lambda c: c.weighted_score)
    return (
        f"{p.route_name} scores {_num(total)}/100 ({grade_label}). "
        f"{top.label} is the strongest contributor at {_num(top.normalized_score)}/100. "
        f"There are {i['charging_station_count']} charging stations across "
        f"{_num(i['total_distance_km'])} km "
        f"({_num(q2(D(i['charging_station_density'])))} per 10 km) including "
        f"{i['fast_charger_count']} DC fast chargers, with a maximum gap of "
        f"{_num(i['max_charging_gap_km'])} km - "
        f"{_num(i['gap_pct'])}% of the {_num(i['reference_range_km'])} km reference range, "
        f"rated {adequacy.lower()}. "
        f"Demand is {_num(i['estimated_daily_trips'])} projected trips/day with "
        f"{i['competition_level'].lower()} operator competition. "
        f"Projected daily margin is NPR {_money(i['daily_margin'])} on NPR "
        f"{_money(i['estimated_daily_revenue'])} revenue ({_num(i['margin_pct'])}%), "
        f"at an energy cost of NPR {_num(q2(D(i['energy_cost_per_km'])))}/km. "
        f"Seasonal exposure is {i['monsoon_disruption_days']} disruption days per year."
    )


def _num(value: Any) -> str:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return str(value)


def _money(value: Any) -> str:
    return f"{q2(D(value)):,}"
