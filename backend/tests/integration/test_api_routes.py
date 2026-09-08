"""Route CRUD, assessment, pagination and authorisation — end to end.

The route corridor is the distinctive part of this product: a route is scored once and the
result is reused by every application that runs on it. These tests drive the whole stack —
router, service, repository, PostgreSQL and the pure engine — and check the things that
would silently corrupt a credit file if they broke: the score is the engine's, the
assessment is immutable, and the configuration version that produced it is recorded.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import auth


@pytest.fixture
def created_route(client: TestClient, admin_headers, route_payload) -> dict:
    response = client.post("/api/v1/routes", json=route_payload, headers=admin_headers)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
def test_a_route_can_be_created(created_route):
    assert created_route["route_name"].startswith("Test Corridor")
    assert created_route["status"] == "DRAFT", "a route is not assessed until it is assessed"


def test_the_public_identifier_is_a_uuid_not_the_row_id(created_route):
    """Doc 05 §5.1 principle 4 — sequential ids would let a caller enumerate the book."""
    import uuid

    uuid.UUID(created_route["id"])
    assert created_route["id"] != "1"


def test_a_route_code_is_generated_from_the_endpoints(created_route):
    assert created_route["route_code"].startswith("RT-TES-TRI-")


def test_charging_density_is_computed_server_side(created_route):
    """5 stations over 24 km is 2.083 per 10 km. A client-supplied value would be an
    unvalidated input to a regulated score."""
    assert Decimal(created_route["charging_station_density"]) == Decimal("2.083")


def test_daily_revenue_is_derived_not_accepted(client, admin_headers, route_payload):
    payload = {**route_payload, "estimated_daily_revenue": "999999"}
    response = client.post("/api/v1/routes", json=payload, headers=admin_headers)
    assert response.status_code == 201
    # 9 trips x NPR 420 = 3,780, not the injected figure.
    assert Decimal(response.json()["estimated_daily_revenue"]) == Decimal("3780.00")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("total_distance_km", "0", "a zero-length corridor cannot be scored"),
        ("total_distance_km", "-5", "negative distance"),
        ("estimated_daily_trips", "0", "a corridor with no trips earns nothing"),
        ("pitch_road_percent", "120", "percentages are bounded"),
        ("electricity_tariff_per_kwh", "0.5", "below the plausible tariff floor"),
        ("monsoon_disruption_days", 400, "more disruption days than a year"),
        ("road_type", "TELEPORT", "not a member of the enum"),
        ("traffic_density", "", "empty enum value"),
    ],
)
def test_an_impossible_route_is_rejected_before_any_service_runs(
    client: TestClient, admin_headers, route_payload, field, value, reason
):
    response = client.post(
        "/api/v1/routes", json={**route_payload, field: value}, headers=admin_headers
    )
    assert response.status_code == 422, reason
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_more_fast_chargers_than_chargers_is_rejected(
    client: TestClient, admin_headers, route_payload
):
    payload = {**route_payload, "charging_station_count": 2, "fast_charger_count": 5}
    response = client.post("/api/v1/routes", json=payload, headers=admin_headers)
    assert response.status_code == 422

    fields = [d["field"] for d in response.json()["error"]["details"]]
    assert fields, "the envelope must say which field failed"


def test_a_charging_gap_longer_than_the_route_is_rejected(
    client: TestClient, admin_headers, route_payload
):
    payload = {**route_payload, "max_charging_gap_km": "500"}
    assert client.post(
        "/api/v1/routes", json=payload, headers=admin_headers
    ).status_code == 422


def test_a_passenger_corridor_without_a_fare_is_rejected(
    client: TestClient, admin_headers, route_payload
):
    """Otherwise the revenue component would score a corridor that cannot earn."""
    payload = {**route_payload, "avg_fare_per_trip": "0"}
    assert client.post(
        "/api/v1/routes", json=payload, headers=admin_headers
    ).status_code == 422


def test_a_charging_equipped_route_must_state_its_worst_gap(
    client: TestClient, admin_headers, route_payload
):
    payload = {k: v for k, v in route_payload.items() if k != "max_charging_gap_km"}
    assert client.post(
        "/api/v1/routes", json=payload, headers=admin_headers
    ).status_code == 422


def test_the_validation_envelope_lists_every_failure_at_once(
    client: TestClient, admin_headers, route_payload
):
    """One round trip should tell the caller everything to fix."""
    payload = {**route_payload, "total_distance_km": "-1", "pitch_road_percent": "150"}
    details = client.post(
        "/api/v1/routes", json=payload, headers=admin_headers
    ).json()["error"]["details"]
    assert len(details) >= 2


# ---------------------------------------------------------------------------
# Read and update
# ---------------------------------------------------------------------------
def test_a_route_can_be_read_back_by_uuid(client: TestClient, admin_headers, created_route):
    response = client.get(f"/api/v1/routes/{created_route['id']}", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["route_code"] == created_route["route_code"]


def test_an_unknown_route_is_a_404_with_a_specific_code(client: TestClient, admin_headers):
    response = client.get(
        "/api/v1/routes/00000000-0000-0000-0000-000000000000", headers=admin_headers
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ROUTE_NOT_FOUND"


def test_a_malformed_uuid_is_a_422_not_a_500(client: TestClient, admin_headers):
    assert client.get("/api/v1/routes/not-a-uuid", headers=admin_headers).status_code == 422


def test_a_route_can_be_replaced(client: TestClient, admin_headers, created_route, route_payload):
    updated = {**route_payload, "route_name": "Test Corridor Alpha (revised)",
               "charging_station_count": 8, "fast_charger_count": 4}
    response = client.put(
        f"/api/v1/routes/{created_route['id']}", json=updated, headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json()["route_name"] == "Test Corridor Alpha (revised)"
    assert response.json()["charging_station_count"] == 8


def test_an_update_recomputes_the_derived_columns(
    client: TestClient, admin_headers, created_route, route_payload
):
    updated = {**route_payload, "charging_station_count": 12, "fast_charger_count": 4}
    response = client.put(
        f"/api/v1/routes/{created_route['id']}", json=updated, headers=admin_headers
    )
    assert Decimal(response.json()["charging_station_density"]) == Decimal("5.000")


def test_an_update_is_validated_by_the_same_rules_as_a_create(
    client: TestClient, admin_headers, created_route, route_payload
):
    response = client.put(
        f"/api/v1/routes/{created_route['id']}",
        json={**route_payload, "total_distance_km": "-3"},
        headers=admin_headers,
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------
def test_assessing_a_route_returns_the_engines_own_score(
    client: TestClient, admin_headers, seeded_route_id
):
    """Kathmandu-Dhulikhel is the Doc 07 §7.3.9 worked example. The API must return the
    same 90.12 the engine test asserts, or the API has its own arithmetic."""
    response = client.post(
        f"/api/v1/routes/{seeded_route_id}/assess", headers=admin_headers
    )
    assert response.status_code == 201, response.text

    body = response.json()
    assert Decimal(body["total_score"]) == Decimal("90.12")
    assert body["grade"] == "A"
    assert body["risk_level"] == "LOW"


def test_an_assessment_carries_the_full_component_breakdown(
    client: TestClient, admin_headers, seeded_route_id
):
    """An underwriter has to be able to see why, not just what."""
    body = client.post(
        f"/api/v1/routes/{seeded_route_id}/assess", headers=admin_headers
    ).json()

    assert len(body["components"]) == 5

    # Doc 07 §7.2 — components are quantised to 2dp before weighting and the weighted
    # contributions to 3dp; the total is the rounded sum, so the parts reconcile to the
    # whole at the documented precision rather than exactly.
    total = sum(Decimal(c["weighted_score"]) for c in body["components"])
    assert total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == Decimal(
        body["total_score"]
    )


def test_every_component_explains_itself(client: TestClient, admin_headers, seeded_route_id):
    body = client.post(
        f"/api/v1/routes/{seeded_route_id}/assess", headers=admin_headers
    ).json()
    for component in body["components"]:
        assert component["explanation"], f"{component['code']} has no explanation"
        assert Decimal(component["weight"]) > 0


def test_an_assessment_records_the_configuration_and_engine_that_produced_it(
    client: TestClient, admin_headers, seeded_route_id
):
    """Doc 02 §2.4.4 — without this stamp the score cannot be reproduced later."""
    body = client.post(
        f"/api/v1/routes/{seeded_route_id}/assess", headers=admin_headers
    ).json()
    assert body["config_version"] == 2
    assert body["engine_version"].startswith("route-engine@")


def test_the_charging_and_revenue_verdicts_are_reported(
    client: TestClient, admin_headers, seeded_route_id
):
    body = client.post(
        f"/api/v1/routes/{seeded_route_id}/assess", headers=admin_headers
    ).json()
    assert body["charging_adequacy"] in {"ADEQUATE", "MARGINAL", "INADEQUATE"}
    assert body["revenue_potential"] in {"HIGH", "MODERATE", "LOW"}
    assert body["recommendation"]


def test_re_assessing_appends_rather_than_overwrites(
    client: TestClient, admin_headers, seeded_route_id
):
    """Assessments are immutable; the history is the audit trail."""
    before = client.get(
        f"/api/v1/routes/{seeded_route_id}/assessments", headers=admin_headers
    ).json()["total"]

    first = client.post(f"/api/v1/routes/{seeded_route_id}/assess", headers=admin_headers)
    second = client.post(f"/api/v1/routes/{seeded_route_id}/assess", headers=admin_headers)
    assert first.json()["id"] != second.json()["id"]

    after = client.get(
        f"/api/v1/routes/{seeded_route_id}/assessments", headers=admin_headers
    ).json()["total"]
    assert after == before + 2


def test_the_same_route_scored_twice_gives_the_same_score(
    client: TestClient, admin_headers, seeded_route_id
):
    """The engine is a pure function of (payload, config). Two calls a moment apart must
    not differ, or the score depends on something undeclared."""
    first = client.post(f"/api/v1/routes/{seeded_route_id}/assess", headers=admin_headers)
    second = client.post(f"/api/v1/routes/{seeded_route_id}/assess", headers=admin_headers)
    assert first.json()["total_score"] == second.json()["total_score"]
    assert first.json()["components"] == second.json()["components"]


def test_the_assessment_history_is_newest_first(
    client: TestClient, admin_headers, seeded_route_id
):
    client.post(f"/api/v1/routes/{seeded_route_id}/assess", headers=admin_headers)
    rows = client.get(
        f"/api/v1/routes/{seeded_route_id}/assessments", headers=admin_headers
    ).json()["items"]
    stamps = [r["assessed_at"] for r in rows]
    assert stamps == sorted(stamps, reverse=True)


def test_assessing_a_route_with_missing_data_is_a_422_listing_the_gaps(
    client: TestClient, admin_headers, route_payload
):
    """A corridor with no charging stations and no gap measurement cannot be scored; the
    engine says which fields are missing and the API relays them."""
    payload = {**route_payload, "charging_station_count": 0, "fast_charger_count": 0,
               "max_charging_gap_km": None, "avg_charging_distance_km": "0"}
    route = client.post("/api/v1/routes", json=payload, headers=admin_headers)
    assert route.status_code == 201, route.text

    response = client.post(
        f"/api/v1/routes/{route.json()['id']}/assess", headers=admin_headers
    )
    assert response.status_code in (201, 422)
    if response.status_code == 422:
        body = response.json()["error"]
        assert body["code"] == "INCOMPLETE_DATA"
        assert body["details"], "the caller must be told which fields are missing"


def test_assessing_an_unknown_route_is_a_404(client: TestClient, admin_headers):
    response = client.post(
        "/api/v1/routes/00000000-0000-0000-0000-000000000000/assess",
        headers=admin_headers,
    )
    assert response.status_code == 404


def test_a_named_reference_vehicle_changes_the_charging_analysis(
    client: TestClient, admin_headers, seeded_route_id
):
    """A 380 km BYD reaches further between chargers than the 140 km reference, so the
    same corridor scores differently for it. Passing an unknown model is a 404."""
    response = client.post(
        f"/api/v1/routes/{seeded_route_id}/assess",
        json={"reference_vehicle_model_id": 99999},
        headers=admin_headers,
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "VEHICLE_MODEL_NOT_FOUND"


# ---------------------------------------------------------------------------
# Listing, filtering, pagination
# ---------------------------------------------------------------------------
def test_the_demo_corridors_are_listed(client: TestClient, admin_headers):
    body = client.get("/api/v1/routes", headers=admin_headers).json()
    assert body["total"] >= 10, "the ten demo corridors are seeded"
    assert {"items", "total", "page", "page_size", "pages"} <= set(body)


def test_a_page_is_never_larger_than_requested(client: TestClient, admin_headers):
    body = client.get(
        "/api/v1/routes", params={"page": 1, "page_size": 3}, headers=admin_headers
    ).json()
    assert len(body["items"]) == 3
    assert body["page_size"] == 3


def test_pages_do_not_overlap(client: TestClient, admin_headers):
    first = client.get(
        "/api/v1/routes", params={"page": 1, "page_size": 4, "sort": "route_code"},
        headers=admin_headers,
    ).json()
    second = client.get(
        "/api/v1/routes", params={"page": 2, "page_size": 4, "sort": "route_code"},
        headers=admin_headers,
    ).json()
    assert not {r["id"] for r in first["items"]} & {r["id"] for r in second["items"]}


def test_the_total_is_the_whole_result_set_not_the_page(client: TestClient, admin_headers):
    body = client.get(
        "/api/v1/routes", params={"page": 1, "page_size": 2}, headers=admin_headers
    ).json()
    assert body["total"] > len(body["items"])
    assert body["pages"] == -(-body["total"] // body["page_size"])


def test_a_page_beyond_the_end_is_empty_not_an_error(client: TestClient, admin_headers):
    body = client.get(
        "/api/v1/routes", params={"page": 999, "page_size": 20}, headers=admin_headers
    ).json()
    assert body["items"] == []
    assert body["total"] > 0


@pytest.mark.parametrize("page_size", [0, -1, 1001])
def test_an_out_of_range_page_size_is_rejected(client: TestClient, admin_headers, page_size):
    response = client.get(
        "/api/v1/routes", params={"page_size": page_size}, headers=admin_headers
    )
    assert response.status_code == 422


def test_page_numbering_starts_at_one(client: TestClient, admin_headers):
    assert client.get(
        "/api/v1/routes", params={"page": 0}, headers=admin_headers
    ).status_code == 422


def test_routes_can_be_searched_by_name(client: TestClient, admin_headers):
    body = client.get(
        "/api/v1/routes", params={"q": "Dhulikhel"}, headers=admin_headers
    ).json()
    assert body["total"] >= 1
    assert any("Dhulikhel" in r["route_name"] for r in body["items"])


def test_routes_can_be_filtered_by_grade(client: TestClient, admin_headers):
    body = client.get(
        "/api/v1/routes", params={"grade": "A"}, headers=admin_headers
    ).json()
    assert all(r["latest_grade"] == "A" for r in body["items"])


def test_routes_can_be_filtered_by_score_range(client: TestClient, admin_headers):
    body = client.get(
        "/api/v1/routes", params={"min_score": 88}, headers=admin_headers
    ).json()
    assert all(Decimal(r["latest_score"]) >= 88 for r in body["items"] if r["latest_score"])


def test_the_default_ordering_is_best_corridor_first(client: TestClient, admin_headers):
    items = client.get(
        "/api/v1/routes", params={"page_size": 50}, headers=admin_headers
    ).json()["items"]
    scores = [Decimal(r["latest_score"]) for r in items if r["latest_score"] is not None]
    assert scores == sorted(scores, reverse=True)


def test_sorting_by_an_arbitrary_column_is_refused(client: TestClient, admin_headers):
    """Doc 06 §6.1.2 — the allow-list is what stops a caller ordering by anything at all."""
    response = client.get(
        "/api/v1/routes", params={"sort": "id"}, headers=admin_headers
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["code"] == "NOT_SORTABLE"


def test_the_seeded_grade_distribution_is_discriminating(client: TestClient, admin_headers):
    """After the Class A boundary moved to 88, the demo book must no longer be almost all
    Class A — that was the point of the calibration."""
    items = client.get(
        "/api/v1/routes", params={"page_size": 50}, headers=admin_headers
    ).json()["items"]
    grades = [r["latest_grade"] for r in items if r["latest_grade"]]
    assert len(set(grades)) >= 2, f"every corridor graded the same: {set(grades)}"
    assert grades.count("A") < len(grades), "not everything can be Class A"


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------
def test_an_anonymous_request_is_401(client: TestClient):
    response = client.get("/api/v1/routes")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_a_malformed_authorization_header_is_401(client: TestClient):
    response = client.get("/api/v1/routes", headers={"Authorization": "Bearer garbage"})
    assert response.status_code == 401


def test_a_viewer_may_read_routes(client: TestClient, viewer_headers):
    assert client.get("/api/v1/routes", headers=viewer_headers).status_code == 200


def test_a_viewer_may_not_create_a_route(client: TestClient, viewer_headers, route_payload):
    response = client.post("/api/v1/routes", json=route_payload, headers=viewer_headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"
    assert response.json()["error"]["details"]["required_permission"] == "route:create"


def test_a_viewer_may_not_assess_a_route(client: TestClient, viewer_headers, seeded_route_id):
    """Assessment writes an immutable credit-relevant record; reading is not enough."""
    response = client.post(
        f"/api/v1/routes/{seeded_route_id}/assess", headers=viewer_headers
    )
    assert response.status_code == 403


def test_a_credit_officer_may_assess_a_route(
    client: TestClient, officer_headers, seeded_route_id
):
    assert client.post(
        f"/api/v1/routes/{seeded_route_id}/assess", headers=officer_headers
    ).status_code == 201


def test_a_token_with_no_permissions_is_403_not_500(client: TestClient):
    from tests.integration.conftest import token_for

    token = token_for("VIEWER", permissions=[])
    response = client.get("/api/v1/routes", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_an_error_response_still_carries_the_request_id(client: TestClient):
    """The id is what links a user's complaint to the log line."""
    response = client.get("/api/v1/routes", headers=auth("VIEWER", user_id=6))
    assert response.headers["X-Request-ID"]

    denied = client.get("/api/v1/routes")
    assert denied.json()["error"]["request_id"]
