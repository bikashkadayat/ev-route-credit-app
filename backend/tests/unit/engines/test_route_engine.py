"""Route engine — Doc 07 §7.3 and Doc 10 §10.2.1 cases RS-01..RS-10.

The sub-factor assertions carry hand-computed interpolation values. They are what makes
the composed total trustworthy: if a curve is edited, these fail before the total does.
"""

from __future__ import annotations

from decimal import Decimal as Dc

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.core.types import q2
from app.engines.normalizer import composite_breakdown
from app.engines.route_engine import (
    IncompleteRouteData,
    RouteScoringEngine,
    RouteScoringInput,
    charging_adequacy,
    derive_route_inputs,
)

ENGINE = RouteScoringEngine()


# ---------------------------------------------------------------------------
# RS-01 the reference case, asserted from sub-factor upwards
# ---------------------------------------------------------------------------
def test_derived_inputs_match_hand_calculation(ktm_dhulikhel):
    i = derive_route_inputs(ktm_dhulikhel)
    assert i["charging_station_density"] == Dc("2")            # 6 / (30/10)
    assert q2(i["gap_to_range_ratio"]) == Dc("0.09")           # 12 / 140 = 0.085714...
    assert i["fast_charger_share"] == Dc("0.5")                # 3 / 6
    assert i["demand_index"] == Dc("1200")                     # 1200 + 0 x 10
    assert i["daily_km"] == Dc("240")                          # 8 x 30
    # (24 / 140) x 12 x 1.08
    assert q2(i["energy_cost_per_km"]) == Dc("2.22")
    assert q2(i["estimated_daily_energy_cost"]) == Dc("533.21")
    assert i["estimated_daily_revenue"] == Dc("7600")          # 8 x 950
    assert q2(i["daily_margin"]) == Dc("5266.79")              # 7600 - 1800 - 533.21
    assert q2(i["margin_ratio"]) == Dc("0.69")
    assert q2(i["revenue_per_km"]) == Dc("31.67")              # 7600 / 240


@pytest.mark.parametrize(
    "component, sub_code, expected",
    [
        # CHARGING: density 2.0 sits exactly on a point -> 90
        ("CHARGING_INFRASTRUCTURE", "STATION_DENSITY", "90"),
        # gap ratio 0.085714 in [0, 0.4]: 100 + (85-100) x 0.0857143/0.4 = 96.785714...
        ("CHARGING_INFRASTRUCTURE", "MAX_GAP_VS_RANGE", "96.79"),
        # fast share 0.5 sits exactly on a point -> 85
        ("CHARGING_INFRASTRUCTURE", "FAST_CHARGER_SHARE", "85"),
        ("ROAD_QUALITY", "ROAD_CONDITION", "85"),   # GOOD
        ("ROAD_QUALITY", "ROAD_TYPE", "100"),       # PITCH
        ("ROAD_QUALITY", "GRADIENT", "80"),         # ROLLING
        ("DEMAND", "TRIP_VOLUME", "88"),            # 8 trips sits on a point
        ("DEMAND", "DEMAND_INDEX", "88"),           # 1200 sits on a point
        ("DEMAND", "TRAFFIC", "100"),               # HIGH
        ("DEMAND", "COMPETITION", "75"),            # MODERATE
        ("REVENUE_POTENTIAL", "DAILY_MARGIN", "100"),   # 5266 > 5000, clamped
        ("REVENUE_POTENTIAL", "MARGIN_RATIO", "100"),   # 0.693 > 0.50, clamped
        # 31.667 in [25, 40]: 88 + 12 x (6.667/15) = 93.3333
        ("REVENUE_POTENTIAL", "REVENUE_PER_KM", "93.33"),
        ("ROUTE_RISK", "FLOOD_LANDSLIDE", "85"),    # LOW
        # 6 days in [0, 10]: 100 + (85-100) x 6/10 = 91
        ("ROUTE_RISK", "MONSOON_DISRUPTION", "91"),
        ("ROUTE_RISK", "SEASONAL", "85"),           # LOW
        ("ROUTE_RISK", "SECURITY", "100"),          # LOW
    ],
)
def test_reference_sub_factor_scores(ktm_dhulikhel, route_config, component, sub_code, expected):
    inputs = derive_route_inputs(ktm_dhulikhel)
    subs = composite_breakdown(route_config.component(component).scoring_rules, inputs)
    actual = {s.code: s.score for s in subs}[sub_code]
    assert q2(actual) == Dc(expected)


