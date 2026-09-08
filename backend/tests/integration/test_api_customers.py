"""Customer CRUD and credit assessment — end to end against PostgreSQL.

The customer surface carries the sensitive data in this system, so two things are checked
as hard as the scoring: the national identifier never comes back out, and a duplicate
application for the same person is refused rather than quietly creating a second file.

Loan applications have no public write endpoint (they belong to the application workflow),
so the fixtures below insert them with SQL. That is test-data setup, not a shortcut past
the service layer — every assertion still goes through the API.
"""

from __future__ import annotations

import itertools
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

_APPLICATION_SEQUENCE = itertools.count(1)


@pytest.fixture
def created_customer(client: TestClient, admin_headers, customer_payload) -> dict:
    payload = {**customer_payload, "id_number": _unique_id_number()}
    response = client.post("/api/v1/customers", json=payload, headers=admin_headers)
    assert response.status_code == 201, response.text
    return response.json()


_ID_SEQUENCE = itertools.count(1)


def _unique_id_number() -> str:
    return f"27-99-70-{next(_ID_SEQUENCE):05d}"


@pytest.fixture
def financial_profile(client: TestClient, admin_headers, created_customer) -> dict:
    """The Doc 07 §7.5.7 applicant's finances: NPR 145,000 income, one existing loan."""
    body = {
        "monthly_income": "145000",
        "monthly_household_expenses": "60000",
        "avg_bank_balance_6m": "83000",
        "bank_account_count": 2,
        "dependants_count": 3,
        "income_proof_type": "BANK_STATEMENT",
        "income_verified": True,
        "obligations": [
            {
                "lender_name": "Nabil Bank",
                "loan_type": "PERSONAL",
                "original_amount": "500000",
                "outstanding_amount": "310000",
                "monthly_emi": "12400",
                "remaining_tenure_months": 26,
                "is_overdue": False,
                "days_past_due": 0,
                "source": "CIB",
            }
        ],
    }
    response = client.post(
        f"/api/v1/customers/{created_customer['id']}/financials",
        json=body,
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def application(engine, created_customer, financial_profile, admin_headers, client):
    """A submitted application on the best-graded seeded corridor, with a bureau report.

    Mirrors the Doc 07 §7.5.7 case: BYD e6, NPR 3.2m over 60 months at 13%.
    """
    number = next(_APPLICATION_SEQUENCE)
    with engine.begin() as conn:
        applicant_id = conn.execute(
            text("SELECT id FROM applicants WHERE uuid = :u"),
            {"u": created_customer["id"]},
        ).scalar_one()
        financial_id = conn.execute(
            text(
                "SELECT id FROM applicant_financials "
                "WHERE applicant_id = :a AND is_current ORDER BY version_no DESC LIMIT 1"
            ),
            {"a": applicant_id},
        ).scalar_one()
        model_id = conn.execute(
            text(
                "SELECT id FROM vehicle_models ORDER BY real_world_range_km DESC LIMIT 1"
            )
        ).scalar_one()
        route_id = conn.execute(
            text(
                "SELECT id FROM routes WHERE latest_score IS NOT NULL "
                "ORDER BY latest_score DESC LIMIT 1"
            )
        ).scalar_one()

        conn.execute(
            text(
                "INSERT INTO credit_bureau_reports (applicant_id, enquiry_id, enquiry_date,"
                " valid_until, bureau_score, credit_history_months, active_loan_count,"
                " total_outstanding, total_monthly_emi, previous_default_count,"
                " max_dpd_last_24m, is_blacklisted, enquiries_last_6m)"
                " VALUES (:a, :e, CURRENT_DATE, CURRENT_DATE + 90, 742, 54, 1,"
                " 310000, 12400, 0, 12, false, 1)"
            ),
            {"a": applicant_id, "e": f"ENQ-TEST-{number:05d}"},
        )
        vehicle_id = conn.execute(
            text(
                "INSERT INTO vehicles (vehicle_model_id, purchase_price, is_new, status)"
                " VALUES (:m, 4600000, true, 'PROPOSED') RETURNING id"
            ),
            {"m": model_id},
        ).scalar_one()
        application_id = conn.execute(
            text(
                "INSERT INTO loan_applications (application_number, applicant_id,"
                " financial_profile_id, vehicle_id, route_id, requested_amount,"
                " requested_tenure_months, proposed_interest_rate, down_payment_amount,"
                " vehicle_on_road_price, expected_daily_km, expected_operating_days_month,"
                " status)"
                " VALUES (:n, :a, :f, :v, :r, 3200000, 60, 13.0, 1400000, 4600000,"
                " 240, 26, 'SUBMITTED') RETURNING id"
            ),
            {
                "n": f"APP-TEST-{number:05d}",
                "a": applicant_id,
                "f": financial_id,
                "v": vehicle_id,
                "r": route_id,
            },
        ).scalar_one()

    return {"customer": created_customer, "application_id": application_id}


# ---------------------------------------------------------------------------
# Create and read
# ---------------------------------------------------------------------------
def test_a_customer_can_be_registered(created_customer):
    assert created_customer["full_name"] == "Test Applicant One"
    assert created_customer["status"] == "ACTIVE"
    assert created_customer["applicant_code"]


def test_the_national_identifier_is_never_returned(created_customer):
    """Doc 09 §9.7.4. The raw value must not appear anywhere in the response body."""
    body = str(created_customer)
    assert "27-99-70-" not in body.replace(created_customer["id_number_masked"], "")
    assert "id_number" not in created_customer


def test_the_identifier_comes_back_masked_but_recognisable(created_customer):
    masked = created_customer["id_number_masked"]
    assert masked.startswith("27-99-70-")
    assert masked.endswith("*") or "*" in masked


def test_a_customer_can_be_read_back(client: TestClient, admin_headers, created_customer):
    response = client.get(
        f"/api/v1/customers/{created_customer['id']}", headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json()["applicant_code"] == created_customer["applicant_code"]


def test_an_unknown_customer_is_a_404(client: TestClient, admin_headers):
    response = client.get(
        "/api/v1/customers/00000000-0000-0000-0000-000000000000", headers=admin_headers
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CUSTOMER_NOT_FOUND"


def test_registering_the_same_identifier_twice_is_a_409(
    client: TestClient, admin_headers, customer_payload, created_customer
):
    """Two files for one person is the failure that makes exposure unmeasurable."""
    duplicate = {**customer_payload, "id_number": _last_id_number()}
    response = client.post("/api/v1/customers", json=duplicate, headers=admin_headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DUPLICATE_RESOURCE"


def _last_id_number() -> str:
    """The identifier the most recent ``created_customer`` used."""
    return f"27-99-70-{next(_ID_SEQUENCE) - 1:05d}"


def test_duplicate_detection_ignores_spacing_and_case(
    client: TestClient, admin_headers, customer_payload
):
    first = {**customer_payload, "id_number": "27-88-70-ab123"}
    assert client.post(
        "/api/v1/customers", json=first, headers=admin_headers
    ).status_code == 201

    second = {**customer_payload, "id_number": " 27-88-70-AB123 ", "phone": "+9779841119999"}
    assert client.post(
        "/api/v1/customers", json=second, headers=admin_headers
    ).status_code == 409


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def test_an_individual_applicant_must_have_a_date_of_birth(
    client: TestClient, admin_headers, customer_payload
):
    """Age and age-at-maturity are scored components; without a birth date they cannot be
    computed and the application would be silently under-assessed."""
    payload = {k: v for k, v in customer_payload.items() if k != "date_of_birth"}
    payload["id_number"] = _unique_id_number()
    response = client.post("/api/v1/customers", json=payload, headers=admin_headers)
    assert response.status_code == 422


def test_a_business_applicant_must_have_a_registration_number(
    client: TestClient, admin_headers, customer_payload
):
    payload = {
        **customer_payload,
        "applicant_type": "FLEET_OPERATOR",
        "id_number": _unique_id_number(),
        "id_type": "COMPANY_REG",
    }
    response = client.post("/api/v1/customers", json=payload, headers=admin_headers)
    assert response.status_code == 422


def test_a_licence_expiring_before_birth_is_rejected(
    client: TestClient, admin_headers, customer_payload
):
    payload = {**customer_payload, "licence_expiry": "1980-01-01",
               "id_number": _unique_id_number()}
    assert client.post(
        "/api/v1/customers", json=payload, headers=admin_headers
    ).status_code == 422


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ward_no", 99),
        ("driving_experience_years", "-2"),
        ("total_experience_years", "120"),
        ("applicant_type", "ROBOT"),
        ("email", "not-an-email"),
        ("phone", "x"),
    ],
)
def test_out_of_range_customer_fields_are_rejected(
    client: TestClient, admin_headers, customer_payload, field, value
):
    payload = {**customer_payload, field: value, "id_number": _unique_id_number()}
    assert client.post(
        "/api/v1/customers", json=payload, headers=admin_headers
    ).status_code == 422


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------
def test_a_customer_can_be_updated(
    client: TestClient, admin_headers, created_customer, customer_payload
):
    body = {**customer_payload, "full_name": "Test Applicant One Revised"}
    body.pop("id_number", None)
    response = client.put(
        f"/api/v1/customers/{created_customer['id']}", json=body, headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json()["full_name"] == "Test Applicant One Revised"


def test_the_identifier_cannot_be_changed_by_an_update(
    client: TestClient, admin_headers, created_customer, customer_payload
):
    """Changing it would orphan every decision already made against the old value."""
    before = created_customer["id_number_masked"]
    body = {**customer_payload, "id_number": "27-77-70-99999", "id_type": "PASSPORT"}
    response = client.put(
        f"/api/v1/customers/{created_customer['id']}", json=body, headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json()["id_number_masked"] == before


# ---------------------------------------------------------------------------
# Financial profile
# ---------------------------------------------------------------------------
def test_a_financial_profile_derives_its_totals_from_the_obligations(financial_profile):
    """Doc 05 §5.3.7 — a client-supplied total would be an unverified input to the DTI."""
    assert Decimal(financial_profile["total_existing_emi"]) == Decimal("12400.00")
    assert financial_profile["existing_loan_count"] == 1
    assert Decimal(financial_profile["total_monthly_income"]) == Decimal("145000.00")


def test_the_first_profile_is_version_one_and_current(financial_profile):
    assert financial_profile["version_no"] == 1
    assert financial_profile["is_current"] is True


def test_recording_a_profile_again_creates_a_new_version(
    client: TestClient, admin_headers, created_customer, financial_profile
):
    """History is kept: an assessment must stay attributable to the figures it saw."""
    response = client.post(
        f"/api/v1/customers/{created_customer['id']}/financials",
        json={"monthly_income": "160000", "monthly_household_expenses": "60000"},
        headers=admin_headers,
    )
    assert response.status_code == 201
    assert response.json()["version_no"] == 2
    assert response.json()["is_current"] is True


def test_the_customer_detail_shows_the_current_profile(
    client: TestClient, admin_headers, created_customer, financial_profile
):
    body = client.get(
        f"/api/v1/customers/{created_customer['id']}", headers=admin_headers
    ).json()
    assert body["current_financial_profile"]["version_no"] == 1
    assert len(body["obligations"]) == 1


# ---------------------------------------------------------------------------
# Credit assessment
# ---------------------------------------------------------------------------
def test_a_credit_assessment_runs_end_to_end(client: TestClient, admin_headers, application):
    response = client.post(
        f"/api/v1/customers/{application['customer']['id']}/credit-assess",
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text

    body = response.json()
    assert Decimal("0") <= Decimal(body["total_score"]) <= Decimal("100")
    assert body["grade"] in {"A", "B", "C", "D", "E"}
    assert body["is_latest"] is True


def test_the_assessment_reports_the_affordability_ratios(
    client: TestClient, admin_headers, application
):
    """DTI and FOIR are what an officer argues about; they must be on the response."""
    body = client.post(
        f"/api/v1/customers/{application['customer']['id']}/credit-assess",
        headers=admin_headers,
    ).json()
    assert Decimal(body["dti_ratio"]) > 0
    assert Decimal(body["foir_ratio"]) > 0
    assert Decimal(body["disposable_income"]) != 0


def test_the_assessment_carries_the_component_breakdown_and_the_stamp(
    client: TestClient, admin_headers, application
):
    body = client.post(
        f"/api/v1/customers/{application['customer']['id']}/credit-assess",
        headers=admin_headers,
    ).json()
    assert len(body["components"]) == 6, "the documented CUSTOMER scorecard has 6 components"
    assert body["config_version"] == 1
    assert body["engine_version"].startswith("customer-engine@")
    assert body["explanation"]


def test_the_vehicle_economics_are_returned_with_the_assessment(
    client: TestClient, admin_headers, application
):
    """The unit economics are why an EV loan is affordable at all; they belong on the
    decision, not in a separate call."""
    body = client.post(
        f"/api/v1/customers/{application['customer']['id']}/credit-assess",
        headers=admin_headers,
    ).json()
    economics = body["vehicle_economics"]
    assert economics is not None
    assert Decimal(economics["monthly_net_contribution"]) != 0


def test_assessing_twice_supersedes_the_earlier_score(
    client: TestClient, admin_headers, application
):
    """Scores are immutable, so a re-run appends and demotes the previous one."""
    url = f"/api/v1/customers/{application['customer']['id']}/credit-assess"
    first = client.post(url, headers=admin_headers).json()
    second = client.post(url, headers=admin_headers).json()
    assert first["id"] != second["id"]

    history = client.get(
        f"/api/v1/customers/{application['customer']['id']}/credit-assessments",
        headers=admin_headers,
    ).json()
    assert history["total"] == 2
    assert sum(1 for row in history["items"] if row["is_latest"]) == 1


def test_the_same_inputs_produce_the_same_score(
    client: TestClient, admin_headers, application
):
    url = f"/api/v1/customers/{application['customer']['id']}/credit-assess"
    first = client.post(url, json={"as_of": "2026-01-15"}, headers=admin_headers).json()
    second = client.post(url, json={"as_of": "2026-01-15"}, headers=admin_headers).json()
    assert first["total_score"] == second["total_score"]
    assert first["components"] == second["components"]


def test_the_business_date_is_supplied_by_the_caller_not_read_from_a_clock(
    client: TestClient, admin_headers, application
):
    """The engine is pure; ageing the business date must change the age-derived inputs and
    so the score, which proves the date really is an input."""
    url = f"/api/v1/customers/{application['customer']['id']}/credit-assess"
    young = client.post(url, json={"as_of": "2026-01-15"}, headers=admin_headers).json()
    old = client.post(url, json={"as_of": "2045-01-15"}, headers=admin_headers).json()
    assert young["total_score"] != old["total_score"]


def test_assessing_a_customer_with_no_application_is_a_422(
    client: TestClient, admin_headers, created_customer
):
    response = client.post(
        f"/api/v1/customers/{created_customer['id']}/credit-assess", headers=admin_headers
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INCOMPLETE_DATA"
    assert response.json()["error"]["details"][0]["field"] == "application_id"


def test_assessing_an_unknown_customer_is_a_404(client: TestClient, admin_headers):
    response = client.post(
        "/api/v1/customers/00000000-0000-0000-0000-000000000000/credit-assess",
        headers=admin_headers,
    )
    assert response.status_code == 404


def test_another_customers_application_cannot_be_assessed(
    client: TestClient, admin_headers, application, customer_payload
):
    """Passing someone else's application id must not cross the file boundary."""
    stranger = client.post(
        "/api/v1/customers",
        json={**customer_payload, "id_number": _unique_id_number(),
              "full_name": "Unrelated Applicant"},
        headers=admin_headers,
    )
    assert stranger.status_code == 201, stranger.text

    response = client.post(
        f"/api/v1/customers/{stranger.json()['id']}/credit-assess",
        json={"application_id": application["application_id"]},
        headers=admin_headers,
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "LOAN_APPLICATION_NOT_FOUND"


def test_the_assessment_history_is_empty_before_any_assessment(
    client: TestClient, admin_headers, created_customer
):
    body = client.get(
        f"/api/v1/customers/{created_customer['id']}/credit-assessments",
        headers=admin_headers,
    ).json()
    assert body["total"] == 0
    assert body["items"] == []


# ---------------------------------------------------------------------------
# Listing and authorisation
# ---------------------------------------------------------------------------
def test_customers_can_be_searched_by_name(
    client: TestClient, admin_headers, created_customer
):
    body = client.get(
        "/api/v1/customers", params={"q": "Test Applicant"}, headers=admin_headers
    ).json()
    assert body["total"] >= 1


def test_the_customer_list_is_paginated(client: TestClient, admin_headers, created_customer):
    body = client.get(
        "/api/v1/customers", params={"page_size": 1}, headers=admin_headers
    ).json()
    assert len(body["items"]) <= 1
    assert {"total", "page", "page_size", "pages"} <= set(body)


def test_the_list_never_carries_an_identifier(
    client: TestClient, admin_headers, created_customer
):
    body = client.get("/api/v1/customers", headers=admin_headers).json()
    for row in body["items"]:
        assert "id_number" not in row
        assert "id_number_masked" not in row


def test_a_viewer_may_not_register_a_customer(
    client: TestClient, viewer_headers, customer_payload
):
    payload = {**customer_payload, "id_number": _unique_id_number()}
    response = client.post("/api/v1/customers", json=payload, headers=viewer_headers)
    assert response.status_code == 403
    assert response.json()["error"]["details"]["required_permission"] == "applicant:create"


def test_a_viewer_may_not_run_a_credit_assessment(
    client: TestClient, viewer_headers, application
):
    response = client.post(
        f"/api/v1/customers/{application['customer']['id']}/credit-assess",
        headers=viewer_headers,
    )
    assert response.status_code == 403


def test_a_credit_officer_may_run_a_credit_assessment(
    client: TestClient, officer_headers, application
):
    response = client.post(
        f"/api/v1/customers/{application['customer']['id']}/credit-assess",
        headers=officer_headers,
    )
    assert response.status_code == 201


def test_an_anonymous_customer_request_is_401(client: TestClient):
    assert client.get("/api/v1/customers").status_code == 401
