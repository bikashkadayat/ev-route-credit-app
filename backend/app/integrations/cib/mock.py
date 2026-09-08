"""Deterministic mock credit bureau — Doc 04 §4.11, Doc 16 §16.5.

Two guarantees that matter more than realism:

1. **Determinism.** The same ID always returns the same report, so a demo run in November
   shows what it showed in September and the E2E suite can assert on specific numbers.
   Derived from a SHA-256 of the identifier — no ``random`` anywhere.
2. **Coverage.** The seeded profiles span the whole decision surface: a blacklisted
   applicant, a defaulted one, a thin file, and a pristine record, so every knock-out and
   every decision branch is reachable in a demo.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.integrations.cib.base import (
    CIBProvider,
    CIBReport,
    CIBUnavailable,
    IdType,
    Obligation,
)

DEFAULT_VALIDITY_DAYS = 90

#: Applicants from the demo pack, keyed by national identifier (Doc 16 §16.5).
SEEDED_PROFILES: dict[str, dict[str, Any]] = {
    # id_number: (score, history_months, defaults, max_dpd, enquiries, blacklisted, overdue)
    "27-01-70-04521": {"score": 742, "history": 54, "defaults": 0, "max_dpd": 12,
                       "enquiries": 1, "blacklisted": False, "overdue": "0",
                       "obligations": [
                           ("Nabil Bank", "PERSONAL", "500000", "385000", "12400", 34)
                       ]},
    "27-02-71-03318": {"score": 688, "history": 38, "defaults": 0, "max_dpd": 22,
                       "enquiries": 2, "blacklisted": False, "overdue": "0",
                       "obligations": [
                           ("Global IME", "PERSONAL", "300000", "196000", "6200", 26)
                       ]},
    "PAN-301245678": {"score": 771, "history": 96, "defaults": 0, "max_dpd": 5,
                      "enquiries": 1, "blacklisted": False, "overdue": "0",
                      "obligations": [
                          ("NIC Asia", "BUSINESS", "8000000", "5100000", "210000", 41)
                      ]},
    "27-03-68-01192": {"score": 795, "history": 72, "defaults": 0, "max_dpd": 0,
                       "enquiries": 0, "blacklisted": False, "overdue": "0", "obligations": []},
    "27-04-74-08876": {"score": 614, "history": 18, "defaults": 0, "max_dpd": 38,
                       "enquiries": 3, "blacklisted": False, "overdue": "0",
                       "obligations": [
                           ("Muktinath Bikas", "PERSONAL", "200000", "142000", "4800", 30)
                       ]},
    "27-05-72-05540": {"score": 651, "history": 44, "defaults": 1, "max_dpd": 47,
                       "enquiries": 2, "blacklisted": False, "overdue": "0",
                       "obligations": [
                           ("Prabhu Bank", "AUTO", "900000", "612000", "21500", 32)
                       ]},
    # thin file — no bureau history at all
    "27-06-77-00214": {"score": None, "history": 0, "defaults": 0, "max_dpd": 0,
                       "enquiries": 0, "blacklisted": False, "overdue": "0", "obligations": []},
    # prior default with live arrears -> KO_ACTIVE_DEFAULT
    "27-07-73-06691": {"score": 572, "history": 29, "defaults": 1, "max_dpd": 92,
                       "enquiries": 4, "blacklisted": False, "overdue": "38000",
                       "obligations": [
                           ("Sanima Bank", "PERSONAL", "400000", "268000", "9500", 22)
                       ]},
    # blacklisted -> KO_BLACKLIST
    "27-08-70-02237": {"score": 598, "history": 61, "defaults": 2, "max_dpd": 118,
                       "enquiries": 5, "blacklisted": True, "overdue": "126000",
                       "obligations": [
                           ("Everest Bank", "AUTO", "1200000", "845000", "14600", 44)
                       ]},
    "27-09-69-04407": {"score": 812, "history": 88, "defaults": 0, "max_dpd": 0,
                       "enquiries": 1, "blacklisted": False, "overdue": "0",
                       "obligations": [
                           ("Himalayan Bank", "PERSONAL", "250000", "96000", "8000", 12)
                       ]},
    "27-10-71-07723": {"score": 733, "history": 57, "defaults": 0, "max_dpd": 15,
                       "enquiries": 1, "blacklisted": False, "overdue": "0",
                       "obligations": [
                           ("Kumari Bank", "AUTO", "700000", "451000", "18200", 26)
                       ]},
}

_GRADE_BANDS: tuple[tuple[int, str], ...] = (
    (800, "A"), (740, "B"), (680, "C"), (620, "D"), (0, "E"),
)


def _grade_for(score: int | None) -> str | None:
    if score is None:
        return None
    for floor, grade in _GRADE_BANDS:
        if score >= floor:
            return grade
    return "E"  # pragma: no cover - the final band starts at zero


class DeterministicStream:
    """A reproducible pseudo-random stream seeded by a string.

    Uses SHA-256 counter mode rather than ``random`` so that behaviour cannot depend on
    global interpreter state and the purity constraints stay honest.
    """

    def __init__(self, seed: str) -> None:
        self._seed = seed.encode("utf-8")
        self._counter = 0

    def _next_int(self) -> int:
        digest = hashlib.sha256(self._seed + self._counter.to_bytes(8, "big")).digest()
        self._counter += 1
        return int.from_bytes(digest[:8], "big")

    def integer(self, low: int, high: int) -> int:
        """Inclusive on both ends."""
        span = high - low + 1
        return low + self._next_int() % span

    def choice(self, options: list[Any]) -> Any:
        return options[self._next_int() % len(options)]

    def fraction(self) -> float:
        return (self._next_int() % 1_000_000) / 1_000_000


class MockCIBProvider(CIBProvider):
    """Deterministic bureau. Never raises unless explicitly configured to."""

    name = "MOCK_CIB"

    def __init__(
        self,
        *,
        validity_days: int = DEFAULT_VALIDITY_DAYS,
        fail_for_ids: frozenset[str] = frozenset(),
    ) -> None:
        self.validity_days = validity_days
        # Lets tests and the demo exercise the degradation path deliberately.
        self.fail_for_ids = fail_for_ids

    async def fetch_report(
        self, *, id_type: IdType, id_number: str, full_name: str, as_of: date
    ) -> CIBReport:
        if id_number in self.fail_for_ids:
            raise CIBUnavailable(f"mock provider configured to fail for {self.mask(id_number)}")
        return self.build_report(
            id_type=id_type, id_number=id_number, full_name=full_name, as_of=as_of
        )

    # Synchronous core so tests do not need an event loop.
    def build_report(
        self, *, id_type: IdType, id_number: str, full_name: str, as_of: date
    ) -> CIBReport:
        profile = SEEDED_PROFILES.get(id_number) or self._derive_profile(id_number)
        stream = DeterministicStream(f"cib:{id_number}")

        obligations = tuple(
            Obligation(
                lender_name=lender, loan_type=kind,
                original_amount=Decimal(original), outstanding_amount=Decimal(outstanding),
                monthly_emi=Decimal(emi), remaining_tenure_months=tenure,
                is_overdue=Decimal(profile["overdue"]) > 0,
                days_past_due=profile["max_dpd"] if Decimal(profile["overdue"]) > 0 else 0,
            )
            for lender, kind, original, outstanding, emi, tenure in profile["obligations"]
        )

        total_outstanding = sum((o.outstanding_amount for o in obligations), Decimal("0"))
        total_emi = sum((o.monthly_emi for o in obligations), Decimal("0"))

        return CIBReport(
            provider=self.name,
            enquiry_id=self._enquiry_id(id_number, as_of),
            enquiry_date=as_of,
            valid_until=as_of + timedelta(days=self.validity_days),
            id_type=id_type,
            id_number_masked=self.mask(id_number),
            full_name=full_name,
            score=profile["score"],
            grade=_grade_for(profile["score"]),
            credit_history_months=profile["history"],
            active_loan_count=len(obligations),
            total_outstanding=total_outstanding,
            total_monthly_emi=total_emi,
            previous_default_count=profile["defaults"],
            current_overdue_amount=Decimal(profile["overdue"]),
            max_dpd_last_24m=profile["max_dpd"],
            is_blacklisted=profile["blacklisted"],
            enquiries_last_6m=profile["enquiries"],
            repayment_history=self._repayment_history(profile, stream, as_of),
            obligations=obligations,
            raw={"provider": self.name, "seeded": id_number in SEEDED_PROFILES},
        )

    # -- derivation for identifiers outside the demo pack --------------------
    def _derive_profile(self, id_number: str) -> dict[str, Any]:
        stream = DeterministicStream(f"profile:{id_number}")
        score = stream.integer(560, 860)
        history = stream.integer(6, 96)
        defaults = 0 if score > 660 else stream.integer(0, 1)
        max_dpd = 0 if score > 780 else stream.integer(0, 60)
        return {
            "score": score, "history": history, "defaults": defaults, "max_dpd": max_dpd,
            "enquiries": stream.integer(0, 4), "blacklisted": False, "overdue": "0",
            "obligations": [],
        }

    def _enquiry_id(self, id_number: str, as_of: date) -> str:
        digest = hashlib.sha256(f"{id_number}:{as_of.isoformat()}".encode()).hexdigest()
        return f"CIB-MOCK-{as_of.year}-{int(digest[:8], 16) % 10_000_000:07d}"

    def _repayment_history(
        self, profile: dict[str, Any], stream: DeterministicStream, as_of: date
    ) -> tuple[dict[str, str], ...]:
        """24 months, worst statuses consistent with max_dpd."""
        if profile["history"] == 0:
            return ()
        months = min(24, profile["history"])
        worst = profile["max_dpd"]
        history: list[dict[str, str]] = []
        # place the single worst month deterministically, the rest on-time or mildly late
        worst_index = stream.integer(0, months - 1) if months > 1 else 0
        for i in range(months):
            year = as_of.year - ((as_of.month - 1 - i) < 0)
            month = (as_of.month - 1 - i) % 12 + 1
            if i == worst_index and worst > 60:
                status = "LATE_60_PLUS"
            elif i == worst_index and worst > 30:
                status = "LATE_31_60"
            elif i == worst_index and worst > 0:
                status = "LATE_1_30"
            else:
                status = "ON_TIME" if stream.fraction() > 0.08 else "LATE_1_30"
            history.append({"month": f"{year:04d}-{month:02d}", "status": status})
        return tuple(history)
