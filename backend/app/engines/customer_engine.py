"""Customer Appraisal & Underwriting Engine — Doc 07 §7.4. `customer-engine@1.0.0`

Pure. Knock-outs run first and short-circuit (Doc 07 §7.4.1); if any fires the engine
returns score 0 / grade E / REJECT with **no component scoring performed**.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.core.types import ZERO, D, q2, safe_div
from app.engines.base import ComponentResult, ScoreResult, ScoringConfig, ScoringEngine
from app.engines.factors import CUSTOMER_FACTOR_RULES, evaluate_factors
from app.engines.knockouts import KnockOut, evaluate_knockouts
from app.engines.normalizer import composite_breakdown, normalize
from app.engines.vehicle_economics import VehicleEconomics

INDIVIDUAL_TYPES = {"INDIVIDUAL_DRIVER", "OWNER_DRIVER"}
BUSINESS_TYPES = {"CORPORATE", "FLEET_OPERATOR", "SME", "TRANSPORT_COMPANY"}

# Doc 07 §7.4.3 — a file with no bureau history scores at this default and is blocked
# from automatic APPROVE by the decision matrix.
DEFAULT_THIN_FILE_SCORE = Decimal("45")
THIN_FILE_MONTHS = 6


@dataclass(frozen=True, slots=True)
class CustomerScoringInput:
    """Doc 01 §1.10.2. Every value the customer engine may see."""

    # identity / classification
    applicant_type: str = "OWNER_DRIVER"
    age_years: Decimal | None = None
    age_at_maturity: Decimal | None = None

    # bureau
    bureau_score: Decimal | None = None
    credit_history_months: int = 0
    previous_default_count: int = 0
    max_dpd_last_24m: int = 0
    enquiries_last_6m: int = 0
    current_overdue_amount: Decimal = Decimal("0")
    is_blacklisted: bool = False

    # financial
    total_monthly_income: Decimal = Decimal("0")
    total_monthly_expenses: Decimal = Decimal("0")
    total_existing_emi: Decimal = Decimal("0")
    existing_loan_count: int = 0
    overdue_obligation_count: int = 0
    avg_bank_balance_6m: Decimal = Decimal("0")
    income_proof_type: str = "SELF_DECLARED"
    income_verified: bool = False

    # loan request
    requested_amount: Decimal = Decimal("0")
    requested_tenure_months: int = 60
    down_payment: Decimal = Decimal("0")
    vehicle_on_road_price: Decimal = Decimal("0")
    proposed_emi: Decimal = Decimal("0")

    # experience
    driving_experience_years: Decimal = Decimal("0")
    commercial_driving_years: Decimal = Decimal("0")
    business_experience_years: Decimal = Decimal("0")
    fleet_size: int = 0
    has_previous_ev_experience: bool = False
    licence_category: str | None = None
    licence_months_remaining: Decimal | None = None
    is_driver_operated: bool = True

    # vehicle / route context
    vehicle_is_new: bool = True
    vehicle_age_years: Decimal | None = None
    route_grade: str | None = None
    route_score: Decimal | None = None
    route_waiver_granted: bool = False

    economics: VehicleEconomics | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def applicant_class(self) -> str:
        return "INDIVIDUAL" if self.applicant_type in INDIVIDUAL_TYPES else "BUSINESS"


def _income_proof_class(proof_type: str, verified: bool) -> str:
    """Collapse (proof type, verified flag) into the banded key used by the config."""
    if proof_type == "AUDITED_FINANCIALS":
        return "AUDITED_FINANCIALS"
    if proof_type == "BANK_STATEMENT":
        return "BANK_STATEMENT_VERIFIED" if verified else "BANK_STATEMENT_UNVERIFIED"
    if proof_type == "SALARY_SLIP":
        return "SALARY_SLIP_VERIFIED" if verified else "BANK_STATEMENT_UNVERIFIED"
    return "SELF_DECLARED"


def derive_customer_inputs(p: CustomerScoringInput) -> dict[str, Any]:
    """All derived affordability metrics — Doc 01 §1.10.3."""
    income = p.total_monthly_income
    emi = p.proposed_emi

    dti = safe_div(p.total_existing_emi, income)
    foir = safe_div(p.total_existing_emi + emi, income)
    disposable = income - p.total_monthly_expenses - p.total_existing_emi
    disposable_to_emi = safe_div(disposable, emi)
    balance_to_emi = safe_div(p.avg_bank_balance_6m, emi)
    down_payment_ratio = safe_div(p.down_payment, p.vehicle_on_road_price)

    econ = p.economics
    dscr = econ.dscr(emi) if econ is not None and emi > ZERO else None

    return {
        "applicant_class": p.applicant_class,
        "applicant_type": p.applicant_type,
        # bureau
        "bureau_score": p.bureau_score,
        "credit_history_months": p.credit_history_months,
        "previous_default_count": p.previous_default_count,
        "max_dpd_last_24m": p.max_dpd_last_24m,
        "enquiries_last_6m": p.enquiries_last_6m,
        "current_overdue_amount": p.current_overdue_amount,
        "is_blacklisted": p.is_blacklisted,
        # affordability
        "total_monthly_income": income,
        "total_monthly_expenses": p.total_monthly_expenses,
        "total_existing_emi": p.total_existing_emi,
        "existing_loan_count": p.existing_loan_count,
        "overdue_obligation_count": p.overdue_obligation_count,
        "avg_bank_balance_6m": p.avg_bank_balance_6m,
        "disposable_income": disposable,
        "disposable_to_emi_ratio": disposable_to_emi,
        "balance_to_emi_ratio": balance_to_emi,
        "dti_ratio": dti,
        "foir_post_loan": foir,
        "foir_pct": q2(foir * Decimal("100")) if foir is not None else None,
        "proposed_emi": emi,
        "income_proof_class": _income_proof_class(p.income_proof_type, p.income_verified),
        "income_verified": p.income_verified,
        # loan
        "requested_amount": p.requested_amount,
        "requested_tenure_months": p.requested_tenure_months,
        "down_payment": p.down_payment,
        "down_payment_ratio": down_payment_ratio,
        "down_payment_pct": (
            q2(down_payment_ratio * Decimal("100")) if down_payment_ratio is not None else None
        ),
        "vehicle_on_road_price": p.vehicle_on_road_price,
        # experience
        "driving_experience_years": p.driving_experience_years,
        "commercial_driving_years": p.commercial_driving_years,
        "business_experience_years": p.business_experience_years,
        "fleet_size": p.fleet_size,
        "has_previous_ev_experience": p.has_previous_ev_experience,
        "licence_category": p.licence_category,
        "licence_months_remaining": p.licence_months_remaining,
        "is_driver_operated": p.is_driver_operated,
        # vehicle economics
        "dscr": dscr,
        "energy_cost_per_km": econ.energy_cost_per_km if econ else None,
        "payback_months": econ.payback_months if econ else None,
        "warranty_to_tenure_ratio": econ.warranty_to_tenure_ratio if econ else None,
        # knock-out context
        "age_years": p.age_years,
        "age_at_maturity": p.age_at_maturity,
        "vehicle_is_new": p.vehicle_is_new,
        "vehicle_age_years": p.vehicle_age_years,
        "route_grade": p.route_grade,
        "route_score": p.route_score,
        "route_waiver_granted": p.route_waiver_granted,
        **p.extras,
    }


class CustomerScoringEngine(ScoringEngine):
    engine_version = "customer-engine@1.0.0"
    config_type = "CUSTOMER"

    def __init__(self, thin_file_score: Decimal = DEFAULT_THIN_FILE_SCORE) -> None:
        self.thin_file_score = thin_file_score

    def score(self, payload: CustomerScoringInput, config: ScoringConfig) -> ScoreResult:
        inputs = derive_customer_inputs(payload)
        knockouts = evaluate_knockouts(inputs, config)
        if knockouts:
            return self._knockout_result(knockouts, config, inputs)

        is_thin = (
            payload.credit_history_months < THIN_FILE_MONTHS or payload.bureau_score is None
        )

        components: list[ComponentResult] = []
        for cfg in config.active_components:
            # A thin file short-circuits only the credit component: there is nothing to
            # break down, so it carries the configured default and no sub-factors.
            thin_credit = cfg.code == "CREDIT_HISTORY" and is_thin
            normalized = (
                self.thin_file_score if thin_credit
                else normalize(cfg.scoring_rules, inputs)
            )
            explanation = (
                f"Thin credit file ({payload.credit_history_months} months of history); "
                f"scored at the configured default of {self.thin_file_score}."
                if thin_credit
                else _component_explanation(cfg.code, inputs)
            )
            subs = () if thin_credit else composite_breakdown(cfg.scoring_rules, inputs)
            components.append(
                self.make_component(
                    cfg, normalized,
                    raw_inputs=_raw_for(cfg.code, inputs),
                    explanation=explanation,
                    sub_factors=subs,
                )
            )

        total = self.aggregate(components)
        band = config.band_for(total)
        risks, positives = evaluate_factors(CUSTOMER_FACTOR_RULES, inputs)

        return ScoreResult(
            total_score=total,
            grade=band.grade,
            grade_label=band.label,
            risk_level=band.risk_level,
            components=tuple(components),
            risk_factors=risks,
            positive_factors=positives,
            explanation=_build_explanation(total, band.label, components, inputs, is_thin),
            config_version_id=config.id,
            config_version_no=config.version_no,
            engine_version=self.engine_version,
            extras={
                "knockouts_triggered": [],
                "is_thin_file": is_thin,
                "dti_ratio": _f(inputs["dti_ratio"], 4),
                "foir_ratio": _f(inputs["foir_post_loan"], 4),
                "disposable_income": _f(inputs["disposable_income"], 2),
            },
        )

    def _knockout_result(
        self, knockouts: tuple[KnockOut, ...], config: ScoringConfig, inputs: dict[str, Any]
    ) -> ScoreResult:
        worst = config.grade_bands[-1]
        for band in config.grade_bands:
            if band.min == ZERO:
                worst = band
                break
        return ScoreResult(
            total_score=ZERO,
            grade=worst.grade,
            grade_label=worst.label,
            risk_level=worst.risk_level,
            components=(),  # Doc 14 CS-01: no component scoring is performed
            risk_factors=(),
            positive_factors=(),
            explanation=(
                "Application declined at eligibility screening. "
                + " ".join(k.message + "." for k in knockouts)
                + " No credit scoring was performed."
            ),
            config_version_id=config.id,
            config_version_no=config.version_no,
            engine_version=self.engine_version,
            extras={
                "knockouts_triggered": [k.to_dict() for k in knockouts],
                "is_thin_file": False,
                "dti_ratio": _f(inputs.get("dti_ratio"), 4),
                "foir_ratio": _f(inputs.get("foir_post_loan"), 4),
                "disposable_income": _f(inputs.get("disposable_income"), 2),
                "recommendation": "REJECT",
            },
        )


# ---------------------------------------------------------------------------
_RAW_KEYS: dict[str, tuple[str, ...]] = {
    "CREDIT_HISTORY": (
        "bureau_score", "max_dpd_last_24m", "previous_default_count",
        "credit_history_months", "enquiries_last_6m",
    ),
    "INCOME_CASHFLOW": (
        "total_monthly_income", "disposable_to_emi_ratio", "income_proof_class",
        "balance_to_emi_ratio", "disposable_income",
    ),
    "EXISTING_DEBT": (
        "foir_post_loan", "dti_ratio", "existing_loan_count", "overdue_obligation_count",
    ),
    "DOWN_PAYMENT": ("down_payment_ratio", "down_payment", "vehicle_on_road_price"),
    "EXPERIENCE": (
        "commercial_driving_years", "driving_experience_years", "business_experience_years",
        "fleet_size", "has_previous_ev_experience", "licence_months_remaining",
    ),
    "VEHICLE_ECONOMICS": (
        "dscr", "energy_cost_per_km", "payback_months", "warranty_to_tenure_ratio",
    ),
}


def _raw_for(code: str, inputs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in _RAW_KEYS.get(code, ()):
        v = inputs.get(k)
        out[k] = float(v) if isinstance(v, Decimal) else v
    return out


def _f(value: Any, places: int) -> float | None:
    if value is None:
        return None
    return float(D(value).quantize(Decimal(1).scaleb(-places)))


def _component_explanation(code: str, i: dict[str, Any]) -> str:
    if code == "CREDIT_HISTORY":
        return (
            f"Bureau score {i['bureau_score']} over {i['credit_history_months']} months of "
            f"history, {i['previous_default_count']} previous default(s), maximum "
            f"{i['max_dpd_last_24m']} days past due in 24 months."
        )
    if code == "INCOME_CASHFLOW":
        return (
            f"Total monthly income NPR {_m(i['total_monthly_income'])} with disposable income "
            f"NPR {_m(i['disposable_income'])}, covering the proposed EMI "
            f"{_r(i['disposable_to_emi_ratio'])}x. Income evidence: "
            f"{str(i['income_proof_class']).lower().replace('_', ' ')}."
        )
    if code == "EXISTING_DEBT":
        return (
            f"Post-loan FOIR {_pct(i['foir_post_loan'])}%, pre-loan DTI {_pct(i['dti_ratio'])}%, "
            f"{i['existing_loan_count']} existing obligation(s), "
            f"{i['overdue_obligation_count']} overdue."
        )
    if code == "DOWN_PAYMENT":
        return (
            f"Down payment of NPR {_m(i['down_payment'])} on a vehicle priced at NPR "
            f"{_m(i['vehicle_on_road_price'])} ({_pct(i['down_payment_ratio'])}%)."
        )
    if code == "EXPERIENCE":
        if i["applicant_class"] == "INDIVIDUAL":
            return (
                f"{_r(i['commercial_driving_years'])} years of commercial driving out of "
                f"{_r(i['driving_experience_years'])} years total; prior EV experience: "
                f"{'yes' if i['has_previous_ev_experience'] else 'no'}."
            )
        return (
            f"{_r(i['business_experience_years'])} years of business operation with a fleet of "
            f"{i['fleet_size']}; prior EV experience: "
            f"{'yes' if i['has_previous_ev_experience'] else 'no'}."
        )
    if code == "VEHICLE_ECONOMICS":
        return (
            f"DSCR {_r(i['dscr'])}x at an energy cost of NPR {_r(i['energy_cost_per_km'])}/km; "
            f"payback in {_r(i['payback_months'])} months; battery warranty covers "
            f"{_r(i['warranty_to_tenure_ratio'])}x the tenure."
        )
    return ""


def _build_explanation(
    total: Decimal, grade_label: str, components: list[ComponentResult],
    i: dict[str, Any], is_thin: bool,
) -> str:
    ranked = sorted(components, key=lambda c: c.normalized_score, reverse=True)
    strongest, weakest = ranked[0], ranked[-1]
    thin = (
        " The file is thin on credit history, so automatic approval is not available."
        if is_thin else ""
    )
    return (
        f"Customer scores {total}/100 ({grade_label}). "
        f"{strongest.label} is the strongest component at {strongest.normalized_score}/100; "
        f"{weakest.label} is the weakest at {weakest.normalized_score}/100. "
        f"Post-loan FOIR is {_pct(i['foir_post_loan'])}% against disposable income of NPR "
        f"{_m(i['disposable_income'])}, and the vehicle covers the proposed EMI "
        f"{_r(i['dscr'])} times.{thin}"
    )


def _m(v: Any) -> str:
    return "n/a" if v is None else f"{q2(D(v)):,}"


def _r(v: Any) -> str:
    if v is None:
        return "n/a"
    return format(q2(D(v)).normalize(), "f")


def _pct(v: Any) -> str:
    return "n/a" if v is None else format(q2(D(v) * Decimal("100")).normalize(), "f")
