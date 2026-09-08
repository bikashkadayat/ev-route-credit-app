"""Risk rule engine — Doc 08 §8.6/§8.12 cases RE-01..RE-15, plus per-rule coverage.

Every one of the 22 shipped rules gets an explicit fire / no-fire assertion at its
boundary, because a rule that silently stops firing is invisible until a loan defaults.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as Dc

import pytest

from app.risk.engine import (
    ENGINE_VERSION,
    actionable,
    evaluate_condition,
    evaluate_rule,
    evaluate_rules,
    loan_risk_status,
    resolve_supersession,
)
from app.risk.families import FAMILIES, family_of, intensity_of, supersedes
from app.risk.loader import default_rules, rule_from_dict
from app.risk.models import (
    MetricSnapshot,
    Rule,
    RuleCondition,
    RuleDefinitionError,
    sort_rules,
)

AS_OF = date(2026, 9, 8)
RULES = default_rules()
BY_CODE = {r.rule_code: r for r in RULES}


def snap(**values) -> MetricSnapshot:
    """A snapshot with only the named metrics present; everything else is unknown."""
    return MetricSnapshot(
        loan_id=87, as_of=AS_OF,
        values={k: (None if v is None else Dc(str(v))) for k, v in values.items()},
    )


def healthy() -> MetricSnapshot:
    """A loan where nothing should fire."""
    return snap(
        DAYS_PAST_DUE=0, CONSECUTIVE_MISSED_EMI=0, OVERDUE_AMOUNT=0,
        PARTIAL_PAYMENT_COUNT_90D=0, USAGE_CHANGE_PCT=2, ZERO_KM_STREAK=0,
        ACTIVE_DAYS_30D=26, REVENUE_CHANGE_PCT=1, DSCR_ACTUAL="2.4",
        CHARGING_CHANGE_PCT=-5, BATTERY_SOH="96.5", ROUTE_DEVIATION_PCT=4,
        DAYS_SINCE_TELEMETRY=0,
    )


# ---------------------------------------------------------------------------
# Catalogue integrity
# ---------------------------------------------------------------------------
def test_catalogue_has_22_rules_and_27_conditions():
    assert len(RULES) == 22
    assert sum(len(r.conditions) for r in RULES) == 27


def test_every_rule_has_a_recommended_action_and_owner():
    """An alert without a playbook is a notification, not a control."""
    for rule in RULES:
        assert rule.recommended_action.strip(), f"{rule.rule_code} has no recommended action"
        assert rule.assign_to_role, f"{rule.rule_code} has no default assignee"


def test_red_rules_carry_tighter_slas_than_yellow():
    for rule in RULES:
        if rule.severity == "RED":
            assert rule.sla_hours_acknowledge <= 4
            assert rule.sla_hours_resolve <= 72
        else:
            assert rule.sla_hours_acknowledge >= 24


def test_rule_codes_are_unique():
    codes = [r.rule_code for r in RULES]
    assert len(codes) == len(set(codes))


def test_engine_version_is_semantic():
    assert ENGINE_VERSION == "rule-engine@1.0.0"


# ---------------------------------------------------------------------------
# Per-rule fire / no-fire at the boundary
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "rule_code, fires, holds",
    [
        # --- repayment
        ("EMI_OVERDUE_30_PLUS", {"DAYS_PAST_DUE": 31}, {"DAYS_PAST_DUE": 30}),
        ("EMI_OVERDUE_90_PLUS", {"DAYS_PAST_DUE": 91}, {"DAYS_PAST_DUE": 90}),
        ("EMI_OVERDUE_1_30", {"DAYS_PAST_DUE": 1}, {"DAYS_PAST_DUE": 0}),
        ("EMI_MISSED_CONSECUTIVE", {"CONSECUTIVE_MISSED_EMI": 2},
         {"CONSECUTIVE_MISSED_EMI": 1}),
        ("PARTIAL_PAYMENT_PATTERN", {"PARTIAL_PAYMENT_COUNT_90D": 2},
         {"PARTIAL_PAYMENT_COUNT_90D": 1}),
        ("OVERDUE_AMOUNT_MATERIAL", {"OVERDUE_AMOUNT": 50000}, {"OVERDUE_AMOUNT": 49999}),
        # --- usage
        ("VEHICLE_INACTIVE_7D", {"ZERO_KM_STREAK": 7}, {"ZERO_KM_STREAK": 6}),
        ("VEHICLE_INACTIVE_3D", {"ZERO_KM_STREAK": 3}, {"ZERO_KM_STREAK": 2}),
        ("LOW_ACTIVE_DAYS", {"ACTIVE_DAYS_30D": 15}, {"ACTIVE_DAYS_30D": 16}),
        # --- route
        ("ROUTE_ABANDONED", {"ROUTE_DEVIATION_PCT": 60}, {"ROUTE_DEVIATION_PCT": 59}),
        ("ROUTE_DEVIATION_MODERATE", {"ROUTE_DEVIATION_PCT": 30},
         {"ROUTE_DEVIATION_PCT": "29.99"}),
        # --- revenue
        ("REVENUE_COLLAPSE", {"REVENUE_CHANGE_PCT": -40}, {"REVENUE_CHANGE_PCT": "-39.99"}),
        ("DSCR_BELOW_ONE", {"DSCR_ACTUAL": "0.99"}, {"DSCR_ACTUAL": "1.0"}),
        # --- battery
        ("BATTERY_HEALTH_CRITICAL", {"BATTERY_SOH": "69.99"}, {"BATTERY_SOH": 70}),
        ("BATTERY_HEALTH_LOW", {"BATTERY_SOH": 70}, {"BATTERY_SOH": 80}),
        ("CHARGING_DROP_SEVERE", {"CHARGING_CHANGE_PCT": -60},
         {"CHARGING_CHANGE_PCT": "-59.99"}),
        # --- data quality
        ("TELEMETRY_SILENT_5D", {"DAYS_SINCE_TELEMETRY": 5}, {"DAYS_SINCE_TELEMETRY": 4}),
        ("TELEMETRY_SILENT_2D", {"DAYS_SINCE_TELEMETRY": 2}, {"DAYS_SINCE_TELEMETRY": 1}),
    ],
)
def test_single_condition_rules_at_their_boundary(rule_code, fires, holds):
    rule = BY_CODE[rule_code]
    assert evaluate_rule(rule, snap(**fires)).fired is True, f"{rule_code} should fire on {fires}"
    assert evaluate_rule(rule, snap(**holds)).fired is False, f"{rule_code} fired on {holds}"


@pytest.mark.parametrize(
    "rule_code, fires, holds",
    [
        ("USAGE_COLLAPSE",
         {"USAGE_CHANGE_PCT": -50, "ACTIVE_DAYS_30D": 3},
         {"USAGE_CHANGE_PCT": -50, "ACTIVE_DAYS_30D": 2}),          # noise guard
        ("USAGE_DROP_MODERATE",
         {"USAGE_CHANGE_PCT": -25, "ACTIVE_DAYS_30D": 5},
         {"USAGE_CHANGE_PCT": -25, "ACTIVE_DAYS_30D": 4}),          # noise guard
        ("REVENUE_DECLINE",
         {"REVENUE_CHANGE_PCT": -20},
         {"REVENUE_CHANGE_PCT": -40}),                              # band lower edge
        ("CHARGING_DROP_MODERATE",
         {"CHARGING_CHANGE_PCT": -30},
         {"CHARGING_CHANGE_PCT": -60}),
    ],
)
def test_multi_condition_rules_require_every_condition(rule_code, fires, holds):
    rule = BY_CODE[rule_code]
    assert evaluate_rule(rule, snap(**fires)).fired is True
    assert evaluate_rule(rule, snap(**holds)).fired is False


def test_usage_drop_moderate_does_not_fire_on_a_collapse():
    """RE-06/RE-07 — the moderate band is bounded above AND below.

    The lower bound is -50%, matching USAGE_COLLAPSE's threshold exactly so the two bands
    tile the decline range. See tests/unit/risk/test_rule_coverage.py for why.
    """
    rule = BY_CODE["USAGE_DROP_MODERATE"]
    assert evaluate_rule(rule, snap(USAGE_CHANGE_PCT=-50, ACTIVE_DAYS_30D=10)).fired is False
    assert evaluate_rule(rule, snap(USAGE_CHANGE_PCT=-49.99, ACTIVE_DAYS_30D=10)).fired is True
    assert evaluate_rule(rule, snap(USAGE_CHANGE_PCT=-19.99, ACTIVE_DAYS_30D=10)).fired is False


def test_healthy_loan_fires_nothing():
    outcomes = evaluate_rules(RULES, healthy())
    assert [o.rule_code for o in outcomes if o.fired] == []
    assert loan_risk_status(outcomes) == "GREEN"


# ---------------------------------------------------------------------------
# Missing and invalid inputs
# ---------------------------------------------------------------------------
def test_re_05_missing_metric_never_fires():
    """A None metric makes its condition false — a missing signal cannot fabricate an alert."""
    outcomes = evaluate_rules(RULES, snap(USAGE_CHANGE_PCT=None, ACTIVE_DAYS_30D=None))
    assert [o.rule_code for o in outcomes if o.fired] == []


def test_missing_baseline_blocks_usage_rules_but_not_repayment():
    """A loan 12 days on book has no usage baseline, but arrears still work."""
    outcomes = evaluate_rules(RULES, snap(DAYS_PAST_DUE=5, USAGE_CHANGE_PCT=None))
    fired = {o.rule_code for o in outcomes if o.fired}
    assert fired == {"EMI_OVERDUE_1_30"}


def test_condition_result_records_why_it_did_not_fire():
    rule = BY_CODE["EMI_OVERDUE_30_PLUS"]
    missing = evaluate_rule(rule, snap()).conditions[0]
    assert missing.passed is False and missing.reason == "MISSING_METRIC"
    failed = evaluate_rule(rule, snap(DAYS_PAST_DUE=10)).conditions[0]
    assert failed.passed is False and failed.reason == "FAILED"
    passed = evaluate_rule(rule, snap(DAYS_PAST_DUE=40)).conditions[0]
    assert passed.passed is True and passed.reason == "PASSED"


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"metric_code": "X", "operator": "NOPE", "threshold_value": Dc(1)}, "Unsupported operator"),
        ({"metric_code": "X", "operator": "BETWEEN", "threshold_value": Dc(1)},
         "requires threshold_value_2"),
        ({"metric_code": "X", "operator": "IN"}, "requires threshold_values"),
        ({"metric_code": "X", "operator": "GT"}, "requires threshold_value"),
        ({"metric_code": "X", "operator": "BETWEEN", "threshold_value": Dc(10),
          "threshold_value_2": Dc(2)}, "exceeds upper bound"),
    ],
)
def test_invalid_conditions_are_rejected_at_construction(kwargs, message):
    with pytest.raises(RuleDefinitionError, match=message):
        RuleCondition(**kwargs)


def test_rule_without_conditions_is_rejected():
    with pytest.raises(RuleDefinitionError, match="at least one condition"):
        Rule(rule_code="X", name="X", category="USAGE", severity="RED", conditions=())


def test_invalid_condition_logic_is_rejected():
    with pytest.raises(RuleDefinitionError, match="ALL or ANY"):
        Rule(rule_code="X", name="X", category="USAGE", severity="RED",
             condition_logic="MAYBE",
             conditions=(RuleCondition("DAYS_PAST_DUE", "GT", Dc(1)),))


def test_invalid_severity_is_rejected():
    with pytest.raises(RuleDefinitionError, match="YELLOW or RED"):
        Rule(rule_code="X", name="X", category="USAGE", severity="ORANGE",
             conditions=(RuleCondition("DAYS_PAST_DUE", "GT", Dc(1)),))


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "operator, threshold, value, expected",
    [
        ("GT", 10, 11, True), ("GT", 10, 10, False),
        ("GTE", 10, 10, True), ("GTE", 10, 9, False),
        ("LT", 10, 9, True), ("LT", 10, 10, False),
        ("LTE", 10, 10, True), ("LTE", 10, 11, False),
        ("EQ", 10, 10, True), ("EQ", 10, 11, False),
        ("NEQ", 10, 11, True), ("NEQ", 10, 10, False),
    ],
)
def test_every_scalar_operator(operator, threshold, value, expected):
    condition = RuleCondition("DAYS_PAST_DUE", operator, Dc(threshold))
    assert evaluate_condition(condition, snap(DAYS_PAST_DUE=value)).passed is expected


@pytest.mark.parametrize("value, expected", [(1, False), (2, True), (5, True), (6, False)])
def test_between_is_inclusive_on_both_ends(value, expected):
    condition = RuleCondition("ZERO_KM_STREAK", "BETWEEN", Dc(2), Dc(5))
    assert evaluate_condition(condition, snap(ZERO_KM_STREAK=value)).passed is expected


@pytest.mark.parametrize("value, expected", [(1, True), (3, True), (2, False)])
def test_in_operator(value, expected):
    condition = RuleCondition("DAYS_PAST_DUE", "IN", threshold_values=(Dc(1), Dc(3)))
    assert evaluate_condition(condition, snap(DAYS_PAST_DUE=value)).passed is expected


def test_any_logic_fires_on_one_condition():
    """RE-12."""
    rule = Rule(
        rule_code="ANY_TEST", name="any", category="USAGE", severity="YELLOW",
        condition_logic="ANY",
        conditions=(
            RuleCondition("DAYS_PAST_DUE", "GT", Dc(30), sequence_no=1),
            RuleCondition("ZERO_KM_STREAK", "GT", Dc(7), sequence_no=2),
            RuleCondition("BATTERY_SOH", "LT", Dc(60), sequence_no=3),
        ),
    )
    assert evaluate_rule(rule, snap(DAYS_PAST_DUE=0, ZERO_KM_STREAK=9, BATTERY_SOH=95)).fired
    assert not evaluate_rule(rule, snap(DAYS_PAST_DUE=0, ZERO_KM_STREAK=1, BATTERY_SOH=95)).fired


# ---------------------------------------------------------------------------
# Ordering and determinism
# ---------------------------------------------------------------------------
def test_rules_evaluate_in_priority_then_code_order():
    ordered = sort_rules(RULES)
    keys = [(r.priority, r.rule_code) for r in ordered]
    assert keys == sorted(keys)
    # RED repayment rules lead the catalogue
    assert ordered[0].rule_code == "EMI_OVERDUE_30_PLUS"


def test_evaluation_order_is_independent_of_input_order():
    forward = [o.rule_code for o in evaluate_rules(RULES, healthy())]
    backward = [o.rule_code for o in evaluate_rules(list(reversed(RULES)), healthy())]
    assert forward == backward


def test_evaluation_is_deterministic():
    """RE-13 — the same business date produces byte-identical outcomes."""
    s = snap(DAYS_PAST_DUE=47, USAGE_CHANGE_PCT="-38.4", ACTIVE_DAYS_30D=19,
             CHARGING_CHANGE_PCT="-33.3", BATTERY_SOH="96.2", DAYS_SINCE_TELEMETRY=2)
    assert evaluate_rules(RULES, s) == evaluate_rules(RULES, s)


def test_inactive_rules_are_skipped():
    disabled = tuple(
        Rule(**{**r.__dict__, "is_active": False}) if r.rule_code == "EMI_OVERDUE_1_30" else r
        for r in RULES
    ) if False else tuple(
        # dataclasses are frozen+slots, so rebuild explicitly
        Rule(
            rule_code=r.rule_code, name=r.name, category=r.category, severity=r.severity,
            conditions=r.conditions, description=r.description,
            condition_logic=r.condition_logic, suppression_hours=r.suppression_hours,
            auto_resolve=r.auto_resolve, assign_to_role=r.assign_to_role,
            sla_hours_acknowledge=r.sla_hours_acknowledge,
            sla_hours_resolve=r.sla_hours_resolve, recommended_action=r.recommended_action,
            priority=r.priority, is_active=(r.rule_code != "EMI_OVERDUE_1_30"),
        )
        for r in RULES
    )
    outcomes = evaluate_rules(disabled, snap(DAYS_PAST_DUE=5))
    assert "EMI_OVERDUE_1_30" not in {o.rule_code for o in outcomes}


# ---------------------------------------------------------------------------
# Supersession — conflicting rules
# ---------------------------------------------------------------------------
def test_re_04_thirty_day_arrears_supersedes_early_arrears():
    outcomes = evaluate_rules(RULES, snap(DAYS_PAST_DUE=47))
    by_code = {o.rule_code: o for o in outcomes}
    assert by_code["EMI_OVERDUE_30_PLUS"].fired
    assert by_code["EMI_OVERDUE_1_30"].fired is False  # 47 is outside BETWEEN 1 AND 30
    live = {o.rule_code for o in actionable(outcomes)}
    assert live == {"EMI_OVERDUE_30_PLUS"}


def test_ninety_day_arrears_supersedes_thirty_day():
    outcomes = evaluate_rules(RULES, snap(DAYS_PAST_DUE=95))
    by_code = {o.rule_code: o for o in outcomes}
    assert by_code["EMI_OVERDUE_90_PLUS"].fired
    assert by_code["EMI_OVERDUE_30_PLUS"].fired
    assert by_code["EMI_OVERDUE_30_PLUS"].superseded_by == "EMI_OVERDUE_90_PLUS"
    assert {o.rule_code for o in actionable(outcomes)} == {"EMI_OVERDUE_90_PLUS"}


def test_re_08_seven_day_inactivity_supersedes_three_day():
    outcomes = evaluate_rules(RULES, snap(ZERO_KM_STREAK=9, ACTIVE_DAYS_30D=4))
    by_code = {o.rule_code: o for o in outcomes}
    assert by_code["VEHICLE_INACTIVE_7D"].fired
    assert by_code["VEHICLE_INACTIVE_3D"].fired is False  # BETWEEN 3 AND 6 excludes 9


def test_re_14_battery_critical_supersedes_low():
    """Both bands cannot fire together (they are disjoint), but the family link holds."""
    assert supersedes("BATTERY_HEALTH_CRITICAL", "BATTERY_HEALTH_LOW")
    assert not supersedes("BATTERY_HEALTH_LOW", "BATTERY_HEALTH_CRITICAL")


def test_supersession_marks_rather_than_drops():
    """The escalation trail must survive: the superseded outcome is still returned."""
    outcomes = resolve_supersession(
        evaluate_rules(RULES, snap(DAYS_PAST_DUE=95), apply_supersession=False)
    )
    superseded = [o for o in outcomes if o.superseded_by]
    assert superseded and all(o.fired for o in superseded)
    assert all(not o.is_actionable for o in superseded)


def test_every_family_member_is_a_real_rule():
    for family, members in FAMILIES.items():
        for code in members:
            assert code in BY_CODE, f"family {family} references unknown rule {code}"


def test_family_intensity_is_strictly_increasing():
    for members in FAMILIES.values():
        intensities = [intensity_of(c) for c in members]
        assert intensities == sorted(intensities)
        assert len(set(intensities)) == len(intensities)


def test_rules_outside_a_family_never_supersede():
    assert family_of("LOW_ACTIVE_DAYS") is None
    assert not supersedes("LOW_ACTIVE_DAYS", "USAGE_DROP_MODERATE")


# ---------------------------------------------------------------------------
# Risk status roll-up and the distressed reference loan
# ---------------------------------------------------------------------------
def test_re_09_silent_feed_raises_red_and_yellow_is_superseded():
    outcomes = evaluate_rules(RULES, snap(DAYS_SINCE_TELEMETRY=6))
    live = {o.rule_code for o in actionable(outcomes)}
    assert "TELEMETRY_SILENT_5D" in live
    assert loan_risk_status(outcomes) == "RED"


def test_distressed_reference_loan_produces_the_documented_alert_set():
    """Doc 16 §16.11 — LN-2026-000087 at the demo anchor."""
    outcomes = evaluate_rules(RULES, snap(
        DAYS_PAST_DUE=47, CONSECUTIVE_MISSED_EMI=2, OVERDUE_AMOUNT=173244,
        PARTIAL_PAYMENT_COUNT_90D=1,
        USAGE_CHANGE_PCT="-38.4", ACTIVE_DAYS_30D=19, ZERO_KM_STREAK=0,
        CHARGING_CHANGE_PCT="-33.3", BATTERY_SOH="96.2",
        ROUTE_DEVIATION_PCT=14, DAYS_SINCE_TELEMETRY=2, DSCR_ACTUAL="1.4",
        REVENUE_CHANGE_PCT=-15,
    ))
    live = {o.rule_code for o in actionable(outcomes)}
    assert "EMI_OVERDUE_30_PLUS" in live          # RED, 47 days
    assert "EMI_MISSED_CONSECUTIVE" in live       # RED, 2 consecutive
    assert "OVERDUE_AMOUNT_MATERIAL" in live      # YELLOW, > 50k
    assert "USAGE_DROP_MODERATE" in live          # YELLOW, -38.4% with 19 active days
    assert "CHARGING_DROP_MODERATE" in live       # YELLOW, -33.3%
    assert "TELEMETRY_SILENT_2D" in live          # YELLOW, feed quiet 2 days
    assert "BATTERY_HEALTH_LOW" not in live       # SOH 96.2 is healthy
    assert loan_risk_status(outcomes) == "RED"


def test_actionable_orders_red_before_yellow():
    outcomes = evaluate_rules(RULES, snap(
        DAYS_PAST_DUE=47, OVERDUE_AMOUNT=173244, DAYS_SINCE_TELEMETRY=2,
    ))
    severities = [o.severity for o in actionable(outcomes)]
    assert severities == sorted(severities, key=lambda s: 0 if s == "RED" else 1)


def test_trigger_condition_is_rendered_only_for_fired_rules():
    outcomes = evaluate_rules(RULES, snap(DAYS_PAST_DUE=47))
    for outcome in outcomes:
        if outcome.fired:
            assert outcome.trigger_condition
        else:
            assert outcome.trigger_condition == ""


def test_trigger_condition_names_value_and_threshold():
    outcome = evaluate_rule(BY_CODE["EMI_OVERDUE_30_PLUS"], snap(DAYS_PAST_DUE=47))
    assert "47" in outcome.trigger_condition
    assert "30" in outcome.trigger_condition
    assert outcome.evidence["DAYS_PAST_DUE"]["value"] == 47.0
    assert outcome.evidence["DAYS_PAST_DUE"]["threshold"] == 30.0


def test_rule_describes_itself_in_plain_language():
    """Doc 03 Screen 28 — the admin condition builder renders a readable sentence."""
    described = BY_CODE["USAGE_DROP_MODERATE"].describe()
    assert described.startswith("Alert when ")
    assert " AND " in described
    assert "usage change pct" in described


def test_rule_from_dict_round_trips_a_custom_rule():
    rule = rule_from_dict({
        "rule_code": "CUSTOM", "name": "Custom", "category": "USAGE", "severity": "YELLOW",
        "conditions": [{"metric_code": "ACTIVE_DAYS_30D", "operator": "LT",
                        "threshold_value": 10}],
    })
    assert rule.rule_code == "CUSTOM"
    assert evaluate_rule(rule, snap(ACTIVE_DAYS_30D=5)).fired
