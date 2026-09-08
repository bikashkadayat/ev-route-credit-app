"""Metric registry, snapshot assembly and behaviour score — Doc 08 §8.3 / §8.9."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal as Dc
from pathlib import Path

import pytest

from app.risk.behaviour import (
    BEHAVIOUR_COMPONENTS,
    behaviour_band,
    behaviour_breakdown,
    behaviour_score,
)
from app.risk.loader import default_rules
from app.risk.metrics import (
    METRIC_REGISTRY,
    LoanSnapshotRow,
    build_metric_snapshot,
    registered_metrics,
)

AS_OF = date(2026, 9, 8)


def row(**kwargs) -> LoanSnapshotRow:
    return LoanSnapshotRow(loan_id=87, as_of=AS_OF, **kwargs)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def test_nineteen_metrics_are_registered():
    assert len(METRIC_REGISTRY) == 19


def test_every_metric_a_rule_references_actually_exists():
    """A rule referencing an unregistered metric would silently never fire."""
    referenced = {c.metric_code for rule in default_rules() for c in rule.conditions}
    missing = referenced - set(METRIC_REGISTRY)
    assert not missing, f"rules reference unregistered metrics: {sorted(missing)}"


def test_registering_a_duplicate_metric_is_refused():
    from app.risk.metrics import metric

    with pytest.raises(ValueError, match="already registered"):
        metric("DAYS_PAST_DUE")(lambda r: None)


def test_metric_codes_are_screaming_snake_case():
    for code in registered_metrics():
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*", code), code


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------
def test_snapshot_contains_every_registered_metric():
    snapshot = build_metric_snapshot(row())
    assert set(snapshot.values) == set(METRIC_REGISTRY)


def test_empty_row_yields_all_none_and_therefore_no_alerts():
    snapshot = build_metric_snapshot(row())
    assert all(v is None for v in snapshot.values.values())


def test_usage_change_pct_is_computed_against_the_baseline():
    snapshot = build_metric_snapshot(
        row(avg_daily_km_7d=Dc("92.5"), baseline_daily_km=Dc("150.2"))
    )
    # (92.5 - 150.2) / 150.2 * 100 = -38.41...
    assert snapshot.get("USAGE_CHANGE_PCT").quantize(Dc("0.1")) == Dc("-38.4")


def test_usage_change_is_none_without_a_baseline():
    """A loan under 30 days on book has no baseline, so usage rules must not fire."""
    assert build_metric_snapshot(row(avg_daily_km_7d=Dc("90"))).get("USAGE_CHANGE_PCT") is None


def test_usage_change_is_none_when_the_baseline_is_zero():
    snapshot = build_metric_snapshot(
        row(avg_daily_km_7d=Dc("90"), baseline_daily_km=Dc("0"))
    )
    assert snapshot.get("USAGE_CHANGE_PCT") is None


def test_dscr_actual_needs_both_contribution_and_emi():
    assert build_metric_snapshot(
        row(monthly_net_contribution=Dc("168017.61"))
    ).get("DSCR_ACTUAL") is None
    snapshot = build_metric_snapshot(
        row(monthly_net_contribution=Dc("168017.61"), emi_amount=Dc("67349.10"))
    )
    assert snapshot.get("DSCR_ACTUAL").quantize(Dc("0.001")) == Dc("2.495")


def test_dscr_actual_is_none_on_a_zero_emi():
    assert build_metric_snapshot(
        row(monthly_net_contribution=Dc("1000"), emi_amount=Dc("0"))
    ).get("DSCR_ACTUAL") is None


def test_revenue_change_compares_actual_against_the_underwriting_projection():
    snapshot = build_metric_snapshot(
        row(estimated_revenue_30d=Dc("120000"), projected_revenue_30d=Dc("200000"))
    )
    assert snapshot.get("REVENUE_CHANGE_PCT") == Dc("-40")


def test_charging_change_uses_the_session_baseline():
    snapshot = build_metric_snapshot(
        row(charging_sessions_30d=14, baseline_charging_sessions_30d=Dc("21"))
    )
    assert snapshot.get("CHARGING_CHANGE_PCT").quantize(Dc("0.1")) == Dc("-33.3")


def test_passthrough_metrics_are_carried_verbatim():
    snapshot = build_metric_snapshot(row(
        days_past_due=47, consecutive_missed_emi=2, overdue_amount=Dc("173244"),
        zero_km_streak_days=3, active_days_30d=19, latest_state_of_health=Dc("96.2"),
        avg_route_deviation_percent=Dc("14"), days_since_last_telemetry=2,
        maintenance_downtime_30d=4, min_state_of_charge_7d=Dc("8"),
    ))
    assert snapshot.get("DAYS_PAST_DUE") == Dc("47")
    assert snapshot.get("CONSECUTIVE_MISSED_EMI") == Dc("2")
    assert snapshot.get("OVERDUE_AMOUNT") == Dc("173244")
    assert snapshot.get("ZERO_KM_STREAK") == Dc("3")
    assert snapshot.get("ACTIVE_DAYS_30D") == Dc("19")
    assert snapshot.get("BATTERY_SOH") == Dc("96.2")
    assert snapshot.get("ROUTE_DEVIATION_PCT") == Dc("14")
    assert snapshot.get("DAYS_SINCE_TELEMETRY") == Dc("2")
    assert snapshot.get("MAINTENANCE_DOWNTIME_30D") == Dc("4")
    assert snapshot.get("BATTERY_MIN_SOC_7D") == Dc("8")


def test_ltv_current_reads_from_extras_and_defaults_to_none():
    assert build_metric_snapshot(row()).get("LTV_CURRENT") is None
    assert build_metric_snapshot(
        row(extras={"LTV_CURRENT": Dc("72.5")})
    ).get("LTV_CURRENT") == Dc("72.5")


def test_snapshot_with_values_overrides_without_mutating():
    original = build_metric_snapshot(row(days_past_due=5))
    amended = original.with_values(DAYS_PAST_DUE=47)
    assert original.get("DAYS_PAST_DUE") == Dc("5")
    assert amended.get("DAYS_PAST_DUE") == Dc("47")


# ---------------------------------------------------------------------------
# Behaviour score
# ---------------------------------------------------------------------------
def test_behaviour_weights_sum_to_one():
    assert sum(c["weight"] for c in BEHAVIOUR_COMPONENTS) == Dc("1.00")


def test_healthy_loan_scores_high():
    snapshot = build_metric_snapshot(row(
        days_past_due=0, avg_daily_km_7d=Dc("160"), baseline_daily_km=Dc("160"),
        active_days_30d=26, latest_state_of_health=Dc("98"), days_since_last_telemetry=0,
    ))
    score = behaviour_score(snapshot)
    assert score >= Dc("90")
    assert behaviour_band(score) == "HEALTHY"


def test_distressed_loan_scores_stressed():
    """Doc 16 §16.10 — the reference distressed loan lands in the STRESSED band."""
    snapshot = build_metric_snapshot(row(
        days_past_due=47, avg_daily_km_7d=Dc("92.5"), baseline_daily_km=Dc("150.2"),
        active_days_30d=19, latest_state_of_health=Dc("96.2"), days_since_last_telemetry=2,
    ))
    score = behaviour_score(snapshot)
    assert Dc("40") <= score < Dc("60")
    assert behaviour_band(score) == "STRESSED"


def test_behaviour_score_is_bounded_and_deterministic():
    snapshot = build_metric_snapshot(row(days_past_due=200, active_days_30d=0))
    score = behaviour_score(snapshot)
    assert Dc("0") <= score <= Dc("100")
    assert behaviour_score(snapshot) == score


@pytest.mark.parametrize(
    "score, band",
    [("100", "HEALTHY"), ("80", "HEALTHY"), ("79.99", "WATCH"), ("60", "WATCH"),
     ("59.99", "STRESSED"), ("40", "STRESSED"), ("39.99", "CRITICAL"), ("0", "CRITICAL")],
)
def test_behaviour_bands(score, band):
    assert behaviour_band(Dc(score)) == band


def test_behaviour_breakdown_reconciles_with_the_total():
    snapshot = build_metric_snapshot(row(days_past_due=15, active_days_30d=20))
    breakdown = behaviour_breakdown(snapshot)
    assert len(breakdown) == len(BEHAVIOUR_COMPONENTS)
    total = sum(c["contribution"] for c in breakdown)
    assert abs(total - behaviour_score(snapshot)) <= Dc("0.05")


def test_missing_metrics_fall_back_to_documented_defaults():
    """A loan with no telemetry still gets a score, driven by repayment."""
    score = behaviour_score(build_metric_snapshot(row(days_past_due=0)))
    assert score > Dc("70")


# ---------------------------------------------------------------------------
# Catalogue / seed drift
# ---------------------------------------------------------------------------
SEED_SQL = Path(__file__).resolve().parents[3].parent / "db" / "seed" / "01_reference_data.sql"


def test_every_rule_in_the_catalogue_appears_in_the_sql_seed():
    sql = SEED_SQL.read_text(encoding="utf-8")
    for rule in default_rules():
        assert f"'{rule.rule_code}'" in sql, f"{rule.rule_code} missing from the SQL seed"


def test_rule_thresholds_match_between_catalogue_and_seed():
    """The exact defect class this guards: a threshold edited in one place only."""
    sql = SEED_SQL.read_text(encoding="utf-8")
    for rule in default_rules():
        for condition in rule.conditions:
            if condition.threshold_value is None:
                continue
            threshold = format(condition.threshold_value.normalize(), "f")
            pattern = (
                rf"\('{re.escape(rule.rule_code)}','{re.escape(condition.metric_code)}',"
                rf"'{condition.operator}',{re.escape(threshold)}\b"
            )
            assert re.search(pattern, sql), (
                f"{rule.rule_code}/{condition.metric_code} {condition.operator} {threshold} "
                f"not found in the SQL seed"
            )
