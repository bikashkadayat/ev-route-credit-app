"""EMI and amortisation — Doc 10 §10.2.2 cases EMI-01..EMI-09."""

from __future__ import annotations

import itertools
from datetime import date
from decimal import Decimal as Dc

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.engines.emi import (
    add_months,
    build_schedule,
    calculate_emi,
    principal_from_emi,
    schedule_totals,
)


def test_emi_01_reference_case():
    """NPR 2,960,000 @ 13% over 60 months. Doc 07 §7.5.7."""
    emi = calculate_emi(Dc("2960000"), Dc("13.0"), 60)
    assert abs(emi - Dc("67349.10")) <= Dc("0.50")


def test_emi_on_requested_structure():
    """NPR 3,200,000 @ 13% over 60 months."""
    assert abs(calculate_emi(Dc("3200000"), Dc("13.0"), 60) - Dc("72809.83")) <= Dc("0.50")


def test_emi_02_zero_interest():
    assert calculate_emi(Dc("120000"), Dc("0"), 12) == Dc("10000.00")


def test_emi_03_single_month_tenure():
    p, rate = Dc("100000"), Dc("12")
    expected = p * (Dc("1") + rate / Dc("1200"))
    assert abs(calculate_emi(p, rate, 1) - expected) <= Dc("0.01")


@pytest.mark.parametrize(
    "principal, rate, tenure",
    [
        ("2960000", "13.0", 60), ("450000", "13.0", 36), ("4800000", "12.5", 72),
        ("690000", "13.5", 48), ("100000", "0", 12), ("2150000", "14.0", 60),
    ],
)
def test_emi_04_05_schedule_sums_exactly(principal, rate, tenure):
    """Sum of principal equals the loan exactly; the final balance is exactly zero."""
    p = Dc(principal)
    rows = build_schedule(p, Dc(rate), tenure, date(2026, 10, 15))
    totals = schedule_totals(rows)
    assert len(rows) == tenure
    assert totals["total_principal"] == p
    assert rows[-1].closing_balance == Dc("0.00")
    assert totals["total_payable"] == totals["total_principal"] + totals["total_interest"]


def test_emi_06_rounding_residue_absorbed_by_final_instalment():
    rows = build_schedule(Dc("2960000"), Dc("13.0"), 60, date(2026, 10, 15))
    emi = calculate_emi(Dc("2960000"), Dc("13.0"), 60)
    for row in rows[:-1]:
        assert row.total_due == emi
    assert abs(rows[-1].total_due - emi) < Dc("2.00")


def test_emi_07_month_end_due_dates_clamp():
    """A 31st anchor maps to the last day of shorter months."""
    rows = build_schedule(Dc("120000"), Dc("12"), 5, date(2026, 1, 31))
    assert [r.due_date for r in rows] == [
        date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31),
        date(2026, 4, 30), date(2026, 5, 31),
    ]


def test_add_months_handles_leap_year():
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2026, 12, 31), 1) == date(2027, 1, 31)
    assert add_months(date(2026, 12, 31), 2) == date(2027, 2, 28)


def test_emi_08_long_tenure_is_ordered():
    rows = build_schedule(Dc("4800000"), Dc("12.5"), 84, date(2026, 3, 10))
    assert len(rows) == 84
    assert all(a.due_date < b.due_date for a, b in itertools.pairwise(rows))
    assert all(a.closing_balance == b.opening_balance for a, b in itertools.pairwise(rows))


def test_emi_09_no_floats_anywhere():
    rows = build_schedule(Dc("450000"), Dc("13"), 36, date(2026, 5, 1))
    for row in rows:
        for value in (row.opening_balance, row.principal_due, row.interest_due,
                      row.total_due, row.closing_balance):
            assert isinstance(value, Dc)


def test_principal_from_emi_is_the_inverse_of_calculate_emi():
    """Doc 07 §7.5.4 step 3 — the affordability-implied principal."""
    principal = Dc("2960000")
    emi = calculate_emi(principal, Dc("13.0"), 60)
    recovered = principal_from_emi(emi, Dc("13.0"), 60)
    assert abs(recovered - principal) <= Dc("5.00")


def test_emi_capacity_reference_case():
    """FOIR-capped EMI capacity of 67,350 supports ~2,960,000 over 60 months at 13%."""
    assert abs(principal_from_emi(Dc("67350"), Dc("13.0"), 60) - Dc("2960412")) <= Dc("500")


@pytest.mark.parametrize("principal, tenure", [("0", 60), ("-1", 60)])
def test_invalid_principal_raises(principal, tenure):
    with pytest.raises(ValueError):
        calculate_emi(Dc(principal), Dc("13"), tenure)


def test_invalid_tenure_raises():
    with pytest.raises(ValueError):
        calculate_emi(Dc("100000"), Dc("13"), 0)


@given(
    principal=st.decimals(min_value=Dc("100000"), max_value=Dc("5000000"), places=0),
    rate=st.decimals(min_value=Dc("0"), max_value=Dc("30"), places=2),
    tenure=st.integers(min_value=6, max_value=84),
)
@settings(max_examples=150, deadline=None)
def test_schedule_always_reconciles(principal, rate, tenure):
    rows = build_schedule(principal, rate, tenure, date(2026, 1, 15))
    totals = schedule_totals(rows)
    assert totals["total_principal"] == principal.quantize(Dc("0.01"))
    assert rows[-1].closing_balance == Dc("0.00")
    assert all(r.principal_due >= Dc("0") for r in rows)
    assert all(r.interest_due >= Dc("0") for r in rows)
