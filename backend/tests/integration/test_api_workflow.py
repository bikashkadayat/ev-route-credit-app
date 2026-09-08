"""The financing workflow, end to end — Doc 06 §6.6, §6.8.

Applicant → application → submit → assess → decide → book → schedule → repay → DPD →
classification → alerts, all through the API against real PostgreSQL. This is the path a
loan actually takes, and until now none of it was reachable over HTTP: the FinalRiskEngine
and the decision matrix were implemented and tested but had no route into them.
"""

from __future__ import annotations

import itertools
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.integration.conftest import auth

_SEQUENCE = itertools.count(1)


@pytest.fixture
def officer_a(client: TestClient) -> dict:
    """The maker. Raises and submits applications.

    A RISK_MANAGER rather than a credit officer, deliberately: they *hold* the approval
    permission, so when they are refused their own file it is the maker-checker rule
    refusing them and not a missing permission. That is the case worth testing — the rule
    has to bite someone who could otherwise approve.
    """
    return auth("RISK_MANAGER", user_id=2)


@pytest.fixture
def officer_b(client: TestClient) -> dict:
    """The checker. A different person, so a decision is permitted."""
    return auth("RISK_MANAGER", user_id=5)


@pytest.fixture
def applicant(client: TestClient, admin_headers, customer_payload) -> dict:
    n = next(_SEQUENCE)
    created = client.post(
        "/api/v1/customers",
        json={**customer_payload, "id_number": f"27-44-70-{n:05d}",
              "phone": f"+97798444{n:05d}", "full_name": f"Workflow Applicant {n:03d}"},
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text

    profile = client.post(
        f"/api/v1/customers/{created.json()['id']}/financials",
        json={
            "monthly_income": "145000",
            "monthly_household_expenses": "60000",
            "avg_bank_balance_6m": "83000",
            "dependants_count": 3,
            "income_proof_type": "BANK_STATEMENT",
            "income_verified": True,
            "obligations": [{
                "lender_name": "Nabil Bank", "loan_type": "PERSONAL",
                "original_amount": "500000", "outstanding_amount": "310000",
                "monthly_emi": "12400", "remaining_tenure_months": 26,
                "is_overdue": False, "days_past_due": 0, "source": "CIB",
            }],
        },
        headers=admin_headers,
    )
    assert profile.status_code == 201, profile.text
    return created.json()


@pytest.fixture
def vehicle_and_route(engine, client: TestClient, admin_headers) -> dict:
    """A vehicle plus the best-scoring seeded corridor.

    Vehicles have no public write endpoint yet, so the row is inserted directly; every
    assertion still runs through the API.
    """
    with engine.begin() as conn:
        model_id = conn.execute(
            text("SELECT id FROM vehicle_models ORDER BY real_world_range_km DESC LIMIT 1")
        ).scalar_one()
        vehicle_uuid = conn.execute(
            text(
                "INSERT INTO vehicles (vehicle_model_id, purchase_price, is_new, status)"
                " VALUES (:m, 4600000, true, 'PROPOSED') RETURNING uuid"
            ),
            {"m": model_id},
        ).scalar_one()

    routes = client.get(
        "/api/v1/routes", params={"page_size": 50}, headers=admin_headers
    ).json()["items"]
    best = max(
        (r for r in routes if r["latest_score"]),
        key=lambda r: Decimal(r["latest_score"]),
    )
    return {"vehicle_id": str(vehicle_uuid), "route_id": best["id"]}


@pytest.fixture
def application_payload(applicant, vehicle_and_route) -> dict:
    return {
        "applicant_id": applicant["id"],
        "vehicle_id": vehicle_and_route["vehicle_id"],
        "route_id": vehicle_and_route["route_id"],
        "requested_amount": "3200000",
        "requested_tenure_months": 60,
        "proposed_interest_rate": "13.0",
        "down_payment_amount": "1400000",
        "expected_daily_km": "240",
        "expected_operating_days_month": 26,
        "purpose": "Taxi operation on the Kathmandu-Dhulikhel corridor",
    }


@pytest.fixture
def draft(client: TestClient, officer_a, application_payload) -> dict:
    created = client.post(
        "/api/v1/applications", json=application_payload, headers=officer_a
    )
    assert created.status_code == 201, created.text
    return created.json()


@pytest.fixture
def submitted(client: TestClient, officer_a, draft) -> dict:
    response = client.post(
        f"/api/v1/applications/{draft['id']}/submit", headers=officer_a
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def assessed(client: TestClient, officer_a, submitted) -> dict:
    response = client.post(
        f"/api/v1/applications/{submitted['id']}/assess", headers=officer_a
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def approved(client: TestClient, officer_b, assessed) -> dict:
    """Approved by a *different* officer, as maker-checker requires."""
    body = {
        "final_decision": "APPROVE",
        "override_justification": (
            "Route grade and vehicle economics support the exposure at the recommended "
            "structure; approving on the committee's standing delegation."
        ),
    }
    response = client.post(
        f"/api/v1/applications/{assessed['application_id']}/decision",
        json=body,
        headers=officer_b,
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def booked(client: TestClient, admin_headers, approved, assessed) -> dict:
    response = client.post(
        f"/api/v1/applications/{assessed['application_id']}/book",
        json={"disbursement_date": "2026-03-01", "first_emi_date": "2026-04-01"},
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Creation and the draft state
# ---------------------------------------------------------------------------
def test_an_application_starts_as_a_draft(draft):
    assert draft["status"] == "DRAFT"
    assert draft["application_number"].startswith("LA-")


def test_the_on_road_price_is_snapshotted_from_the_vehicle(draft):
    """Referencing it live would let a later price change rewrite the LTV a decision was
    taken on."""
    assert Decimal(draft["vehicle_on_road_price"]) == Decimal("4600000.00")


def test_the_financial_profile_version_is_recorded(draft):
    assert draft["financial_profile_version"] == 1


def test_an_applicant_without_a_financial_profile_cannot_apply(
    client: TestClient, officer_a, admin_headers, customer_payload, vehicle_and_route
):
    """Doc 06 §6.6 — without income figures there is nothing to assess affordability with."""
    n = next(_SEQUENCE)
    bare = client.post(
        "/api/v1/customers",
        json={**customer_payload, "id_number": f"27-45-70-{n:05d}",
              "phone": f"+97798445{n:05d}"},
        headers=admin_headers,
    ).json()

    response = client.post(
        "/api/v1/applications",
        json={
            "applicant_id": bare["id"],
            "vehicle_id": vehicle_and_route["vehicle_id"],
            "route_id": vehicle_and_route["route_id"],
            "requested_amount": "3200000", "requested_tenure_months": 60,
            "proposed_interest_rate": "13.0", "down_payment_amount": "1400000",
            "expected_daily_km": "240",
        },
        headers=officer_a,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INCOMPLETE_DATA"


def test_a_down_payment_above_the_price_is_rejected(
    client: TestClient, officer_a, application_payload
):
    response = client.post(
        "/api/v1/applications",
        json={**application_payload, "down_payment_amount": "9000000"},
        headers=officer_a,
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("requested_tenure_months", 3),
        ("requested_tenure_months", 200),
        ("proposed_interest_rate", "45"),
        ("requested_amount", "0"),
        ("expected_daily_km", "5000"),
    ],
)
def test_out_of_range_terms_are_rejected(
    client: TestClient, officer_a, application_payload, field, value
):
    response = client.post(
        "/api/v1/applications",
        json={**application_payload, field: value},
        headers=officer_a,
    )
    assert response.status_code == 422


def test_a_draft_can_be_edited(client: TestClient, officer_a, draft):
    response = client.patch(
        f"/api/v1/applications/{draft['id']}",
        json={"requested_amount": "3000000"},
        headers=officer_a,
    )
    assert response.status_code == 200
    assert Decimal(response.json()["requested_amount"]) == Decimal("3000000.00")


def test_a_submitted_application_cannot_be_edited(client: TestClient, officer_a, submitted):
    """Doc 06 §6.6 — the terms are what the assessment runs against. Changing them after
    submission is how a file gets assessed on one set of numbers and approved on another."""
    response = client.patch(
        f"/api/v1/applications/{submitted['id']}",
        json={"requested_amount": "9000000"},
        headers=officer_a,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_STATE_TRANSITION"


def test_the_applicant_cannot_be_switched_by_an_edit(
    client: TestClient, officer_a, draft, applicant
):
    response = client.patch(
        f"/api/v1/applications/{draft['id']}",
        json={"applicant_id": applicant["id"], "requested_amount": "3000000"},
        headers=officer_a,
    )
    assert response.status_code == 422
    assert "applicant_id" in {
        d["field"] for d in response.json()["error"]["details"]
    }


# ---------------------------------------------------------------------------
# The state machine
# ---------------------------------------------------------------------------
def test_a_draft_can_be_submitted(submitted):
    assert submitted["status"] == "SUBMITTED"
    assert submitted["submitted_at"]


def test_submitting_twice_is_a_409(client: TestClient, officer_a, submitted):
    response = client.post(
        f"/api/v1/applications/{submitted['id']}/submit", headers=officer_a
    )
    assert response.status_code == 409


def test_a_draft_cannot_be_assessed_before_submission(client: TestClient, officer_a, draft):
    response = client.post(
        f"/api/v1/applications/{draft['id']}/assess", headers=officer_a
    )
    assert response.status_code == 409
    assert "SUBMITTED" in str(response.json()["error"]["details"])


def test_an_application_can_be_withdrawn(client: TestClient, officer_a, draft):
    response = client.post(
        f"/api/v1/applications/{draft['id']}/withdraw",
        json={"reason": "Applicant bought the vehicle outright"},
        headers=officer_a,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "WITHDRAWN"


def test_a_withdrawn_application_cannot_be_resubmitted(client: TestClient, officer_a, draft):
    """Withdrawal is terminal. Letting a withdrawn file walk back into the pipeline would
    make the audit trail meaningless."""
    client.post(f"/api/v1/applications/{draft['id']}/withdraw", headers=officer_a)
    response = client.post(
        f"/api/v1/applications/{draft['id']}/submit", headers=officer_a
    )
    assert response.status_code == 409


def test_a_withdrawn_application_cannot_be_assessed(client: TestClient, officer_a, submitted):
    client.post(f"/api/v1/applications/{submitted['id']}/withdraw", headers=officer_a)
    response = client.post(
        f"/api/v1/applications/{submitted['id']}/assess", headers=officer_a
    )
    assert response.status_code == 409


def test_a_decision_before_assessment_is_refused(client: TestClient, officer_b, submitted):
    response = client.post(
        f"/api/v1/applications/{submitted['id']}/decision",
        json={"final_decision": "APPROVE"},
        headers=officer_b,
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------
def test_the_assessment_runs_every_leg(assessed):
    """Route, customer, vehicle economics and the combined score — the whole pipeline in
    one call, which is what the UI's "Run Full Assessment" button needs."""
    assert assessed["route"]["grade"] in {"A", "B", "C"}
    assert Decimal(assessed["customer"]["total_score"]) > 0
    assert Decimal(assessed["vehicle_economics"]["monthly_net_contribution"]) != 0
    assert Decimal(assessed["final"]["final_score"]) > 0


def test_the_assessment_moves_the_application_to_pending_decision(assessed):
    assert assessed["status"] == "PENDING_DECISION"


def test_the_final_score_is_the_weighted_combination(assessed):
    """The three legs and their weights must reconcile to the final score, or the number
    an underwriter sees is not the one the engine computed."""
    final = assessed["final"]
    weights = final["weights"]
    expected = (
        Decimal(assessed["route"]["score"]) * Decimal(str(weights["route"]))
        + Decimal(assessed["customer"]["total_score"]) * Decimal(str(weights["customer"]))
        + Decimal(assessed["vehicle_economics"]["score"]) * Decimal(str(weights["vehicle"]))
    )
    assert abs(Decimal(final["final_score"]) - expected) <= Decimal("0.05")


def test_the_assessment_carries_a_recommendation_and_reasons(assessed):
    """Doc 01 FR-4.4 — no decision without reasons."""
    final = assessed["final"]
    assert final["recommendation"] in {"APPROVE", "MANUAL_REVIEW", "REJECT"}
    if final["recommendation"] != "APPROVE":
        assert final["reasons"] or final["failed_gates"]


def test_the_assessment_recommends_a_structure(assessed):
    structure = assessed["final"]["structure"]
    assert Decimal(structure["recommended_amount"]) > 0
    assert Decimal(structure["estimated_emi"]) > 0
    assert 0 < Decimal(structure["applied_ltv_percent"]) <= Decimal(
        structure["max_ltv_percent"]
    )


def test_the_assessment_stamps_the_config_and_engine_versions(assessed):
    """Doc 02 §2.4.4 — without this a stored score cannot be reproduced."""
    assert assessed["config_versions"]["ROUTE"] == 2
    assert assessed["engine_versions"]["final"].startswith("final-engine@")


def test_the_route_assessment_is_reused_rather_than_re_scored(assessed):
    """A corridor is scored once and shared. Re-scoring per application would bury the
    assessment that actually informed the decision under identical copies."""
    assert assessed["route"]["is_reused"] is True


def test_re_assessing_supersedes_the_previous_assessment(
    client: TestClient, officer_a, assessed
):
    second = client.post(
        f"/api/v1/applications/{assessed['application_id']}/assess", headers=officer_a
    )
    assert second.status_code == 201
    assert second.json()["assessment_id"] != assessed["assessment_id"]

    detail = client.get(
        f"/api/v1/applications/{assessed['application_id']}", headers=officer_a
    ).json()
    assert detail["latest_assessment"]["id"] == second.json()["assessment_id"]


def test_the_same_application_assessed_twice_scores_the_same(
    client: TestClient, officer_a, assessed
):
    """The engines are pure, so two runs a moment apart must not differ."""
    second = client.post(
        f"/api/v1/applications/{assessed['application_id']}/assess", headers=officer_a
    ).json()
    assert second["final"]["final_score"] == assessed["final"]["final_score"]


def test_a_viewer_cannot_assess(client: TestClient, viewer_headers, submitted):
    response = client.post(
        f"/api/v1/applications/{submitted['id']}/assess", headers=viewer_headers
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Four-eyes — Doc 09 §9.3.3
# ---------------------------------------------------------------------------
def test_the_maker_cannot_approve_their_own_application(
    client: TestClient, officer_a, assessed
):
    """The whole point of maker-checker: one officer cannot both raise and approve a file."""
    response = client.post(
        f"/api/v1/applications/{assessed['application_id']}/decision",
        json={"final_decision": "APPROVE"},
        headers=officer_a,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "FOUR_EYES_REQUIRED"


def test_a_different_officer_may_decide(approved):
    assert approved["final_decision"] == "APPROVE"


def test_the_maker_cannot_decline_their_own_application_either(
    client: TestClient, officer_a, assessed
):
    """The rule is about independence, not about the direction of the decision."""
    response = client.post(
        f"/api/v1/applications/{assessed['application_id']}/decision",
        json={"final_decision": "REJECT",
              "override_justification": "Declining on the officer's own recommendation."},
        headers=officer_a,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "FOUR_EYES_REQUIRED"


def test_four_eyes_is_enforced_in_the_domain_not_the_client(
    client: TestClient, officer_a, assessed
):
    """Enforced by the service, so it holds however the API is reached — a client that
    simply omits the check cannot get past it."""
    from app.services.application import ApplicationService

    assert hasattr(ApplicationService, "_enforce_maker_checker")
    response = client.post(
        f"/api/v1/applications/{assessed['application_id']}/decision",
        json={"final_decision": "APPROVE"},
        headers=officer_a,
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Decision and override
# ---------------------------------------------------------------------------
def test_the_decision_records_the_system_recommendation_alongside_the_outcome(approved):
    """Doc 06 §6.6 — the original recommendation is preserved, so an override is visible
    rather than replacing what the engine said."""
    assert approved["system_recommendation"] in {"APPROVE", "MANUAL_REVIEW", "REJECT"}
    assert approved["final_decision"] == "APPROVE"


def test_an_override_requires_a_justification(client: TestClient, officer_b, assessed):
    recommendation = assessed["final"]["recommendation"]
    contrary = "REJECT" if recommendation == "APPROVE" else "APPROVE"

    response = client.post(
        f"/api/v1/applications/{assessed['application_id']}/decision",
        json={"final_decision": contrary, "override_justification": "too short"},
        headers=officer_b,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "OVERRIDE_JUSTIFICATION_REQUIRED"


def test_an_override_without_the_permission_is_refused(client: TestClient, assessed):
    """Deciding and overriding are separate permissions. An approver who may record the
    system's recommendation may not substitute their own judgement for it."""
    from tests.integration.conftest import token_for

    recommendation = assessed["final"]["recommendation"]
    if recommendation == "APPROVE":
        pytest.skip("the engine already recommends approval; nothing to override here")

    approver_only = token_for(
        "RISK_MANAGER",
        user_id=6,
        permissions=["application:read", "application:approve"],
    )
    response = client.post(
        f"/api/v1/applications/{assessed['application_id']}/decision",
        json={
            "final_decision": "APPROVE",
            "override_justification": (
                "Overriding on the strength of the corridor and the borrower's history."
            ),
        },
        headers={"Authorization": f"Bearer {approver_only}"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["details"]["required_permission"] == (
        "application:override"
    )


def test_an_override_is_flagged_and_audited(client: TestClient, officer_b, assessed, engine):
    recommendation = assessed["final"]["recommendation"]
    if recommendation == "APPROVE":
        pytest.skip("the engine already recommends approval; nothing to override here")

    response = client.post(
        f"/api/v1/applications/{assessed['application_id']}/decision",
        json={
            "final_decision": "APPROVE",
            "override_justification": (
                "Committee approved on the strength of the corridor grade and the "
                "borrower's twelve-year operating history."
            ),
        },
        headers=officer_b,
    )
    assert response.status_code == 201
    assert response.json()["is_override"] is True

    with engine.begin() as conn:
        overrides = conn.execute(
            text("SELECT count(*) FROM audit_logs WHERE action = 'DECISION_OVERRIDE'")
        ).scalar_one()
    assert overrides >= 1


def test_the_decision_history_keeps_the_trail(client: TestClient, officer_b, approved, assessed):
    history = client.get(
        f"/api/v1/applications/{assessed['application_id']}/decisions",
        headers=officer_b,
    )
    assert history.status_code == 200
    assert len(history.json()) >= 1
    assert history.json()[0]["system_recommendation"]


def test_an_approval_above_the_ltv_ceiling_is_refused(
    client: TestClient, officer_b, assessed
):
    """Doc 06 §6.6 — the cap comes from the assessment's own max_ltv_percent, which
    structuring derived from the grade and the configured policy."""
    response = client.post(
        f"/api/v1/applications/{assessed['application_id']}/decision",
        json={
            "final_decision": "APPROVE",
            "approved_amount": "4500000",
            "override_justification": (
                "Approving above the recommended structure on the committee's mandate."
            ),
        },
        headers=officer_b,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "POLICY_LIMIT_EXCEEDED"


def test_deciding_twice_is_refused(client: TestClient, officer_b, approved, assessed):
    """The application has left PENDING_DECISION, so a second decision has nothing to
    decide on."""
    response = client.post(
        f"/api/v1/applications/{assessed['application_id']}/decision",
        json={"final_decision": "REJECT",
              "override_justification": "Changed our mind after the approval was recorded."},
        headers=officer_b,
    )
    assert response.status_code == 409


def test_an_approval_moves_the_application_to_approved(
    client: TestClient, officer_b, approved, assessed
):
    detail = client.get(
        f"/api/v1/applications/{assessed['application_id']}", headers=officer_b
    ).json()
    assert detail["status"] == "APPROVED"
    assert detail["decided_at"]


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------
def test_an_approved_application_can_be_booked(booked):
    loan = booked["loan"]
    assert loan["loan_account_number"].startswith("LN-")
    assert loan["status"] == "ACTIVE"
    assert loan["classification"] == "PERFORMING"
    assert Decimal(loan["outstanding_principal"]) == Decimal(loan["principal_amount"])


def test_booking_generates_the_whole_schedule(booked):
    summary = booked["schedule_summary"]
    assert summary["installment_count"] == booked["loan"]["tenure_months"]
    assert summary["first_due"] == "2026-04-01"


def test_the_booked_terms_come_from_the_decision_not_the_request(booked, approved):
    """A client must not be able to book different terms from the ones approved."""
    assert Decimal(booked["loan"]["principal_amount"]) == Decimal(
        approved["approved_amount"]
    )
    assert booked["loan"]["tenure_months"] == approved["approved_tenure_months"]


def test_an_unapproved_application_cannot_be_booked(
    client: TestClient, admin_headers, submitted
):
    response = client.post(
        f"/api/v1/applications/{submitted['id']}/book", headers=admin_headers
    )
    assert response.status_code == 409


def test_the_same_application_cannot_be_booked_twice(
    client: TestClient, admin_headers, booked, assessed
):
    """One loan per application, enforced in the service and by a unique constraint."""
    response = client.post(
        f"/api/v1/applications/{assessed['application_id']}/book", headers=admin_headers
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DUPLICATE_RESOURCE"


def test_booking_disburses_the_application_and_finances_the_vehicle(
    client: TestClient, admin_headers, booked, assessed, engine
):
    detail = client.get(
        f"/api/v1/applications/{assessed['application_id']}", headers=admin_headers
    ).json()
    assert detail["status"] == "DISBURSED"

    with engine.begin() as conn:
        vehicle_status = conn.execute(
            text("SELECT v.status FROM vehicles v JOIN loans l ON l.vehicle_id = v.id "
                 "WHERE l.loan_account_number = :n"),
            {"n": booked["loan"]["loan_account_number"]},
        ).scalar_one()
    assert vehicle_status == "FINANCED"


def test_a_first_instalment_beyond_forty_five_days_is_refused(
    client: TestClient, admin_headers, approved, assessed
):
    """Doc 06 §6.8 — interest would accrue unbilled for months otherwise."""
    response = client.post(
        f"/api/v1/applications/{assessed['application_id']}/book",
        json={"disbursement_date": "2026-03-01", "first_emi_date": "2026-06-01"},
        headers=admin_headers,
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------
def test_the_schedule_is_readable(client: TestClient, admin_headers, booked):
    response = client.get(
        f"/api/v1/loans/{booked['loan']['id']}/schedule",
        params={"page_size": 100},
        headers=admin_headers,
    )
    assert response.status_code == 200
    rows = response.json()["items"]
    assert rows[0]["installment_no"] == 1
    assert rows[0]["status"] == "PENDING"


def test_the_schedule_amortises_to_zero(client: TestClient, admin_headers, booked):
    """The final instalment must clear the balance exactly; a residue would leave a loan
    that can never be closed."""
    rows = client.get(
        f"/api/v1/loans/{booked['loan']['id']}/schedule",
        params={"page_size": 100}, headers=admin_headers,
    ).json()["items"]
    assert Decimal(rows[-1]["closing_balance"]) == Decimal("0.00")


def test_the_principal_repaid_equals_the_amount_financed(
    client: TestClient, admin_headers, booked
):
    rows = client.get(
        f"/api/v1/loans/{booked['loan']['id']}/schedule",
        params={"page_size": 100}, headers=admin_headers,
    ).json()["items"]
    total_principal = sum(Decimal(r["principal_due"]) for r in rows)
    assert total_principal == Decimal(booked["loan"]["principal_amount"])


def test_each_row_opens_where_the_previous_one_closed(
    client: TestClient, admin_headers, booked
):
    rows = client.get(
        f"/api/v1/loans/{booked['loan']['id']}/schedule",
        params={"page_size": 100}, headers=admin_headers,
    ).json()["items"]
    for previous, current in itertools.pairwise(rows):
        assert Decimal(current["opening_balance"]) == Decimal(previous["closing_balance"])


def test_instalments_fall_one_month_apart(client: TestClient, admin_headers, booked):
    rows = client.get(
        f"/api/v1/loans/{booked['loan']['id']}/schedule",
        params={"page_size": 100}, headers=admin_headers,
    ).json()["items"]
    assert rows[0]["due_date"] == "2026-04-01"
    assert rows[1]["due_date"] == "2026-05-01"


# ---------------------------------------------------------------------------
# Repayment
# ---------------------------------------------------------------------------
def test_a_full_instalment_settles_it(client: TestClient, admin_headers, booked):
    schedule = client.get(
        f"/api/v1/loans/{booked['loan']['id']}/schedule", headers=admin_headers
    ).json()["items"]
    due = Decimal(schedule[0]["total_due"])

    response = client.post(
        f"/api/v1/loans/{booked['loan']['id']}/repayments",
        json={"payment_date": "2026-04-01", "amount_paid": str(due),
              "payment_mode": "BANK_TRANSFER", "reference_number": "TXN-1"},
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text

    body = response.json()
    assert body["allocation"]["settled_installments"] == [1]
    assert body["loan"]["installments_paid"] == 1


def test_the_allocation_follows_the_documented_order(
    client: TestClient, admin_headers, booked
):
    """Doc 06 §6.8 — interest before principal. A partial payment must not quietly reduce
    the interest owed."""
    response = client.post(
        f"/api/v1/loans/{booked['loan']['id']}/repayments",
        json={"payment_date": "2026-04-01", "amount_paid": "1000",
              "payment_mode": "CASH"},
        headers=admin_headers,
    ).json()
    assert Decimal(response["allocation"]["interest"]) == Decimal("1000.00")
    assert Decimal(response["allocation"]["principal"]) == Decimal("0.00")


def test_an_overpayment_is_reported_as_unallocated(
    client: TestClient, admin_headers, booked
):
    loan_id = booked["loan"]["id"]
    payable = Decimal(booked["schedule_summary"]["total_payable"])

    response = client.post(
        f"/api/v1/loans/{loan_id}/repayments",
        json={"payment_date": "2026-04-01",
              "amount_paid": str(payable + Decimal("50000")),
              "payment_mode": "BANK_TRANSFER"},
        headers=admin_headers,
    ).json()
    assert Decimal(response["allocation"]["unallocated"]) == Decimal("50000.00")
    assert response["repayment"]["is_advance"] is True


def test_paying_the_whole_book_closes_the_loan(client: TestClient, admin_headers, booked):
    loan_id = booked["loan"]["id"]
    payable = Decimal(booked["schedule_summary"]["total_payable"])

    client.post(
        f"/api/v1/loans/{loan_id}/repayments",
        json={"payment_date": "2026-04-01", "amount_paid": str(payable),
              "payment_mode": "BANK_TRANSFER"},
        headers=admin_headers,
    )
    loan = client.get(f"/api/v1/loans/{loan_id}", headers=admin_headers).json()
    assert loan["status"] == "CLOSED"
    assert Decimal(loan["outstanding_principal"]) == Decimal("0.00")


def test_no_payment_can_be_posted_to_a_closed_loan(
    client: TestClient, admin_headers, booked
):
    loan_id = booked["loan"]["id"]
    payable = Decimal(booked["schedule_summary"]["total_payable"])
    client.post(
        f"/api/v1/loans/{loan_id}/repayments",
        json={"payment_date": "2026-04-01", "amount_paid": str(payable),
              "payment_mode": "BANK_TRANSFER"},
        headers=admin_headers,
    )
    response = client.post(
        f"/api/v1/loans/{loan_id}/repayments",
        json={"payment_date": "2026-05-01", "amount_paid": "1000",
              "payment_mode": "CASH"},
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_a_repeated_idempotency_key_does_not_post_twice(
    client: TestClient, admin_headers, booked
):
    """Doc 06 §6.8 — a retried request must not take the borrower's money twice."""
    loan_id = booked["loan"]["id"]
    body = {"payment_date": "2026-04-01", "amount_paid": "20000", "payment_mode": "CASH"}
    headers = {**admin_headers, "Idempotency-Key": "retry-key-001"}

    first = client.post(f"/api/v1/loans/{loan_id}/repayments", json=body, headers=headers)
    second = client.post(f"/api/v1/loans/{loan_id}/repayments", json=body, headers=headers)

    assert first.status_code == second.status_code == 201
    assert first.json()["repayment"]["receipt_number"] == (
        second.json()["repayment"]["receipt_number"]
    )

    history = client.get(
        f"/api/v1/loans/{loan_id}/repayments", headers=admin_headers
    ).json()
    assert history["total"] == 1


def test_a_repayment_appears_in_the_history(client: TestClient, admin_headers, booked):
    loan_id = booked["loan"]["id"]
    client.post(
        f"/api/v1/loans/{loan_id}/repayments",
        json={"payment_date": "2026-04-01", "amount_paid": "20000",
              "payment_mode": "CASH"},
        headers=admin_headers,
    )
    history = client.get(f"/api/v1/loans/{loan_id}/repayments", headers=admin_headers)
    assert history.status_code == 200
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["receipt_number"].startswith("RC-")


def test_a_viewer_cannot_post_a_repayment(client: TestClient, viewer_headers, booked):
    response = client.post(
        f"/api/v1/loans/{booked['loan']['id']}/repayments",
        json={"payment_date": "2026-04-01", "amount_paid": "1000",
              "payment_mode": "CASH"},
        headers=viewer_headers,
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# DPD, classification and the servicing jobs
# ---------------------------------------------------------------------------
def test_a_missed_instalment_produces_arrears_and_a_classification(
    client: TestClient, admin_headers, booked
):
    """The nightly pass, run on demand: arrears first, then classification, then alerts."""
    response = client.post(
        "/api/v1/admin/jobs/run", params={"as_of": "2026-05-15"}, headers=admin_headers
    )
    assert response.status_code == 202, response.text

    loan = client.get(
        f"/api/v1/loans/{booked['loan']['id']}", headers=admin_headers
    ).json()
    assert loan["days_past_due"] == 44
    assert loan["classification"] == "SUBSTANDARD"
    assert loan["risk_status"] == "RED"
    assert Decimal(loan["overdue_amount"]) > 0


def test_the_servicing_pass_is_idempotent(client: TestClient, admin_headers, booked):
    """Running the same business date twice must write the same numbers — otherwise a
    retry after a partial night corrupts the book."""
    client.post(
        "/api/v1/admin/jobs/run", params={"as_of": "2026-05-15"}, headers=admin_headers
    )
    first = client.get(
        f"/api/v1/loans/{booked['loan']['id']}", headers=admin_headers
    ).json()

    client.post(
        "/api/v1/admin/jobs/run", params={"as_of": "2026-05-15"}, headers=admin_headers
    )
    second = client.get(
        f"/api/v1/loans/{booked['loan']['id']}", headers=admin_headers
    ).json()

    for field in ("days_past_due", "overdue_amount", "outstanding_principal",
                  "classification", "risk_status", "installments_paid"):
        assert first[field] == second[field], f"{field} changed on a re-run"


def test_a_current_loan_stays_performing(client: TestClient, admin_headers, booked):
    """Arrears classification only.

    ``risk_status`` is deliberately *not* asserted here. The nightly chain evaluates the
    risk rules last, and they may flag a loan that owes nothing — a usage collapse on a
    current loan is exactly the signal this product exists to surface, so a GREEN
    assertion would be asserting that the early warning does not work. Classification is
    what tracks arrears, and that is what this test is about.
    """
    client.post(
        "/api/v1/admin/jobs/run", params={"as_of": "2026-03-15"}, headers=admin_headers
    )
    loan = client.get(
        f"/api/v1/loans/{booked['loan']['id']}", headers=admin_headers
    ).json()
    assert loan["days_past_due"] == 0
    assert loan["classification"] == "PERFORMING"
    assert Decimal(loan["overdue_amount"]) == Decimal("0.00")


@pytest.mark.parametrize(
    ("as_of", "expected"),
    [
        ("2026-04-01", "PERFORMING"),
        ("2026-04-15", "WATCHLIST"),
        ("2026-06-01", "SUBSTANDARD"),
        ("2026-08-01", "DOUBTFUL"),
        ("2026-11-01", "LOSS"),
    ],
)
def test_the_documented_classification_bands_apply_end_to_end(
    client: TestClient, admin_headers, booked, as_of, expected
):
    """Doc 01 BR-4, exercised through the API rather than only in the unit tests."""
    client.post(
        "/api/v1/admin/jobs/run", params={"as_of": as_of}, headers=admin_headers
    )
    loan = client.get(
        f"/api/v1/loans/{booked['loan']['id']}", headers=admin_headers
    ).json()
    assert loan["classification"] == expected


def test_a_payment_clears_the_arrears_and_the_classification(
    client: TestClient, admin_headers, booked
):
    loan_id = booked["loan"]["id"]
    client.post(
        "/api/v1/admin/jobs/run", params={"as_of": "2026-05-15"}, headers=admin_headers
    )
    assert client.get(f"/api/v1/loans/{loan_id}", headers=admin_headers).json()[
        "classification"
    ] == "SUBSTANDARD"

    schedule = client.get(
        f"/api/v1/loans/{loan_id}/schedule", headers=admin_headers
    ).json()["items"]
    owed = sum(Decimal(r["total_due"]) for r in schedule[:2])
    client.post(
        f"/api/v1/loans/{loan_id}/repayments",
        json={"payment_date": "2026-05-15", "amount_paid": str(owed),
              "payment_mode": "BANK_TRANSFER"},
        headers=admin_headers,
    )

    loan = client.get(f"/api/v1/loans/{loan_id}", headers=admin_headers).json()
    assert loan["days_past_due"] == 0
    assert loan["classification"] == "PERFORMING"


def test_each_job_execution_records_a_job_run(client: TestClient, admin_headers, engine, booked):
    """TRD §2.10 — a silently missing night is what the dead-job alert exists to catch."""
    client.post(
        "/api/v1/admin/jobs/run", params={"as_of": "2026-05-15"}, headers=admin_headers
    )
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT job_name, status, as_of_date FROM job_runs "
                 "WHERE as_of_date = DATE '2026-05-15'")
        ).all()
    names = {r[0] for r in rows}
    assert {"recompute_dpd_and_outstanding", "recompute_classification",
            "evaluate_risk_rules"} <= names
    assert all(r[1] == "SUCCESS" for r in rows)


def test_running_the_jobs_requires_the_settings_permission(
    client: TestClient, officer_headers
):
    response = client.post("/api/v1/admin/jobs/run", headers=officer_headers)
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Alert integration
# ---------------------------------------------------------------------------
def test_a_delinquent_booked_loan_raises_the_documented_alerts(
    client: TestClient, admin_headers, booked
):
    """The end of the chain: a loan created through the API is now something the risk
    rules can actually evaluate."""
    client.post(
        "/api/v1/admin/jobs/run", params={"as_of": "2026-06-15"}, headers=admin_headers
    )

    with_loan = client.get(
        "/api/v1/alerts", params={"severity": "RED", "page_size": 50},
        headers=admin_headers,
    ).json()
    conditions = " ".join(a["trigger_condition"] or "" for a in with_loan["items"])
    assert "Days Past Due" in conditions


def test_the_loan_appears_in_the_portfolio(client: TestClient, admin_headers, booked):
    # Newest first, so the loan just booked is on the first page however large the book
    # has grown — ordering by arrears would bury it behind every delinquent loan.
    body = client.get(
        "/api/v1/loans",
        params={"page_size": 50, "sort": "-created_at"},
        headers=admin_headers,
    ).json()
    assert any(
        row["loan_account_number"] == booked["loan"]["loan_account_number"]
        for row in body["items"]
    )


def test_the_loan_book_can_be_filtered_by_arrears(
    client: TestClient, admin_headers, booked
):
    client.post(
        "/api/v1/admin/jobs/run", params={"as_of": "2026-06-15"}, headers=admin_headers
    )
    body = client.get(
        "/api/v1/loans", params={"min_dpd": 30, "page_size": 50}, headers=admin_headers
    ).json()
    assert all(row["days_past_due"] >= 30 for row in body["items"])


# ---------------------------------------------------------------------------
# The full happy path, in one test
# ---------------------------------------------------------------------------
def test_the_whole_financing_journey(
    client: TestClient, officer_a, officer_b, admin_headers, application_payload
):
    """Applicant → application → submit → assess → approve → book → schedule → repay →
    recompute → classify → evaluate. One test that fails if any link breaks."""
    created = client.post(
        "/api/v1/applications", json=application_payload, headers=officer_a
    )
    assert created.status_code == 201
    application_id = created.json()["id"]

    assert client.post(
        f"/api/v1/applications/{application_id}/submit", headers=officer_a
    ).status_code == 200

    assessment = client.post(
        f"/api/v1/applications/{application_id}/assess", headers=officer_a
    )
    assert assessment.status_code == 201
    assert assessment.json()["status"] == "PENDING_DECISION"

    decision = client.post(
        f"/api/v1/applications/{application_id}/decision",
        json={
            "final_decision": "APPROVE",
            "override_justification": (
                "Corridor and vehicle economics support the exposure; approving under "
                "the standing delegation."
            ),
        },
        headers=officer_b,
    )
    assert decision.status_code == 201

    booked = client.post(
        f"/api/v1/applications/{application_id}/book",
        json={"disbursement_date": "2026-03-01", "first_emi_date": "2026-04-01"},
        headers=admin_headers,
    )
    assert booked.status_code == 201
    loan_id = booked.json()["loan"]["id"]

    schedule = client.get(
        f"/api/v1/loans/{loan_id}/schedule", params={"page_size": 100},
        headers=admin_headers,
    ).json()["items"]
    assert len(schedule) == booked.json()["loan"]["tenure_months"]

    paid = client.post(
        f"/api/v1/loans/{loan_id}/repayments",
        json={"payment_date": "2026-04-01",
              "amount_paid": schedule[0]["total_due"],
              "payment_mode": "BANK_TRANSFER"},
        headers=admin_headers,
    )
    assert paid.status_code == 201
    assert paid.json()["loan"]["installments_paid"] == 1

    assert client.post(
        "/api/v1/admin/jobs/run", params={"as_of": "2026-05-15"}, headers=admin_headers
    ).status_code == 202

    final_state = client.get(f"/api/v1/loans/{loan_id}", headers=admin_headers).json()
    assert final_state["days_past_due"] == 14
    assert final_state["classification"] == "WATCHLIST"
    assert final_state["installments_paid"] == 1


def test_a_refused_request_is_recorded_as_a_security_event(
    client: TestClient, viewer_headers, engine, submitted
):
    """Doc 09 §9.8 — ten of these from one caller in five minutes is the documented alert
    for someone probing what their token can reach, so a refusal has to leave a trace."""
    with engine.begin() as conn:
        before = conn.execute(
            text("SELECT count(*) FROM audit_logs WHERE action = 'PERMISSION_DENIED'")
        ).scalar_one()

    denied = client.post(
        f"/api/v1/applications/{submitted['id']}/assess", headers=viewer_headers
    )
    assert denied.status_code == 403

    with engine.begin() as conn:
        after = conn.execute(
            text("SELECT count(*) FROM audit_logs WHERE action = 'PERMISSION_DENIED'")
        ).scalar_one()
    assert after == before + 1


def test_the_denial_record_names_the_caller_and_what_they_wanted(
    client: TestClient, viewer_headers, engine, submitted
):
    client.post(
        f"/api/v1/applications/{submitted['id']}/assess", headers=viewer_headers
    )
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT user_role, error_message, status FROM audit_logs "
                 "WHERE action = 'PERMISSION_DENIED' ORDER BY id DESC LIMIT 1")
        ).one()
    assert row[0] == "VIEWER"
    assert "application:assess" in row[1]
    assert row[2] == "FAILURE"


def test_a_denial_never_turns_a_403_into_a_500(client: TestClient, viewer_headers, submitted):
    """The audit write must not change the outcome the caller sees."""
    response = client.post(
        f"/api/v1/applications/{submitted['id']}/assess", headers=viewer_headers
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"
