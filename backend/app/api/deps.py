"""Re-exports of the shared FastAPI dependencies.

The definitions live in ``app.core.dependencies`` so that services and jobs can use them
without importing anything from the API package; this module is the convenience import
routers use.
"""

from app.core.dependencies import (
    AuthenticatedUser,
    DbSession,
    Pagination,
    PaginationParams,
    RequestId,
    get_alert_service,
    get_config_service,
    get_credit_service,
    get_customer_service,
    get_portfolio_service,
    get_route_assessment_service,
    pagination_params,
)
from app.core.permissions import CurrentUser, require_any_permission, require_permission

__all__ = [
    "AuthenticatedUser", "CurrentUser", "DbSession", "Pagination", "PaginationParams",
    "RequestId", "get_alert_service", "get_config_service", "get_credit_service",
    "get_customer_service", "get_portfolio_service", "get_route_assessment_service",
    "pagination_params", "require_any_permission", "require_permission",
]
