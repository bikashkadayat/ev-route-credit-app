"""Hard eligibility knock-outs — Doc 07 §7.4.1, Doc 01 BR-1.

Evaluated **before** any scoring. Any hit forces REJECT and short-circuits, so a
rejection is cheap, unambiguous, and cannot be outvoted by a high score elsewhere
(Doc 14 CS-01: "no component scoring is performed").

Pure. All thresholds come from ``config.decision_rules['knockouts']``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.core.types import D
from app.engines.base import ScoringConfig

DEFAULTS: dict[str, Any] = {
    "blacklist": True,
    "active_default": True,
    "bureau_score_floor": 550,
    "min_age": 21,
    "max_age_at_maturity": 65,
    "min_down_payment_ratio": 0.20,
    "max_amount": 5000000,
    "max_tenure_months": 84,
    "route_class_c": True,
    "licence_required": True,
    "max_used_vehicle_age_years": 3,
}


@dataclass(frozen=True, slots=True)
class KnockOut:
    code: str
    message: str
    value: Any = None
    threshold: Any = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.value is not None:
            out["value"] = _plain(self.value)
        if self.threshold is not None:
            out["threshold"] = _plain(self.threshold)
        return out


def _plain(v: Any) -> Any:
    return float(v) if isinstance(v, Decimal) else v


def _setting(config: ScoringConfig | None, key: str) -> Any:
    if config is not None:
        value = config.rule(f"knockouts.{key}")
        if value is not None:
            return value
    return DEFAULTS[key]


def evaluate_knockouts(
    inputs: Mapping[str, Any], config: ScoringConfig | None = None
) -> tuple[KnockOut, ...]:
    """Return every knock-out that fired, in the documented order.

    All of them are returned rather than only the first: Doc 16 §16.7 requires the demo
    application LA-2026-000115 to display *two* simultaneous knock-outs, and an officer
    telling an applicant why they were declined should get the whole answer at once.
    """
    hits: list[KnockOut] = []

    if _setting(config, "blacklist") and inputs.get("is_blacklisted"):
        hits.append(KnockOut("KO_BLACKLIST", "Applicant appears on the credit bureau blacklist"))

    if _setting(config, "active_default"):
        defaults = inputs.get("previous_default_count") or 0
        overdue = D(inputs.get("current_overdue_amount") or 0)
        if defaults > 0 and overdue > 0:
            hits.append(
                KnockOut(
                    "KO_ACTIVE_DEFAULT",
                    f"{defaults} previous default(s) with NPR {overdue:,} currently overdue",
                    value=overdue,
                )
            )

    floor = D(_setting(config, "bureau_score_floor"))
    score = inputs.get("bureau_score")
    if score is not None and D(score) < floor:
        hits.append(
            KnockOut(
                "KO_BUREAU_SCORE_FLOOR",
                f"Credit bureau score {score} is below the floor of {floor}",
                value=score, threshold=floor,
            )
        )

    applicant_class = inputs.get("applicant_class")
    if applicant_class == "INDIVIDUAL":
        min_age = D(_setting(config, "min_age"))
        max_age = D(_setting(config, "max_age_at_maturity"))
        age_now = inputs.get("age_years")
        age_at_maturity = inputs.get("age_at_maturity")
        if age_now is not None and D(age_now) < min_age:
            hits.append(
                KnockOut(
                    "KO_AGE_LIMIT",
                    f"Applicant age {age_now} is below the minimum of {min_age}",
                    value=age_now, threshold=min_age,
                )
            )
        elif age_at_maturity is not None and D(age_at_maturity) > max_age:
            hits.append(
                KnockOut(
                    "KO_AGE_LIMIT",
                    f"Applicant would be {age_at_maturity} at maturity, "
                    f"above the limit of {max_age}",
                    value=age_at_maturity, threshold=max_age,
                )
            )

        if _setting(config, "licence_required") and inputs.get("is_driver_operated", True):
            months = inputs.get("licence_months_remaining")
            if inputs.get("licence_category") in (None, "") or (
                months is not None and D(months) <= 0
            ):
                hits.append(
                    KnockOut(
                        "KO_LICENCE_INVALID",
                        "Commercial driving licence is missing or expired",
                    )
                )

    min_dp = D(_setting(config, "min_down_payment_ratio"))
    dp_ratio = inputs.get("down_payment_ratio")
    if dp_ratio is not None and D(dp_ratio) < min_dp:
        hits.append(
            KnockOut(
                "KO_MIN_DOWN_PAYMENT",
                f"Down payment of {D(dp_ratio) * 100:.1f}% is below the minimum of "
                f"{min_dp * 100:.0f}%",
                value=dp_ratio, threshold=min_dp,
            )
        )

    max_amount = D(_setting(config, "max_amount"))
    amount = inputs.get("requested_amount")
    if amount is not None and D(amount) > max_amount:
        hits.append(
            KnockOut(
                "KO_MAX_AMOUNT",
                f"Requested amount NPR {D(amount):,} exceeds the product maximum of "
                f"NPR {max_amount:,}",
                value=amount, threshold=max_amount,
            )
        )

    max_tenure = D(_setting(config, "max_tenure_months"))
    tenure = inputs.get("requested_tenure_months")
    if tenure is not None and D(tenure) > max_tenure:
        hits.append(
            KnockOut(
                "KO_MAX_TENURE",
                f"Requested tenure of {tenure} months exceeds the maximum of {max_tenure}",
                value=tenure, threshold=max_tenure,
            )
        )

    if _setting(config, "route_class_c") and not inputs.get("route_waiver_granted"):
        if inputs.get("route_grade") == "C":
            hits.append(
                KnockOut(
                    "KO_ROUTE_CLASS_C",
                    "Route is Class C (high risk) and no Risk Manager waiver has been granted",
                    value=inputs.get("route_score"),
                )
            )

    max_age_vehicle = D(_setting(config, "max_used_vehicle_age_years"))
    v_age = inputs.get("vehicle_age_years")
    if v_age is not None and not inputs.get("vehicle_is_new", True):
        if D(v_age) > max_age_vehicle:
            hits.append(
                KnockOut(
                    "KO_VEHICLE_AGE",
                    f"Used vehicle is {v_age} years old, above the {max_age_vehicle}-year limit",
                    value=v_age, threshold=max_age_vehicle,
                )
            )

    return tuple(hits)
