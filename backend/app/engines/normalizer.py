"""Scoring rule primitives — Doc 07 §7.2, grammar in Doc 05 §5.5.

Four rule kinds, dispatched on ``type``:

  BANDED       categorical / step lookup
  LINEAR       piecewise-linear interpolation over an ordered point table
  COMPOSITE    weighted sub-factors inside one component
  PASSTHROUGH  the input is already a 0-100 score (used by the FINAL config)

Piecewise-linear was chosen over a sigmoid deliberately (Doc 07 §7.2.2): a credit
committee can read the point table and check it against the policy document, and a
risk analyst can move one point without understanding curve algebra.

Every function here is pure.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from app.core.types import ZERO, D, clamp
from app.engines.base import SubFactorResult

DEFAULT_MISSING_SCORE = Decimal("50")


class ScoringRuleError(ValueError):
    """Raised when a scoring rule is malformed. Surfaces as 409 CONFIG_NOT_PUBLISHED."""


# ---------------------------------------------------------------------------
# BANDED
# ---------------------------------------------------------------------------
def _banded(rule: Mapping[str, Any], inputs: Mapping[str, Any]) -> Decimal:
    key = rule.get("input")
    if key is None:
        raise ScoringRuleError("BANDED rule is missing 'input'")
    value = inputs.get(key)
    default = D(rule.get("default_score", DEFAULT_MISSING_SCORE))

    if value is None:
        return clamp(default)

    for band in rule.get("bands", ()):
        if band.get("value") == value:
            score = D(band["score"])
            # A score of -1 is the documented escape hatch for a band whose value is
            # computed rather than looked up (Doc 07 §7.3.3: road_type MIXED is
            # 40 + 0.6 x pitch_percent). The caller supplies the computed value under
            # "<input>_computed_score".
            if score == Decimal("-1"):
                computed = inputs.get(f"{key}_computed_score")
                if computed is None:
                    raise ScoringRuleError(
                        f"BANDED band {band.get('value')!r} defers to a computed score but "
                        f"{key}_computed_score was not supplied"
                    )
                return clamp(D(computed))
            return clamp(score)

    return clamp(default)


# ---------------------------------------------------------------------------
# LINEAR
# ---------------------------------------------------------------------------
def _linear(rule: Mapping[str, Any], inputs: Mapping[str, Any]) -> Decimal:
    key = rule.get("input")
    if key is None:
        raise ScoringRuleError("LINEAR rule is missing 'input'")
    raw = inputs.get(key)
    if raw is None:
        return clamp(D(rule.get("default_score", DEFAULT_MISSING_SCORE)))

    points = rule.get("points") or []
    if len(points) < 2:
        raise ScoringRuleError(f"LINEAR rule for {key!r} needs at least two points")

    pts: list[tuple[Decimal, Decimal]] = [(D(p["x"]), D(p["y"])) for p in points]
    for a, b in itertools.pairwise(pts):
        if b[0] <= a[0]:
            raise ScoringRuleError(
                f"LINEAR rule for {key!r}: points must be strictly increasing in x "
                f"(saw {a[0]} then {b[0]})"
            )

    v = D(raw)
    if v <= pts[0][0]:
        return clamp(pts[0][1])
    if v >= pts[-1][0]:
        return clamp(pts[-1][1])

    for (x0, y0), (x1, y1) in itertools.pairwise(pts):
        if x0 <= v < x1:
            return clamp(y0 + (y1 - y0) * (v - x0) / (x1 - x0))

    return clamp(pts[-1][1])  # pragma: no cover - unreachable given the guards above


# ---------------------------------------------------------------------------
# COMPOSITE
# ---------------------------------------------------------------------------
def _composite_sub_factors(
    rule: Mapping[str, Any], inputs: Mapping[str, Any]
) -> Sequence[Mapping[str, Any]]:
    """Resolve the sub-factor list, honouring the ``variant_by`` switch.

    Doc 07 §7.4.7: the EXPERIENCE component uses a different sub-factor set for
    individual and business applicants. That is configuration, not code.
    """
    if "variant_by" in rule:
        selector = rule["variant_by"]
        variant_key = inputs.get(selector)
        variants = rule.get("variants") or {}
        if variant_key is None or variant_key not in variants:
            raise ScoringRuleError(
                f"COMPOSITE rule selects a variant on {selector!r} but no variant matches "
                f"{variant_key!r} (available: {sorted(variants)})"
            )
        selected: Sequence[Mapping[str, Any]] = variants[variant_key]
        return selected
    subs = rule.get("sub_factors")
    if subs is None:
        raise ScoringRuleError("COMPOSITE rule is missing 'sub_factors'")
    sub_factors: Sequence[Mapping[str, Any]] = subs
    return sub_factors


def _composite(rule: Mapping[str, Any], inputs: Mapping[str, Any]) -> Decimal:
    total = ZERO
    for sub in _composite_sub_factors(rule, inputs):
        total += D(sub["weight"]) * normalize(sub["rule"], inputs)
    return clamp(total)


def composite_breakdown(
    rule: Mapping[str, Any], inputs: Mapping[str, Any]
) -> tuple[SubFactorResult, ...]:
    """Same arithmetic as ``_composite`` but retaining the per-sub-factor detail.

    This is what makes the expandable component rows on the score result screen
    (Doc 03 Screen 7) possible without recomputation in the UI.
    """
    if rule.get("type") != "COMPOSITE":
        return ()
    out: list[SubFactorResult] = []
    for sub in _composite_sub_factors(rule, inputs):
        weight = D(sub["weight"])
        score = normalize(sub["rule"], inputs)
        inner = sub["rule"]
        value = None
        if isinstance(inner, Mapping):
            inner_key = inner.get("input")
            if isinstance(inner_key, str):
                value = inputs.get(inner_key)
        out.append(
            SubFactorResult(
                code=sub["code"],
                value=value,
                score=score,
                weight=weight,
                contribution=weight * score,
            )
        )
    return tuple(out)


# ---------------------------------------------------------------------------
# PASSTHROUGH
# ---------------------------------------------------------------------------
def _passthrough(rule: Mapping[str, Any], inputs: Mapping[str, Any]) -> Decimal:
    key = rule.get("input")
    if key is None:
        raise ScoringRuleError("PASSTHROUGH rule is missing 'input'")
    raw = inputs.get(key)
    if raw is None:
        raise ScoringRuleError(f"PASSTHROUGH rule requires {key!r} to be present")
    return clamp(D(raw))


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
_DISPATCH = {
    "BANDED": _banded,
    "LINEAR": _linear,
    "COMPOSITE": _composite,
    "PASSTHROUGH": _passthrough,
}


def normalize(rule: Mapping[str, Any], inputs: Mapping[str, Any]) -> Decimal:
    """Convert one raw input into a 0-100 sub-score. Pure."""
    kind = rule.get("type")
    handler = _DISPATCH.get(kind)  # type: ignore[arg-type]
    if handler is None:
        raise ScoringRuleError(f"Unknown scoring rule type: {kind!r}")
    return handler(rule, inputs)
