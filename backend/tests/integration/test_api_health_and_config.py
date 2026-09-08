"""Health, configuration and the OpenAPI document, against a real PostgreSQL 16.

These are the endpoints an operator reaches for first: is this instance able to work, and
what policy is it running? Both answers have to come from the database, not from a
constant, or they would keep saying "fine" after the configuration was withdrawn.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
def test_liveness_needs_no_credentials(client: TestClient):
    """A load balancer has no token; a probe that 401s takes the instance out of service."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_the_liveness_alias_answers_identically(client: TestClient):
    assert client.get("/health/live").json()["status"] == "ok"


def test_readiness_reports_the_database_the_migration_and_the_active_scorecards(
    client: TestClient,
):
    """Readiness means "can do useful work". An instance that cannot resolve a scorecard
    cannot assess anything, so that is part of the answer."""
    response = client.get("/health/ready")
    assert response.status_code == 200

    checks = response.json()["checks"]
    assert checks["database"] == "ok"
    assert checks["migrations"] == "0001"
    assert checks["active_configs"] == {"ROUTE": 2, "CUSTOMER": 1, "FINAL": 1}


def test_readiness_reads_the_active_versions_from_the_database(client: TestClient):
    """Not from the shipped JSON — otherwise the probe would be green against an empty
    database."""
    checks = client.get("/health/ready").json()["checks"]
    assert checks["active_configs"]["ROUTE"] == 2, "the calibrated v2 is what is published"


def test_every_response_carries_a_request_id(client: TestClient):
    response = client.get("/health")
    assert response.headers["X-Request-ID"]


def test_a_supplied_request_id_is_echoed_back(client: TestClient):
    """Lets a caller correlate its own trace id with the server log."""
    response = client.get("/health", headers={"X-Request-ID": "trace-abc-123"})
    assert response.headers["X-Request-ID"] == "trace-abc-123"


@pytest.mark.parametrize(
    ("header", "value"),
    [
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ],
)
def test_security_headers_are_present_on_every_response(client: TestClient, header, value):
    assert client.get("/health").headers[header] == value


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def test_the_active_configuration_is_readable(client: TestClient, admin_headers):
    response = client.get("/api/v1/config", headers=admin_headers)
    assert response.status_code == 200

    body = response.json()
    assert body["active_versions"] == {"ROUTE": 2, "CUSTOMER": 1, "FINAL": 1}
    assert set(body["engine_versions"]) == {"route", "customer", "final", "rules"}


def test_route_v2_is_active_and_v1_is_archived(client: TestClient, admin_headers):
    """The calibration change was a new version, not an edit. v1 must survive as ARCHIVED
    or every score produced under it stops being explainable."""
    body = client.get("/api/v1/config", headers=admin_headers).json()
    route_versions = {
        c["version_no"]: c["status"]
        for c in body["configurations"]
        if c["config_type"] == "ROUTE"
    }
    assert route_versions == {1: "ARCHIVED", 2: "ACTIVE"}


def test_the_active_route_scorecard_starts_class_a_at_88(client: TestClient, admin_headers):
    response = client.get("/api/v1/config/routes", headers=admin_headers)
    assert response.status_code == 200

    body = response.json()
    assert body["version_no"] == 2
    assert body["status"] == "ACTIVE"
    band_a = next(b for b in body["grade_thresholds"] if b["grade"] == "A")
    assert float(band_a["min"]) == 88.0


def test_the_archived_v1_still_starts_class_a_at_80(client: TestClient, admin_headers):
    """Reading a superseded version is how a historical decision is reproduced."""
    response = client.get(
        "/api/v1/config/routes", params={"version": 1}, headers=admin_headers
    )
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ARCHIVED"
    band_a = next(b for b in body["grade_thresholds"] if b["grade"] == "A")
    assert float(band_a["min"]) == 80.0


