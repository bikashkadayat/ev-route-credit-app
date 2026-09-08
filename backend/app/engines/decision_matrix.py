"""Decision matrix and reason generation — Doc 07 §7.5.5 / §7.5.6, Doc 01 BR-2.

First matching row wins. Every parameter lives in ``config.decision_rules``. Pure.

No decision is ever returned without reasons: the reason list is part of the return
type, not an optional extra (Doc 01 FR-4.4).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from app.core.types import D, q2
from app.engines.base import Factor, ScoringConfig
from app.engines.knockouts import KnockOut

Decision = Literal["APPROVE", "MANUAL_REVIEW", "REJECT"]

DEFAULT_RULES: dict[str, Any] = {
    "approve": {
        "min_final_score": 75,
        "allowed_customer_grades": ["A", "B"],
        "allowed_route_grades": ["A", "B"],
        "min_dscr": 1.30,
        "max_foir": 0.50,
        "require_credit_history": True,
    },
    "reject": {
        "max_final_score": 50,
        "disallowed_customer_grades": ["E"],
        "min_dscr": 1.00,
    },
}

IMPACT_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


@dataclass(frozen=True, slots=True)
class Reason:
    code: str
    type: Literal["NEGATIVE", "POSITIVE"]
    impact: Literal["HIGH", "MEDIUM", "LOW"]
    message: str
    metric: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code,
            "type": self.type,
            "impact": self.impact,
            "message": self.message,
        }
        if self.metric:
            out["metric"] = self.metric
        return out


@dataclass(frozen=True, slots=True)
class DecisionOutcome:
    decision: Decision
    reasons: tuple[Reason, ...]
    failed_gates: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendation": self.decision,
            "reasons": [r.to_dict() for r in self.reasons],
            "failed_gates": list(self.failed_gates),
        }


def _rules(config: ScoringConfig | None, path: str, default: Any) -> Any:
    if config is not None:
        value = config.rule(path)
        if value is not None:
            return value
    node: Any = DEFAULT_RULES
    for part in path.split("."):
        node = node[part]
    return node if node is not None else default


def _metric(name: str, value: Any, threshold: Any) -> dict[str, Any]:
    return {
        "name": name,
        "value": float(D(value)) if value is not None else None,
        "threshold": float(D(threshold)) if threshold is not None else None,
    }


def decide(
    *,
    final_score: Decimal,
    customer_grade: str,
    route_grade: str,
    dscr: Decimal | None,
    foir_post_loan: Decimal | None,
    is_thin_file: bool,
    knockouts: Sequence[KnockOut] = (),
    structure_viable: bool = True,
    config: ScoringConfig | None = None,
) -> DecisionOutcome:
    """Doc 07 §7.5.5 — four rows, first match wins."""

    # Row 1 — any knock-out
    if knockouts:
        reasons = tuple(
            Reason(k.code, "NEGATIVE", "HIGH", k.message,
                   _metric(k.code.lower(), k.value, k.threshold) if k.value is not None else None)
            for k in knockouts
        )
        return DecisionOutcome("REJECT", reasons, tuple(k.code for k in knockouts))

    approve = _rules(config, "approve", DEFAULT_RULES["approve"])
    reject = _rules(config, "reject", DEFAULT_RULES["reject"])

    min_score = D(approve.get("min_final_score", 75))
    ok_customer = set(approve.get("allowed_customer_grades", ["A", "B"]))
    ok_route = set(approve.get("allowed_route_grades", ["A", "B"]))
    min_dscr = D(approve.get("min_dscr", "1.30"))
    max_foir = D(approve.get("max_foir", "0.50"))
    require_history = bool(approve.get("require_credit_history", True))

    reject_score = D(reject.get("max_final_score", 50))
    bad_grades = set(reject.get("disallowed_customer_grades", ["E"]))
    reject_dscr = D(reject.get("min_dscr", "1.00"))

    # Row 3 — hard rejection (evaluated before approval so a fatal DSCR always wins)
    reject_gates: list[str] = []
    if final_score < reject_score:
        reject_gates.append("FINAL_SCORE_BELOW_MINIMUM")
    if customer_grade in bad_grades:
        reject_gates.append("CUSTOMER_GRADE_UNACCEPTABLE")
    if dscr is not None and dscr < reject_dscr:
        reject_gates.append("DSCR_BELOW_MINIMUM")
    if not structure_viable:
        reject_gates.append("AMOUNT_BELOW_MINIMUM")

    if reject_gates:
        return DecisionOutcome("REJECT", (), tuple(reject_gates))

    # Row 2 — automatic approval
    approve_gates: list[str] = []
    if final_score < min_score:
        approve_gates.append("FINAL_SCORE_BELOW_APPROVAL_THRESHOLD")
    if customer_grade not in ok_customer:
        approve_gates.append("CUSTOMER_GRADE_OUTSIDE_APPROVAL_SET")
    if route_grade not in ok_route:
        approve_gates.append("ROUTE_GRADE_OUTSIDE_APPROVAL_SET")
    if dscr is None or dscr < min_dscr:
        approve_gates.append("DSCR_BELOW_APPROVAL_THRESHOLD")
    if foir_post_loan is None or foir_post_loan > max_foir:
        approve_gates.append("FOIR_ABOVE_APPROVAL_THRESHOLD")
    if require_history and is_thin_file:
        approve_gates.append("THIN_CREDIT_FILE")

    if not approve_gates:
        return DecisionOutcome("APPROVE", (), ())

    # Row 4 — everything else
    return DecisionOutcome("MANUAL_REVIEW", (), tuple(approve_gates))


def build_reasons(
    *,
    outcome: DecisionOutcome,
    final_score: Decimal,
    route_score: Decimal,
    route_grade: str,
    customer_grade: str,
    dscr: Decimal | None,
    foir_post_loan: Decimal | None,
    disposable_to_emi: Decimal | None,
    bureau_score: Decimal | None,
    down_payment_ratio: Decimal | None,
    route_factors: Sequence[Factor] = (),
    customer_factors: Sequence[Factor] = (),
    config: ScoringConfig | None = None,
) -> tuple[Reason, ...]:
    """Doc 07 §7.5.6 — negatives first by impact, then positives.

    Officers read the top of the list first, and the top of the list is what blocks
    approval.
    """
    if outcome.reasons:  # knock-out path already carries its reasons
        return outcome.reasons

    approve = _rules(config, "approve", DEFAULT_RULES["approve"])
    max_foir = D(approve.get("max_foir", "0.50"))
    min_dscr = D(approve.get("min_dscr", "1.30"))
    min_score = D(approve.get("min_final_score", 75))

    reasons: list[Reason] = []
    gates = set(outcome.failed_gates)

    # --- negatives, driven by the gates that actually failed --------------
    if "FOIR_ABOVE_APPROVAL_THRESHOLD" in gates and foir_post_loan is not None:
        reasons.append(Reason(
            "HIGH_EMI_BURDEN", "NEGATIVE", "HIGH",
            f"Post-loan FOIR of {q2(foir_post_loan * 100)}% exceeds the "
            f"{q2(max_foir * 100)}% approval threshold",
            _metric("foir_post_loan", foir_post_loan, max_foir),
        ))
    if disposable_to_emi is not None and disposable_to_emi < Decimal("1.3"):
        reasons.append(Reason(
            "TIGHT_DISPOSABLE_INCOME", "NEGATIVE", "HIGH",
            f"Disposable income covers the proposed EMI only {q2(disposable_to_emi)}x",
            _metric("disposable_to_emi_ratio", disposable_to_emi, "1.30"),
        ))
    if "DSCR_BELOW_MINIMUM" in gates or "DSCR_BELOW_APPROVAL_THRESHOLD" in gates:
        reasons.append(Reason(
            "LOW_DSCR", "NEGATIVE", "HIGH",
            f"Vehicle contribution covers only {q2(dscr) if dscr is not None else 'n/a'}x "
            f"the proposed EMI (threshold {q2(min_dscr)}x)",
            _metric("dscr", dscr, min_dscr),
        ))
    if "FINAL_SCORE_BELOW_APPROVAL_THRESHOLD" in gates or "FINAL_SCORE_BELOW_MINIMUM" in gates:
        reasons.append(Reason(
            "FINAL_SCORE_BELOW_THRESHOLD", "NEGATIVE", "HIGH",
            f"Final risk score {final_score} is below the approval threshold of {min_score}",
            _metric("final_score", final_score, min_score),
        ))
    if "ROUTE_GRADE_OUTSIDE_APPROVAL_SET" in gates:
        reasons.append(Reason(
            "WEAK_ROUTE", "NEGATIVE", "HIGH",
            f"Route is Class {route_grade}, outside the automatic approval set",
            _metric("route_score", route_score, None),
        ))
    if "CUSTOMER_GRADE_OUTSIDE_APPROVAL_SET" in gates or "CUSTOMER_GRADE_UNACCEPTABLE" in gates:
        reasons.append(Reason(
            "WEAK_CUSTOMER_GRADE", "NEGATIVE", "HIGH",
            f"Customer grade {customer_grade} is outside the automatic approval set",
            None,
        ))
    if "THIN_CREDIT_FILE" in gates:
        reasons.append(Reason(
            "THIN_CREDIT_FILE", "NEGATIVE", "MEDIUM",
            "Insufficient credit history for an automatic decision", None,
        ))
    if "AMOUNT_BELOW_MINIMUM" in gates:
        reasons.append(Reason(
            "AMOUNT_BELOW_MINIMUM", "NEGATIVE", "HIGH",
            "Affordable loan amount falls below the product minimum", None,
        ))

    # carry forward the material negative factors the component engines found
    seen = {r.code for r in reasons}
    for f in (*customer_factors, *route_factors):
        if f.type == "RISK" and f.severity in {"CRITICAL", "HIGH"} and f.code not in seen:
            reasons.append(Reason(f.code, "NEGATIVE", "MEDIUM", f.message, f.metric or None))
            seen.add(f.code)

    # --- positives ---------------------------------------------------------
    if dscr is not None and dscr >= Decimal("1.5"):
        reasons.append(Reason(
            "HEALTHY_DSCR", "POSITIVE", "HIGH",
            f"Vehicle generates {q2(dscr)}x the proposed EMI in net contribution",
            _metric("dscr", dscr, min_dscr),
        ))
    if route_grade in {"A", "B"}:
        reasons.append(Reason(
            "STRONG_ROUTE", "POSITIVE", "HIGH" if route_grade == "A" else "MEDIUM",
            f"Route is Class {route_grade} with a score of {route_score}",
            _metric("route_score", route_score, 80),
        ))
    if bureau_score is not None and D(bureau_score) >= Decimal("700"):
        reasons.append(Reason(
            "STRONG_CREDIT_RECORD", "POSITIVE", "MEDIUM",
            f"Credit bureau score of {bureau_score}",
            _metric("bureau_score", bureau_score, 680),
        ))
    if down_payment_ratio is not None and down_payment_ratio >= Decimal("0.25"):
        reasons.append(Reason(
            "STRONG_DOWN_PAYMENT", "POSITIVE", "MEDIUM",
            f"Down payment of {q2(down_payment_ratio * 100)}% is above the minimum",
            _metric("down_payment_ratio", down_payment_ratio, "0.20"),
        ))

    seen = {r.code for r in reasons}
    for f in (*customer_factors, *route_factors):
        if f.type == "POSITIVE" and f.code not in seen and len(reasons) < 12:
            reasons.append(Reason(f.code, "POSITIVE", "LOW", f.message, f.metric or None))
            seen.add(f.code)

    reasons.sort(key=lambda r: (0 if r.type == "NEGATIVE" else 1, IMPACT_ORDER[r.impact], r.code))
    return tuple(reasons)
