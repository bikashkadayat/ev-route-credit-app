"""Loading rules from the canonical JSON catalogue or from database rows.

Mirrors the scorecard pattern: the JSON is the source of truth for the shipped defaults,
the seeding CLI writes it into ``risk_rules`` / ``risk_rule_conditions``, and a drift test
asserts the SQL seed agrees.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.types import D
from app.risk.models import Rule, RuleCondition

RULE_DEFAULTS_DIR = Path(__file__).parent / "rule_defaults"
DEFAULT_CATALOGUE = "risk_rules_v1.json"


def load_catalogue(filename: str = DEFAULT_CATALOGUE) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (RULE_DEFAULTS_DIR / filename).read_text(encoding="utf-8")
    )
    return data


def _condition_from_dict(d: dict[str, Any]) -> RuleCondition:
    return RuleCondition(
        metric_code=d["metric_code"],
        operator=d["operator"],
        threshold_value=None if d.get("threshold_value") is None else D(d["threshold_value"]),
        threshold_value_2=(
            None if d.get("threshold_value_2") is None else D(d["threshold_value_2"])
        ),
        threshold_values=(
            tuple(D(v) for v in d["threshold_values"]) if d.get("threshold_values") else None
        ),
        window_days=d.get("window_days"),
        aggregation=d.get("aggregation"),
        sequence_no=int(d.get("sequence_no", 1)),
    )


def rule_from_dict(d: dict[str, Any]) -> Rule:
    return Rule(
        rule_code=d["rule_code"],
        name=d["name"],
        description=d.get("description", ""),
        category=d["category"],
        severity=d["severity"],
        condition_logic=d.get("condition_logic", "ALL"),
        evaluation_frequency=d.get("evaluation_frequency", "DAILY"),
        suppression_hours=int(d.get("suppression_hours", 24)),
        auto_resolve=bool(d.get("auto_resolve", True)),
        assign_to_role=d.get("assign_to_role"),
        sla_hours_acknowledge=int(d.get("sla_hours_acknowledge", 48)),
        sla_hours_resolve=int(d.get("sla_hours_resolve", 240)),
        recommended_action=d.get("recommended_action", ""),
        priority=int(d.get("priority", 100)),
        is_active=bool(d.get("is_active", True)),
        conditions=tuple(_condition_from_dict(c) for c in d["conditions"]),
    )


@lru_cache(maxsize=4)
def default_rules(filename: str = DEFAULT_CATALOGUE) -> tuple[Rule, ...]:
    """The 22 shipped rules, in catalogue order. Cached; Rule is frozen."""
    return tuple(rule_from_dict(r) for r in load_catalogue(filename)["rules"])


def rules_from_db_rows(rule_rows: Sequence[Any]) -> tuple[Rule, ...]:
    """Build rules from ORM rows (used by the nightly evaluation job)."""
    out: list[Rule] = []
    for row in rule_rows:
        out.append(
            Rule(
                rule_code=row.rule_code,
                name=row.name,
                description=row.description or "",
                category=_enum(row.category),
                severity=_enum(row.severity),
                condition_logic=row.condition_logic,
                evaluation_frequency=row.evaluation_frequency,
                suppression_hours=row.suppression_hours,
                auto_resolve=row.auto_resolve,
                assign_to_role=getattr(row, "assign_to_role_code", None),
                sla_hours_acknowledge=row.sla_hours_acknowledge,
                sla_hours_resolve=row.sla_hours_resolve,
                recommended_action=row.recommended_action or "",
                priority=row.priority,
                is_active=row.is_active,
                conditions=tuple(
                    RuleCondition(
                        metric_code=c.metric_code,
                        operator=_enum(c.operator),
                        threshold_value=c.threshold_value,
                        threshold_value_2=c.threshold_value_2,
                        threshold_values=(
                            tuple(D(v) for v in c.threshold_values)
                            if c.threshold_values else None
                        ),
                        window_days=c.window_days,
                        aggregation=c.aggregation,
                        sequence_no=c.sequence_no,
                    )
                    for c in row.conditions
                ),
            )
        )
    return tuple(out)


def _enum(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value
