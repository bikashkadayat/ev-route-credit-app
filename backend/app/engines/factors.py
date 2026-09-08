"""Declarative factor rules — Doc 07 §7.3.8 (route) and §7.4.10 (customer).

Factors are produced by declarative checks over the *same* derived-input mapping the
components score from, so the explanation cannot drift from the score. Adding a factor
is a row in a table below, not a branch inside an engine.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.core.types import D
from app.engines.base import Factor, FactorType, Severity

Predicate = Callable[[Mapping[str, Any]], bool]


@dataclass(frozen=True, slots=True)
class FactorRule:
    code: str
    type: FactorType
    template: str
    predicate: Predicate
    severity: Severity | None = None
    metric_keys: tuple[str, ...] = ()

    def render(self, inputs: Mapping[str, Any]) -> Factor:
        values = {k: inputs.get(k) for k in _template_keys(self.template)}
        return Factor(
            code=self.code,
            type=self.type,
            severity=self.severity,
            message=self.template.format(**{k: _fmt(v) for k, v in values.items()}),
            metric={k: _plain(inputs.get(k)) for k in self.metric_keys},
        )


def _template_keys(template: str) -> tuple[str, ...]:
    import string

    return tuple(
        field for _, field, _, _ in string.Formatter().parse(template) if field
    )


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, Decimal):
        # Trim trailing zeros so "6" does not render as "6.00"
        normalised = value.normalize()
        as_str = format(normalised, "f")
        return as_str
    if isinstance(value, float):  # pragma: no cover - engines never produce floats
        return f"{value:g}"
    return str(value)


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return value


def evaluate_factors(
    rules: Sequence[FactorRule], inputs: Mapping[str, Any]
) -> tuple[tuple[Factor, ...], tuple[Factor, ...]]:
    """Return (risk_factors, positive_factors), each ordered by severity then code."""
    risks: list[Factor] = []
    positives: list[Factor] = []
    for rule in rules:
        try:
            fired = rule.predicate(inputs)
        except (TypeError, KeyError, ArithmeticError):
            # A factor rule that cannot be evaluated (missing optional input) simply
            # does not fire. It must never break a scoring run.
            continue
        if not fired:
            continue
        factor = rule.render(inputs)
        (risks if rule.type == "RISK" else positives).append(factor)

    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, None: 4}
    risks.sort(key=lambda f: (severity_order.get(f.severity, 4), f.code))
    positives.sort(key=lambda f: f.code)
    return tuple(risks), tuple(positives)


# ---------------------------------------------------------------------------
# helpers used by the predicates
# ---------------------------------------------------------------------------
def _n(inputs: Mapping[str, Any], key: str) -> Decimal | None:
    v = inputs.get(key)
    if v is None:
        return None
    return D(v)


def _ge(inputs: Mapping[str, Any], key: str, threshold: str) -> bool:
    v = _n(inputs, key)
    return v is not None and v >= D(threshold)


def _le(inputs: Mapping[str, Any], key: str, threshold: str) -> bool:
    v = _n(inputs, key)
    return v is not None and v <= D(threshold)


def _lt(inputs: Mapping[str, Any], key: str, threshold: str) -> bool:
    v = _n(inputs, key)
    return v is not None and v < D(threshold)


def _gt(inputs: Mapping[str, Any], key: str, threshold: str) -> bool:
    v = _n(inputs, key)
    return v is not None and v > D(threshold)


# ---------------------------------------------------------------------------
# ROUTE factor rules — Doc 07 §7.3.8 (all 17)
# ---------------------------------------------------------------------------
ROUTE_FACTOR_RULES: tuple[FactorRule, ...] = (
    FactorRule(
        "NO_CHARGING_STATIONS", "RISK", "No charging infrastructure on the corridor",
        lambda i: _n(i, "charging_station_count") == 0, "CRITICAL",
        ("charging_station_count",),
    ),
    FactorRule(
        "CHARGING_GAP_EXCEEDS_RANGE", "RISK",
        "Longest charging gap ({max_charging_gap_km} km) is {gap_pct}% of usable range",
        lambda i: _gt(i, "gap_to_range_ratio", "0.85"), "CRITICAL",
        ("max_charging_gap_km", "gap_to_range_ratio"),
    ),
    FactorRule(
        "SPARSE_CHARGING", "RISK",
        "Only {charging_station_count} station(s) over {total_distance_km} km",
        lambda i: _lt(i, "charging_station_density", "0.5")
        and _n(i, "charging_station_count") != 0,
        "HIGH", ("charging_station_count", "charging_station_density"),
    ),
    FactorRule(
        "POOR_ROAD_CONDITION", "RISK",
        "Road condition rated {road_condition}; higher wear and downtime expected",
        lambda i: i.get("road_condition") in {"POOR", "VERY_POOR"}, "HIGH",
        ("road_condition",),
    ),
    FactorRule(
        "STEEP_GRADIENT", "RISK",
        "Steep gradient increases energy consumption by ~30%",
        lambda i: i.get("gradient_profile") == "STEEP", "MEDIUM", ("gradient_profile",),
    ),
    FactorRule(
        "HIGH_MONSOON_DISRUPTION", "RISK",
        "{monsoon_disruption_days} disruption days per year",
        lambda i: _gt(i, "monsoon_disruption_days", "30"), "HIGH",
        ("monsoon_disruption_days",),
    ),
    FactorRule(
        "LANDSLIDE_EXPOSURE", "RISK",
        "Corridor exposed to flood/landslide risk ({flood_landslide_risk})",
        lambda i: i.get("flood_landslide_risk") in {"HIGH", "SEVERE"}, "HIGH",
        ("flood_landslide_risk",),
    ),
    FactorRule(
        "SATURATED_COMPETITION", "RISK",
        "Saturated operator competition compresses fares",
        lambda i: i.get("competition_level") == "SATURATED", "MEDIUM",
        ("competition_level",),
    ),
    FactorRule(
        "THIN_MARGIN", "RISK",
        "Operating margin only {margin_pct}% of revenue",
        lambda i: _lt(i, "margin_ratio", "0.15"), "HIGH", ("margin_ratio",),
    ),
    FactorRule(
        "LOW_TRIP_VOLUME", "RISK",
        "Only {estimated_daily_trips} trips/day projected",
        lambda i: _lt(i, "estimated_daily_trips", "3"), "MEDIUM",
        ("estimated_daily_trips",),
    ),
    FactorRule(
        "SEVERE_CONGESTION", "RISK",
        "Severe congestion reduces achievable trips",
        lambda i: i.get("traffic_density") == "SEVERE", "MEDIUM", ("traffic_density",),
    ),
    FactorRule(
        "DENSE_CHARGING", "POSITIVE",
        "{charging_station_count} stations over {total_distance_km} km - dense charging coverage",
        lambda i: _ge(i, "charging_station_density", "1.5"), None,
        ("charging_station_density",),
    ),
    FactorRule(
        "FAST_CHARGING_AVAILABLE", "POSITIVE",
        "{fast_charger_count} DC fast chargers available on the corridor",
        lambda i: _ge(i, "fast_charger_count", "2"), None, ("fast_charger_count",),
    ),
    FactorRule(
        "EXCELLENT_ROAD", "POSITIVE",
        "Fully pitched road in {road_condition} condition",
        lambda i: i.get("road_condition") in {"EXCELLENT", "GOOD"}
        and i.get("road_type") == "PITCH",
        None, ("road_condition", "road_type"),
    ),
    FactorRule(
        "STRONG_DEMAND", "POSITIVE",
        "High passenger/freight volume on the corridor",
        lambda i: _ge(i, "demand_index", "800"), None, ("demand_index",),
    ),
    FactorRule(
        "HEALTHY_MARGIN", "POSITIVE",
        "Operating margin {margin_pct}% of revenue",
        lambda i: _ge(i, "margin_ratio", "0.30"), None, ("margin_ratio",),
    ),
    FactorRule(
        "SHORT_ROUTE_EV_FRIENDLY", "POSITIVE",
        "Short corridor well within single-charge range",
        lambda i: _le(i, "total_distance_km", "40") and _ge(i, "charging_station_density", "1.0"),
        None, ("total_distance_km",),
    ),
    FactorRule(
        "NO_SEASONAL_DISRUPTION", "POSITIVE",
        "Year-round operability",
        lambda i: _le(i, "monsoon_disruption_days", "5"), None,
        ("monsoon_disruption_days",),
    ),
)


# ---------------------------------------------------------------------------
# CUSTOMER factor rules — Doc 07 §7.4.10
# ---------------------------------------------------------------------------
CUSTOMER_FACTOR_RULES: tuple[FactorRule, ...] = (
    FactorRule(
        "HIGH_EMI_BURDEN", "RISK",
        "Post-loan FOIR of {foir_pct}% exceeds the policy threshold",
        lambda i: _gt(i, "foir_post_loan", "0.55"), "HIGH", ("foir_post_loan",),
    ),
    FactorRule(
        "WEAK_BUREAU_SCORE", "RISK",
        "Credit bureau score {bureau_score} is below 600",
        lambda i: _lt(i, "bureau_score", "600"), "HIGH", ("bureau_score",),
    ),
    FactorRule(
        "MODERATE_BUREAU_SCORE", "RISK",
        "Credit bureau score {bureau_score} is in the moderate band (600-680)",
        lambda i: _ge(i, "bureau_score", "600") and _lt(i, "bureau_score", "680"),
        "MEDIUM", ("bureau_score",),
    ),
    FactorRule(
        "RECENT_DELINQUENCY", "RISK",
        "Maximum {max_dpd_last_24m} days past due in the last 24 months",
        lambda i: _gt(i, "max_dpd_last_24m", "30"), "HIGH", ("max_dpd_last_24m",),
    ),
    FactorRule(
        "THIN_CREDIT_FILE", "RISK",
        "Only {credit_history_months} months of credit history",
        lambda i: _lt(i, "credit_history_months", "6"), "MEDIUM",
        ("credit_history_months",),
    ),
    FactorRule(
        "UNVERIFIED_INCOME", "RISK",
        "Income is self-declared and unverified",
        lambda i: i.get("income_proof_class") == "SELF_DECLARED", "MEDIUM",
        ("income_proof_class",),
    ),
    FactorRule(
        "MULTIPLE_OBLIGATIONS", "RISK",
        "{existing_loan_count} existing loan obligations",
        lambda i: _ge(i, "existing_loan_count", "3"), "MEDIUM", ("existing_loan_count",),
    ),
    FactorRule(
        "CREDIT_HUNGRY", "RISK",
        "{enquiries_last_6m} credit enquiries in the last 6 months",
        lambda i: _ge(i, "enquiries_last_6m", "4"), "MEDIUM", ("enquiries_last_6m",),
    ),
    FactorRule(
        "LOW_DSCR", "RISK",
        "Vehicle contribution covers only {dscr}x the proposed EMI",
        lambda i: _lt(i, "dscr", "1.25"), "HIGH", ("dscr",),
    ),
    FactorRule(
        "INEXPERIENCED_OPERATOR", "RISK",
        "Under one year of commercial operating experience",
        lambda i: i.get("applicant_class") == "INDIVIDUAL"
        and _lt(i, "commercial_driving_years", "1"),
        "MEDIUM", ("commercial_driving_years",),
    ),
    FactorRule(
        "LICENCE_EXPIRING", "RISK",
        "Driving licence expires within {licence_months_remaining} months",
        lambda i: i.get("applicant_class") == "INDIVIDUAL"
        and _lt(i, "licence_months_remaining", "6"),
        "LOW", ("licence_months_remaining",),
    ),
    FactorRule(
        "STRONG_CREDIT_RECORD", "POSITIVE",
        "Strong bureau record: score {bureau_score} with no previous defaults",
        lambda i: _ge(i, "bureau_score", "750") and _n(i, "previous_default_count") == 0,
        None, ("bureau_score", "previous_default_count"),
    ),
    FactorRule(
        "CLEAN_REPAYMENT", "POSITIVE",
        "No days past due recorded in the last 24 months",
        lambda i: _n(i, "max_dpd_last_24m") == 0, None, ("max_dpd_last_24m",),
    ),
    FactorRule(
        "STRONG_DOWN_PAYMENT", "POSITIVE",
        "Down payment of {down_payment_pct}% is well above the minimum",
        lambda i: _ge(i, "down_payment_ratio", "0.35"), None, ("down_payment_ratio",),
    ),
    FactorRule(
        "HEALTHY_DSCR", "POSITIVE",
        "Vehicle generates {dscr}x the proposed EMI in net contribution",
        lambda i: _ge(i, "dscr", "1.5"), None, ("dscr",),
    ),
    FactorRule(
        "EXPERIENCED_OPERATOR", "POSITIVE",
        "{commercial_driving_years} years of commercial operating experience",
        lambda i: i.get("applicant_class") == "INDIVIDUAL"
        and _ge(i, "commercial_driving_years", "5"),
        None, ("commercial_driving_years",),
    ),
    FactorRule(
        "ESTABLISHED_BUSINESS", "POSITIVE",
        "{business_experience_years} years of business operating history",
        lambda i: i.get("applicant_class") == "BUSINESS"
        and _ge(i, "business_experience_years", "5"),
        None, ("business_experience_years",),
    ),
    FactorRule(
        "VERIFIED_INCOME", "POSITIVE",
        "Income verified from documentary evidence",
        lambda i: bool(i.get("income_verified")), None, ("income_proof_class",),
    ),
    FactorRule(
        "PRIOR_EV_EXPERIENCE", "POSITIVE",
        "Applicant has prior experience operating an electric vehicle",
        lambda i: bool(i.get("has_previous_ev_experience")), None, (),
    ),
)
