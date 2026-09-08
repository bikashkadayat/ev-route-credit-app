"""EMI and amortisation — Doc 07 §7.5.3, Doc 14 EMI-01..EMI-09.

Reducing-balance equal monthly instalment. Decimal throughout; a float never touches
a money value. The final instalment absorbs the rounding residue so that
``sum(principal_due) == principal`` exactly (Doc 14 EMI-04/05).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.core.types import ZERO, D, q2

MONTHS_PER_YEAR = Decimal("12")
PERCENT = Decimal("100")


def monthly_rate(annual_rate_percent: Decimal) -> Decimal:
    return D(annual_rate_percent) / MONTHS_PER_YEAR / PERCENT


def calculate_emi(principal: Decimal, annual_rate_percent: Decimal, tenure_months: int) -> Decimal:
    """EMI = P.r.(1+r)^n / ((1+r)^n - 1), or P/n when r == 0."""
    p = D(principal)
    n = int(tenure_months)
    if p <= ZERO:
        raise ValueError("principal must be positive")
    if n <= 0:
        raise ValueError("tenure_months must be positive")

    r = monthly_rate(annual_rate_percent)
    if r == ZERO:
        return q2(p / Decimal(n))
    growth = (Decimal("1") + r) ** n
    return q2(p * r * growth / (growth - Decimal("1")))


def principal_from_emi(
    emi: Decimal, annual_rate_percent: Decimal, tenure_months: int
) -> Decimal:
    """Inverse of ``calculate_emi`` — the loan an affordable EMI supports.

    Doc 07 §7.5.4 step 3: ``amount_by_emi``.
    """
    e = D(emi)
    n = int(tenure_months)
    if e <= ZERO or n <= 0:
        return ZERO
    r = monthly_rate(annual_rate_percent)
    if r == ZERO:
        return q2(e * Decimal(n))
    growth = (Decimal("1") + r) ** n
    return q2(e * (growth - Decimal("1")) / (r * growth))


def total_interest(principal: Decimal, emi: Decimal, tenure_months: int) -> Decimal:
    return q2(D(emi) * Decimal(int(tenure_months)) - D(principal))


@dataclass(frozen=True, slots=True)
class Installment:
    installment_no: int
    due_date: date
    opening_balance: Decimal
    principal_due: Decimal
    interest_due: Decimal
    total_due: Decimal
    closing_balance: Decimal


def add_months(anchor: date, months: int) -> date:
    """Month arithmetic that clamps to the last day of a shorter month (Doc 14 EMI-07)."""
    month_index = anchor.month - 1 + months
    year = anchor.year + month_index // 12
    month = month_index % 12 + 1
    if month == 12:
        next_month_start = date(year + 1, 1, 1)
    else:
        next_month_start = date(year, month + 1, 1)
    last_day = (next_month_start.toordinal() - 1) - date(year, month, 1).toordinal() + 1
    return date(year, month, min(anchor.day, last_day))


def build_schedule(
    principal: Decimal,
    annual_rate_percent: Decimal,
    tenure_months: int,
    first_due_date: date,
    emi: Decimal | None = None,
) -> tuple[Installment, ...]:
    """Full amortisation schedule. Pure — the caller supplies the first due date."""
    p = D(principal)
    n = int(tenure_months)
    r = monthly_rate(annual_rate_percent)
    instalment = D(emi) if emi is not None else calculate_emi(p, annual_rate_percent, n)

    rows: list[Installment] = []
    balance = p
    for k in range(1, n + 1):
        interest = q2(balance * r)
        if k == n:
            # Final instalment clears the balance exactly, absorbing all rounding residue.
            principal_part = balance
            due = q2(principal_part + interest)
        else:
            principal_part = q2(instalment - interest)
            if principal_part > balance:
                principal_part = balance
            due = instalment
        closing = q2(balance - principal_part)
        rows.append(
            Installment(
                installment_no=k,
                due_date=add_months(first_due_date, k - 1),
                opening_balance=q2(balance),
                principal_due=q2(principal_part),
                interest_due=interest,
                total_due=q2(due),
                closing_balance=closing,
            )
        )
        balance = closing
    return tuple(rows)


def schedule_totals(rows: Sequence[Installment]) -> dict[str, Decimal]:
    return {
        "total_principal": q2(sum((r.principal_due for r in rows), start=ZERO)),
        "total_interest": q2(sum((r.interest_due for r in rows), start=ZERO)),
        "total_payable": q2(sum((r.total_due for r in rows), start=ZERO)),
    }
