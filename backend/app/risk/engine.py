"""Risk rule engine — Doc 08 §8.6.2. `rule-engine@1.0.0`

PURE. A function of (rules, snapshot) only. No database, no clock, no randomness — the
business date arrives on the snapshot, so an evaluation for 8 September produces the same
result whenever it is run (Doc 08 §8.6.3 idempotency).

Null semantics (Doc 08 §8.3): a metric that is None makes its condition evaluate to
**false**, never true. A missing signal never fabricates an alert — which is precisely why
DAYS_SINCE_TELEMETRY exists as a rule of its own.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from app.risk.families import supersedes
from app.risk.models import (
    ConditionResult,
    MetricSnapshot,
    Rule,
    RuleCondition,
    RuleOutcome,
    sort_rules,
)

ENGINE_VERSION = "rule-engine@1.0.0"

REASON_PASSED = "PASSED"
REASON_FAILED = "FAILED"
REASON_MISSING = "MISSING_METRIC"


def evaluate_condition(condition: RuleCondition, snapshot: MetricSnapshot) -> ConditionResult:
    """Evaluate one condition. Never raises on missing data."""
    value = snapshot.get(condition.metric_code)

    if value is None:
        return ConditionResult(
            metric_code=condition.metric_code,
            operator=condition.operator,
            value=None,
            threshold=condition.threshold_value,
            threshold_2=condition.threshold_value_2,
            passed=False,
            reason=REASON_MISSING,
        )

    t1, t2 = condition.threshold_value, condition.threshold_value_2
    op = condition.operator
    if op == "GT":
        passed = value > t1  # type: ignore[operator]
    elif op == "GTE":
        passed = value >= t1  # type: ignore[operator]
    elif op == "LT":
        passed = value < t1  # type: ignore[operator]
    elif op == "LTE":
        passed = value <= t1  # type: ignore[operator]
    elif op == "EQ":
        passed = value == t1
    elif op == "NEQ":
        passed = value != t1
    elif op == "BETWEEN":
        passed = t1 <= value <= t2  # type: ignore[operator]
    else:  # IN — validated at construction
        passed = value in set(condition.threshold_values or ())

    return ConditionResult(
        metric_code=condition.metric_code,
        operator=op,
        value=value,
        threshold=t1,
        threshold_2=t2,
        passed=passed,
        reason=REASON_PASSED if passed else REASON_FAILED,
    )


def render_trigger(rule: Rule, results: Sequence[ConditionResult]) -> str:
    """The human sentence stored on the alert (Doc 05 `risk_alerts.trigger_condition`)."""
    deciding = [r for r in results if r.passed] or list(results)
    parts = []
    for r in deciding:
        metric = r.metric_code.replace("_", " ").title()
        if r.value is None:
            parts.append(f"{metric} unavailable")
        elif r.operator == "BETWEEN":
            parts.append(
                f"{metric} = {_n(r.value)} (threshold: between {_n(r.threshold)} "
                f"and {_n(r.threshold_2)})"
            )
        else:
            symbol = {"GT": ">", "GTE": ">=", "LT": "<", "LTE": "<=",
                      "EQ": "=", "NEQ": "!=", "IN": "in"}[r.operator]
            parts.append(f"{metric} = {_n(r.value)} (threshold: {symbol} {_n(r.threshold)})")
    joiner = " and " if rule.condition_logic == "ALL" else " or "
    return joiner.join(parts)


def evaluate_rule(rule: Rule, snapshot: MetricSnapshot) -> RuleOutcome:
    """Evaluate one rule against one snapshot. Pure."""
    ordered = sorted(rule.conditions, key=lambda c: c.sequence_no)
    results = tuple(evaluate_condition(c, snapshot) for c in ordered)

    if rule.condition_logic == "ALL":
        fired = all(r.passed for r in results)
    else:
        fired = any(r.passed for r in results)

    return RuleOutcome(
        rule_code=rule.rule_code,
        fired=fired,
        severity=rule.severity,
        category=rule.category,
        priority=rule.priority,
        conditions=results,
        trigger_condition=render_trigger(rule, results) if fired else "",
    )


def evaluate_rules(
    rules: Sequence[Rule],
    snapshot: MetricSnapshot,
    *,
    apply_supersession: bool = True,
) -> tuple[RuleOutcome, ...]:
    """Evaluate every active rule in deterministic priority order.

    Returns outcomes for **all** rules (fired or not) so the caller can auto-resolve
    alerts whose condition has cleared (Doc 08 §8.6 auto-resolution). Supersession marks
    lower-intensity siblings rather than dropping them, preserving the escalation trail.
    """
    active = [r for r in rules if r.is_active]
    outcomes = [evaluate_rule(rule, snapshot) for rule in sort_rules(active)]
    if not apply_supersession:
        return tuple(outcomes)
    return resolve_supersession(outcomes)


def resolve_supersession(outcomes: Sequence[RuleOutcome]) -> tuple[RuleOutcome, ...]:
    """Mark fired outcomes that a higher-intensity sibling in the same family displaces."""
    fired_codes = [o.rule_code for o in outcomes if o.fired]
    resolved: list[RuleOutcome] = []
    for outcome in outcomes:
        superseded_by = None
        if outcome.fired:
            for other in fired_codes:
                if other != outcome.rule_code and supersedes(other, outcome.rule_code):
                    # keep the most intense superseder deterministically
                    if superseded_by is None or _more_intense(other, superseded_by):
                        superseded_by = other
        resolved.append(
            outcome if superseded_by is None
            else RuleOutcome(
                rule_code=outcome.rule_code, fired=outcome.fired, severity=outcome.severity,
                category=outcome.category, priority=outcome.priority,
                conditions=outcome.conditions, trigger_condition=outcome.trigger_condition,
                superseded_by=superseded_by, suppressed_reason=outcome.suppressed_reason,
            )
        )
    return tuple(resolved)


def actionable(outcomes: Sequence[RuleOutcome]) -> tuple[RuleOutcome, ...]:
    """Outcomes that should create or refresh an alert, most severe first then oldest rule."""
    live = [o for o in outcomes if o.is_actionable]
    live.sort(key=lambda o: (-_severity_rank(o.severity), o.priority, o.rule_code))
    return tuple(live)


def loan_risk_status(outcomes: Sequence[RuleOutcome]) -> str:
    """Doc 08 §8.2 — RED if any live RED, else YELLOW if any live YELLOW, else GREEN."""
    live = actionable(outcomes)
    if any(o.severity == "RED" for o in live):
        return "RED"
    if any(o.severity == "YELLOW" for o in live):
        return "YELLOW"
    return "GREEN"


def _severity_rank(severity: str) -> int:
    return 2 if severity == "RED" else 1


def _more_intense(candidate: str, incumbent: str) -> bool:
    return supersedes(candidate, incumbent)


def _n(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    return format(value.normalize(), "f")