@pytest.mark.parametrize(
    "code, expected_normalized, expected_weighted",
    [
        ("CHARGING_INFRASTRUCTURE", "91.13", "27.339"),
        ("ROAD_QUALITY", "88.25", "17.650"),
        ("DEMAND", "87.85", "26.355"),
        ("REVENUE_POTENTIAL", "98.67", "9.867"),
        ("ROUTE_RISK", "89.05", "8.905"),
    ],
)
def test_reference_component_scores(
    ktm_dhulikhel, route_config, code, expected_normalized, expected_weighted
):
    result = ENGINE.score(ktm_dhulikhel, route_config)
    component = result.component(code)
    assert component.normalized_score == Dc(expected_normalized)
    assert component.weighted_score == Dc(expected_weighted)


def test_kathmandu_dhulikhel_reference_case(ktm_dhulikhel, route_config):
    """RS-01 — the canonical Class A corridor. Doc 07 §7.3.9."""
    r = ENGINE.score(ktm_dhulikhel, route_config)
    assert r.total_score == Dc("90.12")
    assert r.grade == "A"
    assert r.grade_label == "Class A - Low Risk"
    assert r.risk_level == "LOW"
    assert r.extras["charging_adequacy"] == "ADEQUATE"
    assert r.extras["revenue_potential"] == "HIGH"
    assert r.extras["recommendation"] == "ELIGIBLE"
    assert r.engine_version == "route-engine@1.0.0"
    assert not r.risk_factors
    codes = {f.code for f in r.positive_factors}
    assert {"DENSE_CHARGING", "FAST_CHARGING_AVAILABLE", "EXCELLENT_ROAD",
            "STRONG_DEMAND", "HEALTHY_MARGIN", "SHORT_ROUTE_EV_FRIENDLY"} <= codes
    # 6 monsoon days is above the 5-day threshold, so year-round operability does NOT fire
    assert "NO_SEASONAL_DISRUPTION" not in codes


def test_kathmandu_jiri_is_class_c(ktm_jiri, route_config):
    """RS-03 — the unfinanceable long rural corridor.

    Note on the adequacy verdict: Doc 07 §7.3.2 derives it from the *maximum gap* against
    usable range only. Here the gap is 96 km against a 140 km reference range (68.6%),
    which is MARGINAL by that rule even though the route is 187 km long and therefore
    cannot be completed on a single charge. The charging component still scores 23.2 on
    its own curves, so the outcome (Class C, not eligible) is correct; only the label
    understates the problem. See the spec-gap note in docs/07 §7.3.2.
    """
    r = ENGINE.score(ktm_jiri, route_config)
    assert r.grade == "C"
    assert r.extras["charging_adequacy"] == "MARGINAL"
    assert r.extras["recommendation"] == "NOT_ELIGIBLE"
    assert r.component("CHARGING_INFRASTRUCTURE").normalized_score <= Dc("25")
    risk_codes = {f.code for f in r.risk_factors}
    assert "SPARSE_CHARGING" in risk_codes
    assert "POOR_ROAD_CONDITION" in risk_codes
    assert "STEEP_GRADIENT" in risk_codes
    assert "HIGH_MONSOON_DISRUPTION" in risk_codes


# ---------------------------------------------------------------------------
# RS-02 .. RS-07 edge cases
# ---------------------------------------------------------------------------
def test_no_charging_stations_caps_component(ktm_dhulikhel, route_config):
    """RS-02."""
    payload = _replace(ktm_dhulikhel, charging_station_count=0, fast_charger_count=0,
                       max_charging_gap_km=Dc("30"))
    r = ENGINE.score(payload, route_config)
    assert r.extras["charging_adequacy"] == "INADEQUATE"
    assert r.component("CHARGING_INFRASTRUCTURE").normalized_score <= Dc("25")
    assert any(f.code == "NO_CHARGING_STATIONS" and f.severity == "CRITICAL"
               for f in r.risk_factors)


