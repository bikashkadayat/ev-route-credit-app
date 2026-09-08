"""Combined risk engine, structuring and decision matrix — Doc 07 §7.5, cases FS-01..FS-09."""

from __future__ import annotations

import dataclasses
from decimal import Decimal as Dc

import pytest

from app.engines.customer_engine import CustomerScoringEngine
from app.engines.decision_matrix import decide
from app.engines.final_engine import FinalRiskEngine, FinalScoringInput
from app.engines.knockouts import KnockOut
from app.engines.route_engine import RouteScoringEngine
from app.engines.structuring import StructuringInput, structure_loan

ROUTE = RouteScoringEngine()
CUSTOMER = CustomerScoringEngine()
FINAL = FinalRiskEngine()


@pytest.fixture
def reference_assessment(
    ktm_dhulikhel, ram_bahadur, byd_e6_economics, route_config, customer_config, final_config
):
    route = ROUTE.score(ktm_dhulikhel, route_config)
    customer = CUSTOMER.score(ram_bahadur, customer_config)
    payload = FinalScoringInput(
        route_score=route.total_score,
        route_grade=route.grade,
        customer_score=customer.total_score,
        customer_grade=customer.grade,
        vehicle_economics_score=customer.component("VEHICLE_ECONOMICS").normalized_score,
        economics=byd_e6_economics,
        customer_inputs=ram_bahadur,
        is_thin_file=customer.extras["is_thin_file"],
        battery_warranty_months=96,
    )
    return FINAL.assess(payload, final_config, route.risk_factors, customer.risk_factors)


# ---------------------------------------------------------------------------
# FS-01 the reference case
# ---------------------------------------------------------------------------
def test_fs_01_reference_combined_assessment(reference_assessment):
    """Doc 07 §7.5.7 — route 90.12, customer 75.17, vehicle 90.96."""
    a = reference_assessment
    assert a.score.total_score == Dc("84.31")
    assert a.score.grade == "B"
    assert a.recommendation == "MANUAL_REVIEW"
    assert a.outcome.failed_gates == ("FOIR_ABOVE_APPROVAL_THRESHOLD",)
    assert a.score.extras["weights"] == {"route": 0.4, "customer": 0.4, "vehicle": 0.2}


def test_fs_01_reference_structuring(reference_assessment):
    """The recommended structure that pulls the file back inside policy."""
    s = reference_assessment.structure
    assert s.recommended_amount == Dc("2960000.00")
    assert s.recommended_tenure_months == 60
    assert s.recommended_interest_rate == Dc("13.00")     # base 12.5 + grade B premium 0.5
    assert s.max_ltv_percent == Dc("75.00")
    assert s.applied_ltv_percent == Dc("64.35")
    assert s.binding_constraint == "EMI_CAPACITY_FOIR"
    assert s.emi_capacity == Dc("67350.00")               # 0.55 x 145,000 - 12,400
    assert abs(s.estimated_emi - Dc("67349.10")) <= Dc("0.50")
    assert s.dscr is not None and abs(s.dscr - Dc("2.4947")) <= Dc("0.001")
    assert s.is_viable


def test_fs_01_reference_requested_metrics(reference_assessment):
    a = reference_assessment
    assert abs(a.requested_emi - Dc("72809.83")) <= Dc("0.50")
    assert abs(a.requested_dscr - Dc("2.3076")) <= Dc("0.001")
    assert abs(a.requested_foir - Dc("0.5877")) <= Dc("0.0001")
    assert abs(a.requested_ltv - Dc("69.5652")) <= Dc("0.001")


def test_fs_01_reasons_are_negative_first_and_explain_the_gate(reference_assessment):
    reasons = reference_assessment.score.extras["_reasons"]
    assert reasons[0].type == "NEGATIVE"
    assert reasons[0].code == "HIGH_EMI_BURDEN"
    assert reasons[0].metric["value"] == pytest.approx(0.5877, abs=1e-4)
    assert reasons[0].metric["threshold"] == pytest.approx(0.50)
    codes = [r.code for r in reasons]
    assert "HEALTHY_DSCR" in codes and "STRONG_ROUTE" in codes
    # negatives always precede positives
    types = [r.type for r in reasons]
    assert types == sorted(types, key=lambda t: 0 if t == "NEGATIVE" else 1)


# ---------------------------------------------------------------------------
# FS-02..FS-05 the decision matrix
# ---------------------------------------------------------------------------
def test_fs_02_all_gates_pass_gives_approve(final_config):
    outcome = decide(
        final_score=Dc("88"), customer_grade="A", route_grade="A", dscr=Dc("1.60"),
        foir_post_loan=Dc("0.45"), is_thin_file=False, config=final_config,
    )
    assert outcome.decision == "APPROVE"
    assert outcome.failed_gates == ()


def test_fs_03_low_dscr_rejects_despite_a_high_score(final_config):
    """A strong asset and route do NOT rescue a borrower who cannot service the loan."""
    outcome = decide(
        final_score=Dc("82"), customer_grade="B", route_grade="A", dscr=Dc("0.95"),
        foir_post_loan=Dc("0.40"), is_thin_file=False, config=final_config,
    )
    assert outcome.decision == "REJECT"
    assert "DSCR_BELOW_MINIMUM" in outcome.failed_gates


@pytest.mark.parametrize(
    "score, expected", [("49.99", "REJECT"), ("50", "MANUAL_REVIEW"), ("74.99", "MANUAL_REVIEW")]
)
def test_fs_04_05_score_boundaries(final_config, score, expected):
    outcome = decide(
        final_score=Dc(score), customer_grade="B", route_grade="A", dscr=Dc("1.60"),
        foir_post_loan=Dc("0.45"), is_thin_file=False, config=final_config,
    )
    assert outcome.decision == expected


