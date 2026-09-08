"""FastAPI application factory — Doc 02 §2.4.1, Doc 04 §4.2.

The middleware chain and the exception handlers are registered once here, so every
endpoint inherits the request id, the CORS policy, the security headers and the single
error envelope without repeating anything.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import OPENAPI_TAGS, unversioned_routers, v1_routers
from app.core.config import settings
from app.core.errors import (
    AppError,
    app_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.core.observability import (
    configure_logging,
    get_logger,
    http_request_duration_seconds,
    http_requests_total,
)

logger = get_logger("app.request")

DESCRIPTION = """
Route-aware EV credit risk, from application to portfolio.

**Architecture.** Routers are thin: they parse, authorise, call a service and return a
schema. Services own transactions and orchestration and are the only layer permitted to
call the pure engines. The scoring and rule engines are pure functions of
`(payload, configuration)` — no database, no clock, no randomness — so any stored score
can be reproduced exactly from the configuration version stamped on it.

**Configuration is data.** Weights, grade bands and alert rules live in the database and
are versioned. ROUTE v2 is currently ACTIVE (Class A from 88); v1 is retained as ARCHIVED
so scores produced under it remain explainable.
"""


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(
        title=settings.app_title,
        version=settings.api_version,
        description=DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
        docs_url=settings.docs_url,
        redoc_url=None if settings.is_production else f"{settings.api_v1_prefix}/redoc",
        openapi_url=settings.openapi_url,
        contact={"name": "EV-RCA Engineering"},
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Correlation id + timing + security headers, applied to every response.

        The id is echoed in ``X-Request-ID`` and embedded in every error body, so a user
        reporting a failure hands over the exact key to the log line.
        """
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()

        response = await call_next(request)

        duration_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

        # TRD 2.11 — the route *template* is the metric label, never the raw path: one
        # series per endpoint, not one per route id, which is what keeps cardinality flat.
        route = _route_template(request)
        labels = (route, request.method, str(response.status_code))
        http_requests_total.labels(*labels).inc()
        http_request_duration_seconds.labels(*labels).observe(duration_ms / 1000)

        user = getattr(request.state, "current_user", None)
        logger.info(
            "request",
            request_id=request_id,
            user_id=getattr(user, "id", None),
            role=getattr(user, "role", None),
            method=request.method,
            path=request.url.path,
            route=route,
            status=response.status_code,
            duration_ms=round(duration_ms, 2),
        )
        return response

    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    for router in unversioned_routers:
        app.include_router(router)
    for router in v1_routers:
        app.include_router(router, prefix=settings.api_v1_prefix)

    def _route_template(request: Request) -> str:
        """The matched path template, e.g. ``/api/v1/routes/{route_id}``."""
        route = request.scope.get("route")
        return getattr(route, "path", request.url.path)

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {
            "name": settings.app_name,
            "version": settings.api_version,
            "docs": settings.docs_url or "disabled in production",
            "openapi": settings.openapi_url,
        }

    return app


app = create_app()
