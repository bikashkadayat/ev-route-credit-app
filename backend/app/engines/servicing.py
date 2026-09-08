"""Loan servicing arithmetic — Doc 06 §6.8, Doc 01 BR-4. `servicing@1.0.0`

Repayment allocation, days past due and classification, as pure functions of
``(loan state, business date)``. They live beside the scoring engines and under the same
purity guard for the same reason: a borrower's arrears position decides whether their loan
is reported as substandard, and that has to be reproducible from the stored state alone —
never from whatever the server clock happened to say when a nightly job ran.

The amortisation schedule itself is not re-implemented here; ``app.engines.emi`` already
owns it and is already tested.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from app.core.types import ZERO, D, q2

ENGINE_VERSION = "servicing@1.0.0"

#: Doc 01 BR-4. Upper bound of each DPD bucket; the last is open-ended.
CLASSIFICATION_BANDS: tuple[tuple[int | None, str, str], ...] = (
    (0, "PERFORMING", "GREEN"),
    (30, "WATCHLIST", "YELLOW"),
    (90, "SUBSTANDARD", "RED"),
    (180, "DOUBTFUL", "RED"),
    (None, "LOSS", "RED"),
)

#: Doc 06 §6.8 — "penalty → interest → principal, oldest unpaid instalment first".
ALLOCATION_ORDER: tuple[str, ...] = ("penalty", "interest", "principal")


@dataclass(frozen=True, slots=True)
class InstallmentState:
    """One schedule row as the allocator sees it. Mirrors ``repayment_schedules``."""

    installment_no: int
    due_date: date
    principal_due: Decimal
    interest_due: Decimal
    penalty_due: Decimal = ZERO
    principal_paid: Decimal = ZERO
    interest_paid: Decimal = ZERO
    penalty_paid: Decimal = ZERO

    @property
    def principal_outstanding(self) -> Decimal:
        return q2(D(self.principal_due) - D(self.principal_paid))

    @property
    def interest_outstanding(self) -> Decimal:
        return q2(D(self.interest_due) - D(self.interest_paid))

    @property
    def penalty_outstanding(self) -> Decimal:
        return q2(D(self.penalty_due) - D(self.penalty_paid))

    @property
    def total_outstanding(self) -> Decimal:
        return q2(
            self.principal_outstanding + self.interest_outstanding
            + self.penalty_outstanding
        )

    @property
    def is_settled(self) -> bool:
        return self.total_outstanding <= ZERO


@dataclass(frozen=True, slots=True)
class InstallmentAllocation:
    """What one payment did to one instalment."""

    installment_no: int
    penalty: Decimal
    interest: Decimal
    principal: Decimal

    @property
    def total(self) -> Decimal:
        return q2(self.penalty + self.interest + self.principal)

    def to_dict(self) -> dict[str, Any]:
        return {
            "installment_no": self.installment_no,
            "penalty": float(self.penalty),
            "interest": float(self.interest),
            "principal": float(self.principal),
            "total": float(self.total),
        }


@dataclass(frozen=True, slots=True)
class Allocation:
    """The full result of applying one payment across the schedule."""

    lines: tuple[InstallmentAllocation, ...]
    penalty_component: Decimal
    interest_component: Decimal
    principal_component: Decimal
    unallocated: Decimal
    settled_installments: tuple[int, ...]

    @property
    def allocated(self) -> Decimal:
        return q2(
            self.penalty_component + self.interest_component + self.principal_component
        )

    @property
    def is_advance(self) -> bool:
        """Money received beyond every outstanding instalment (Doc 06 §6.8)."""
        return self.unallocated > ZERO

    def to_dict(self) -> dict[str, Any]:
        return {
            "penalty": float(self.penalty_component),
            "interest": float(self.interest_component),
            "principal": float(self.principal_component),
            "allocated": float(self.allocated),
            "unallocated": float(self.unallocated),
            "settled_installments": list(self.settled_installments),
            "lines": [line.to_dict() for line in self.lines],
        }


def allocate_payment(
    amount: Decimal, schedule: Sequence[InstallmentState]
) -> Allocation:
    """Doc 06 §6.8 — penalty, then interest, then principal, oldest instalment first.

    The order is not arbitrary and must not be "improved": paying principal before interest
    would quietly reduce the interest the borrower owes, and paying a newer instalment
    before an older one would understate arrears and mask a delinquency the classification
    rules are supposed to catch.

    An excess beyond every outstanding instalment is returned as ``unallocated`` rather
    than being forced onto future instalments — the caller decides whether that becomes an
    advance or a refund.
    """
    remaining = q2(D(amount))
    if remaining <= ZERO:
        return Allocation((), ZERO, ZERO, ZERO, q2(max(D(amount), ZERO)), ())

    lines: list[InstallmentAllocation] = []
    settled: list[int] = []
    totals = {"penalty": ZERO, "interest": ZERO, "principal": ZERO}

    for row in sorted(schedule, key=lambda r: (r.due_date, r.installment_no)):
        if remaining <= ZERO:
            break
        if row.is_settled:
            continue

        taken = {"penalty": ZERO, "interest": ZERO, "principal": ZERO}
        outstanding = {
            "penalty": row.penalty_outstanding,
            "interest": row.interest_outstanding,
            "principal": row.principal_outstanding,
        }
        for bucket in ALLOCATION_ORDER:
            if remaining <= ZERO:
                break
            due = outstanding[bucket]
            if due <= ZERO:
                continue
            applied = due if due <= remaining else remaining
            taken[bucket] = q2(applied)
            totals[bucket] = q2(totals[bucket] + applied)
            remaining = q2(remaining - applied)

        if any(v > ZERO for v in taken.values()):
            lines.append(
                InstallmentAllocation(
                    installment_no=row.installment_no,
                    penalty=taken["penalty"],
                    interest=taken["interest"],
                    principal=taken["principal"],
                )
            )
            cleared = all(
                taken[bucket] >= outstanding[bucket] for bucket in ALLOCATION_ORDER
            )
            if cleared:
                settled.append(row.installment_no)

    return Allocation(
        lines=tuple(lines),
        penalty_component=totals["penalty"],
        interest_component=totals["interest"],
        principal_component=totals["principal"],
        unallocated=q2(remaining),
        settled_installments=tuple(settled),
    )


def days_past_due(schedule: Sequence[InstallmentState], as_of: date) -> int:
    """Age of the **oldest** unpaid instalment that is already due.

    Taking the oldest rather than the most recent is what makes DPD escalate: a borrower
    who pays instalment 5 while instalment 3 is still open is not up to date, and a
    "latest instalment" reading would quietly reset their arrears clock.
    """
    overdue = [
        (as_of - row.due_date).days
        for row in schedule
        if not row.is_settled and row.due_date < as_of
    ]
    return max(overdue) if overdue else 0


def overdue_amount(schedule: Sequence[InstallmentState], as_of: date) -> Decimal:
    """Everything already due and unpaid. Instalments not yet due are not arrears."""
    return q2(
        sum(
            (row.total_outstanding for row in schedule if row.due_date < as_of),
            start=ZERO,
        )
    )


def installments_overdue(schedule: Sequence[InstallmentState], as_of: date) -> int:
    return sum(1 for row in schedule if not row.is_settled and row.due_date < as_of)


def installments_paid(schedule: Sequence[InstallmentState]) -> int:
    return sum(1 for row in schedule if row.is_settled)


def outstanding_principal(schedule: Sequence[InstallmentState]) -> Decimal:
    """Principal not yet repaid, across the whole schedule — the loan's book value."""
    return q2(sum((row.principal_outstanding for row in schedule), start=ZERO))


