"""Portfolio endpoints — Doc 06 §6.9 (read-side slice)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.schemas.common import ERROR_RESPONSES, Page
from app.api.schemas.config import LoanSummaryOut, PortfolioSummaryOut
from app.core.dependencies import (
    MonitoringServiceDep,
    PaginationParams,
    PortfolioServiceDep,
)
from app.core.permissions import CurrentUser, require_permission

router = APIRouter(prefix="/portfolio", tags=["Portfolio"], responses=ERROR_RESPONSES)


@router.get(
    "/summary",
    response_model=PortfolioSummaryOut,
    summary="Portfolio KPIs",
    description=(
        "The dashboard header: exposure, arrears, PAR30, grade distribution and DPD "
        "buckets. Every figure is aggregated in SQL, so the response time does not grow "
        "with the size of the book."
    ),
)
def portfolio_summary(
    service: PortfolioServiceDep,
    _user: CurrentUser = Depends(require_permission("portfolio:read")),
) -> PortfolioSummaryOut:
    summary = service.summary()
    return PortfolioSummaryOut(
        kpis=summary.kpis,
        by_risk_grade=list(summary.by_risk_grade),
        dpd_buckets=list(summary.dpd_buckets),
        open_alerts=summary.open_alerts,
    )


@router.get(
    "",
    response_model=Page[LoanSummaryOut],
    summary="List the loan book",
    description="The risk table. Ordered by days past due, worst first.",
)
def list_loans(
    pagination: PaginationParams,
    service: PortfolioServiceDep,
    _user: CurrentUser = Depends(require_permission("portfolio:read")),
    risk_status: Annotated[str | None, Query(description="GREEN, YELLOW or RED")] = None,
    risk_grade: str | None = None,
    classification: str | None = None,
    min_dpd: Annotated[int | None, Query(ge=0)] = None,
    assigned_officer_id: int | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = "ACTIVE",
) -> Page[LoanSummaryOut]:
    rows, total = service.list_loans(
        offset=pagination.offset,
        limit=pagination.limit,
        risk_status=risk_status,
        risk_grade=risk_grade,
        classification=classification,
        min_dpd=min_dpd,
        assigned_officer_id=assigned_officer_id,
        status=status_filter,
    )
    return Page.build(
        [LoanSummaryOut.model_validate(r) for r in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )

# ---------------------------------------------------------------------------
# Dashboard — PRD §1.11, Doc 06 §6.9
# ---------------------------------------------------------------------------
dashboard_router = APIRouter(
    prefix="/dashboard", tags=["Portfolio"], responses=ERROR_RESPONSES
)


@dashboard_router.get(
    "/summary",
    summary="Role dashboard KPIs and distributions",
    description=(
        "The ten KPI cards from PRD 1.11 plus the distribution data behind the portfolio, "
        "route-class, customer-grade and DPD-bucket charts. Every figure is aggregated in "
        "SQL, so the response time does not grow with the size of the book. The `as_of` "
        "date is returned so a client can caption how fresh the numbers are."
    ),
)
def dashboard_summary(
    service: MonitoringServiceDep,
    _user: CurrentUser = Depends(require_permission("portfolio:read")),
) -> dict:
    result = service.dashboard_summary()
    return {
        "as_of": result["as_of"].isoformat(),
        "kpis": {k: _plain(v) for k, v in result["kpis"].items()},
        "portfolio_by_grade": [
            {k: _plain(v) for k, v in row.items()} for row in result["portfolio_by_grade"]
        ],
        "dpd_buckets": [
            {k: _plain(v) for k, v in row.items()} for row in result["dpd_buckets"]
        ],
        "route_class_distribution": list(result["route_class_distribution"]),
        "customer_grade_distribution": list(result["customer_grade_distribution"]),
        "par30": _plain(result["par30"]),
    }


def _plain(value: object) -> object:
    from decimal import Decimal

    return float(value) if isinstance(value, Decimal) else value