def test_off_road_very_poor_scores_low(ktm_dhulikhel, route_config):
    """RS-04."""
    payload = _replace(ktm_dhulikhel, road_type="OFF_ROAD", road_condition="VERY_POOR",
                       gradient_profile="STEEP")
    r = ENGINE.score(payload, route_config)
    assert r.component("ROAD_QUALITY").normalized_score < Dc("25")


def test_heavy_monsoon_scores_risk_low(ktm_dhulikhel, route_config):
    """RS-05."""
    payload = _replace(ktm_dhulikhel, monsoon_disruption_days=90, flood_landslide_risk="SEVERE",
                       seasonal_risk="SEVERE", security_risk="HIGH")
    r = ENGINE.score(payload, route_config)
    assert r.component("ROUTE_RISK").normalized_score < Dc("25")


def test_saturated_competition_depresses_demand(ktm_dhulikhel, route_config):
    """RS-06."""
    payload = _replace(ktm_dhulikhel, competition_level="SATURATED", traffic_density="LOW",
                       estimated_daily_trips=Dc("1.5"), passenger_volume_daily=150)
    r = ENGINE.score(payload, route_config)
    # 1.5 trips -> 22.5 x 0.40, index 150 -> 30 x 0.30, LOW traffic 55 x 0.15,
    # SATURATED 20 x 0.15  =  29.25
    assert r.component("DEMAND").normalized_score == Dc("29.25")
    assert any(f.code == "SATURATED_COMPETITION" for f in r.risk_factors)


def test_negative_margin_collapses_revenue_component(ktm_dhulikhel, route_config):
    """RS-07 — a loss-making route.

    The margin and margin-ratio sub-factors clamp to zero, but REVENUE_PER_KM is a
    gross-revenue signal and is unaffected by cost, so it still contributes
    93.33 x 0.20 = 18.67. The component collapses rather than zeroing, and the
    THIN_MARGIN risk factor is what makes the loss explicit.
    """
    payload = _replace(ktm_dhulikhel, estimated_daily_operating_cost=Dc("9000"))
    r = ENGINE.score(payload, route_config)
    component = r.component("REVENUE_POTENTIAL")
    assert component.normalized_score == Dc("18.67")
    sub = {s.code: s.score for s in component.sub_factors}
    assert sub["DAILY_MARGIN"] == Dc("0")
    assert sub["MARGIN_RATIO"] == Dc("0")
    assert any(f.code == "THIN_MARGIN" and f.severity == "HIGH" for f in r.risk_factors)


def test_mixed_road_uses_computed_pitch_score(ktm_dhulikhel, route_config):
    """Doc 07 §7.3.3 — MIXED scores 40 + 0.6 x pitch%."""
    payload = _replace(ktm_dhulikhel, road_type="MIXED", pitch_road_percent=Dc("70"))
    inputs = derive_route_inputs(payload)
    subs = composite_breakdown(route_config.component("ROAD_QUALITY").scoring_rules, inputs)
    road_type_score = {s.code: s.score for s in subs}["ROAD_TYPE"]
    assert road_type_score == Dc("82.0")  # 40 + 0.6 x 70


# ---------------------------------------------------------------------------
# RS-08/RS-09 grade boundaries
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "score, expected_grade",
    [("88", "A"), ("87.99", "B"), ("60", "B"), ("59.99", "C"), ("100", "A"), ("0", "C")],
)
def test_grade_boundaries_are_inclusive(route_config, score, expected_grade):
    """ROUTE v2 boundaries. Class A starts at 88 after the calibration review."""
    assert route_config.band_for(Dc(score)).grade == expected_grade


