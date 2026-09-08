"""Shared FastAPI dependencies — pagination, request context and service wiring.

Services are constructed here rather than inside routers, so a router never knows which
repositories a service needs and swapping an implementation touches one file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.permissions import CurrentUser, get_current_user
from app.services.admin import AdminService
from app.services.alert import AlertService
from app.services.application import ApplicationService
from app.services.audit import AuditService
from app.services.auth import AuthService
from app.services.bureau import BureauService
from app.services.catalog import CatalogService
from app.services.config import ConfigService
from app.services.credit import CreditService
from app.services.customer_assessment import CustomerService
from app.services.loan import LoanService
from app.services.monitoring import MonitoringService
from app.services.portfolio import PortfolioService
from app.services.route_assessment import RouteAssessmentService
from app.services.scoring import ScoringService
from app.services.servicing_jobs import ServicingJobService

DbSession = Annotated[Session, Depends(get_db)]
AuthenticatedUser = Annotated[CurrentUser, Depends(get_current_user)]


@dataclass(frozen=True, slots=True)
class Pagination:
    """Doc 06 §6.1.2 — page/page_size with a hard server-side cap."""

    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


def pagination_params(
    page: Annotated[int, Query(ge=1, description="1-based page number")] = 1,
    page_size: Annotated[
        int | None,
        Query(ge=1, le=100, description="Rows per page (server maximum 100)"),
    ] = None,
) -> Pagination:
    resolved = page_size or settings.default_page_size
    return Pagination(page=page, page_size=min(resolved, settings.max_page_size))


PaginationParams = Annotated[Pagination, Depends(pagination_params)]


def get_request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


RequestId = Annotated[str | None, Depends(get_request_id)]


# ---------------------------------------------------------------------------
# Service factories
#
# Each returns an ``Annotated`` alias as well as the factory, so a router declares the
# service it needs by type — ``service: RouteAssessmentServiceDep`` — and both the reader
# and the type checker can see which service a handler talks to.
# ---------------------------------------------------------------------------
def get_auth_service(db: DbSession) -> AuthService:
    return AuthService(db)


def get_audit_service(db: DbSession) -> AuditService:
    return AuditService(db)


def get_config_service(db: DbSession) -> ConfigService:
    return ConfigService(db)


def get_route_assessment_service(db: DbSession) -> RouteAssessmentService:
    return RouteAssessmentService(db)


def get_credit_service(db: DbSession) -> CreditService:
    return CreditService(db)


def get_customer_service(db: DbSession) -> CustomerService:
    return CustomerService(db)


def get_alert_service(db: DbSession) -> AlertService:
    return AlertService(db)


def get_portfolio_service(db: DbSession) -> PortfolioService:
    return PortfolioService(db)


def get_application_service(db: DbSession) -> ApplicationService:
    return ApplicationService(db)


def get_loan_service(db: DbSession) -> LoanService:
    return LoanService(db)


def get_catalog_service(db: DbSession) -> CatalogService:
    return CatalogService(db)


def get_bureau_service(db: DbSession) -> BureauService:
    return BureauService(db)


def get_scoring_service(db: DbSession) -> ScoringService:
    return ScoringService(db)


def get_monitoring_service(db: DbSession) -> MonitoringService:
    return MonitoringService(db)


def get_admin_service(db: DbSession) -> AdminService:
    return AdminService(db)


def get_servicing_job_service(db: DbSession) -> ServicingJobService:
    return ServicingJobService(db)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
AuditServiceDep = Annotated[AuditService, Depends(get_audit_service)]
ConfigServiceDep = Annotated[ConfigService, Depends(get_config_service)]
RouteServiceDep = Annotated[RouteAssessmentService, Depends(get_route_assessment_service)]
CreditServiceDep = Annotated[CreditService, Depends(get_credit_service)]
CustomerServiceDep = Annotated[CustomerService, Depends(get_customer_service)]
AlertServiceDep = Annotated[AlertService, Depends(get_alert_service)]
PortfolioServiceDep = Annotated[PortfolioService, Depends(get_portfolio_service)]
ApplicationServiceDep = Annotated[ApplicationService, Depends(get_application_service)]
LoanServiceDep = Annotated[LoanService, Depends(get_loan_service)]
CatalogServiceDep = Annotated[CatalogService, Depends(get_catalog_service)]
BureauServiceDep = Annotated[BureauService, Depends(get_bureau_service)]
ScoringServiceDep = Annotated[ScoringService, Depends(get_scoring_service)]
MonitoringServiceDep = Annotated[MonitoringService, Depends(get_monitoring_service)]
AdminServiceDep = Annotated[AdminService, Depends(get_admin_service)]
ServicingJobServiceDep = Annotated[
    ServicingJobService, Depends(get_servicing_job_service)
]