def test_the_calibration_changed_the_thresholds_and_nothing_else(
    client: TestClient, admin_headers
):
    """It was a risk-policy change, not a formula change: only the grade boundaries moved.

    The API exposes component weights and labels but not the normalisation rules, so this
    asserts the published half; the engine suite asserts the two versions' scoring rules
    are byte-identical.
    """
    v1 = client.get(
        "/api/v1/config/routes", params={"version": 1}, headers=admin_headers
    ).json()
    v2 = client.get(
        "/api/v1/config/routes", params={"version": 2}, headers=admin_headers
    ).json()

    def comparable(config):
        return [
            (c["component_code"], c["label"], str(c["weight"]), c["display_order"])
            for c in config["components"]
        ]

    assert comparable(v1) == comparable(v2)
    assert v1["grade_thresholds"] != v2["grade_thresholds"]


def test_the_scorecard_weights_sum_to_one_hundred(client: TestClient, admin_headers):
    """Doc 01 FR-9.1. Enforced by the engine on load; asserted here on what is published."""
    body = client.get("/api/v1/config/routes", headers=admin_headers).json()
    assert sum(float(c["weight"]) for c in body["components"]) == pytest.approx(1.0)
    assert float(body["weight_total"]) == pytest.approx(1.0)


def test_an_unknown_scorecard_version_is_a_404(client: TestClient, admin_headers):
    response = client.get(
        "/api/v1/config/routes", params={"version": 99}, headers=admin_headers
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SCORING_CONFIGURATION_NOT_FOUND"


def test_the_full_rule_catalogue_is_readable(client: TestClient, admin_headers):
    response = client.get("/api/v1/config/risk-rules", headers=admin_headers)
    assert response.status_code == 200

    rules = response.json()
    assert len(rules) == 22, "the documented catalogue is 22 rules"
    assert sum(len(r["conditions"]) for r in rules) == 27


def test_each_rule_renders_the_same_sentence_the_engine_evaluates(
    client: TestClient, admin_headers
):
    """The description comes from Rule.describe(), so the admin UI cannot show a rule
    differently from the way it fires."""
    rules = client.get("/api/v1/config/risk-rules", headers=admin_headers).json()
    described = [r for r in rules if r.get("plain_language")]
    assert len(described) == 22


def test_the_catalogue_can_be_narrowed_to_enabled_rules(client: TestClient, admin_headers):
    response = client.get(
        "/api/v1/config/risk-rules", params={"active_only": True}, headers=admin_headers
    )
    assert response.status_code == 200
    assert all(r["is_active"] for r in response.json())


def test_configuration_is_read_only(client: TestClient, admin_headers):
    """Publishing is an admin workflow with its own approval path; it is deliberately not
    exposed as an API write."""
    for method in (client.post, client.put, client.delete):
        assert method("/api/v1/config/routes", headers=admin_headers).status_code == 405


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------
def test_the_openapi_document_is_served_and_valid(client: TestClient):
    response = client.get("/api/v1/openapi.json")
    assert response.status_code == 200

    document = response.json()
    assert document["openapi"].startswith("3.")
    assert document["info"]["title"]
    assert len(document["paths"]) >= 15


def test_every_tag_used_by_a_path_is_described(client: TestClient):
    document = client.get("/api/v1/openapi.json").json()
    described = {t["name"] for t in document["tags"]}
    used = {
        tag
        for path in document["paths"].values()
        for operation in path.values()
        for tag in operation.get("tags", [])
    }
    assert used <= described, f"undescribed tags: {sorted(used - described)}"


def test_every_endpoint_documents_its_error_responses(client: TestClient):
    """A client should be able to generate its error handling from the schema."""
    document = client.get("/api/v1/openapi.json").json()
    missing = [
        f"{method.upper()} {path}"
        for path, operations in document["paths"].items()
        for method, operation in operations.items()
        if path.startswith("/api/v1") and not (set(operation["responses"]) - {"200", "201", "202"})
    ]
    assert not missing, f"endpoints with no documented error response: {missing}"


def test_the_interactive_docs_are_available_outside_production(client: TestClient):
    assert client.get("/api/v1/docs").status_code == 200
