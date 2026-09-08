"""Decimal primitives shared by every engine.

Doc 07 §7.1 principle 3: "Everything is `Decimal`. Quantised to 2dp with ROUND_HALF_UP.
Never `float`." These helpers are the only place rounding policy is expressed, so the
policy cannot drift between engines.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

# Quantisation targets
Q2 = Decimal("0.01")  # scores, money
Q3 = Decimal("0.001")  # weighted contributions (NUMERIC(6,3) in the schema)
Q4 = Decimal("0.0001")  # ratios and weights (NUMERIC(6,4))

ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")


def D(value: Any) -> Decimal:
    """Coerce anything numeric to Decimal without ever passing through float.

    ``Decimal(0.1)`` is 0.1000000000000000055511151231257827, which is why every
    non-Decimal input is stringified first.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        # bool is an int subclass; guard so True does not silently become 1
        raise TypeError("Refusing to coerce bool to Decimal")
    if value is None:
        raise TypeError("Refusing to coerce None to Decimal")
    return Decimal(str(value))


def q2(value: Decimal) -> Decimal:
    return value.quantize(Q2, rounding=ROUND_HALF_UP)


def q3(value: Decimal) -> Decimal:
    return value.quantize(Q3, rounding=ROUND_HALF_UP)


def q4(value: Decimal) -> Decimal:
    return value.quantize(Q4, rounding=ROUND_HALF_UP)


def clamp(value: Decimal, lo: Decimal = ZERO, hi: Decimal = ONE_HUNDRED) -> Decimal:
    """Clamp a sub-score into [lo, hi]. Doc 07 §7.1 principle 5."""
    return max(lo, min(hi, value))


def safe_div(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    """Division that returns None rather than raising on a zero denominator.

    A None propagates as "unknown", which the rule engine treats as *not fired*
    (Doc 08 §8.3 null semantics) and the scoring engine treats as *use default_score*.
    """
    if denominator == ZERO:
        return None
    return numerator / denominator
