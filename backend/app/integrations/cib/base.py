"""Credit bureau adapter contract — Doc 04 §4.11.

One interface, two implementations. Switching from mock to live is a single environment
variable (`CIB_PROVIDER`); no service or engine code changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

IdType = Literal["CITIZENSHIP", "PASSPORT", "PAN", "COMPANY_REG"]
RepaymentStatus = Literal["ON_TIME", "LATE_1_30", "LATE_31_60", "LATE_60_PLUS", "NO_DATA"]

SCORE_MIN = 300
SCORE_MAX = 900


class CIBUnavailable(RuntimeError):
    """Provider unreachable or circuit open.

    The service layer degrades to manual bureau entry rather than failing the
    application (Doc 04 §4.14). It never becomes a 500.
    """


@dataclass(frozen=True, slots=True)
class Obligation:
    lender_name: str
    loan_type: str
    original_amount: Decimal
    outstanding_amount: Decimal
    monthly_emi: Decimal
    remaining_tenure_months: int | None = None
    is_overdue: bool = False
    days_past_due: int = 0


@dataclass(frozen=True, slots=True)
class CIBReport:
    provider: str
    enquiry_id: str
    enquiry_date: date
    valid_until: date
    id_type: IdType
    id_number_masked: str
    full_name: str

    score: int | None
    grade: str | None
    credit_history_months: int
    active_loan_count: int
    total_outstanding: Decimal
    total_monthly_emi: Decimal
    previous_default_count: int
    current_overdue_amount: Decimal
    max_dpd_last_24m: int
    is_blacklisted: bool
    enquiries_last_6m: int
    repayment_history: tuple[dict[str, str], ...] = ()
    obligations: tuple[Obligation, ...] = ()
    is_manual_entry: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.score is not None and not (SCORE_MIN <= self.score <= SCORE_MAX):
            raise ValueError(
                f"bureau score {self.score} outside the valid range "
                f"{SCORE_MIN}-{SCORE_MAX}"
            )
        if self.valid_until < self.enquiry_date:
            raise ValueError("valid_until precedes enquiry_date")


class CIBProvider(ABC):
    """Every bureau implementation satisfies this and nothing more."""

    name: str

    @abstractmethod
    async def fetch_report(
        self, *, id_type: IdType, id_number: str, full_name: str, as_of: date
    ) -> CIBReport:
        """Return a bureau report, or raise CIBUnavailable."""

    @staticmethod
    def mask(id_number: str) -> str:
        """Doc 09 §9.7.4 — identifiers are masked by default (27-01-70-****).

        Segment-aware: a Nepali citizenship number is district-ward-year-serial, and it is
        the serial that identifies the person, so the whole final segment is replaced with
        a fixed-width mask. A fixed width also avoids leaking the serial's length.
        """
        raw = id_number.strip()
        if "-" in raw:
            head, _, _tail = raw.rpartition("-")
            return f"{head}-****"
        if len(raw) <= 4:
            return "*" * len(raw)
        return raw[: len(raw) - 4] + "****"
