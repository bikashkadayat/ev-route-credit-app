"""Administration — FR-9.1 to FR-9.7, Doc 03 Screens 26-30.

The theme running through all of it: policy is data, and published policy is immutable. A
scorecard that could be edited after publication would make every score taken under it
unexplainable, so the tests below spend most of their effort proving that cannot happen.
"""

from __future__ import annotations

import itertools
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

_SEQ = itertools.count(1)


@pytest.fixture(autouse=True)
def _restore_active_configuration(engine):
    """Publishing changes global policy, and the rest of the suite scores against it.

    These tests deliberately activate new scorecard versions, so each one restores the
    configuration that was active before it ran. Without this the assessment suites would
    start scoring against a three-component test scorecard and fail for reasons that have
    nothing to do with them.
    """
    from app.services.config import clear_config_cache

    with engine.begin() as conn:
        before = conn.execute(
            text("SELECT id, config_type, status FROM scoring_configurations")
        ).all()
    yield

    with engine.begin() as conn:
        # Put every row back to the status it held, then delete anything these tests
        # created so version numbering stays predictable for the next one.
        known = sorted(row[0] for row in before)
        # A literal list rather than a bound array: psycopg cannot deduce the element type
        # of an empty or single-element parameter here, and the ids are integers we
        # produced ourselves, so there is nothing to inject.
        id_list = ", ".join(str(int(i)) for i in known) or "0"
        conn.execute(
            text(f"DELETE FROM scoring_config_components WHERE config_id NOT IN ({id_list})")
        )
        conn.execute(
            text(f"DELETE FROM scoring_configurations WHERE id NOT IN ({id_list})")
        )
        for config_id, _config_type, status in before:
            # The status is cast explicitly: the column is an enum and the same parameter
            # cannot be deduced twice in one statement.
            conn.execute(
                text("UPDATE scoring_configurations "
                     "SET status = CAST(:s AS config_status_enum) WHERE id = :i"),
                {"s": str(status), "i": config_id},
            )
    clear_config_cache()


#: A minimal but valid FINAL scorecard: three components summing to exactly 1.0000.
VALID_COMPONENTS = [
    {"component_code": "ROUTE_SCORE", "weight": "0.40",
     "scoring_rules": {"type": "PASSTHROUGH", "field": "route_score"}},
    {"component_code": "CUSTOMER_SCORE", "weight": "0.40",
     "scoring_rules": {"type": "PASSTHROUGH", "field": "customer_score"}},
    {"component_code": "VEHICLE_ECONOMICS_SCORE", "weight": "0.20",
     "scoring_rules": {"type": "PASSTHROUGH", "field": "vehicle_economics_score"}},
]

GRADE_BANDS = [
    {"grade": "A", "min": 85, "max": 100, "label": "Low risk", "risk_level": "LOW"},
    {"grade": "B", "min": 70, "max": 84.99, "label": "Medium", "risk_level": "MEDIUM"},
    {"grade": "C", "min": 0, "max": 69.99, "label": "High", "risk_level": "HIGH"},
]