def test_archived_v1_still_reproduces_its_original_grading():
    """Doc 02 §2.4.4 — a superseded configuration stays queryable so a historical
    decision can be recomputed and byte-matched.

    Under v1 a score of 80.00 was Class A; under v2 it is Class B. Both must remain
    computable, because loans booked on v1 were graded by v1.
    """
    from app.engines.config_loader import default_config

    v1 = default_config("ROUTE", 1)
    v2 = default_config("ROUTE", 2)
    assert v1.version_no == 1 and v2.version_no == 2
    assert v1.band_for(Dc("80.00")).grade == "A"
    assert v2.band_for(Dc("80.00")).grade == "B"
    # the calibration changed grade bands ONLY - every weight and curve is identical
    assert [(c.code, c.weight, c.scoring_rules) for c in v1.active_components] ==            [(c.code, c.weight, c.scoring_rules) for c in v2.active_components]


def test_calibration_produces_a_discriminating_distribution(route_config):
    """The point of raising the boundary: the reference corridors must not all be Class A."""
    import json as _json
    from pathlib import Path as _Path

    scores = _json.loads(
        (_Path(__file__).resolve().parents[3] / "scripts" / "demo_route_scores.json")
        .read_text(encoding="utf-8")
    )
    grades = [route_config.band_for(Dc(v["score"])).grade for v in scores.values()]
    assert grades.count("A") == 3
    assert grades.count("B") == 6
    assert grades.count("C") == 1
    # no single grade may dominate the book
    assert max(grades.count(g) for g in "ABC") <= len(grades) * 0.7


# ---------------------------------------------------------------------------
# RS-10 validation
# ---------------------------------------------------------------------------
def test_missing_required_fields_raises_listing_all_of_them(route_config):
    payload = RouteScoringInput(route_name="Incomplete")
    with pytest.raises(IncompleteRouteData) as exc:
        ENGINE.score(payload, route_config)
    assert "total_distance_km" in exc.value.fields
    assert "estimated_daily_trips" in exc.value.fields


def test_passenger_route_requires_a_fare(ktm_dhulikhel, route_config):
    payload = _replace(ktm_dhulikhel, avg_fare_per_trip=Dc("0"))
    with pytest.raises(IncompleteRouteData) as exc:
        ENGINE.score(payload, route_config)
    assert "avg_fare_per_trip" in exc.value.fields


# ---------------------------------------------------------------------------
# charging adequacy gate — Doc 07 §7.3.2
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "stations, ratio, expected",
    [
        (6, "0.10", "ADEQUATE"),
        (2, "0.60", "ADEQUATE"),   # boundary: <= 0.6 is adequate
        (2, "0.61", "MARGINAL"),
        (1, "0.10", "MARGINAL"),   # a single station is never better than marginal
        (2, "0.85", "MARGINAL"),   # boundary: <= 0.85 is marginal
        (2, "0.86", "INADEQUATE"),
        (0, "0.10", "INADEQUATE"),
    ],
)
def test_charging_adequacy_thresholds(stations, ratio, expected):
    assert charging_adequacy(
        {"charging_station_count": stations, "gap_to_range_ratio": Dc(ratio)}
    ) == expected


