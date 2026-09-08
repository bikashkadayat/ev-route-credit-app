"""Structured logging and Prometheus metrics — TRD §2.11.

Two obligations from the document, both of which have to be impossible to forget rather
than remembered at each call site:

* **Structured JSON to stdout**, one line per request, carrying `request_id`, `user_id`,
  `role`, `method`, `path`, `status` and `duration_ms`.
* **Never log a secret.** Passwords, tokens, national identifiers, full bureau payloads
  and account numbers are prohibited. A processor strips them by key name before any
  renderer sees the event, so a careless ``logger.info(..., password=x)`` is neutralised
  instead of shipped.

The metric names below are exactly those tabulated in TRD §2.11; the alerting rules in that
section are written against these names, so they are a contract, not an implementation
detail.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

from app.core.config import settings
from app.services.audit import REDACTED, REDACTED_KEY_PARTS

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

#: A private registry rather than the global default, so a test can build the app twice
#: without "Duplicated timeseries in CollectorRegistry" bringing the process down.
REGISTRY = CollectorRegistry()

http_requests_total = Counter(
    "http_requests_total",
    "HTTP requests by route, method and status.",
    ("route", "method", "status"),
    registry=REGISTRY,
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency; the p95 SLO is measured from this.",
    ("route", "method", "status"),
    registry=REGISTRY,
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

scoring_runs_total = Counter(
    "scoring_runs_total",
    "Completed scoring runs, labelled by engine and resulting grade — grade drift shows "
    "up here before it shows up in arrears.",
    ("engine", "result_grade"),
    registry=REGISTRY,
)

scoring_duration_seconds = Histogram(
    "scoring_duration_seconds",
    "Time spent inside a scoring engine.",
    ("engine",),
    registry=REGISTRY,
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)

alerts_generated_total = Counter(
    "alerts_generated_total",
    "Alerts raised, by severity and rule — the rule-noise signal.",
    ("severity", "rule_code"),
    registry=REGISTRY,
)

job_last_success_timestamp = Gauge(
    "job_last_success_timestamp",
    "Unix time of each background job's last success; feeds the dead-job alert.",
    ("job",),
    registry=REGISTRY,
)

db_pool_in_use = Gauge(
    "db_pool_in_use", "Connections checked out of the pool.", registry=REGISTRY
)
db_pool_size = Gauge(
    "db_pool_size", "Configured pool size.", registry=REGISTRY
)

external_call_duration_seconds = Histogram(
    "external_call_duration_seconds",
    "Bureau and telematics call latency, by provider and outcome.",
    ("provider", "status"),
    registry=REGISTRY,
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)


def render_metrics() -> bytes:
    """The Prometheus text exposition of the private registry."""
    _refresh_pool_gauges()
    return generate_latest(REGISTRY)


def _refresh_pool_gauges() -> None:
    """Pool saturation is a gauge, so it is sampled at scrape time rather than tracked."""
    try:
        from app.core.database import get_engine

        pool = get_engine().pool
        db_pool_size.set(float(getattr(pool, "size", lambda: 0)()))
        db_pool_in_use.set(float(getattr(pool, "checkedout", lambda: 0)()))
    except Exception:
        return


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def _redact_processor(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """TRD §2.11 — the prohibited-field filter, enforced by key name.

    Shares its key list with the audit redactor so "sensitive" means one thing across the
    system: adding a field there protects both the audit trail and the logs.
    """
    for key in list(event_dict):
        if any(part in key.lower() for part in REDACTED_KEY_PARTS):
            event_dict[key] = REDACTED
    return event_dict


def configure_logging() -> None:
    """JSON to stdout. Called once from the application factory."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level, force=True)

    # Taking over the root handler means third-party libraries log through it too, and some
    # of them log during interpreter shutdown, when the stream may already be closed. A
    # logging failure must never print a traceback over real output — or, worse, surface as
    # an error in a process that was otherwise healthy.
    logging.raiseExceptions = False

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact_processor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "app") -> Any:
    return structlog.get_logger(name)