def draft_payload(**overrides) -> dict:
    payload = {
        "config_type": "FINAL",
        "name": f"FINAL draft {next(_SEQ)}",
        "grade_thresholds": GRADE_BANDS,
        "components": VALID_COMPONENTS,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Weight validation — FR-9.1
# ---------------------------------------------------------------------------
def test_weights_that_sum_to_one_are_valid(client: TestClient, admin_headers):
    response = client.post(
        "/api/v1/admin/scoring-configs/validate-weights",
        json={"components": VALID_COMPONENTS},
        headers=admin_headers,
    )
    assert response.status_code == 200

    body = response.json()
    assert body["is_valid"] is True
    assert body["total_percent"] == pytest.approx(100.0)


def test_weights_that_miss_are_reported_not_rejected(client: TestClient, admin_headers):
    """The editor calls this on every keystroke; a half-typed scorecard is not an error."""
    response = client.post(
        "/api/v1/admin/scoring-configs/validate-weights",
        json={"components": [{"component_code": "ROUTE_SCORE", "weight": "0.30"}]},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["is_valid"] is False
    assert response.json()["total_percent"] == pytest.approx(30.0)


def test_inactive_components_are_excluded_from_the_total(client: TestClient, admin_headers):
    components = [*VALID_COMPONENTS,
                  {"component_code": "RETIRED", "weight": "0.50", "is_active": False}]
    body = client.post(
        "/api/v1/admin/scoring-configs/validate-weights",
        json={"components": components}, headers=admin_headers,
    ).json()
    assert body["is_valid"] is True


def test_validating_weights_writes_nothing(client: TestClient, admin_headers, engine):
    with engine.begin() as conn:
        before = conn.execute(
            text("SELECT count(*) FROM scoring_configurations")
        ).scalar_one()
    client.post(
        "/api/v1/admin/scoring-configs/validate-weights",
        json={"components": VALID_COMPONENTS}, headers=admin_headers,
    )
    with engine.begin() as conn:
        after = conn.execute(
            text("SELECT count(*) FROM scoring_configurations")
        ).scalar_one()
    assert after == before


# ---------------------------------------------------------------------------
# Draft and publish — FR-9.2
# ---------------------------------------------------------------------------
@pytest.fixture
def draft(client: TestClient, admin_headers) -> dict:
    response = client.post(
        "/api/v1/admin/scoring-configs", json=draft_payload(), headers=admin_headers
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_a_draft_is_created_with_the_next_version_number(draft):
    assert draft["status"] == "DRAFT"
    assert draft["version_no"] >= 2  # v1 is already seeded


def test_a_draft_can_be_edited(client: TestClient, admin_headers, draft):
    response = client.patch(
        f"/api/v1/admin/scoring-configs/{draft['id']}",
        json={"notes": "Raised the Class A boundary after the calibration review"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert "calibration" in response.json()["notes"]


def test_publishing_makes_the_draft_active(client: TestClient, admin_headers, draft):
    response = client.post(
        f"/api/v1/admin/scoring-configs/{draft['id']}/publish",
        json={"note": "Approved at the September credit committee"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ACTIVE"
    assert response.json()["published_at"]


def test_publishing_archives_the_previous_active_version(
    client: TestClient, admin_headers, draft
):
    """FR-9.2 — the superseded version stays queryable so its scores stay explainable."""
    before = client.get(
        "/api/v1/admin/scoring-configs", params={"config_type": "FINAL"},
        headers=admin_headers,
    ).json()
    previously_active = next(r for r in before if r["status"] == "ACTIVE")

    client.post(
        f"/api/v1/admin/scoring-configs/{draft['id']}/publish", headers=admin_headers
    )

    after = client.get(
        "/api/v1/admin/scoring-configs", params={"config_type": "FINAL"},
        headers=admin_headers,
    ).json()
    superseded = next(r for r in after if r["id"] == previously_active["id"])
    assert superseded["status"] == "ARCHIVED"
    assert superseded["archived_at"]
    assert sum(1 for r in after if r["status"] == "ACTIVE") == 1


def test_a_published_configuration_cannot_be_edited(
    client: TestClient, admin_headers, draft
):
    """The heart of it: immutability is what makes a stored score reproducible."""
    client.post(
        f"/api/v1/admin/scoring-configs/{draft['id']}/publish", headers=admin_headers
    )
    response = client.patch(
        f"/api/v1/admin/scoring-configs/{draft['id']}",
        json={"notes": "Quietly changing published policy"},
        headers=admin_headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_STATE_TRANSITION"


def test_a_published_configuration_cannot_be_published_twice(
    client: TestClient, admin_headers, draft
):
    client.post(
        f"/api/v1/admin/scoring-configs/{draft['id']}/publish", headers=admin_headers
    )
    second = client.post(
        f"/api/v1/admin/scoring-configs/{draft['id']}/publish", headers=admin_headers
    )
    assert second.status_code == 409


def test_a_draft_whose_weights_miss_cannot_be_published(
    client: TestClient, admin_headers
):
    """A scorecard that did not sum to 100% would make every score it produced wrong, so
    it must never reach ACTIVE."""
    bad = client.post(
        "/api/v1/admin/scoring-configs",
        json=draft_payload(components=[
            {"component_code": "ROUTE_SCORE", "weight": "0.50"},
            {"component_code": "CUSTOMER_SCORE", "weight": "0.30"},
        ]),
        headers=admin_headers,
    ).json()

    response = client.post(
        f"/api/v1/admin/scoring-configs/{bad['id']}/publish", headers=admin_headers
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "WEIGHTS_NOT_100"


def test_a_failed_publish_leaves_the_draft_alone(client: TestClient, admin_headers):
    bad = client.post(
        "/api/v1/admin/scoring-configs",
        json=draft_payload(components=[
            {"component_code": "ROUTE_SCORE", "weight": "0.50"},
        ]),
        headers=admin_headers,
    ).json()
    client.post(
        f"/api/v1/admin/scoring-configs/{bad['id']}/publish", headers=admin_headers
    )
    listed = client.get(
        "/api/v1/admin/scoring-configs", params={"config_type": "FINAL"},
        headers=admin_headers,
    ).json()
    assert next(r for r in listed if r["id"] == bad["id"])["status"] == "DRAFT"


def test_publishing_is_audit_logged(client: TestClient, admin_headers, draft, engine):
    client.post(
        f"/api/v1/admin/scoring-configs/{draft['id']}/publish",
        json={"note": "Approved at the September credit committee"},
        headers=admin_headers,
    )
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT after_state FROM audit_logs WHERE action = 'CONFIG_PUBLISHED' "
                 "ORDER BY id DESC LIMIT 1")
        ).scalar_one()
    assert row["status"] == "ACTIVE"
    assert row["weight_total"] == pytest.approx(1.0)


def test_publishing_needs_the_publish_permission(client: TestClient, officer_headers, draft):
    response = client.post(
        f"/api/v1/admin/scoring-configs/{draft['id']}/publish", headers=officer_headers
    )
    assert response.status_code == 403


def test_a_newly_published_version_becomes_the_one_assessments_use(
    client: TestClient, admin_headers, draft
):
    """FR-9.2 read back through the read-only surface the rest of the API uses."""
    client.post(
        f"/api/v1/admin/scoring-configs/{draft['id']}/publish", headers=admin_headers
    )
    active = client.get("/api/v1/config", headers=admin_headers).json()
    assert active["active_versions"]["FINAL"] == draft["version_no"]


# ---------------------------------------------------------------------------
# Risk rules — FR-9.4
# ---------------------------------------------------------------------------
@pytest.fixture
def rule(client: TestClient, admin_headers) -> dict:
    rules = client.get("/api/v1/admin/risk-rules", headers=admin_headers).json()
    return next(r for r in rules if r["rule_code"] == "LOW_ACTIVE_DAYS")


def test_the_catalogue_is_listed_for_administration(client: TestClient, admin_headers):
    rules = client.get("/api/v1/admin/risk-rules", headers=admin_headers).json()
    assert len(rules) == 22


def test_a_rule_can_be_disabled_and_re_enabled(
    client: TestClient, admin_headers, rule, engine
):
    rule_id = _rule_row_id(engine, rule["rule_code"])
    disabled = client.post(
        f"/api/v1/admin/risk-rules/{rule_id}/toggle",
        json={"enabled": False, "reason": "Too noisy pending recalibration"},
        headers=admin_headers,
    )
    assert disabled.status_code == 200
    assert disabled.json()["is_active"] is False

    enabled = client.post(
        f"/api/v1/admin/risk-rules/{rule_id}/toggle",
        json={"enabled": True}, headers=admin_headers,
    )
    assert enabled.json()["is_active"] is True


def test_toggling_to_the_current_state_is_refused(
    client: TestClient, admin_headers, rule, engine
):
    rule_id = _rule_row_id(engine, rule["rule_code"])
    response = client.post(
        f"/api/v1/admin/risk-rules/{rule_id}/toggle",
        json={"enabled": True}, headers=admin_headers,
    )
    assert response.status_code == 409


def test_a_disabled_rule_stops_firing(client: TestClient, admin_headers, rule, engine):
    """FR-7.9 — disabling is how a risk manager silences a noisy rule without a release."""
    rule_id = _rule_row_id(engine, rule["rule_code"])
    client.post(
        f"/api/v1/admin/risk-rules/{rule_id}/toggle",
        json={"enabled": False}, headers=admin_headers,
    )
    try:
        active = client.get(
            "/api/v1/config/risk-rules", params={"active_only": True},
            headers=admin_headers,
        ).json()
        assert all(r["rule_code"] != rule["rule_code"] for r in active)
    finally:
        client.post(
            f"/api/v1/admin/risk-rules/{rule_id}/toggle",
            json={"enabled": True}, headers=admin_headers,
        )


def test_a_rules_thresholds_can_be_edited(client: TestClient, admin_headers, rule, engine):
    rule_id = _rule_row_id(engine, rule["rule_code"])
    response = client.patch(
        f"/api/v1/admin/risk-rules/{rule_id}",
        json={"sla_hours_acknowledge": 12, "recommended_action": "Call the borrower"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["sla_hours_acknowledge"] == 12


def test_the_rule_code_cannot_be_changed(client: TestClient, admin_headers, rule, engine):
    """It is stamped on every alert the rule ever raised."""
    rule_id = _rule_row_id(engine, rule["rule_code"])
    response = client.patch(
        f"/api/v1/admin/risk-rules/{rule_id}",
        json={"rule_code": "RENAMED"}, headers=admin_headers,
    )
    assert response.status_code == 422


def test_editing_a_rule_is_audit_logged(client: TestClient, admin_headers, rule, engine):
    rule_id = _rule_row_id(engine, rule["rule_code"])
    client.patch(
        f"/api/v1/admin/risk-rules/{rule_id}",
        json={"priority": 42}, headers=admin_headers,
    )
    with engine.begin() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM audit_logs WHERE action = 'RISK_RULE_UPDATED'")
        ).scalar_one()
    assert count >= 1


def _rule_row_id(engine, rule_code: str) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text("SELECT id FROM risk_rules WHERE rule_code = :c"), {"c": rule_code}
        ).scalar_one()


# ---------------------------------------------------------------------------
# Settings — FR-9.3, FR-9.7
# ---------------------------------------------------------------------------
def test_settings_are_listed_with_their_bounds(client: TestClient, admin_headers):
    settings = client.get("/api/v1/admin/settings", headers=admin_headers).json()
    ltv = next(s for s in settings if s["setting_key"] == "loan.max_ltv_percent")
    assert ltv["value_type"] == "NUMBER"
    assert Decimal(ltv["min_value"]) == Decimal("10")
    assert Decimal(ltv["max_value"]) == Decimal("100")


def test_settings_can_be_filtered_by_category(client: TestClient, admin_headers):
    settings = client.get(
        "/api/v1/admin/settings", params={"category": "LOAN_RULES"},
        headers=admin_headers,
    ).json()
    assert settings
    assert all(s["category"] == "LOAN_RULES" for s in settings)


def test_a_loan_rule_can_be_changed(client: TestClient, admin_headers):
    """FR-9.3 — the whole point: policy moves without a release."""
    original = client.get(
        "/api/v1/admin/settings", params={"category": "LOAN_RULES"},
        headers=admin_headers,
    ).json()
    before = next(
        s for s in original if s["setting_key"] == "loan.max_ltv_percent"
    )["setting_value"]

    try:
        response = client.put(
            "/api/v1/admin/settings/loan.max_ltv_percent",
            json={"setting_value": "75"}, headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["setting_value"] == "75"
    finally:
        client.put(
            "/api/v1/admin/settings/loan.max_ltv_percent",
            json={"setting_value": before}, headers=admin_headers,
        )


def test_a_value_above_the_stored_maximum_is_refused(client: TestClient, admin_headers):
    """A typo must not set the LTV ceiling to 800% and quietly widen every approval."""
    response = client.put(
        "/api/v1/admin/settings/loan.max_ltv_percent",
        json={"setting_value": "800"}, headers=admin_headers,
    )
    assert response.status_code == 422
    assert "ABOVE_MAXIMUM" in {d["code"] for d in response.json()["error"]["details"]}


def test_a_value_below_the_stored_minimum_is_refused(client: TestClient, admin_headers):
    response = client.put(
        "/api/v1/admin/settings/loan.max_ltv_percent",
        json={"setting_value": "1"}, headers=admin_headers,
    )
    assert response.status_code == 422


def test_a_non_numeric_value_for_a_number_setting_is_refused(
    client: TestClient, admin_headers
):
    response = client.put(
        "/api/v1/admin/settings/loan.max_ltv_percent",
        json={"setting_value": "eighty"}, headers=admin_headers,
    )
    assert response.status_code == 422


def test_invalid_json_for_a_json_setting_is_refused(client: TestClient, admin_headers):
    response = client.put(
        "/api/v1/admin/settings/loan.ltv_by_grade",
        json={"setting_value": "{not json"}, headers=admin_headers,
    )
    assert response.status_code == 422


def test_an_unknown_setting_is_a_404(client: TestClient, admin_headers):
    assert client.put(
        "/api/v1/admin/settings/nope.nothing",
        json={"setting_value": "1"}, headers=admin_headers,
    ).status_code == 404


def test_changing_a_setting_is_audit_logged(client: TestClient, admin_headers, engine):
    client.put(
        "/api/v1/admin/settings/loan.penalty_rate_percent",
        json={"setting_value": "2.5"}, headers=admin_headers,
    )
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT before_state, after_state FROM audit_logs "
                 "WHERE action = 'SETTING_UPDATED' ORDER BY id DESC LIMIT 1")
        ).one()
    assert row[0]["setting_key"] == "loan.penalty_rate_percent"
    assert row[1]["setting_value"] == "2.5"


def test_changing_a_setting_needs_the_update_permission(
    client: TestClient, officer_headers
):
    assert client.put(
        "/api/v1/admin/settings/loan.max_ltv_percent",
        json={"setting_value": "75"}, headers=officer_headers,
    ).status_code == 403


# ---------------------------------------------------------------------------
# Users and roles — FR-9.5
# ---------------------------------------------------------------------------
def _new_user(n: int) -> dict:
    return {
        "email": f"admin.created{n}@bank.com.np",
        "full_name": f"Admin Created {n}",
        "role_code": "CREDIT_OFFICER",
        "password": "Str0ng!Initial#2026",
        "branch_code": "BR-KTM-01",
    }


def test_the_seeded_users_are_listed(client: TestClient, admin_headers):
    body = client.get("/api/v1/admin/users", headers=admin_headers).json()
    assert body["total"] >= 6
    # `must_change_password` is a legitimate field; what must never appear is the hash.
    for row in body["items"]:
        assert "password_hash" not in row
        assert "argon2" not in str(row)


def test_a_user_can_be_created(client: TestClient, admin_headers):
    response = client.post(
        "/api/v1/admin/users", json=_new_user(next(_SEQ)), headers=admin_headers
    )
    assert response.status_code == 201
    assert response.json()["role_code"] == "CREDIT_OFFICER"


def test_a_created_user_must_change_their_password(client: TestClient, admin_headers):
    """FR-1.7 — an admin knows the initial password, so it cannot stay in use."""
    response = client.post(
        "/api/v1/admin/users", json=_new_user(next(_SEQ)), headers=admin_headers
    )
    assert response.json()["must_change_password"] is True


def test_a_weak_initial_password_is_refused(client: TestClient, admin_headers):
    """An administrator must not be able to seed a weak credential."""
    response = client.post(
        "/api/v1/admin/users",
        json={**_new_user(next(_SEQ)), "password": "password1234"},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_a_duplicate_email_is_refused(client: TestClient, admin_headers):
    payload = _new_user(next(_SEQ))
    assert client.post(
        "/api/v1/admin/users", json=payload, headers=admin_headers
    ).status_code == 201
    assert client.post(
        "/api/v1/admin/users", json=payload, headers=admin_headers
    ).status_code == 409


def test_an_unknown_role_is_a_404(client: TestClient, admin_headers):
    response = client.post(
        "/api/v1/admin/users",
        json={**_new_user(next(_SEQ)), "role_code": "WIZARD"},
        headers=admin_headers,
    )
    assert response.status_code == 404


def test_a_new_user_can_sign_in(client: TestClient, admin_headers):
    payload = _new_user(next(_SEQ))
    client.post("/api/v1/admin/users", json=payload, headers=admin_headers)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    assert response.status_code == 200
    assert response.json()["user"]["must_change_password"] is True


def test_deactivating_a_user_revokes_their_sessions(client: TestClient, admin_headers):
    """Doc 09 §9.2.4 — a departing employee holding a refresh token for seven days is the
    reason this is more than a flag."""
    payload = _new_user(next(_SEQ))
    created = client.post(
        "/api/v1/admin/users", json=payload, headers=admin_headers
    ).json()
    tokens = client.post(
        "/api/v1/auth/login",
        json={"email": payload["email"], "password": payload["password"]},
    ).json()

    response = client.post(
        f"/api/v1/admin/users/{created['id']}/status",
        json={"active": False, "reason": "Left the organisation"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False

    refreshed = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 401


def test_a_deactivated_user_cannot_sign_in(client: TestClient, admin_headers):
    payload = _new_user(next(_SEQ))
    created = client.post(
        "/api/v1/admin/users", json=payload, headers=admin_headers
    ).json()
    client.post(
        f"/api/v1/admin/users/{created['id']}/status",
        json={"active": False}, headers=admin_headers,
    )
    response = client.post(
        "/api/v1/auth/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    assert response.status_code == 403


def test_deactivation_is_audit_logged(client: TestClient, admin_headers, engine):
    payload = _new_user(next(_SEQ))
    created = client.post(
        "/api/v1/admin/users", json=payload, headers=admin_headers
    ).json()
    client.post(
        f"/api/v1/admin/users/{created['id']}/status",
        json={"active": False}, headers=admin_headers,
    )
    with engine.begin() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM audit_logs WHERE action = 'USER_DEACTIVATED'")
        ).scalar_one()
    assert count >= 1


def test_roles_expose_the_permissions_they_grant(client: TestClient, admin_headers):
    """Read from role_permissions — the same source the token claims are minted from, so
    the screen cannot disagree with enforcement."""
    roles = client.get("/api/v1/admin/roles", headers=admin_headers).json()
    viewer = next(r for r in roles if r["code"] == "VIEWER")
    assert all(p.endswith(":read") for p in viewer["permissions"])

    risk_manager = next(r for r in roles if r["code"] == "RISK_MANAGER")
    assert "risk:config:publish" in risk_manager["permissions"]


def test_user_administration_needs_the_settings_permission(
    client: TestClient, officer_headers
):
    assert client.get(
        "/api/v1/admin/users", headers=officer_headers
    ).status_code == 403


# ---------------------------------------------------------------------------
# Audit reader — FR-9.6
# ---------------------------------------------------------------------------
def test_the_audit_trail_is_readable(client: TestClient, admin_headers):
    client.get("/api/v1/routes", headers=admin_headers)
    response = client.get("/api/v1/admin/audit-logs", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["total"] > 0


def test_the_trail_is_newest_first(client: TestClient, admin_headers):
    rows = client.get(
        "/api/v1/admin/audit-logs", params={"page_size": 20}, headers=admin_headers
    ).json()["items"]
    stamps = [r["event_time"] for r in rows]
    assert stamps == sorted(stamps, reverse=True)


def test_the_trail_can_be_filtered_by_action(client: TestClient, admin_headers):
    """Doc 12 step 12 shows exactly this: filtered to DECISION_OVERRIDE."""
    rows = client.get(
        "/api/v1/admin/audit-logs", params={"action": "LOGIN_SUCCESS"},
        headers=admin_headers,
    ).json()["items"]
    assert all(r["action"] == "LOGIN_SUCCESS" for r in rows)


def test_the_trail_can_be_filtered_by_outcome(client: TestClient, admin_headers):
    rows = client.get(
        "/api/v1/admin/audit-logs", params={"status": "FAILURE"},
        headers=admin_headers,
    ).json()["items"]
    assert all(r["status"] == "FAILURE" for r in rows)


def test_the_trail_never_exposes_a_credential(client: TestClient, admin_headers):
    """The writer redacts by key name before anything is stored; this asserts the result
    at the read side, which is where a leak would actually reach someone."""
    body = client.get(
        "/api/v1/admin/audit-logs", params={"page_size": 100}, headers=admin_headers
    ).json()
    blob = str(body).lower()
    assert "password_hash" not in blob
    assert "refresh_token" not in blob
    assert "argon2" not in blob


def test_reading_the_trail_needs_the_audit_permission(client: TestClient, officer_headers):
    assert client.get(
        "/api/v1/admin/audit-logs", headers=officer_headers
    ).status_code == 403


def test_the_trail_is_append_only_through_the_api(client: TestClient, admin_headers):
    """FR-9.6 — there is no write path at all, not merely a guarded one."""
    for method in (client.post, client.put, client.delete, client.patch):
        assert method(
            "/api/v1/admin/audit-logs", headers=admin_headers
        ).status_code == 405


# ---------------------------------------------------------------------------
# Job history — TRD §2.10
# ---------------------------------------------------------------------------
def test_job_runs_are_visible(client: TestClient, admin_headers):
    client.post(
        "/api/v1/admin/jobs/run", params={"as_of": "2026-07-01"}, headers=admin_headers
    )
    rows = client.get("/api/v1/admin/jobs", headers=admin_headers).json()
    assert rows
    assert {"job_name", "status", "records_processed"} <= set(rows[0])


def test_job_history_needs_the_settings_permission(client: TestClient, officer_headers):
    assert client.get("/api/v1/admin/jobs", headers=officer_headers).status_code == 403