def test_grade_e_always_rejects(final_config):
    outcome = decide(
        final_score=Dc("80"), customer_grade="E", route_grade="A", dscr=Dc("2.0"),
        foir_post_loan=Dc("0.30"), is_thin_file=False, config=final_config,
    )
    assert outcome.decision == "REJECT"
    assert "CUSTOMER_GRADE_UNACCEPTABLE" in outcome.failed_gates


def test_thin_file_cannot_auto_approve(final_config):
    outcome = decide(
        final_score=Dc("88"), customer_grade="A", route_grade="A", dscr=Dc("1.60"),
        foir_post_loan=Dc("0.45"), is_thin_file=True, config=final_config,
    )
    assert outcome.decision == "MANUAL_REVIEW"
    assert "THIN_CREDIT_FILE" in outcome.failed_gates


def test_knockouts_reject_before_anything_else(final_config):
    outcome = decide(
        final_score=Dc("95"), customer_grade="A", route_grade="A", dscr=Dc("3.0"),
        foir_post_loan=Dc("0.20"), is_thin_file=False,
        knockouts=(KnockOut("KO_BLACKLIST", "Applicant is blacklisted"),),
        config=final_config,
    )
    assert outcome.decision == "REJECT"
    assert outcome.reasons[0].code == "KO_BLACKLIST"


def test_decision_thresholds_come_from_configuration(final_config):
    """Raising the approval bar to 90 turns the reference APPROVE into MANUAL_REVIEW."""
    stricter = dataclasses.replace(
        final_config,
        decision_rules={**final_config.decision_rules,
                        "approve": {**final_config.decision_rules["approve"],
                                    "min_final_score": 90}},
    )
    outcome = decide(
        final_score=Dc("88"), customer_grade="A", route_grade="A", dscr=Dc("1.60"),
        foir_post_loan=Dc("0.45"), is_thin_file=False, config=stricter,
    )
    assert outcome.decision == "MANUAL_REVIEW"


# ---------------------------------------------------------------------------
# FS-06..FS-09 structuring
# ---------------------------------------------------------------------------
def _structuring(**overrides) -> StructuringInput:
    base = {
        "grade": "B", "requested_amount": Dc("3200000"), "requested_tenure_months": 60,
        "vehicle_on_road_price": Dc("4600000"), "total_monthly_income": Dc("145000"),
        "total_existing_emi": Dc("12400"), "monthly_net_contribution": Dc("168017.61"),
        "battery_warranty_months": 96,
    }
    base.update(overrides)
    return StructuringInput(**base)


def test_fs_06_ltv_cap_binds_when_the_request_is_large(final_config):
    s = structure_loan(
        _structuring(requested_amount=Dc("4200000"), total_monthly_income=Dc("900000")),
        final_config,
    )
    assert s.binding_constraint == "LTV_CAP"
    assert s.recommended_amount <= Dc("3450000")     # 75% of 4,600,000
    assert s.applied_ltv_percent <= s.max_ltv_percent


def test_fs_07_emi_capacity_binds_when_income_is_thin(final_config):
    s = structure_loan(_structuring(total_monthly_income=Dc("90000")), final_config)
    assert s.binding_constraint.startswith("EMI_CAPACITY")
    assert s.recommended_amount < Dc("3200000")


def test_fs_08_below_product_minimum_is_not_viable(final_config):
    s = structure_loan(
        _structuring(total_monthly_income=Dc("25000"), total_existing_emi=Dc("11000"),
                     monthly_net_contribution=Dc("1500")),
        final_config,
    )
    assert not s.is_viable
    assert s.binding_constraint == "BELOW_PRODUCT_MINIMUM"


def test_fs_09_tenure_is_capped_by_battery_warranty(final_config):
    """Doc 01 LR-6 — tenure may not exceed battery warranty + grace."""
    s = structure_loan(
        _structuring(requested_tenure_months=84, grade="A", battery_warranty_months=36),
        final_config,
    )
    assert s.recommended_tenure_months == 48        # 36 + 12 months grace


def test_tenure_is_capped_by_grade(final_config):
    s = structure_loan(_structuring(requested_tenure_months=84, grade="C",
                                    battery_warranty_months=120), final_config)
    assert s.recommended_tenure_months == 60


@pytest.mark.parametrize(
    "grade, expected_ltv, expected_rate",
    [("A", "80.00", "12.50"), ("B", "75.00", "13.00"),
     ("C", "70.00", "14.00"), ("D", "60.00", "15.50")],
)
def test_ltv_and_pricing_by_grade(final_config, grade, expected_ltv, expected_rate):
    s = structure_loan(_structuring(grade=grade, total_monthly_income=Dc("900000")), final_config)
    assert s.max_ltv_percent == Dc(expected_ltv)
    assert s.recommended_interest_rate == Dc(expected_rate)


def test_grade_e_is_not_offered(final_config):
    s = structure_loan(_structuring(grade="E"), final_config)
    assert not s.is_viable
    assert s.binding_constraint == "GRADE_NOT_OFFERED"
    assert s.recommended_amount == Dc("0")


def test_ltv_never_exceeds_the_absolute_ceiling(final_config):
    """Even a grade-A file cannot breach the regulatory ceiling."""
    s = structure_loan(_structuring(grade="A", total_monthly_income=Dc("2000000"),
                                    requested_amount=Dc("5000000")), final_config)
    assert s.applied_ltv_percent <= Dc("80.00")
