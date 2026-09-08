"""Credit-score and loan-application persistence.

Credit scores are immutable: a re-run inserts a new row and flips ``is_latest``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from app.models.credit import (
    CreditScore,
    CreditScoreComponent,
    LoanApplication,
    LoanAssessment,
)
from app.repositories.base import BaseRepository, apply_soft_delete_filter, apply_sort


class CreditScoreRepository(BaseRepository[CreditScore]):
    model = CreditScore

    def latest_for_application(self, application_id: int) -> CreditScore | None:
        stmt = (
            select(CreditScore)
            .options(selectinload(CreditScore.components))
            .where(
                CreditScore.application_id == application_id,
                CreditScore.is_latest.is_(True),
            )
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_for_application(
        self, application_id: int, *, offset: int, limit: int
    ) -> tuple[Sequence[CreditScore], int]:
        stmt = (
            select(CreditScore)
            .options(selectinload(CreditScore.components))
            .where(CreditScore.application_id == application_id)
            .order_by(CreditScore.scored_at.desc(), CreditScore.id.desc())
        )
        return self.paginate(stmt, offset=offset, limit=limit)

    def list_for_applicant(
        self, applicant_id: int, *, offset: int, limit: int
    ) -> tuple[Sequence[CreditScore], int]:
        """Every credit assessment across all of an applicant's applications."""
        stmt = (
            select(CreditScore)
            .options(selectinload(CreditScore.components))
            .join(LoanApplication, LoanApplication.id == CreditScore.application_id)
            .where(LoanApplication.applicant_id == applicant_id)
            .order_by(CreditScore.scored_at.desc(), CreditScore.id.desc())
        )
        total = int(
            self.session.execute(
                select(func.count())
                .select_from(CreditScore)
                .join(LoanApplication, LoanApplication.id == CreditScore.application_id)
                .where(LoanApplication.applicant_id == applicant_id)
            ).scalar_one()
        )
        rows = self.session.execute(stmt.offset(offset).limit(limit)).scalars().all()
        return rows, total

    def supersede_latest(self, application_id: int) -> None:
        self.session.execute(
            update(CreditScore)
            .where(
                CreditScore.application_id == application_id,
                CreditScore.is_latest.is_(True),
            )
            .values(is_latest=False)
        )
        self.session.flush()

    def create(self, **values: Any) -> CreditScore:
        return self.add(CreditScore(**values))

    def add_component(self, **values: Any) -> CreditScoreComponent:
        component = CreditScoreComponent(**values)
        self.session.add(component)
        return component


class LoanApplicationRepository(BaseRepository[LoanApplication]):
    model = LoanApplication

    def get_active(self, application_id: int) -> LoanApplication | None:
        stmt = apply_soft_delete_filter(
            select(LoanApplication).where(LoanApplication.id == application_id),
            LoanApplication,
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def latest_for_applicant(self, applicant_id: int) -> LoanApplication | None:
        stmt = (
            apply_soft_delete_filter(
                select(LoanApplication).where(
                    LoanApplication.applicant_id == applicant_id
                ),
                LoanApplication,
            )
            .order_by(LoanApplication.created_at.desc(), LoanApplication.id.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_for_applicant(self, applicant_id: int) -> Sequence[LoanApplication]:
        stmt = apply_soft_delete_filter(
            select(LoanApplication).where(LoanApplication.applicant_id == applicant_id),
            LoanApplication,
        ).order_by(LoanApplication.created_at.desc())
        return self.session.execute(stmt).scalars().all()

    def next_application_number(self, year: int) -> str:
        prefix = f"LA-{year}-"
        stmt = (
            select(LoanApplication.application_number)
            .where(LoanApplication.application_number.like(f"{prefix}%"))
            .order_by(LoanApplication.application_number.desc())
            .limit(1)
        )
        latest = self.session.execute(stmt).scalar_one_or_none()
        sequence = int(latest.rsplit("-", 1)[1]) + 1 if latest else 1
        return f"{prefix}{sequence:06d}"

    def create(self, **values: Any) -> LoanApplication:
        return self.add(LoanApplication(**values))

    SORTABLE = frozenset({"created_at", "submitted_at", "requested_amount", "status"})

    def list_applications(
        self,
        *,
        offset: int,
        limit: int,
        status: str | None = None,
        applicant_id: int | None = None,
        route_id: int | None = None,
        branch_code: str | None = None,
        created_from: Any = None,
        created_to: Any = None,
        q: str | None = None,
        sort: str | None = "-created_at",
    ) -> tuple[Sequence[LoanApplication], int]:
        """Doc 06 §6.6 list filters. Row scoping by branch is applied by the caller."""
        stmt = apply_soft_delete_filter(select(LoanApplication), LoanApplication)
        if status:
            stmt = stmt.where(LoanApplication.status == status)
        if applicant_id is not None:
            stmt = stmt.where(LoanApplication.applicant_id == applicant_id)
        if route_id is not None:
            stmt = stmt.where(LoanApplication.route_id == route_id)
        if branch_code:
            stmt = stmt.where(LoanApplication.branch_code == branch_code)
        if created_from is not None:
            stmt = stmt.where(LoanApplication.created_at >= created_from)
        if created_to is not None:
            stmt = stmt.where(LoanApplication.created_at <= created_to)
        if q:
            stmt = stmt.where(LoanApplication.application_number.ilike(f"%{q}%"))
        stmt = apply_sort(stmt, LoanApplication, sort, self.SORTABLE)
        return self.paginate(stmt, offset=offset, limit=limit)


class LoanAssessmentRepository(BaseRepository[LoanAssessment]):
    model = LoanAssessment

    def latest_for_application(self, application_id: int) -> LoanAssessment | None:
        stmt = select(LoanAssessment).where(
            LoanAssessment.application_id == application_id,
            LoanAssessment.is_latest.is_(True),
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def create(self, **values: Any) -> LoanAssessment:
        return self.add(LoanAssessment(**values))
