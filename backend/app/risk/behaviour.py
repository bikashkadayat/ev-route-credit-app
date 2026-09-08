"""Composite behaviour score — Doc 08 §8.9.

A 0-100 portfolio-health ranking used to sort the risk table. It is a *ranking aid*, not a
decision input: alerts remain the actionable artefact.

Reuses the same piecewise-linear primitive as the scorecards, so there is one definition
of interpolation in the codebase.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.core.types import ZERO, clamp, q2
from app.engines.normalizer import normalize
from app.risk.models import MetricSnapshot

BEHAVIOUR_COMPONENTS: tuple[dict[str, Any], ...] = (
    {
        "code": "REPAYMENT", "weight": Decimal("0.45"),
        "rule": {"type": "LINEAR", "input": "DAYS_PAST_DUE", "default_score": 100,
                 "points": [{"x": 0, "y": 100}, {"x": 5, "y": 85}, {"x": 15, "y": 65},
                            {"x": 30, "y": 40}, {"x": 60, "y": 15}, {"x": 90, "y": 0}]},
    },
    {
        "code": "USAGE_STABILITY", "weight": Decimal("0.25"),
        "rule": {"type": "LINEAR", "input": "USAGE_CHANGE_PCT", "default_score": 80,
                 "points": [{"x": -60, "y": 0}, {"x": -40, "y": 20}, {"x": -25, "y": 50},
                            {"x": -10, "y": 80}, {"x": 0, "y": 95}, {"x": 10, "y": 100}]},
    },
    {
        "code": "OPERATING_CONSISTENCY", "weight": Decimal("0.15"),
        "rule": {"type": "LINEAR", "input": "ACTIVE_DAYS_30D", "default_score": 70,
                 "points": [{"x": 0, "y": 0}, {"x": 8, "y": 30}, {"x": 15, "y": 60},
                            {"x": 22, "y": 85}, {"x": 26, "y": 100}]},
    },
    {
        "code": "ASSET_HEALTH", "weight": Decimal("0.10"),
        "rule": {"type": "LINEAR", "input": "BATTERY_SOH", "default_score": 85,
                 "points": [{"x": 60, "y": 0}, {"x": 70, "y": 35}, {"x": 80, "y": 70},
                            {"x": 90, "y": 90}, {"x": 95, "y": 100}]},
    },
    {
        "code": "DATA_QUALITY", "weight": Decimal("0.05"),
        "rule": {"type": "LINEAR", "input": "DAYS_SINCE_TELEMETRY", "default_score": 50,
                 "points": [{"x": 0, "y": 100}, {"x": 1, "y": 95}, {"x": 2, "y": 70},
                            {"x": 5, "y": 30}, {"x": 10, "y": 0}]},
    },
)

BANDS: tuple[tuple[Decimal, str], ...] = (
    (Decimal("80"), "HEALTHY"),
    (Decimal("60"), "WATCH"),
    (Decimal("40"), "STRESSED"),
    (ZERO, "CRITICAL"),
)


def behaviour_score(snapshot: MetricSnapshot) -> Decimal:
    """0-100. Missing metrics fall back to each component's documented default."""
    total = ZERO
    for component in BEHAVIOUR_COMPONENTS:
        sub = normalize(component["rule"], snapshot.values)
        total += q2(clamp(sub)) * component["weight"]
    return q2(clamp(total))


def behaviour_band(score: Decimal) -> str:
    for threshold, label in BANDS:
        if score >= threshold:
            return label
    return "CRITICAL"  # pragma: no cover - the final band starts at zero


def behaviour_breakdown(snapshot: MetricSnapshot) -> tuple[dict[str, Any], ...]:
    """Per-component detail, for the monitoring screen's behaviour tile."""
    out = []
    for component in BEHAVIOUR_COMPONENTS:
        sub = q2(clamp(normalize(component["rule"], snapshot.values)))
        weight = component["weight"]
        out.append({
            "code": component["code"],
            "metric": component["rule"]["input"],
            "value": snapshot.get(component["rule"]["input"]),
            "score": sub,
            "weight": weight,
            "contribution": q2(sub * weight),
        })
    return tuple(out)
