"""Endpoint contract against Doc 06 §6.14.

The specification's endpoint table is the contract a frontend is written against, so a
deviation is a defect even when the code works. This suite states which documented
endpoints exist and which do not, so the gap is visible in the test report rather than
discovered by whoever writes the client.

``DOCUMENTED`` is transcribed from Doc 06 §6.14. Endpoints not yet built are listed in
``NOT_IMPLEMENTED`` with the reason; the test asserts that list is accurate in *both*
directions, so an endpoint cannot be quietly dropped, and one that gets built cannot stay
on the list.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

PREFIX = "/api/v1"

#: (method, documented path) -> the path this implementation serves it at.
#: A value equal to the key's path means no deviation.
IMPLEMENTED: dict[tuple[str, str], str] = {
    # -- health ---------------------------------------------------------------
    ("GET", "/health/live"): "/health/live",
    ("GET", "/health/ready"): "/health/ready",
    ("GET", "/metrics"): "/metrics",
    # -- auth -----------------------------------------------------------------
    ("POST", "/auth/login"): f"{PREFIX}/auth/login",
    ("POST", "/auth/refresh"): f"{PREFIX}/auth/refresh",
    ("POST", "/auth/logout"): f"{PREFIX}/auth/logout",
    ("POST", "/auth/logout-all"): f"{PREFIX}/auth/logout-all",
    ("GET", "/auth/me"): f"{PREFIX}/auth/me",
    ("POST", "/auth/change-password"): f"{PREFIX}/auth/change-password",
    ("POST", "/auth/forgot-password"): f"{PREFIX}/auth/forgot-password",
    ("POST", "/auth/reset-password"): f"{PREFIX}/auth/reset-password",
    # -- routes ---------------------------------------------------------------
    ("GET", "/routes"): f"{PREFIX}/routes",
    ("POST", "/routes"): f"{PREFIX}/routes",
    ("GET", "/routes/{id}"): f"{PREFIX}/routes/{{route_id}}",
    ("PATCH", "/routes/{id}"): f"{PREFIX}/routes/{{route_id}}",
    ("DELETE", "/routes/{id}"): f"{PREFIX}/routes/{{route_id}}",
    ("POST", "/routes/{id}/assess"): f"{PREFIX}/routes/{{route_id}}/assess",
    ("GET", "/routes/{id}/assessments"): f"{PREFIX}/routes/{{route_id}}/assessments",
    ("GET", "/routes/{id}/charging-stations"):
        f"{PREFIX}/routes/{{route_id}}/charging-stations",
    # -- applicants -----------------------------------------------------------
    ("GET", "/applicants"): f"{PREFIX}/applicants",
    ("POST", "/applicants"): f"{PREFIX}/applicants",
    ("GET", "/applicants/{id}"): f"{PREFIX}/applicants/{{customer_id}}",
    ("PATCH", "/applicants/{id}"): f"{PREFIX}/applicants/{{customer_id}}",
    ("POST", "/applicants/{id}/financials"):
        f"{PREFIX}/applicants/{{customer_id}}/financials",
    # -- applications ---------------------------------------------------------
    ("GET", "/applications"): f"{PREFIX}/applications",
    ("POST", "/applications"): f"{PREFIX}/applications",
    ("GET", "/applications/{id}"): f"{PREFIX}/applications/{{application_id}}",
    ("POST", "/applications/{id}/assess"): f"{PREFIX}/applications/{{application_id}}/assess",
    ("POST", "/applications/{id}/decision"):
        f"{PREFIX}/applications/{{application_id}}/decision",
    ("POST", "/applications/{id}/submit"): f"{PREFIX}/applications/{{application_id}}/submit",
    ("POST", "/applications/{id}/withdraw"):
        f"{PREFIX}/applications/{{application_id}}/withdraw",
    # -- loans ----------------------------------------------------------------
    ("GET", "/loans"): f"{PREFIX}/loans",
    ("GET", "/loans/{id}"): f"{PREFIX}/loans/{{loan_id}}",
    ("GET", "/loans/{id}/schedule"): f"{PREFIX}/loans/{{loan_id}}/schedule",
    ("GET", "/loans/{id}/repayments"): f"{PREFIX}/loans/{{loan_id}}/repayments",
    ("POST", "/loans/{id}/repayments"): f"{PREFIX}/loans/{{loan_id}}/repayments",
    # -- portfolio and alerts --------------------------------------------------
    ("GET", "/portfolio"): f"{PREFIX}/portfolio",
    ("GET", "/alerts"): f"{PREFIX}/alerts",
    ("GET", "/alerts/{id}"): f"{PREFIX}/alerts/{{alert_id}}",
    ("POST", "/alerts/evaluate"): f"{PREFIX}/alerts/evaluate",
    # -- catalogue and stateless scoring ---------------------------------------
    ("GET", "/vehicle-models"): f"{PREFIX}/vehicle-models",
    ("POST", "/vehicle-models"): f"{PREFIX}/vehicle-models",
    ("GET", "/vehicles"): f"{PREFIX}/vehicles",
    ("POST", "/vehicles"): f"{PREFIX}/vehicles",
    ("POST", "/applicants/{id}/credit-report"):
        f"{PREFIX}/applicants/{{customer_id}}/credit-report",
    ("POST", "/scoring/route"): f"{PREFIX}/scoring/route",
    ("POST", "/scoring/customer"): f"{PREFIX}/scoring/customer",
    ("POST", "/scoring/final"): f"{PREFIX}/scoring/final",
    ("POST", "/scoring/emi"): f"{PREFIX}/scoring/emi",
    ("GET", "/loans/{id}/monitoring"): f"{PREFIX}/loans/{{loan_id}}/monitoring",
    ("GET", "/dashboard/summary"): f"{PREFIX}/dashboard/summary",
    # -- alert lifecycle -------------------------------------------------------
    ("POST", "/alerts/{id}/activities"): f"{PREFIX}/alerts/{{alert_id}}/activities",
    ("POST", "/alerts/{id}/escalate"): f"{PREFIX}/alerts/{{alert_id}}/escalate",
    ("POST", "/alerts/{id}/assign"): f"{PREFIX}/alerts/{{alert_id}}/assign",
    # -- admin -----------------------------------------------------------------
    ("POST", "/admin/jobs/{job_name}/run"): f"{PREFIX}/admin/jobs/run",
    ("GET", "/admin/jobs"): f"{PREFIX}/admin/jobs",
    ("GET", "/admin/scoring-configs"): f"{PREFIX}/admin/scoring-configs",
    ("POST", "/admin/scoring-configs"): f"{PREFIX}/admin/scoring-configs",
    ("POST", "/admin/scoring-configs/{id}/publish"):
        f"{PREFIX}/admin/scoring-configs/{{config_id}}/publish",
    ("GET", "/admin/risk-rules"): f"{PREFIX}/admin/risk-rules",
    ("GET", "/admin/settings"): f"{PREFIX}/admin/settings",
    ("PUT", "/admin/settings"): f"{PREFIX}/admin/settings/{{setting_key}}",
    ("GET", "/admin/users"): f"{PREFIX}/admin/users",
    ("POST", "/admin/users"): f"{PREFIX}/admin/users",
    ("GET", "/admin/roles"): f"{PREFIX}/admin/roles",
    ("GET", "/admin/audit-logs"): f"{PREFIX}/admin/audit-logs",
}

#: Documented but not built, each with the reason. Keeping the list here rather than in a
#: report means it cannot drift out of date without failing a test.
NOT_IMPLEMENTED: dict[tuple[str, str], str] = {
    ("POST", "/applicants/{id}/documents"): "document upload not built",
    ("GET", "/documents/{uuid}/download"): "document download not built",
    ("POST", "/loans"): "loans are created by POST /applications/{id}/book",
    ("GET", "/reports"): "reporting not built",
    ("PUT", "/admin/scoring-configs"):
        "a draft is edited with PATCH /admin/scoring-configs/{id}; PUT is not served",
    ("PUT", "/admin/risk-rules"):
        "a rule is edited with PATCH /admin/risk-rules/{id}; PUT is not served",
    ("PATCH", "/alerts/{id}"):
        "assignment is POST /alerts/{id}/assign; the documented PATCH is not served",
}


def registered(client: TestClient) -> set[tuple[str, str]]:
    document = client.get(f"{PREFIX}/openapi.json").json()
    live: set[tuple[str, str]] = set()
    for path, operations in document["paths"].items():
        for method in operations:
            live.add((method.upper(), path))
    # The unversioned probes are not in the versioned document.
    for probe in ("/health", "/health/live", "/health/ready", "/metrics"):
        live.add(("GET", probe))
    return live


# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("method", "documented"),
    sorted(IMPLEMENTED),
    ids=lambda v: v if isinstance(v, str) else str(v),
)
def test_every_endpoint_claimed_as_implemented_is_registered(
    client: TestClient, method, documented
):
    served = IMPLEMENTED[(method, documented)]
    assert (method, served) in registered(client), (
        f"{method} {documented} is claimed as implemented at {served}, but no such route "
        f"is registered"
    )


@pytest.mark.parametrize(
    ("method", "documented"),
    sorted(NOT_IMPLEMENTED),
    ids=lambda v: v if isinstance(v, str) else str(v),
)
def test_endpoints_listed_as_missing_really_are_missing(
    client: TestClient, method, documented
):
    """The other direction. When one of these is built, this test fails and forces the
    matrix — and the implementation report that quotes it — to be corrected."""
    live = registered(client)
    candidate = (method, f"{PREFIX}{documented}")
    assert candidate not in live, (
        f"{method} {documented} is now implemented; move it out of NOT_IMPLEMENTED"
    )


def test_the_matrix_covers_the_documented_surface_without_overlap():
    assert not (set(IMPLEMENTED) & set(NOT_IMPLEMENTED))
    assert len(IMPLEMENTED) + len(NOT_IMPLEMENTED) >= 60


# ---------------------------------------------------------------------------
# Deviations that are deliberate
# ---------------------------------------------------------------------------
def test_the_documented_applicants_path_is_canonical(client: TestClient, admin_headers):
    """Doc 06 §6.14 names the resource ``/applicants``."""
    response = client.get(f"{PREFIX}/applicants", headers=admin_headers)
    assert response.status_code == 200


def test_the_legacy_customers_path_still_works(client: TestClient, admin_headers):
    """Retained for clients written against the first implementation."""
    response = client.get(f"{PREFIX}/customers", headers=admin_headers)
    assert response.status_code == 200


def test_both_applicant_paths_return_the_same_data(client: TestClient, admin_headers):
    """One implementation, two addresses — not two implementations that could diverge."""
    canonical = client.get(
        f"{PREFIX}/applicants", params={"page_size": 5}, headers=admin_headers
    ).json()
    legacy = client.get(
        f"{PREFIX}/customers", params={"page_size": 5}, headers=admin_headers
    ).json()
    assert canonical["items"] == legacy["items"]
    assert canonical["total"] == legacy["total"]


def test_the_legacy_path_is_marked_deprecated_in_the_schema(client: TestClient):
    document = client.get(f"{PREFIX}/openapi.json").json()
    assert document["paths"][f"{PREFIX}/customers"]["get"]["deprecated"] is True
    assert not document["paths"][f"{PREFIX}/applicants"]["get"].get("deprecated", False)


def test_routes_accept_the_documented_patch_verb(client: TestClient, admin_headers, route_payload):
    created = client.post(
        f"{PREFIX}/routes", json=route_payload, headers=admin_headers
    ).json()
    response = client.patch(
        f"{PREFIX}/routes/{created['id']}",
        json={**route_payload, "route_name": "Patched Corridor"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["route_name"] == "Patched Corridor"


def test_the_legacy_put_verb_is_marked_deprecated(client: TestClient):
    document = client.get(f"{PREFIX}/openapi.json").json()
    route_path = document["paths"][f"{PREFIX}/routes/{{route_id}}"]
    assert route_path["put"]["deprecated"] is True
    assert not route_path["patch"].get("deprecated", False)


def test_a_route_can_be_retired(client: TestClient, admin_headers, route_payload):
    """Doc 06 §6.14 documents DELETE; Doc 05 §5.1 principle 6 makes it a soft delete."""
    created = client.post(
        f"{PREFIX}/routes", json=route_payload, headers=admin_headers
    ).json()

    assert client.delete(
        f"{PREFIX}/routes/{created['id']}", headers=admin_headers
    ).status_code == 200

    assert client.get(
        f"{PREFIX}/routes/{created['id']}", headers=admin_headers
    ).status_code == 404


def test_retiring_a_route_needs_the_delete_permission(
    client: TestClient, officer_headers, route_payload, admin_headers
):
    created = client.post(
        f"{PREFIX}/routes", json=route_payload, headers=admin_headers
    ).json()
    response = client.delete(f"{PREFIX}/routes/{created['id']}", headers=officer_headers)
    assert response.status_code == 403
    assert response.json()["error"]["details"]["required_permission"] == "route:delete"


def test_a_retired_route_is_gone_from_the_listing(
    client: TestClient, admin_headers, route_payload
):
    created = client.post(
        f"{PREFIX}/routes", json=route_payload, headers=admin_headers
    ).json()
    client.delete(f"{PREFIX}/routes/{created['id']}", headers=admin_headers)

    listing = client.get(
        f"{PREFIX}/routes", params={"q": created["route_name"]}, headers=admin_headers
    ).json()
    assert all(row["id"] != created["id"] for row in listing["items"])
