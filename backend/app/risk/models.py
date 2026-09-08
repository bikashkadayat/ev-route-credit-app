"""Value objects for the risk rule engine — Doc 08 §8.4, Doc 05 §5.3.32/33.

Everything here is frozen. The engine is a pure function over these types, so a rule
evaluation can be reproduced exactly from its stored snapshot.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from app.core.types import D

Severity = Literal["YELLOW", "RED"]
Category = Literal["REPAYMENT", "USAGE", "REVENUE", "BATTERY", "ROUTE", "DATA_QUALITY"]
ConditionLogic = Literal["ALL", "ANY"]
Operator = Literal["GT", "GTE", "LT", "LTE", "EQ", "NEQ", "BETWEEN", "IN"]

SEVERITY_RANK: dict[str, int] = {"YELLOW": 1, "RED": 2}


class RuleDefinitionError(ValueError):
    """Raised when a rule is structurally invalid (bad operator, missing threshold)."""


@dataclass(frozen=True, slots=True)
class RuleCondition:
    metric_code: str
    operator: Operator
    threshold_value: Decimal | None = None
    threshold_value_2: Decimal | None = None
    threshold_values: tuple[Decimal, ...] | None = None
    window_days: int | None = None
    aggregation: str | None = None
    sequence_no: int = 1

    def __post_init__(self) -> None:
        if self.operator not in {"GT", "GTE", "LT", "LTE", "EQ", "NEQ", "BETWEEN", "IN"}:
            raise RuleDefinitionError(f"Unsupported operator {self.operator!r}")
        if self.operator == "BETWEEN" and self.threshold_value_2 is None:
            raise RuleDefinitionError(
                f"{self.metric_code}: BETWEEN requires threshold_value_2"
            )
        if self.operator == "IN" and not self.threshold_values:
            raise RuleDefinitionError(f"{self.metric_code}: IN requires threshold_values")
        if self.operator not in {"BETWEEN", "IN"} and self.threshold_value is None:
            raise RuleDefinitionError(
                f"{self.metric_code}: {self.operator} requires threshold_value"
            )
        if self.operator == "BETWEEN" and self.threshold_value is not None:
            if self.threshold_value > self.threshold_value_2:  # type: ignore[operator]
                raise RuleDefinitionError(
                    f"{self.metric_code}: BETWEEN lower bound {self.threshold_value} exceeds "
                    f"upper bound {self.threshold_value_2}"
                )

    def describe(self) -> str:
        """Plain-language rendering, used by the admin rule builder (Doc 03 Screen 28)."""
        words = {
            "GT": "is greater than", "GTE": "is at least", "LT": "is less than",
            "LTE": "is at most", "EQ": "equals", "NEQ": "does not equal",
        }
        metric = self.metric_code.lower().replace("_", " ")
        if self.operator == "BETWEEN":
            body = (
                f"{metric} is between {_n(self.threshold_value)} "
                f"and {_n(self.threshold_value_2)}"
            )
        elif self.operator == "IN":
            body = f"{metric} is one of {', '.join(_n(v) for v in self.threshold_values or ())}"
        else:
            body = f"{metric} {words[self.operator]} {_n(self.threshold_value)}"
        if self.window_days:
            body += f" over {self.window_days} days"
        return body


@dataclass(frozen=True, slots=True)
class Rule:
    rule_code: str
    name: str
    category: Category
    severity: Severity
    conditions: tuple[RuleCondition, ...]
    description: str = ""
    condition_logic: ConditionLogic = "ALL"
    evaluation_frequency: str = "DAILY"
    suppression_hours: int = 24
    auto_resolve: bool = True
    assign_to_role: str | None = None
    sla_hours_acknowledge: int = 48
    sla_hours_resolve: int = 240
    recommended_action: str = ""
    priority: int = 100
    is_active: bool = True

    def __post_init__(self) -> None:
        if not self.conditions:
            raise RuleDefinitionError(f"{self.rule_code}: a rule needs at least one condition")
        if self.condition_logic not in {"ALL", "ANY"}:
            raise RuleDefinitionError(
                f"{self.rule_code}: condition_logic must be ALL or ANY"
            )
        if self.severity not in SEVERITY_RANK:
            raise RuleDefinitionError(f"{self.rule_code}: severity must be YELLOW or RED")

    @property
    def severity_rank(self) -> int:
        return SEVERITY_RANK[self.severity]

    def describe(self) -> str:
        joiner = " AND " if self.condition_logic == "ALL" else " OR "
        return "Alert when " + joiner.join(c.describe() for c in
                                           sorted(self.conditions, key=lambda c: c.sequence_no))


@dataclass(frozen=True, slots=True)
class MetricSnapshot:
    """Every metric for one loan on one business date.

    The engine sees nothing else. Building this is the only part of monitoring that
    touches the database, which is why the evaluation itself is trivially testable.
    """

    loan_id: int
    as_of: date
    values: Mapping[str, Decimal | None] = field(default_factory=dict)

    def get(self, metric_code: str) -> Decimal | None:
        return self.values.get(metric_code)

    def with_values(self, **overrides: Any) -> MetricSnapshot:
        merged = dict(self.values)
        for key, value in overrides.items():
            merged[key] = None if value is None else D(value)
        return MetricSnapshot(loan_id=self.loan_id, as_of=self.as_of, values=merged)


@dataclass(frozen=True, slots=True)
class ConditionResult:
    metric_code: str
    operator: str
    value: Decimal | None
    threshold: Decimal | None
    threshold_2: Decimal | None
    passed: bool
    reason: str  # "PASSED" | "FAILED" | "MISSING_METRIC"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "operator": self.operator,
            "value": float(self.value) if self.value is not None else None,
            "threshold": float(self.threshold) if self.threshold is not None else None,
            "passed": self.passed,
            "reason": self.reason,
        }
        if self.threshold_2 is not None:
            out["threshold_2"] = float(self.threshold_2)
        return out


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    rule_code: str
    fired: bool
    severity: Severity
    category: Category
    priority: int
    conditions: tuple[ConditionResult, ...]
    trigger_condition: str = ""
    superseded_by: str | None = None
    suppressed_reason: str | None = None

    @property
    def is_actionable(self) -> bool:
        """Fired, not superseded by a higher-severity sibling, not suppressed."""
        return self.fired and self.superseded_by is None and self.suppressed_reason is None

    @property
    def evidence(self) -> dict[str, Any]:
        return {c.metric_code: c.to_dict() for c in self.conditions}

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_code": self.rule_code,
            "fired": self.fired,
            "severity": self.severity,
            "category": self.category,
            "trigger_condition": self.trigger_condition,
            "trigger_metrics": self.evidence,
            "superseded_by": self.superseded_by,
            "suppressed_reason": self.suppressed_reason,
        }


def _n(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    return format(value.normalize(), "f")


def sort_rules(rules: Sequence[Rule]) -> tuple[Rule, ...]:
    """Deterministic evaluation order: priority ascending, then rule_code.

    Priority ordering matters because supersession assumes a higher-severity sibling has
    already been evaluated (Doc 08 §8.5). rule_code is the tie-break so the order never
    depends on dict or database ordering.
    """
    return tuple(sorted(rules, key=lambda r: (r.priority, r.rule_code)))
