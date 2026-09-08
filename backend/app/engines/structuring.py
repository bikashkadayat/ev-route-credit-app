"""Loan structuring — Doc 07 §7.5.4, Doc 01 BR-3.

Turns a score into a concrete credit structure: amount, LTV, tenure, rate, EMI, DSCR.
Every cap is policy, read from configuration. Pure.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.core.types import ZERO, D, q2, safe_div
from app.engines.base import ScoringConfig
from app.engines.emi import calculate_emi, principal_from_emi, total_interest

DEFAULT_POLICY: dict[str, Any] = {
    "ltv_by_grade": {"A": 80, "B": 75, "C": 70, "D": 60},
    "absolute_ltv_ceiling": 80,
    "tenure_by_grade": {"A": 84, "B": 72, "C": 60, "D": 48},
    "base_interest_rate": 12.5,
    "grade_rate_premium": {"A": 0.0, "B": 0.5, "C": 1.5, "D": 3.0},
    "foir_cap": 0.55,
    "dscr_min": 1.25,
    "min_amount": 100000,
    "max_amount": 5000000,
    "warranty_grace_months": 12,
    "amount_rounding": 10000,
}


@dataclass(frozen=True, slots=True)
class LoanStructure:
    recommended_amount: Decimal
    max_ltv_percent: Decimal
    applied_ltv_percent: Decimal
    recommended_tenure_months: int
    recommended_interest_rate: Decimal
    estimated_emi: Decimal
    dscr: Decimal | None
    foir_post_loan: Decimal | None
    total_interest: Decimal
    binding_constraint: str
    amount_by_ltv: Decimal
    amount_by_emi_capacity: Decimal
    emi_capacity: Decimal
    is_viable: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommended_amount": float(self.recommended_amount),
            "max_ltv_percent": float(self.max_ltv_percent),
            "applied_ltv_percent": float(self.applied_ltv_percent),
            "recommended_tenure_months": self.recommended_tenure_months,
            "recommended_interest_rate": float(self.recommended_interest_rate),
            "estimated_emi": float(self.estimated_emi),
            "dscr": float(self.dscr) if self.dscr is not None else None,
            "foir_post_loan": (
                float(self.foir_post_loan) if self.foir_post_loan is not None else None
            ),
            "total_interest": float(self.total_interest),
            "binding_constraint": self.binding_constraint,
            "is_viable": self.is_viable,
        }


@dataclass(frozen=True, slots=True)
class StructuringInput:
    grade: str
    requested_amount: Decimal
    requested_tenure_months: int
    vehicle_on_road_price: Decimal
    total_monthly_income: Decimal
    total_existing_emi: Decimal
    monthly_net_contribution: Decimal
    battery_warranty_months: int = 0


def _policy(config: ScoringConfig | None, key: str) -> Any:
    if config is not None:
        value = config.rule(f"structuring.{key}")
        if value is not None:
            return value
    return DEFAULT_POLICY[key]


def _grade_lookup(table: dict[str, Any], grade: str, fallback: Any) -> Any:
    return table.get(grade, fallback)


def structure_loan(p: StructuringInput, config: ScoringConfig | None = None) -> LoanStructure:
    ltv_table = _policy(config, "ltv_by_grade")
    ceiling = D(_policy(config, "absolute_ltv_ceiling"))
    tenure_table = _policy(config, "tenure_by_grade")
    base_rate = D(_policy(config, "base_interest_rate"))
    premium_table = _policy(config, "grade_rate_premium")
    foir_cap = D(_policy(config, "foir_cap"))
    dscr_min = D(_policy(config, "dscr_min"))
    min_amount = D(_policy(config, "min_amount"))
    max_amount = D(_policy(config, "max_amount"))
    warranty_grace = int(_policy(config, "warranty_grace_months"))
    rounding = D(_policy(config, "amount_rounding"))

    # Grade E (or any grade absent from the table) is not offered.
    grade_ltv = _grade_lookup(ltv_table, p.grade, None)
    if grade_ltv is None:
        return LoanStructure(
            recommended_amount=ZERO, max_ltv_percent=ZERO, applied_ltv_percent=ZERO,
            recommended_tenure_months=0, recommended_interest_rate=ZERO,
            estimated_emi=ZERO, dscr=None, foir_post_loan=None, total_interest=ZERO,
            binding_constraint="GRADE_NOT_OFFERED", amount_by_ltv=ZERO,
            amount_by_emi_capacity=ZERO, emi_capacity=ZERO, is_viable=False,
        )

    max_ltv = min(D(grade_ltv), ceiling)

    # 1. tenure first: it determines what an affordable EMI can support
    tenure = min(
        int(p.requested_tenure_months),
        int(_grade_lookup(tenure_table, p.grade, p.requested_tenure_months)),
    )
    if p.battery_warranty_months:
        tenure = min(tenure, int(p.battery_warranty_months) + warranty_grace)
    tenure = max(tenure, 6)

    rate = q2(base_rate + D(_grade_lookup(premium_table, p.grade, 0)))

    # 2. what the collateral supports
    amount_by_ltv = q2(p.vehicle_on_road_price * max_ltv / Decimal("100"))

    # 3. what the borrower's income and the vehicle's earnings support
    emi_by_foir = foir_cap * p.total_monthly_income - p.total_existing_emi
    emi_by_dscr = (
        safe_div(p.monthly_net_contribution, dscr_min)
        if p.monthly_net_contribution > ZERO
        else ZERO
    ) or ZERO
    emi_capacity = max(min(emi_by_foir, emi_by_dscr), ZERO)
    amount_by_emi = principal_from_emi(emi_capacity, rate, tenure)

    # 4. the binding constraint
    candidates = {
        "REQUESTED": p.requested_amount,
        "LTV_CAP": amount_by_ltv,
        "EMI_CAPACITY_FOIR" if emi_by_foir <= emi_by_dscr else "EMI_CAPACITY_DSCR": amount_by_emi,
        "PRODUCT_MAX": max_amount,
    }
    binding = min(candidates, key=lambda k: candidates[k])
    recommended = candidates[binding]

    # 5. round down to a clean figure a sanction letter can carry
    if rounding > ZERO and recommended > ZERO:
        recommended = (recommended / rounding).to_integral_value(rounding="ROUND_FLOOR") * rounding
    recommended = q2(recommended)

    viable = recommended >= min_amount
    if not viable:
        binding = "BELOW_PRODUCT_MINIMUM"

    emi = calculate_emi(recommended, rate, tenure) if recommended > ZERO else ZERO
    dscr = safe_div(p.monthly_net_contribution, emi) if emi > ZERO else None
    foir = safe_div(p.total_existing_emi + emi, p.total_monthly_income)
    applied_ltv = safe_div(recommended * Decimal("100"), p.vehicle_on_road_price) or ZERO

    return LoanStructure(
        recommended_amount=recommended,
        max_ltv_percent=q2(max_ltv),
        applied_ltv_percent=q2(applied_ltv),
        recommended_tenure_months=tenure,
        recommended_interest_rate=rate,
        estimated_emi=emi,
        dscr=dscr,
        foir_post_loan=foir,
        total_interest=total_interest(recommended, emi, tenure) if emi > ZERO else ZERO,
        binding_constraint=binding,
        amount_by_ltv=amount_by_ltv,
        amount_by_emi_capacity=amount_by_emi,
        emi_capacity=q2(emi_capacity),
        is_viable=viable,
    )
