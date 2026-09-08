"""Repayment allocation, DPD and classification — Doc 06 §6.8, Doc 01 BR-4.

Pure arithmetic, so it is pinned here rather than through the API. Every case in Doc 10
§10.2.3 has a test, and the allocation order is asserted directly rather than inferred from
a total: a payment that lands on the right total by the wrong route still misstates what
the borrower owes.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.engines.servicing import (
    ALLOCATION_ORDER,
    Allocation,
    InstallmentState,
    allocate_payment,
    classify,
    days_past_due,
    installment_status,
    installments_overdue,
    loan_position,
    outstanding_principal,
    overdue_amount,
    penalty_for,
    risk_status,
)

D = Decimal


def row(no: int, due: date, principal="35282", interest="32067", **paid) -> InstallmentState:
    return InstallmentState(
        installment_no=no,
        due_date=due,
        principal_due=D(principal),
        interest_due=D(interest),
        penalty_due=D(paid.pop("penalty_due", "0")),
        principal_paid=D(paid.pop("principal_paid", "0")),
        interest_paid=D(paid.pop("interest_paid", "0")),
        penalty_paid=D(paid.pop("penalty_paid", "0")),
    )


def three_month_schedule() -> list[InstallmentState]:
    return [
        row(1, date(2026, 4, 1)),
        row(2, date(2026, 5, 1)),
        row(3, date(2026, 6, 1)),
    ]


# ---------------------------------------------------------------------------
# Allocation order — Doc 06 §6.8
# ---------------------------------------------------------------------------
def test_the_documented_order_is_penalty_interest_principal():
    assert ALLOCATION_ORDER == ("penalty", "interest", "principal")


def test_an_exact_instalment_payment_settles_it():
    """Doc 10 §10.2.3 RA-01."""
    schedule = three_month_schedule()
    result = allocate_payment(D("67349"), schedule)

    assert result.settled_installments == (1,)
    assert result.principal_component == D("35282.00")
    assert result.interest_component == D("32067.00")
    assert result.unallocated == D("0.00")


def test_interest_is_paid_before_principal():
    """Paying principal first would quietly reduce the interest the borrower owes."""
    schedule = [row(1, date(2026, 4, 1), principal="1000", interest="500")]
    result = allocate_payment(D("500"), schedule)

    assert result.interest_component == D("500.00")
    assert result.principal_component == D("0.00")


def test_penalty_is_paid_before_interest():
    schedule = [
        row(1, date(2026, 4, 1), principal="1000", interest="500", penalty_due="200")
    ]
    result = allocate_payment(D("200"), schedule)

    assert result.penalty_component == D("200.00")
    assert result.interest_component == D("0.00")
    assert result.principal_component == D("0.00")


def test_a_payment_covering_every_component_splits_in_the_documented_order():
    schedule = [
        row(1, date(2026, 4, 1), principal="1000", interest="500", penalty_due="200")
    ]
    result = allocate_payment(D("1700"), schedule)

    assert (result.penalty_component, result.interest_component,
            result.principal_component) == (D("200.00"), D("500.00"), D("1000.00"))
    assert result.settled_installments == (1,)


# ---------------------------------------------------------------------------
# Partial, over and multi-instalment payments
# ---------------------------------------------------------------------------
def test_a_partial_payment_leaves_the_balance_carried():
    """Doc 10 §10.2.3 RA-02."""
    schedule = three_month_schedule()
    result = allocate_payment(D("20000"), schedule)

    assert result.settled_installments == ()
    assert result.interest_component == D("20000.00")
    assert result.principal_component == D("0.00")
    assert result.unallocated == D("0.00")


def test_a_payment_spanning_two_instalments_settles_the_older_one_first():
    """Doc 10 §10.2.3 RA-03 — oldest unpaid instalment first."""
    schedule = three_month_schedule()
    result = allocate_payment(D("100000"), schedule)

    assert result.settled_installments == (1,)
    assert [line.installment_no for line in result.lines] == [1, 2]

    # 100,000 - 67,349 = 32,651 spills onto instalment 2: its interest of 32,067 first,
    # then 584 against principal — the documented order, across the boundary.
    assert result.lines[1].penalty == D("0.00")
    assert result.lines[1].interest == D("32067.00")
    assert result.lines[1].principal == D("584.00")


def test_an_overpayment_beyond_the_schedule_is_reported_as_unallocated():
    """The caller decides whether that is an advance or a refund; silently forcing it onto
    a future instalment would misstate what has actually been settled."""
    schedule = [row(1, date(2026, 4, 1), principal="1000", interest="500")]
    result = allocate_payment(D("2000"), schedule)

    assert result.allocated == D("1500.00")
    assert result.unallocated == D("500.00")
    assert result.is_advance is True


def test_an_exactly_covering_payment_is_not_an_advance():
    schedule = [row(1, date(2026, 4, 1), principal="1000", interest="500")]
    assert allocate_payment(D("1500"), schedule).is_advance is False


def test_multiple_overdue_instalments_are_cleared_oldest_first():
    """Doc 10 §10.2.3 RA-04."""
    schedule = [
        row(1, date(2026, 4, 1), principal="1000", interest="200"),
        row(2, date(2026, 5, 1), principal="1000", interest="200"),
        row(3, date(2026, 6, 1), principal="1000", interest="200"),
    ]
    result = allocate_payment(D("2400"), schedule)
    assert result.settled_installments == (1, 2)


def test_an_already_settled_instalment_is_skipped():
    schedule = [
        row(1, date(2026, 4, 1), principal="1000", interest="200",
            principal_paid="1000", interest_paid="200"),
        row(2, date(2026, 5, 1), principal="1000", interest="200"),
    ]
    result = allocate_payment(D("1200"), schedule)
    assert result.settled_installments == (2,)
    assert [line.installment_no for line in result.lines] == [2]


def test_a_partly_paid_instalment_only_receives_its_remainder():
    schedule = [
        row(1, date(2026, 4, 1), principal="1000", interest="200", interest_paid="200")
    ]
    result = allocate_payment(D("1000"), schedule)
    assert result.interest_component == D("0.00")
    assert result.principal_component == D("1000.00")


@pytest.mark.parametrize("amount", ["0", "-100"])
def test_a_non_positive_payment_allocates_nothing(amount):
    result = allocate_payment(D(amount), three_month_schedule())
    assert result.allocated == D("0.00")
    assert result.lines == ()


def test_allocation_against_an_empty_schedule_is_entirely_unallocated():
    result = allocate_payment(D("5000"), [])
    assert result.unallocated == D("5000.00")


def test_allocation_never_exceeds_the_payment():
    """Property: the parts can never add to more than what was received."""
    for amount in ("1", "999", "67349", "250000", "1000000"):
        result: Allocation = allocate_payment(D(amount), three_month_schedule())
        assert result.allocated + result.unallocated == D(amount).quantize(D("0.01"))


def test_every_amount_is_decimal_never_float():
    result = allocate_payment(D("67349"), three_month_schedule())
    for value in (result.penalty_component, result.interest_component,
                  result.principal_component, result.unallocated):
        assert isinstance(value, Decimal)


# ---------------------------------------------------------------------------
# Days past due
# ---------------------------------------------------------------------------
def test_a_loan_with_nothing_due_has_no_dpd():
    schedule = three_month_schedule()
    assert days_past_due(schedule, date(2026, 3, 15)) == 0


def test_an_instalment_due_today_is_not_yet_overdue():
    """Doc 10 — the grace boundary. Charging DPD on the due date itself would put every
    borrower one day late from the moment they were billed."""
    schedule = three_month_schedule()
    assert days_past_due(schedule, date(2026, 4, 1)) == 0


def test_one_day_overdue_reads_as_one():
    assert days_past_due(three_month_schedule(), date(2026, 4, 2)) == 1


def test_dpd_counts_from_the_oldest_unpaid_instalment():
    """A borrower who pays instalment 2 while 1 is open is not up to date; reading the
    most recent instalment would reset their arrears clock."""
    schedule = [
        row(1, date(2026, 4, 1)),
        row(2, date(2026, 5, 1), principal_paid="35282", interest_paid="32067"),
    ]
    assert days_past_due(schedule, date(2026, 5, 11)) == 40


def test_a_settled_instalment_contributes_no_dpd():
    schedule = [row(1, date(2026, 4, 1), principal_paid="35282", interest_paid="32067")]
    assert days_past_due(schedule, date(2026, 7, 1)) == 0


def test_a_fully_paid_loan_has_no_dpd():
    schedule = [
        row(n, date(2026, 3 + n, 1), principal_paid="35282", interest_paid="32067")
        for n in (1, 2, 3)
    ]
    assert days_past_due(schedule, date(2027, 1, 1)) == 0


def test_multiple_overdue_instalments_are_counted():
    assert installments_overdue(three_month_schedule(), date(2026, 6, 15)) == 3


# ---------------------------------------------------------------------------
# Outstanding
# ---------------------------------------------------------------------------
def test_outstanding_principal_covers_the_whole_schedule_not_just_arrears():
    schedule = three_month_schedule()
    assert outstanding_principal(schedule) == D("105846.00")


def test_overdue_amount_excludes_instalments_not_yet_due():
    """Arrears is what is late, not what remains — conflating them would classify a
    perfectly current borrower as in default."""
    schedule = three_month_schedule()
    assert overdue_amount(schedule, date(2026, 5, 2)) == D("134698.00")


def test_outstanding_falls_as_payments_land():
    schedule = three_month_schedule()
    before = outstanding_principal(schedule)
    schedule[0] = row(1, date(2026, 4, 1), principal_paid="35282", interest_paid="32067")
    assert outstanding_principal(schedule) == before - D("35282.00")


# ---------------------------------------------------------------------------
# Classification — Doc 01 BR-4
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("dpd", "expected"),
    [
        (0, "PERFORMING"),
        (1, "WATCHLIST"), (30, "WATCHLIST"),
        (31, "SUBSTANDARD"), (90, "SUBSTANDARD"),
        (91, "DOUBTFUL"), (180, "DOUBTFUL"),
        (181, "LOSS"), (5000, "LOSS"),
    ],
)
def test_the_documented_dpd_buckets(dpd, expected):
    assert classify(dpd) == expected


@pytest.mark.parametrize(
    ("dpd", "expected"),
    [(0, "GREEN"), (1, "YELLOW"), (30, "YELLOW"), (31, "RED"), (200, "RED")],
)
def test_the_portfolio_flag_follows_the_same_bands(dpd, expected):
    assert risk_status(dpd) == expected


def test_classification_is_deterministic():
    assert all(classify(45) == "SUBSTANDARD" for _ in range(5))


# ---------------------------------------------------------------------------
# Instalment status
# ---------------------------------------------------------------------------
def test_an_untouched_future_instalment_is_pending():
    assert installment_status(row(1, date(2026, 5, 1)), date(2026, 4, 1)) == "PENDING"


def test_an_untouched_due_instalment_is_overdue():
    assert installment_status(row(1, date(2026, 4, 1)), date(2026, 4, 15)) == "OVERDUE"


def test_a_part_paid_instalment_is_partial():
    part = row(1, date(2026, 4, 1), interest_paid="1000")
    assert installment_status(part, date(2026, 4, 15)) == "PARTIAL"


def test_a_settled_instalment_is_paid():
    settled = row(1, date(2026, 4, 1), principal_paid="35282", interest_paid="32067")
    assert installment_status(settled, date(2026, 4, 15)) == "PAID"


# ---------------------------------------------------------------------------
# Penalty accrual
# ---------------------------------------------------------------------------
def test_no_penalty_accrues_before_the_due_date():
    assert penalty_for(row(1, date(2026, 5, 1)), date(2026, 4, 1), D("2.0")) == D("0.00")


def test_no_penalty_accrues_on_a_settled_instalment():
    settled = row(1, date(2026, 4, 1), principal_paid="35282", interest_paid="32067")
    assert penalty_for(settled, date(2026, 6, 1), D("2.0")) == D("0.00")


def test_penalty_accrues_daily_on_the_overdue_balance():
    """2% annual on 67,349 for 30 days = 67,349 x 0.02 / 365 x 30."""
    overdue = row(1, date(2026, 4, 1))
    expected = (D("67349") * D("0.02") / D("365") * D("30")).quantize(D("0.01"))
    assert penalty_for(overdue, date(2026, 5, 1), D("2.0")) == expected


def test_penalty_is_not_compounded():
    """Sixty days must cost exactly twice thirty; a compounding penalty is a second loan."""
    overdue = row(1, date(2026, 4, 1))
    thirty = penalty_for(overdue, date(2026, 5, 1), D("2.0"))
    sixty = penalty_for(overdue, date(2026, 5, 31), D("2.0"))
    assert sixty == (thirty * 2).quantize(D("0.01"))


# ---------------------------------------------------------------------------
# The combined position
# ---------------------------------------------------------------------------
def test_the_loan_position_is_computed_in_one_pass():
    """One function, so a nightly job cannot read the schedule twice and derive DPD and
    classification from two different states."""
    position = loan_position(three_month_schedule(), date(2026, 5, 15))

    assert position.days_past_due == 44
    assert position.classification == "SUBSTANDARD"
    assert position.risk_status == "RED"
    assert position.installments_overdue == 2
    assert position.installments_paid == 0
    assert position.is_fully_repaid is False


def test_the_position_of_a_current_loan_is_performing():
    position = loan_position(three_month_schedule(), date(2026, 3, 20))
    assert position.days_past_due == 0
    assert position.classification == "PERFORMING"
    assert position.risk_status == "GREEN"


def test_a_fully_repaid_loan_reports_itself_as_such():
    schedule = [
        row(n, date(2026, 3 + n, 1), principal_paid="35282", interest_paid="32067")
        for n in (1, 2, 3)
    ]
    position = loan_position(schedule, date(2026, 8, 1))
    assert position.is_fully_repaid is True
    assert position.outstanding_principal == D("0.00")
    assert position.classification == "PERFORMING"


def test_the_classification_always_agrees_with_the_dpd_it_was_derived_from():
    for as_of in (date(2026, 3, 1), date(2026, 4, 10), date(2026, 6, 1), date(2027, 1, 1)):
        position = loan_position(three_month_schedule(), as_of)
        assert position.classification == classify(position.days_past_due)
        assert position.risk_status == risk_status(position.days_past_due)
