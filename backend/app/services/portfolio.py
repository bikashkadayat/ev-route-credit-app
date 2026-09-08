"""PortfolioService — read-side aggregates for the portfolio dashboard (Doc 03 Screen 18).

Pure reads. The aggregates are computed in SQL by the repository rather than by looping
over loans in Python, so the endpoint stays flat as the book grows.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.portfolio import Loan
from app.repositories.alert import LoanRepository
from app.repositories.portfolio import PortfolioRepository


@dataclass(frozen=True, slots=True)
class PortfolioSummary:
    kpis: dict[str, Any]
    by_risk_grade: Sequence[dict[str, Any]]
    dpd_buckets: Sequence[dict[str, Any]]
    open_alerts: dict[str, int]


class PortfolioService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.portfolio = PortfolioRepository(session)
        self.loans = LoanRepository(session)

    def summary(self) -> PortfolioSummary:
        return PortfolioSummary(
            kpis=self.portfolio.summary(),
            by_risk_grade=self.portfolio.by_risk_grade(),
            dpd_buckets=self.portfolio.dpd_buckets(),
            open_alerts=self.portfolio.open_alert_counts(),
        )

    def list_loans(self, **filters: Any) -> tuple[Sequence[Loan], int]:
        return self.loans.list_portfolio(**filters)
