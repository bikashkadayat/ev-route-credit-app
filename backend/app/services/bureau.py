"""BureauService — credit bureau enquiry — FR-3.5, Doc 06 §6.4.

The provider is resolved from configuration (`CIB_PROVIDER`), so switching from the mock to
a live bureau is an environment variable and nothing else. This service owns everything
around that call: the validity-window cache, persistence of the raw response, and the audit
entry. The adapter owns the call itself and knows nothing about the database.

Caching matters commercially as well as technically. A bureau enquiry is billed per call
(Doc 01 R-8), so a report inside its validity window is reused rather than re-purchased —
and the reuse is visible in the response so an officer knows what they are looking at.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import NotFoundError, ProviderUnavailableError
from app.integrations.cib.base import CIBReport, CIBUnavailable
from app.integrations.registry import build_cib_provider
from app.models.credit import Applicant, CreditBureauReport
from app.repositories.customer import BureauReportRepository, CustomerRepository
from app.services.audit import AuditService

BUREAU_REPORT_FETCHED = "BUREAU_REPORT_FETCHED"


@dataclass(frozen=True, slots=True)
class BureauFetchResult:
    report: CreditBureauReport
    is_cached: bool
    provider: str


class BureauService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.customers = CustomerRepository(session)
        self.reports = BureauReportRepository(session)
        self.audit = AuditService(session)

    def latest_for(self, applicant_id: int, as_of: date) -> CreditBureauReport | None:
        return self.reports.latest_valid(applicant_id, as_of)

    def fetch(
        self,
        applicant_id: int,
        *,
        fetched_by: int,
        force_refresh: bool = False,
        as_of: date | None = None,
    ) -> BureauFetchResult:
        """FR-3.5 — pull a report, persisting the raw response.

        A valid cached report is returned untouched unless ``force_refresh`` is set: the
        enquiry costs money, and re-pulling inside the validity window tells an officer
        nothing new.
        """
        business_date = as_of or datetime.now(UTC).date()
        applicant = self.customers.get_active(applicant_id)
        if applicant is None:
            raise NotFoundError("applicant", applicant_id)

        if not force_refresh:
            cached = self.reports.latest_valid(applicant.id, business_date)
            if cached is not None:
                return BureauFetchResult(cached, is_cached=True, provider=cached.provider)

        provider = build_cib_provider(settings.cib_provider)
        try:
            report = asyncio.run(
                provider.fetch_report(
                    id_type=str(applicant.id_type),
                    id_number=self._identifier(applicant),
                    full_name=applicant.full_name,
                    as_of=business_date,
                )
            )
        except CIBUnavailable as exc:
            raise ProviderUnavailableError(
                f"The credit bureau is not reachable: {exc}",
                details={"provider": provider.name},
            ) from exc

        # The bureau's own reference identifies the enquiry, and it is globally unique in
        # the schema. If the provider returns one we already hold, it is the *same*
        # enquiry — the mock is deterministic within a business day, and a real bureau
        # that echoed a reference would mean the same thing. Returning the stored row is
        # correct; inserting a second would be inventing an enquiry that never happened.
        existing = self.reports.by_enquiry_id(report.enquiry_id)
        if existing is not None:
            return BureauFetchResult(existing, is_cached=True, provider=report.provider)

        row = self._persist(applicant, report, fetched_by)

        self.audit.record(
            action=BUREAU_REPORT_FETCHED,
            user_id=fetched_by,
            entity_type="credit_bureau_report",
            entity_id=row.id,
            after={
                "provider": report.provider,
                "enquiry_id": report.enquiry_id,
                # The score and the blacklist flag are the material facts; the full payload
                # is stored on the row but is deliberately not copied into the audit entry.
                "bureau_score": report.score,
                "is_blacklisted": report.is_blacklisted,
            },
        )
        return BureauFetchResult(row, is_cached=False, provider=report.provider)

    @staticmethod
    def _identifier(applicant: Applicant) -> str:
        """The stored identifier, decoded for the enquiry.

        This is the one place the raw value is needed — the bureau matches on it. It never
        leaves this call: the response carries the masked form, and the audit entry carries
        neither.
        """
        raw = applicant.id_number_enc
        return raw.decode("utf-8", errors="ignore") if raw else ""

    def _persist(
        self, applicant: Applicant, report: CIBReport, fetched_by: int
    ) -> CreditBureauReport:
        return self.reports.create(
            applicant_id=applicant.id,
            provider=report.provider,
            enquiry_id=report.enquiry_id,
            enquiry_date=report.enquiry_date,
            valid_until=report.valid_until,
            bureau_score=report.score,
            bureau_grade=report.grade,
            credit_history_months=report.credit_history_months,
            active_loan_count=report.active_loan_count,
            total_outstanding=Decimal(report.total_outstanding),
            total_monthly_emi=Decimal(report.total_monthly_emi),
            previous_default_count=report.previous_default_count,
            current_overdue_amount=Decimal(report.current_overdue_amount),
            max_dpd_last_24m=report.max_dpd_last_24m,
            is_blacklisted=report.is_blacklisted,
            enquiries_last_6m=report.enquiries_last_6m,
            repayment_history=[dict(h) for h in report.repayment_history],
            raw_response=self._raw(report),
            is_manual_entry=report.is_manual_entry,
            fetched_by=fetched_by,
        )

    @staticmethod
    def _raw(report: CIBReport) -> dict[str, Any]:
        """FR-3.5 requires the raw response to be persisted.

        The masked identifier is what the provider returned; the unmasked one is never in
        the payload, so storing this verbatim does not widen the blast radius of a
        database compromise beyond what the report itself contains.
        """
        payload = dict(report.raw)
        payload.setdefault("enquiry_id", report.enquiry_id)
        payload.setdefault("id_number_masked", report.id_number_masked)
        payload.setdefault("obligations", [
            {
                "lender_name": o.lender_name,
                "loan_type": o.loan_type,
                "original_amount": float(o.original_amount),
                "outstanding_amount": float(o.outstanding_amount),
                "monthly_emi": float(o.monthly_emi),
                "is_overdue": o.is_overdue,
                "days_past_due": o.days_past_due,
            }
            for o in report.obligations
        ])
        return payload