# ---------------------------------------------------------------------------
# Invariants — Doc 07 §7.6, property-based
# ---------------------------------------------------------------------------
route_inputs = st.builds(
    RouteScoringInput,
    total_distance_km=st.decimals(min_value=Dc("1"), max_value=Dc("500"), places=1),
    road_type=st.sampled_from(["PITCH", "GRAVEL", "OFF_ROAD", "MIXED"]),
    road_condition=st.sampled_from(["EXCELLENT", "GOOD", "FAIR", "POOR", "VERY_POOR"]),
    gradient_profile=st.sampled_from(["FLAT", "ROLLING", "HILLY", "STEEP"]),
    pitch_road_percent=st.decimals(min_value=Dc("0"), max_value=Dc("100"), places=0),
    charging_station_count=st.integers(min_value=0, max_value=30),
    fast_charger_count=st.integers(min_value=0, max_value=10),
    max_charging_gap_km=st.decimals(min_value=Dc("0"), max_value=Dc("200"), places=1),
    passenger_volume_daily=st.integers(min_value=0, max_value=5000),
    estimated_daily_trips=st.decimals(min_value=Dc("0.5"), max_value=Dc("40"), places=1),
    avg_fare_per_trip=st.decimals(min_value=Dc("10"), max_value=Dc("3000"), places=0),
    traffic_density=st.sampled_from(["LOW", "MODERATE", "HIGH", "SEVERE"]),
    competition_level=st.sampled_from(["LOW", "MODERATE", "HIGH", "SATURATED"]),
    seasonal_risk=st.sampled_from(["NONE", "LOW", "MODERATE", "HIGH", "SEVERE"]),
    monsoon_disruption_days=st.integers(min_value=0, max_value=180),
    flood_landslide_risk=st.sampled_from(["NONE", "LOW", "MODERATE", "HIGH", "SEVERE"]),
    security_risk=st.sampled_from(["LOW", "MODERATE", "HIGH"]),
    electricity_tariff_per_kwh=st.decimals(min_value=Dc("5"), max_value=Dc("30"), places=1),
    estimated_daily_operating_cost=st.decimals(min_value=Dc("0"), max_value=Dc("20000"), places=0),
)


@given(payload=route_inputs)
@settings(max_examples=250, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_score_is_bounded_and_additive(payload, route_config):
    r = ENGINE.score(payload, route_config)
    assert Dc("0") <= r.total_score <= Dc("100")
    total = sum(c.weighted_score for c in r.components)
    assert abs(total - r.total_score) <= Dc("0.01")
    assert r.grade == route_config.band_for(r.total_score).grade
    for c in r.components:
        assert Dc("0") <= c.normalized_score <= Dc("100")


@given(payload=route_inputs)
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_scoring_is_deterministic(payload, route_config):
    assert ENGINE.score(payload, route_config) == ENGINE.score(payload, route_config)


@given(payload=route_inputs, extra=st.integers(min_value=1, max_value=10))
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_more_fast_charging_never_lowers_the_score(payload, extra, route_config):
    """Adding DC fast chargers improves every charging sub-factor at once."""
    better = _replace(
        payload,
        charging_station_count=payload.charging_station_count + extra,
        fast_charger_count=payload.fast_charger_count + extra,
    )
    assert (
        ENGINE.score(better, route_config).component("CHARGING_INFRASTRUCTURE").normalized_score
        >= ENGINE.score(payload, route_config).component("CHARGING_INFRASTRUCTURE").normalized_score
    )


def test_adding_a_slow_charger_can_dilute_fast_charger_share(ktm_dhulikhel, route_config):
    """A deliberate, documented property of the configured curves, pinned so it cannot
    change silently.

    FAST_CHARGER_SHARE is a ratio, so adding a slow station to a corridor that is already
    well served by DC chargers lowers that sub-factor even though absolute coverage
    improved. Density rises at the same time, so the net effect here is small and
    positive, but on a corridor already at maximum density the net effect is negative.
    Flagged in the calibration notes as a candidate for switching the sub-factor to an
    absolute fast-charger count.
    """
    saturated = _replace(ktm_dhulikhel, charging_station_count=9, fast_charger_count=9,
                         max_charging_gap_km=Dc("4"))
    diluted = _replace(saturated, charging_station_count=10)
    before = ENGINE.score(saturated, route_config).component("CHARGING_INFRASTRUCTURE")
    after = ENGINE.score(diluted, route_config).component("CHARGING_INFRASTRUCTURE")
    assert after.normalized_score < before.normalized_score


@given(payload=route_inputs, extra_days=st.integers(min_value=1, max_value=60))
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_more_disruption_never_raises_the_risk_score(payload, extra_days, route_config):
    worse = _replace(payload, monsoon_disruption_days=payload.monsoon_disruption_days + extra_days)
    assert (
        ENGINE.score(worse, route_config).component("ROUTE_RISK").normalized_score
        <= ENGINE.score(payload, route_config).component("ROUTE_RISK").normalized_score
    )


def _replace(payload: RouteScoringInput, **changes) -> RouteScoringInput:
    import dataclasses

    return dataclasses.replace(payload, **changes)