def outstanding_interest(schedule: Sequence[InstallmentState]) -> Decimal:
    return q2(sum((row.interest_outstanding for row in schedule), start=ZERO))


def total_paid(schedule: Sequence[InstallmentState]) -> Decimal:
    return q2(
        sum(
            (D(r.principal_paid) + D(r.interest_paid) + D(r.penalty_paid) for r in schedule),
            start=ZERO,
        )
    )


def classify(dpd: int) -> str:
    """Doc 01 BR-4. Deterministic from days past due alone."""
    for upper, classification, _flag in CLASSIFICATION_BANDS:
        if upper is None or dpd <= upper:
            return classification
    return CLASSIFICATION_BANDS[-1][1]


def risk_status(dpd: int) -> str:
    """The portfolio traffic light that accompanies the classification (Doc 01 BR-4)."""
    for upper, _classification, flag in CLASSIFICATION_BANDS:
        if upper is None or dpd <= upper:
            return flag
    return CLASSIFICATION_BANDS[-1][2]


def installment_status(row: InstallmentState, as_of: date) -> str:
    """Maps a schedule row onto ``installment_status_enum``."""
    if row.is_settled:
        return "PAID"
    paid_anything = (
        D(row.principal_paid) + D(row.interest_paid) + D(row.penalty_paid)
    ) > ZERO
    if row.due_date < as_of:
        return "PARTIAL" if paid_anything else "OVERDUE"
    return "PARTIAL" if paid_anything else "PENDING"


def penalty_for(
    row: InstallmentState, as_of: date, annual_rate_percent: Decimal
) -> Decimal:
    """Simple-interest penalty on the overdue instalment, accrued daily.

    ``loan.penalty_rate_percent`` is annual (Doc 05 system settings), so the daily rate is
    the annual one over 365. Not compounded: the specification describes a penalty rate,
    not a second loan.
    """
    if row.is_settled or row.due_date >= as_of:
        return ZERO
    days = (as_of - row.due_date).days
    base = row.principal_outstanding + row.interest_outstanding
    daily = D(annual_rate_percent) / Decimal("100") / Decimal("365")
    return q2(base * daily * Decimal(days))


@dataclass(frozen=True, slots=True)
class LoanPosition:
    """Everything the servicing job writes back to ``loans``, derived in one pass."""

    days_past_due: int
    overdue_amount: Decimal
    outstanding_principal: Decimal
    outstanding_interest: Decimal
    total_paid: Decimal
    installments_paid: int
    installments_overdue: int
    classification: str
    risk_status: str
    is_fully_repaid: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "days_past_due": self.days_past_due,
            "overdue_amount": float(self.overdue_amount),
            "outstanding_principal": float(self.outstanding_principal),
            "outstanding_interest": float(self.outstanding_interest),
            "total_paid": float(self.total_paid),
            "installments_paid": self.installments_paid,
            "installments_overdue": self.installments_overdue,
            "classification": self.classification,
            "risk_status": self.risk_status,
            "is_fully_repaid": self.is_fully_repaid,
        }


def loan_position(schedule: Sequence[InstallmentState], as_of: date) -> LoanPosition:
    """The whole servicing picture from the schedule and a business date.

    One function so the nightly job cannot compute DPD from one reading of the schedule and
    classification from another.
    """
    dpd = days_past_due(schedule, as_of)
    return LoanPosition(
        days_past_due=dpd,
        overdue_amount=overdue_amount(schedule, as_of),
        outstanding_principal=outstanding_principal(schedule),
        outstanding_interest=outstanding_interest(schedule),
        total_paid=total_paid(schedule),
        installments_paid=installments_paid(schedule),
        installments_overdue=installments_overdue(schedule, as_of),
        classification=classify(dpd),
        risk_status=risk_status(dpd),
        is_fully_repaid=all(row.is_settled for row in schedule) if schedule else False,
    )
