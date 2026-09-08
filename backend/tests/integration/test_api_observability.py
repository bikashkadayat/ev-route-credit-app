"""Metrics and structured logging — TRD §2.11, Doc 06 §6.13.

The alerting rules in TRD §2.11 are written against specific metric names, so the names are
a contract. These tests fail if one is renamed or stops being emitted, which is the failure
mode that otherwise goes unnoticed until an incident nobody was paged for.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.core.observability import REGISTRY, configure_logging, get_logger

#: Every metric TRD §2.11 tabulates.
DOCUMENTED_METRICS = (
    "http_request_duration_seconds",
    "http_requests_total",
    "scoring_runs_total",
    "scoring_duration_seconds",
    "alerts_generated_total",
    "job_last_success_timestamp",
    "db_pool_in_use",
    "db_pool_size",
    "external_call_duration_seconds",
)


def scrape(client: TestClient) -> str:
    response = client.get("/metrics")
    assert response.status_code == 200
    return response.text


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------
def test_metrics_are_served_in_prometheus_text_format(client: TestClient):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "# HELP" in response.text and "# TYPE" in response.text


def test_a_scraper_needs_no_credentials(client: TestClient):
    """Doc 06 §6.13 — a Prometheus scraper has no user identity. The control is network
    placement, which is why the endpoint documents that restriction explicitly."""
    assert client.get("/metrics").status_code == 200


@pytest.mark.parametrize("metric", DOCUMENTED_METRICS)
def test_every_documented_metric_is_exposed(client: TestClient, metric):
    assert metric in scrape(client)


def test_the_scrape_survives_a_database_it_cannot_reach(monkeypatch, client: TestClient):
    """Pool gauges are sampled from the live engine at scrape time. If that sample can
    fail the scrape, monitoring goes dark at exactly the moment it is needed — when the
    database is the thing that broke."""
    import app.core.database as database

    def unreachable():
        raise RuntimeError("could not connect")

    monkeypatch.setattr(database, "get_engine", unreachable)

    response = client.get("/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text


# ---------------------------------------------------------------------------
# HTTP metrics
# ---------------------------------------------------------------------------
def test_requests_are_counted_by_route_method_and_status(
    client: TestClient, admin_headers
):
    before = REGISTRY.get_sample_value(
        "http_requests_total", {"route": "/api/v1/routes", "method": "GET", "status": "200"}
    ) or 0.0

    client.get("/api/v1/routes", headers=admin_headers)

    after = REGISTRY.get_sample_value(
        "http_requests_total", {"route": "/api/v1/routes", "method": "GET", "status": "200"}
    )
    assert after == before + 1


def test_the_metric_label_is_the_route_template_not_the_raw_path(
    client: TestClient, admin_headers, seeded_route_id
):
    """One series per endpoint. Labelling by raw path would create a new time series for
    every route id and eventually take the metrics store down."""
    client.get(f"/api/v1/routes/{seeded_route_id}", headers=admin_headers)

    templated = REGISTRY.get_sample_value(
        "http_requests_total",
        {"route": "/api/v1/routes/{route_id}", "method": "GET", "status": "200"},
    )
    assert templated is not None
    assert str(seeded_route_id) not in scrape(client)


def test_failed_requests_are_counted_under_their_status(client: TestClient):
    client.get("/api/v1/routes")  # 401
    assert REGISTRY.get_sample_value(
        "http_requests_total",
        {"route": "/api/v1/routes", "method": "GET", "status": "401"},
    )


def test_latency_is_observed_for_every_request(client: TestClient, admin_headers):
    client.get("/api/v1/routes", headers=admin_headers)
    count = REGISTRY.get_sample_value(
        "http_request_duration_seconds_count",
        {"route": "/api/v1/routes", "method": "GET", "status": "200"},
    )
    assert count and count >= 1


# ---------------------------------------------------------------------------
# Domain metrics
# ---------------------------------------------------------------------------
def test_a_route_assessment_increments_the_scoring_counter(
    client: TestClient, admin_headers, seeded_route_id
):
    """Grade drift shows up here long before it shows up in arrears."""
    response = client.post(
        f"/api/v1/routes/{seeded_route_id}/assess", headers=admin_headers
    )
    assert response.status_code == 201
    grade = response.json()["grade"]

    assert REGISTRY.get_sample_value(
        "scoring_runs_total", {"engine": "route", "result_grade": grade}
    )


def test_scoring_duration_is_recorded_for_the_route_engine(
    client: TestClient, admin_headers, seeded_route_id
):
    client.post(f"/api/v1/routes/{seeded_route_id}/assess", headers=admin_headers)
    assert REGISTRY.get_sample_value(
        "scoring_duration_seconds_count", {"engine": "route"}
    )


def test_the_counter_records_the_grade_the_engine_actually_returned(
    client: TestClient, admin_headers, seeded_route_id
):
    """A metric that disagreed with the stored assessment would send an analyst chasing a
    drift that never happened."""
    body = client.post(
        f"/api/v1/routes/{seeded_route_id}/assess", headers=admin_headers
    ).json()
    assert REGISTRY.get_sample_value(
        "scoring_runs_total", {"engine": "route", "result_grade": body["grade"]}
    )


def test_instrumentation_did_not_leak_into_the_engines():
    """The counter belongs to the service. An engine that recorded a metric would have a
    side effect, and would no longer be a pure function of its inputs."""
    import pathlib

    engines = pathlib.Path(__file__).resolve().parents[2] / "app" / "engines"
    risk = pathlib.Path(__file__).resolve().parents[2] / "app" / "risk"
    for path in list(engines.glob("*.py")) + list(risk.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "observability" not in source, f"{path.name} imports observability"
        assert "prometheus" not in source, f"{path.name} imports prometheus"


# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------
def test_log_lines_are_json(capsys):
    configure_logging()
    get_logger("test").info("event", request_id="abc123")
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["event"] == "event"
    assert payload["request_id"] == "abc123"


def test_a_log_line_carries_the_mandatory_fields(capsys):
    """TRD §2.11 names them: without `request_id` a log cannot be correlated to a user's
    complaint, and without `duration_ms` the latency SLO is unmeasurable."""
    configure_logging()
    get_logger("app.request").info(
        "request", request_id="r1", user_id=3, role="RISK_MANAGER",
        method="GET", path="/api/v1/routes", status=200, duration_ms=12.5,
    )
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    for field in ("timestamp", "level", "request_id", "user_id", "role",
                  "method", "path", "status", "duration_ms"):
        assert field in payload, f"missing mandatory log field {field}"


@pytest.mark.parametrize(
    "field", ["password", "access_token", "refresh_token", "id_number", "api_key"]
)
def test_prohibited_fields_never_reach_the_log(capsys, field):
    """TRD §2.11 forbids these outright. Stripping by key name means a careless call site
    is neutralised rather than shipped."""
    configure_logging()
    get_logger("test").info("event", **{field: "SHOULD-NOT-APPEAR"})
    output = capsys.readouterr().out
    assert "SHOULD-NOT-APPEAR" not in output
    assert "[REDACTED]" in output


def test_ordinary_fields_still_reach_the_log(capsys):
    configure_logging()
    get_logger("test").info("event", route="/api/v1/routes", status=200)
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["route"] == "/api/v1/routes"
    assert payload["status"] == 200
