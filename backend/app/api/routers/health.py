"""Health and readiness — Doc 06 §6.13.

Unauthenticated by design: a load balancer must be able to probe them.

``/health`` answers "is the process alive"; ``/health/ready`` answers "can this instance do
useful work", which for this system means the database is reachable, migrations are at
head, and an active scoring configuration exists. An instance that cannot resolve a
scorecard cannot assess anything and should not receive traffic.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.api.schemas.common import HealthResponse, ReadinessResponse
from app.core.config import settings
from app.core.database import check_database
from app.core.dependencies import DbSession
from app.core.errors import NotFoundError
from app.core.observability import PROMETHEUS_CONTENT_TYPE, render_metrics

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe",
    description="Returns ok whenever the process is serving. Performs no I/O.",
)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/health/live",
    response_model=HealthResponse,
    summary="Liveness probe (alias)",
    include_in_schema=False,
)
def live() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    description=(
        "Checks the database connection, the migration revision and the active scoring "
        "configuration versions. Returns 503 when any check fails."
    ),
    responses={503: {"model": ReadinessResponse, "description": "A dependency is unavailable"}},
)
def ready(db: DbSession, response: Response) -> ReadinessResponse:
    try:
        checks = check_database(db)
    except Exception as exc:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(
            status="error",
            checks={"database": "error", "detail": str(exc).splitlines()[0][:200]},
        )

    healthy = checks["database"] == "ok" and bool(checks.get("active_configs"))
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="ok" if healthy else "degraded", checks=checks)


@router.get(
    "/metrics",
    summary="Prometheus metrics",
    description=(
        "Text exposition of the metrics named in TRD 2.11: request rate and latency, "
        "scoring runs by engine and grade, alerts by rule, job freshness and connection "
        "pool saturation. "
        "**Exposure:** unauthenticated, because a scraper has no user identity. It must be "
        "reachable only from the internal network — restricted at the ingress, and "
        "disableable with `METRICS_ENABLED=false`."
    ),
    responses={404: {"description": "Metrics are disabled in this environment"}},
    include_in_schema=True,
    tags=["Health"],
)
def metrics() -> Response:
    if not settings.metrics_enabled:
        raise NotFoundError("metrics endpoint")
    return Response(content=render_metrics(), media_type=PROMETHEUS_CONTENT_TYPE)
