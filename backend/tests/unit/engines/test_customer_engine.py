"""Customer engine and knock-outs — Doc 07 §7.4, Doc 10 cases CS-01..CS-10."""

from __future__ import annotations

import dataclasses
from decimal import Decimal as Dc

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.core.types import q2
from app.engines.customer_engine import (
    CustomerScoringEngine,
    CustomerScoringInput,
    derive_customer_inputs,
)
from app.engines.knockouts import evaluate_knockouts
from app.engines.normalizer import composite_breakdown

ENGINE = CustomerScoringEngine()


def _replace(payload: CustomerScoringInput, **changes) -> CustomerScoringInput:
    return dataclasses.replace(payload, **changes)


# ---------------------------------------------------------------------------
# CS-07 the reference borrower, asserted from sub-factor upwards
# ---------------------------------------------------------------------------
def test_derived_affordability_metrics(ram_bahadur):
    i = derive_customer_inputs(ram_bahadur)
    assert i["disposable_income"] == Dc("72600")            # 145000 - 60000 - 12400
    assert q2(i["dti_ratio"]) == Dc("0.09")                 # 12400 / 145000
    assert q2(i["foir_post_loan"]) == Dc("0.59")            # (12400 + 72809.83) / 145000
    assert q2(i["down_payment_ratio"]) == Dc("0.30")        # 1400000 / 4600000
    assert i["income_proof_class"] == "BANK_STATEMENT_VERIFIED"
    assert i["applicant_class"] == "INDIVIDUAL"


@pytest.mark.parametrize(
    "component, sub_code, expected",
    [
        # 742 in [700, 750]: 65 + 17 x (42/50) = 79.28
        ("CREDIT_HISTORY", "BUREAU_SCORE", "79.28"),
        # 12 dpd in [0, 15]: 100 - 20 x (12/15) = 84
        ("CREDIT_HISTORY", "REPAYMENT_RECORD", "84"),
        ("CREDIT_HISTORY", "DEFAULT_HISTORY", "100"),
        # 54 months in [48, 72]: 95 + 5 x (6/24) = 96.25
        ("CREDIT_HISTORY", "CREDIT_DEPTH", "96.25"),
        ("CREDIT_HISTORY", "ENQUIRY_BEHAVIOUR", "95"),
        # 145000 in [100k, 200k]: 92 + 8 x 0.45 = 95.6
        ("INCOME_CASHFLOW", "INCOME_ADEQUACY", "95.6"),
        ("INCOME_CASHFLOW", "INCOME_STABILITY", "90"),
        # FOIR 0.5877 in [0.55, 0.65]: 40 - 25 x 0.3765 = 30.59
        ("EXISTING_DEBT", "FOIR_POST_LOAN", "30.59"),
        # DTI 0.08552 in [0, 0.15]: 100 - 15 x 0.5701 = 91.45
        ("EXISTING_DEBT", "DTI_PRE_LOAN", "91.45"),
        ("EXISTING_DEBT", "OBLIGATION_COUNT", "90"),
        ("EXISTING_DEBT", "OVERDUE_OBLIGATIONS", "100"),
        # 6 commercial years in [5, 8]: 80 + 12 x (1/3) = 84
        ("EXPERIENCE", "COMMERCIAL_YEARS", "84"),
        # 9 driving years in [5, 10]: 70 + 20 x 0.8 = 86
        ("EXPERIENCE", "DRIVING_YEARS", "86"),
        ("EXPERIENCE", "EV_EXPERIENCE", "50"),
        ("EXPERIENCE", "LICENCE_VALIDITY", "100"),
        ("VEHICLE_ECONOMICS", "WARRANTY_COVERAGE", "100"),
    ],
)
def test_reference_sub_factor_scores(ram_bahadur, customer_config, component, sub_code, expected):
    inputs = derive_customer_inputs(ram_bahadur)
    subs = composite_breakdown(customer_config.component(component).scoring_rules, inputs)
    assert q2({s.code: s.score for s in subs}[sub_code]) == Dc(expected)


