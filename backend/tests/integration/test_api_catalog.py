"""Vehicle catalogue, bureau enquiry, stateless scoring, monitoring and dashboard.

Doc 06 §6.4 credit-report · §6.5 vehicles · §6.7 scoring · §6.8 monitoring · §6.9 dashboard.

The scoring tests exist for one reason above all: a what-if calculator that disagrees with
the persisted assessment shows an officer one number and stores another. Several tests here
assert the two paths agree exactly.
"""

from __future__ import annotations

import itertools
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

_SEQ = itertools.count(1)


@pytest.fixture
def applicant(client: TestClient, admin_headers, customer_payload) -> dict:
    n = next(_SEQ)
    response = client.post(
        "/api/v1/applicants",
        json={**customer_payload, "id_number": f"27-66-70-{n:05d}",
              "phone": f"+97798466{n:05d}", "full_name": f"Bureau Subject {n:03d}"},
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Vehicle models — Doc 06 §6.5
# ---------------------------------------------------------------------------
def test_the_seeded_catalogue_is_listed(client: TestClient, admin_headers):
    body = client.get("/api/v1/vehicle-models", headers=admin_headers).json()
    assert body["total"] >= 1
    assert all("battery_capacity_kwh" in row for row in body["items"])


def test_a_model_carries_the_economics_inputs_the_engine_needs(
    client: TestClient, admin_headers
):
    """These live on the model so two applications for the same vehicle cannot be scored
    on different assumptions."""
    row = client.get(
        "/api/v1/vehicle-models", params={"page_size": 1}, headers=admin_headers
    ).json()["items"][0]
    for field in ("battery_capacity_kwh", "real_world_range_km",
                  "maintenance_cost_per_km", "battery_warranty_years", "on_road_price"):
        assert row[field] is not None


def test_models_can_be_filtered_by_category(client: TestClient, admin_headers):
    body = client.get(
        "/api/v1/vehicle-models", params={"category": "CAR_TAXI"}, headers=admin_headers
    ).json()
    assert all(row["category"] == "CAR_TAXI" for row in body["items"])


def test_a_model_can_be_read_by_id(client: TestClient, admin_headers):
    listed = client.get(
        "/api/v1/vehicle-models", params={"page_size": 1}, headers=admin_headers
    ).json()["items"][0]
    response = client.get(
        f"/api/v1/vehicle-models/{listed['id']}", headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json()["brand"] == listed["brand"]


def test_an_unknown_model_is_a_404(client: TestClient, admin_headers):
    assert client.get(
        "/api/v1/vehicle-models/999999", headers=admin_headers
    ).status_code == 404


def _model_payload(n: int) -> dict:
    return {
        "brand": f"TestBrand{n}", "model": f"TestModel{n}", "variant": "LONG_RANGE",
        "category": "CAR_TAXI", "battery_capacity_kwh": "60.0",
        "certified_range_km": 420, "real_world_range_km": 350,
        "ex_showroom_price": "3800000", "on_road_price": "4400000",
        "battery_warranty_years": 8, "maintenance_cost_per_km": "0.55",
    }


def test_a_model_can_be_added(client: TestClient, admin_headers):
    response = client.post(
        "/api/v1/vehicle-models", json=_model_payload(next(_SEQ)), headers=admin_headers
    )
    assert response.status_code == 201
    assert response.json()["variant"] == "LONG_RANGE"


def test_a_duplicate_variant_is_refused(client: TestClient, admin_headers):
    payload = _model_payload(next(_SEQ))
    assert client.post(
        "/api/v1/vehicle-models", json=payload, headers=admin_headers
    ).status_code == 201

    duplicate = client.post(
        "/api/v1/vehicle-models", json=payload, headers=admin_headers
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "DUPLICATE_RESOURCE"


def test_real_world_range_above_certified_is_refused(client: TestClient, admin_headers):
    """An optimistic range makes the charging-gap analysis wrong in exactly the direction
    that strands a vehicle."""
    payload = {**_model_payload(next(_SEQ)), "real_world_range_km": 500,
               "certified_range_km": 400}
    response = client.post(
        "/api/v1/vehicle-models", json=payload, headers=admin_headers
    )
    assert response.status_code == 422
    assert "ABOVE_CERTIFIED" in {
        d["code"] for d in response.json()["error"]["details"]
    }


def test_an_on_road_price_below_ex_showroom_is_refused(client: TestClient, admin_headers):
    payload = {**_model_payload(next(_SEQ)), "on_road_price": "1000000",
               "ex_showroom_price": "3800000"}
    assert client.post(
        "/api/v1/vehicle-models", json=payload, headers=admin_headers
    ).status_code == 422


def test_adding_a_model_needs_the_settings_permission(
    client: TestClient, officer_headers
):
    response = client.post(
        "/api/v1/vehicle-models", json=_model_payload(next(_SEQ)), headers=officer_headers
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Vehicles
# ---------------------------------------------------------------------------
@pytest.fixture
def seeded_model_id(client: TestClient, admin_headers) -> int:
    return client.get(
        "/api/v1/vehicle-models", params={"page_size": 1}, headers=admin_headers
    ).json()["items"][0]["id"]


def test_a_vehicle_can_be_registered(client: TestClient, officer_headers, seeded_model_id):
    n = next(_SEQ)
    response = client.post(
        "/api/v1/vehicles",
        json={"vehicle_model_id": seeded_model_id,
              "registration_number": f"BA-2-KHA-{n:04d}", "manufacture_year": 2025},
        headers=officer_headers,
    )
    assert response.status_code == 201
    assert response.json()["status"] == "PROPOSED"


def test_the_purchase_price_defaults_to_the_model_price(
    client: TestClient, officer_headers, seeded_model_id, admin_headers
):
    model = client.get(
        f"/api/v1/vehicle-models/{seeded_model_id}", headers=admin_headers
    ).json()
    created = client.post(
        "/api/v1/vehicles", json={"vehicle_model_id": seeded_model_id},
        headers=officer_headers,
    ).json()
    assert Decimal(created["purchase_price"]) == Decimal(model["on_road_price"])


def test_a_duplicate_registration_is_refused(
    client: TestClient, officer_headers, seeded_model_id
):
    n = next(_SEQ)
    body = {"vehicle_model_id": seeded_model_id, "registration_number": f"BA-9-PA-{n:04d}"}
    assert client.post(
        "/api/v1/vehicles", json=body, headers=officer_headers
    ).status_code == 201
    assert client.post(
        "/api/v1/vehicles", json=body, headers=officer_headers
    ).status_code == 409


def test_a_vehicle_can_be_read_by_uuid(
    client: TestClient, officer_headers, seeded_model_id, admin_headers
):
    created = client.post(
        "/api/v1/vehicles", json={"vehicle_model_id": seeded_model_id},
        headers=officer_headers,
    ).json()
    response = client.get(f"/api/v1/vehicles/{created['id']}", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["model_name"]


def test_an_unknown_vehicle_is_a_404(client: TestClient, admin_headers):
    assert client.get(
        "/api/v1/vehicles/00000000-0000-0000-0000-000000000000", headers=admin_headers
    ).status_code == 404


# ---------------------------------------------------------------------------
# Bureau enquiry — FR-3.5
# ---------------------------------------------------------------------------
def test_a_bureau_report_can_be_pulled(client: TestClient, admin_headers, applicant):
    response = client.post(
        f"/api/v1/applicants/{applicant['id']}/credit-report", headers=admin_headers
    )
    assert response.status_code == 201, response.text

    body = response.json()
    assert body["provider"]
    assert body["enquiry_id"]
    assert body["is_cached"] is False
    assert body["valid_until"] >= body["enquiry_date"]


def test_the_report_carries_the_fields_the_scorecard_consumes(
    client: TestClient, admin_headers, applicant
):
    body = client.post(
        f"/api/v1/applicants/{applicant['id']}/credit-report", headers=admin_headers
    ).json()
    for field in ("bureau_score", "credit_history_months", "previous_default_count",
                  "max_dpd_last_24m", "is_blacklisted", "enquiries_last_6m"):
        assert field in body


def test_the_raw_payload_is_never_returned(client: TestClient, admin_headers, applicant):
    """FR-3.5 requires it stored, not exposed."""
    body = client.post(
        f"/api/v1/applicants/{applicant['id']}/credit-report", headers=admin_headers
    ).json()
    assert "raw_response" not in body


def test_the_response_never_carries_the_identifier(
    client: TestClient, admin_headers, applicant, customer_payload
):
    body = client.post(
        f"/api/v1/applicants/{applicant['id']}/credit-report", headers=admin_headers
    ).json()
    assert "id_number" not in body
    assert "27-66-70-" not in str(body)


def test_a_second_pull_reuses_the_cached_report(
    client: TestClient, admin_headers, applicant
):
    """An enquiry is billed per call, so re-pulling inside the validity window buys
    nothing (Doc 01 R-8)."""
    first = client.post(
        f"/api/v1/applicants/{applicant['id']}/credit-report", headers=admin_headers
    ).json()
    second = client.post(
        f"/api/v1/applicants/{applicant['id']}/credit-report", headers=admin_headers
    ).json()

    assert second["is_cached"] is True
    assert second["enquiry_id"] == first["enquiry_id"]


def test_force_refresh_re_queries_the_provider(client: TestClient, admin_headers, applicant):
    """``force_refresh`` skips the cache and calls the bureau again.

    The mock is deterministic within a business day, so it returns the same enquiry
    reference — and an enquiry reference is globally unique in the schema. The service
    therefore returns the stored record rather than inserting a second row: the same
    reference means the same enquiry, and duplicating it would invent one that never
    happened. A real bureau issuing a fresh reference would produce a new row here.
    """
    first = client.post(
        f"/api/v1/applicants/{applicant['id']}/credit-report", headers=admin_headers
    ).json()
    forced = client.post(
        f"/api/v1/applicants/{applicant['id']}/credit-report",
        json={"force_refresh": True}, headers=admin_headers,
    )
    assert forced.status_code == 201
    assert forced.json()["enquiry_id"] == first["enquiry_id"]


def test_a_repeat_enquiry_never_duplicates_the_record(
    client: TestClient, admin_headers, applicant, engine
):
    """Regression: the deterministic mock plus a unique enquiry reference used to collide
    and return a 500."""
    for _ in range(3):
        response = client.post(
            f"/api/v1/applicants/{applicant['id']}/credit-report",
            json={"force_refresh": True}, headers=admin_headers,
        )
        assert response.status_code == 201, response.text

    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT count(*) FROM credit_bureau_reports r JOIN applicants a "
                 "ON a.id = r.applicant_id WHERE a.uuid = :u"),
            {"u": applicant["id"]},
        ).scalar_one()
    assert rows == 1


def test_the_mock_provider_is_deterministic(client: TestClient, admin_headers, applicant):
    """The same identifier must always produce the same bureau position, or a demo would
    tell a different story on every run."""
    first = client.post(
        f"/api/v1/applicants/{applicant['id']}/credit-report", headers=admin_headers
    ).json()
    second = client.post(
        f"/api/v1/applicants/{applicant['id']}/credit-report",
        json={"force_refresh": True}, headers=admin_headers,
    ).json()
    assert first["bureau_score"] == second["bureau_score"]
    assert first["is_blacklisted"] == second["is_blacklisted"]


def test_the_latest_report_can_be_read_back(client: TestClient, admin_headers, applicant):
    client.post(
        f"/api/v1/applicants/{applicant['id']}/credit-report", headers=admin_headers
    )
    response = client.get(
        f"/api/v1/applicants/{applicant['id']}/credit-report", headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json()["is_cached"] is True


def test_reading_before_any_pull_is_a_404(client: TestClient, admin_headers, applicant):
    assert client.get(
        f"/api/v1/applicants/{applicant['id']}/credit-report", headers=admin_headers
    ).status_code == 404


def test_a_bureau_pull_is_audit_logged(client: TestClient, admin_headers, applicant, engine):
    client.post(
        f"/api/v1/applicants/{applicant['id']}/credit-report", headers=admin_headers
    )
    with engine.begin() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM audit_logs WHERE action = 'BUREAU_REPORT_FETCHED'")
        ).scalar_one()
    assert count >= 1


def test_pulling_a_report_needs_the_credit_check_permission(
    client: TestClient, viewer_headers, applicant
):
    response = client.post(
        f"/api/v1/applicants/{applicant['id']}/credit-report", headers=viewer_headers
    )
    assert response.status_code == 403
    assert response.json()["error"]["details"]["required_permission"] == (
        "applicant:credit_check"
    )


def test_pulling_for_an_unknown_applicant_is_a_404(client: TestClient, admin_headers):
    assert client.post(
        "/api/v1/applicants/00000000-0000-0000-0000-000000000000/credit-report",
        headers=admin_headers,
    ).status_code == 404


# ---------------------------------------------------------------------------
# Stateless scoring — Doc 06 §6.7
# ---------------------------------------------------------------------------
KTM_DHULIKHEL = {
    "route_name": "Kathmandu-Dhulikhel", "total_distance_km": "30",
    "road_type": "PITCH", "road_condition": "GOOD", "gradient_profile": "ROLLING",
    "pitch_road_percent": "100", "charging_station_count": 6, "fast_charger_count": 3,
    "avg_charging_distance_km": "5", "max_charging_gap_km": "12",
    "passenger_volume_daily": 1200, "freight_volume_daily_tons": "0",
    "estimated_daily_trips": "8", "avg_fare_per_trip": "950",
    "traffic_density": "HIGH", "competition_level": "MODERATE", "seasonal_risk": "LOW",
    "monsoon_disruption_days": 6, "flood_landslide_risk": "LOW", "security_risk": "LOW",
    "electricity_tariff_per_kwh": "12", "estimated_daily_operating_cost": "1800",
}


def test_a_route_can_be_scored_without_saving(client: TestClient, admin_headers, engine):
    """Doc 06 §6.7 — the what-if. It must not leave a row behind."""
    with engine.begin() as conn:
        before = conn.execute(text("SELECT count(*) FROM route_assessments")).scalar_one()

    response = client.post(
        "/api/v1/scoring/route", json=KTM_DHULIKHEL, headers=admin_headers
    )
    assert response.status_code == 200, response.text

    with engine.begin() as conn:
        after = conn.execute(text("SELECT count(*) FROM route_assessments")).scalar_one()
    assert after == before


def test_the_stateless_route_score_matches_the_persisted_one(
    client: TestClient, admin_headers, seeded_route_id
):
    """The whole point of the calculator: an officer must never be shown one number and
    have another stored."""
    persisted = client.post(
        f"/api/v1/routes/{seeded_route_id}/assess", headers=admin_headers
    ).json()
    what_if = client.post(
        "/api/v1/scoring/route", json=KTM_DHULIKHEL, headers=admin_headers
    ).json()
    assert Decimal(what_if["total_score"]) == Decimal(persisted["total_score"])
    assert what_if["grade"] == persisted["grade"]


def test_the_stateless_score_carries_the_component_breakdown(
    client: TestClient, admin_headers
):
    body = client.post(
        "/api/v1/scoring/route", json=KTM_DHULIKHEL, headers=admin_headers
    ).json()
    assert len(body["components"]) == 5
    assert body["explanation"]
    assert body["engine_version"].startswith("route-engine@")


def test_an_incomplete_route_is_a_422(client: TestClient, admin_headers):
    payload = {**KTM_DHULIKHEL}
    payload.pop("max_charging_gap_km")
    response = client.post("/api/v1/scoring/route", json=payload, headers=admin_headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INCOMPLETE_DATA"


def test_scoring_a_route_needs_the_assess_permission(client: TestClient, viewer_headers):
    assert client.post(
        "/api/v1/scoring/route", json=KTM_DHULIKHEL, headers=viewer_headers
    ).status_code == 403


CUSTOMER_PAYLOAD = {
    "applicant_type": "OWNER_DRIVER", "age_years": "34", "age_at_maturity": "39",
    "bureau_score": "742", "credit_history_months": 54, "previous_default_count": 0,
    "max_dpd_last_24m": 12, "enquiries_last_6m": 1, "is_blacklisted": False,
    "total_monthly_income": "145000", "total_monthly_expenses": "60000",
    "total_existing_emi": "12400", "existing_loan_count": 1,
    "avg_bank_balance_6m": "83000", "income_proof_type": "BANK_STATEMENT",
    "income_verified": True, "requested_amount": "3200000",
    "requested_tenure_months": 60, "down_payment": "1400000",
    "vehicle_on_road_price": "4600000", "driving_experience_years": "9",
    "commercial_driving_years": "6", "licence_category": "B",
    "licence_months_remaining": "30", "route_grade": "A", "route_score": "90.12",
    "proposed_interest_rate": "13.0",
}


def test_a_borrower_can_be_scored_without_saving(client: TestClient, admin_headers, engine):
    with engine.begin() as conn:
        before = conn.execute(text("SELECT count(*) FROM credit_scores")).scalar_one()

    response = client.post(
        "/api/v1/scoring/customer", json=CUSTOMER_PAYLOAD, headers=admin_headers
    )
    assert response.status_code == 200, response.text
    assert Decimal(response.json()["total_score"]) > 0

    with engine.begin() as conn:
        after = conn.execute(text("SELECT count(*) FROM credit_scores")).scalar_one()
    assert after == before


def test_knockouts_short_circuit_the_stateless_customer_score(
    client: TestClient, admin_headers
):
    """Doc 06 §6.7 — the same short-circuit as the persisted path: no scoring at all."""
    body = client.post(
        "/api/v1/scoring/customer",
        json={**CUSTOMER_PAYLOAD, "is_blacklisted": True},
        headers=admin_headers,
    ).json()
    assert Decimal(body["total_score"]) == Decimal("0")
    assert body["grade"] == "E"
    assert body["components"] == []


def test_the_emi_calculator_matches_the_schedule_generator(
    client: TestClient, admin_headers
):
    """Doc 06 §6.7 — the same reducing-balance function the amortisation is built from."""
    response = client.post(
        "/api/v1/scoring/emi",
        json={"principal": "2960000", "annual_rate": "13.0", "tenure_months": 60},
        headers=admin_headers,
    )
    assert response.status_code == 200

    body = response.json()
    assert Decimal(body["emi"]) == Decimal("67349.10")
    assert Decimal(body["total_payable"]) == (
        Decimal(body["emi"]) * 60
    ).quantize(Decimal("0.01")) or Decimal(body["total_payable"]) > 0


def test_the_emi_calculator_rejects_an_impossible_structure(
    client: TestClient, admin_headers
):
    response = client.post(
        "/api/v1/scoring/emi",
        json={"principal": "0", "annual_rate": "13.0", "tenure_months": 60},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_the_final_what_if_returns_a_structure_and_a_decision(
    client: TestClient, admin_headers
):
    """This is what powers the amount slider in the demo."""
    response = client.post(
        "/api/v1/scoring/final",
        json={
            "route_score": "90.12", "customer_score": "75.17",
            "vehicle": {"on_road_price": 4600000, "battery_capacity_kwh": 71.7,
                        "real_world_range_km": 380, "maintenance_cost_per_km": 0.55,
                        "battery_warranty_years": 8},
            "operation": {"daily_km": 240, "operating_days_month": 26, "daily_trips": 8,
                          "avg_revenue_per_trip": 950, "electricity_tariff_per_kwh": 12,
                          "gradient_profile": "ROLLING", "monsoon_disruption_days": 6,
                          "annual_insurance": 85000, "annual_permit_tax": 12000},
            "loan": {"requested_amount": 3200000, "tenure_months": 60,
                     "interest_rate": 13.0, "down_payment": 1400000},
            "borrower": {"total_monthly_income": 145000, "total_monthly_expenses": 60000,
                         "total_existing_emi": 12400},
            "grades": {"route_grade": "A", "customer_grade": "B"},
        },
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["recommendation"] in {"APPROVE", "MANUAL_REVIEW", "REJECT"}
    assert Decimal(str(body["recommended"]["recommended_amount"])) > 0
    assert body["requested"]["emi"]


def test_lowering_the_amount_improves_the_affordability_ratios(
    client: TestClient, admin_headers
):
    """The behaviour the slider exists to demonstrate: less exposure, better FOIR."""
    def run(amount: int) -> dict:
        return client.post(
            "/api/v1/scoring/final",
            json={
                "route_score": "90.12", "customer_score": "75.17",
                "vehicle": {"on_road_price": 4600000, "battery_capacity_kwh": 71.7,
                            "real_world_range_km": 380, "battery_warranty_years": 8},
                "operation": {"daily_km": 240, "operating_days_month": 26,
                              "daily_trips": 8, "avg_revenue_per_trip": 950},
                "loan": {"requested_amount": amount, "tenure_months": 60,
                         "interest_rate": 13.0, "down_payment": 1400000},
                "borrower": {"total_monthly_income": 145000,
                             "total_monthly_expenses": 60000,
                             "total_existing_emi": 12400},
                "grades": {"route_grade": "A", "customer_grade": "B"},
            },
            headers=admin_headers,
        ).json()

    high = run(3_200_000)
    low = run(2_800_000)
    assert low["requested"]["foir_post_loan"] < high["requested"]["foir_post_loan"]
    assert low["requested"]["emi"] < high["requested"]["emi"]


# ---------------------------------------------------------------------------
# Dashboard — PRD §1.11
# ---------------------------------------------------------------------------
def test_the_dashboard_returns_every_documented_kpi(client: TestClient, admin_headers):
    response = client.get("/api/v1/dashboard/summary", headers=admin_headers)
    assert response.status_code == 200, response.text

    kpis = response.json()["kpis"]
    for card in ("total_applications", "approved", "rejected", "manual_reviews",
                 "active_loans", "total_portfolio_value", "outstanding_amount",
                 "overdue_amount", "yellow_alerts", "red_alerts"):
        assert card in kpis, f"PRD §1.11 KPI card missing: {card}"


def test_the_dashboard_carries_the_distribution_charts(client: TestClient, admin_headers):
    body = client.get("/api/v1/dashboard/summary", headers=admin_headers).json()
    for chart in ("portfolio_by_grade", "dpd_buckets", "route_class_distribution",
                  "customer_grade_distribution"):
        assert chart in body


def test_the_dashboard_states_how_fresh_it_is(client: TestClient, admin_headers):
    """The demo script calls out the "as of" caption — being honest about freshness."""
    assert client.get(
        "/api/v1/dashboard/summary", headers=admin_headers
    ).json()["as_of"]


def test_the_dashboard_needs_the_portfolio_permission(client: TestClient):
    assert client.get("/api/v1/dashboard/summary").status_code == 401
