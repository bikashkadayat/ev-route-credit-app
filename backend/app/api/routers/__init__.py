"""Router registry — Doc 06 §6.14.

One place lists every router, so ``main`` never grows a wall of ``include_router`` calls
and the tag ordering in the OpenAPI document is deliberate rather than accidental.
"""

from fastapi import APIRouter

from app.api.routers import (
    admin,
    alerts,
    applications,
    auth,
    catalog,
    config,
    customers,
    health,
    loans,
    portfolio,
    routes,
)

#: Mounted at the root, outside the versioned prefix, so probes survive an API version bump.
unversioned_routers: tuple[APIRouter, ...] = (health.router,)

#: Mounted under settings.api_v1_prefix.
v1_routers: tuple[APIRouter, ...] = (
    auth.router,
    routes.router,
    customers.applicants_router,
    catalog.router,
    catalog.scoring_router,
    customers.legacy_router,
    applications.router,
    loans.router,
    loans.jobs_router,
    admin.router,
    alerts.router,
    config.router,
    portfolio.router,
    portfolio.dashboard_router,
)

#: Tag order and descriptions shown in the OpenAPI document.
OPENAPI_TAGS: list[dict[str, str]] = [
    {"name": "Health", "description": "Liveness and readiness probes. Unauthenticated."},
    {
        "name": "Authentication",
        "description": (
            "Sign in, rotate and revoke sessions. Refresh tokens rotate on every use; "
            "presenting a used one revokes the whole family."
        ),
    },
    {
        "name": "Routes",
        "description": (
            "Operating corridors and their assessments. A route is scored once and reused "
            "across every application that runs on it."
        ),
    },
    {"name": "Customers", "description": "Applicants, financial profiles and obligations."},
    {
        "name": "Catalogue",
        "description": (
            "Vehicle models and financed units. A model row carries the economics inputs "
            "the engine uses, so they are not retyped per application."
        ),
    },
    {
        "name": "Scoring",
        "description": (
            "Stateless calculators for what-if analysis: same engines, same active "
            "configuration, nothing persisted."
        ),
    },
    {
        "name": "Applications",
        "description": (
            "The underwriting workflow: draft, submit, assess, decide, withdraw. The "
            "state machine is enforced in the service, so an invalid transition is a "
            "409 rather than a silently accepted write."
        ),
    },
    {
        "name": "Loans",
        "description": (
            "Booking, amortisation schedule, repayments and the servicing position. "
            "Allocation is penalty then interest then principal, oldest instalment first."
        ),
    },
    {
        "name": "Credit",
        "description": (
            "Customer credit assessment. Knock-outs are evaluated before scoring and "
            "short-circuit to a rejection with reasons."
        ),
    },
    {
        "name": "Alerts",
        "description": (
            "Early-warning alerts and rule evaluation. Deduplicated, superseded and "
            "auto-resolved by the rule engine."
        ),
    },
    {
        "name": "Configuration",
        "description": (
            "Read-only view of the versioned scorecards and the risk-rule catalogue. "
            "Publishing is an admin workflow and is not exposed here."
        ),
    },
    {"name": "Portfolio", "description": "Post-disbursement exposure and the risk table."},
    {
        "name": "Admin",
        "description": (
            "Operational endpoints: manual triggers for the nightly servicing jobs."
        ),
    },
]

__all__ = ["OPENAPI_TAGS", "unversioned_routers", "v1_routers"]