@pytest.mark.parametrize(
    "code, expected",
    [
        ("CREDIT_HISTORY", "85.82"),
        ("INCOME_CASHFLOW", "64.43"),
        ("EXISTING_DEBT", "61.66"),
        ("DOWN_PAYMENT", "68.74"),
        ("EXPERIENCE", "81.00"),
        ("VEHICLE_ECONOMICS", "90.96"),
    ],
)
def test_reference_component_scores(ram_bahadur, customer_config, code, expected):
    r = ENGINE.score(ram_bahadur, customer_config)
    assert r.component(code).normalized_score == Dc(expected)


def test_cs_07_reference_borrower(ram_bahadur, customer_config):
    """Doc 07 §7.5.7 — Ram Bahadur Tamang."""
    r = ENGINE.score(ram_bahadur, customer_config)
    assert r.total_score == Dc("75.17")
    assert r.grade == "B"
    assert r.grade_label == "B - Good"
    assert r.engine_version == "customer-engine@1.0.0"
    assert r.extras["knockouts_triggered"] == []
    assert r.extras["is_thin_file"] is False
    assert r.extras["dti_ratio"] == pytest.approx(0.0855, abs=1e-4)
    assert r.extras["foir_ratio"] == pytest.approx(0.5877, abs=1e-4)
    assert r.extras["disposable_income"] == pytest.approx(72600.0)


# ---------------------------------------------------------------------------
# Knock-outs — CS-01..CS-10
# ---------------------------------------------------------------------------
def test_cs_01_blacklist_short_circuits(ram_bahadur, customer_config):
    """A blacklisted applicant scores 0 and NO component scoring is performed."""
    r = ENGINE.score(_replace(ram_bahadur, is_blacklisted=True), customer_config)
    assert r.total_score == Dc("0")
    assert r.grade == "E"
    assert r.components == ()
    assert [k["code"] for k in r.extras["knockouts_triggered"]] == ["KO_BLACKLIST"]
    assert r.extras["recommendation"] == "REJECT"


@pytest.mark.parametrize("score, expect_knockout", [("550", False), ("549", True)])
def test_cs_02_03_bureau_floor_is_inclusive(ram_bahadur, customer_config, score, expect_knockout):
    r = ENGINE.score(_replace(ram_bahadur, bureau_score=Dc(score)), customer_config)
    codes = [k["code"] for k in r.extras["knockouts_triggered"]]
    assert ("KO_BUREAU_SCORE_FLOOR" in codes) is expect_knockout


@pytest.mark.parametrize(
    "down_payment, expect_knockout",
    [("920000", False), ("919999", True)],  # 20% of 4,600,000 = 920,000
)
def test_cs_04_05_down_payment_floor_is_inclusive(
    ram_bahadur, customer_config, down_payment, expect_knockout
):
    r = ENGINE.score(_replace(ram_bahadur, down_payment=Dc(down_payment)), customer_config)
    codes = [k["code"] for k in r.extras["knockouts_triggered"]]
    assert ("KO_MIN_DOWN_PAYMENT" in codes) is expect_knockout


def test_cs_06_thin_file_scores_at_the_default(ram_bahadur, customer_config):
    thin = _replace(ram_bahadur, credit_history_months=0, bureau_score=None)
    r = ENGINE.score(thin, customer_config)
    assert r.component("CREDIT_HISTORY").normalized_score == Dc("45")
    assert r.extras["is_thin_file"] is True


def test_cs_08_business_applicant_uses_the_business_variant(ram_bahadur, customer_config):
    corporate = _replace(
        ram_bahadur, applicant_type="TRANSPORT_COMPANY",
        business_experience_years=Dc("11"), fleet_size=14, is_driver_operated=False,
    )
    r = ENGINE.score(corporate, customer_config)
    sub_codes = {s.code for s in r.component("EXPERIENCE").sub_factors}
    assert sub_codes == {"BUSINESS_YEARS", "FLEET_SIZE", "EV_EXPERIENCE"}


