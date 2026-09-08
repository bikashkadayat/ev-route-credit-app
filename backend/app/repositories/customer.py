"""Applicant, financial-profile, obligation and bureau-report persistence."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.models.credit import (
    Applicant,
    ApplicantFinancial,
    CreditBureauReport,
    ExistingObligation,
)
from app.repositories.base import BaseRepository, apply_soft_delete_filter, apply_sort

SORTABLE = frozenset({"full_name", "applicant_code", "created_at", "district"})


class CustomerRepository(BaseRepository[Applicant]):
    model = Applicant

    def get_active(self, applicant_id: int) -> Applicant | None:
        stmt = apply_soft_delete_filter(
            select(Applicant).where(Applicant.id == applicant_id), Applicant
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def get_with_relations(self, applicant_id: int) -> Applicant | None:
        stmt = (
            apply_soft_delete_filter(
                select(Applicant).where(Applicant.id == applicant_id), Applicant
            )
            .options(
                selectinload(Applicant.financials),
                selectinload(Applicant.obligations),
                selectinload(Applicant.bureau_reports),
            )
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def get_by_code(self, applicant_code: str) -> Applicant | None:
        stmt = apply_soft_delete_filter(
            select(Applicant).where(Applicant.applicant_code == applicant_code), Applicant
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def find_by_id_hash(self, id_number_hash: str) -> Applicant | None:
        """Duplicate detection without decrypting anything (Doc 05 §5.3.6)."""
        stmt = apply_soft_delete_filter(
            select(Applicant).where(Applicant.id_number_hash == id_number_hash), Applicant
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def next_applicant_code(self, year: int) -> str:
        prefix = f"APP-{year}-"
        stmt = (
            select(Applicant.applicant_code)
            .where(Applicant.applicant_code.like(f"{prefix}%"))
            .order_by(Applicant.applicant_code.desc())
            .limit(1)
        )
        latest = self.session.execute(stmt).scalar_one_or_none()
        sequence = int(latest.rsplit("-", 1)[1]) + 1 if latest else 1
        return f"{prefix}{sequence:05d}"

    def list_customers(
        self,
        *,
        offset: int,
        limit: int,
        q: str | None = None,
        applicant_type: str | None = None,
        status: str | None = None,
        province: str | None = None,
        district: str | None = None,
        sort: str | None = "-created_at",
    ) -> tuple[Sequence[Applicant], int]:
        stmt = apply_soft_delete_filter(select(Applicant), Applicant)
        if q:
            pattern = f"%{q.lower()}%"
            stmt = stmt.where(
                Applicant.full_name.ilike(pattern)
                | Applicant.applicant_code.ilike(pattern)
                | Applicant.phone.ilike(pattern)
            )
        if applicant_type:
            stmt = stmt.where(Applicant.applicant_type == applicant_type)
        if status:
            stmt = stmt.where(Applicant.status == status)
        if province:
            stmt = stmt.where(Applicant.province == province)
        if district:
            stmt = stmt.where(Applicant.district == district)

        stmt = apply_sort(stmt, Applicant, sort, SORTABLE)
        return self.paginate(stmt, offset=offset, limit=limit)

    def create(self, **values: Any) -> Applicant:
        return self.add(Applicant(**values))

    def update(self, applicant: Applicant, **values: Any) -> Applicant:
        for key, value in values.items():
            setattr(applicant, key, value)
        self.session.flush()
        self.session.refresh(applicant)
        return applicant


class FinancialProfileRepository(BaseRepository[ApplicantFinancial]):
    model = ApplicantFinancial

    def current_for(self, applicant_id: int) -> ApplicantFinancial | None:
        stmt = select(ApplicantFinancial).where(
            ApplicantFinancial.applicant_id == applicant_id,
            ApplicantFinancial.is_current.is_(True),
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def next_version_no(self, applicant_id: int) -> int:
        stmt = (
            select(ApplicantFinancial.version_no)
            .where(ApplicantFinancial.applicant_id == applicant_id)
            .order_by(ApplicantFinancial.version_no.desc())
            .limit(1)
        )
        latest = self.session.execute(stmt).scalar_one_or_none()
        return (latest or 0) + 1

    def supersede_current(self, applicant_id: int) -> None:
        """Only one row may be ``is_current`` per applicant (DB-enforced)."""
        self.session.execute(
            update(ApplicantFinancial)
            .where(
                ApplicantFinancial.applicant_id == applicant_id,
                ApplicantFinancial.is_current.is_(True),
            )
            .values(is_current=False)
        )
        self.session.flush()

    def create(self, **values: Any) -> ApplicantFinancial:
        return self.add(ApplicantFinancial(**values))


class ObligationRepository(BaseRepository[ExistingObligation]):
    model = ExistingObligation

    def for_applicant(self, applicant_id: int) -> Sequence[ExistingObligation]:
        stmt = select(ExistingObligation).where(
            ExistingObligation.applicant_id == applicant_id
        )
        return self.session.execute(stmt).scalars().all()

    def replace_all(
        self, applicant_id: int, rows: Sequence[dict[str, Any]]
    ) -> Sequence[ExistingObligation]:
        for existing in self.for_applicant(applicant_id):
            self.session.delete(existing)
        self.session.flush()
        created = [
            ExistingObligation(applicant_id=applicant_id, **row) for row in rows
        ]
        self.session.add_all(created)
        self.session.flush()
        return created


class BureauReportRepository(BaseRepository[CreditBureauReport]):
    model = CreditBureauReport

    def latest_valid(
        self, applicant_id: int, as_of: date
    ) -> CreditBureauReport | None:
        """Cache lookup — Doc 06 §6.4. Bureau enquiries are billed per call."""
        stmt = (
            select(CreditBureauReport)
            .where(
                CreditBureauReport.applicant_id == applicant_id,
                CreditBureauReport.valid_until >= as_of,
            )
            .order_by(CreditBureauReport.enquiry_date.desc(), CreditBureauReport.id.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def latest_for(self, applicant_id: int) -> CreditBureauReport | None:
        stmt = (
            select(CreditBureauReport)
            .where(CreditBureauReport.applicant_id == applicant_id)
            .order_by(CreditBureauReport.enquiry_date.desc(), CreditBureauReport.id.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def by_enquiry_id(self, enquiry_id: str) -> CreditBureauReport | None:
        """``uq_cib_enquiry`` makes the bureau's own reference globally unique."""
        stmt = select(CreditBureauReport).where(
            CreditBureauReport.enquiry_id == enquiry_id
        ).limit(1)
        return self.session.execute(stmt).scalar_one_or_none()

    def create(self, **values: Any) -> CreditBureauReport:
        return self.add(CreditBureauReport(**values))