@pytest.mark.parametrize("age_at_maturity, expect_knockout", [("65", False), ("66", True)])
def test_cs_09_10_age_limit_is_inclusive(
    ram_bahadur, customer_config, age_at_maturity, expect_knockout
):
    r = ENGINE.score(_replace(ram_bahadur, age_at_maturity=Dc(age_at_maturity)), customer_config)
    codes = [k["code"] for k in r.extras["knockouts_triggered"]]
    assert ("KO_AGE_LIMIT" in codes) is expect_knockout


def test_active_default_knockout_requires_both_default_and_overdue(ram_bahadur, customer_config):
    settled = _replace(ram_bahadur, previous_default_count=1, current_overdue_amount=Dc("0"))
    assert not any(
        k["code"] == "KO_ACTIVE_DEFAULT"
        for k in ENGINE.score(settled, customer_config).extras["knockouts_triggered"]
    )
    live = _replace(ram_bahadur, previous_default_count=1, current_overdue_amount=Dc("45000"))
    assert any(
        k["code"] == "KO_ACTIVE_DEFAULT"
        for k in ENGINE.score(live, customer_config).extras["knockouts_triggered"]
    )


def test_class_c_route_knocks_out_unless_waived(ram_bahadur, customer_config):
    on_class_c = _replace(ram_bahadur, route_grade="C", route_score=Dc("37.94"))
    codes = [
        k["code"] for k in ENGINE.score(on_class_c, customer_config).extras["knockouts_triggered"]
    ]
    assert "KO_ROUTE_CLASS_C" in codes

    waived = _replace(on_class_c, route_waiver_granted=True)
    codes = [
        k["code"] for k in ENGINE.score(waived, customer_config).extras["knockouts_triggered"]
    ]
    assert "KO_ROUTE_CLASS_C" not in codes


def test_multiple_knockouts_are_all_reported(ram_bahadur, customer_config):
    """Doc 16 §16.7 — LA-2026-000115 fires blacklist and Class C simultaneously."""
    both = _replace(ram_bahadur, is_blacklisted=True, route_grade="C")
    codes = [k["code"] for k in ENGINE.score(both, customer_config).extras["knockouts_triggered"]]
    assert {"KO_BLACKLIST", "KO_ROUTE_CLASS_C"} <= set(codes)


def test_knockout_thresholds_come_from_configuration(ram_bahadur, customer_config):
    """Raising the floor to 800 must reject a 742 file without any code change."""
    import dataclasses as dc

    stricter = dc.replace(
        customer_config,
        decision_rules={**customer_config.decision_rules,
                        "knockouts": {**customer_config.decision_rules["knockouts"],
                                      "bureau_score_floor": 800}},
    )
    codes = [k.code for k in evaluate_knockouts(derive_customer_inputs(ram_bahadur), stricter)]
    assert "KO_BUREAU_SCORE_FLOOR" in codes


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------
@given(
    bureau=st.decimals(min_value=Dc("550"), max_value=Dc("900"), places=0),
    income=st.decimals(min_value=Dc("20000"), max_value=Dc("500000"), places=0),
    dpd=st.integers(min_value=0, max_value=180),
    loans=st.integers(min_value=0, max_value=6),
)
@settings(max_examples=150, deadline=None,
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_customer_score_is_bounded_and_additive(
    ram_bahadur, customer_config, bureau, income, dpd, loans
):
    payload = _replace(
        ram_bahadur, bureau_score=bureau, total_monthly_income=income,
        max_dpd_last_24m=dpd, existing_loan_count=loans,
    )
    r = ENGINE.score(payload, customer_config)
    assert Dc("0") <= r.total_score <= Dc("100")
    if r.components:
        total = sum(c.weighted_score for c in r.components)
        assert abs(total - r.total_score) <= Dc("0.01")
    assert r.grade == customer_config.band_for(r.total_score).grade


def test_scoring_is_deterministic(ram_bahadur, customer_config):
    assert ENGINE.score(ram_bahadur, customer_config) == ENGINE.score(ram_bahadur, customer_config)
