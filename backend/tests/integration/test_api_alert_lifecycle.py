"""The alert lifecycle — FR-7.7, FR-7.8, BR-5.

OPEN → ACKNOWLEDGED → IN_PROGRESS → RESOLVED, with FALSE_POSITIVE and ESCALATED as the
other exits. The transitions are asserted in both directions: what must be allowed, and
what must be refused. A queue that lets a resolved alert be reopened, or lets an officer
skip the trail, is a queue nobody can audit.

The rule semantics themselves are not re-tested here — they have their own suite. What is
tested is everything a person does to an alert after the engine raises it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.integration.conftest import make_loan as _make_loan
from tests.integration.test_api_alerts import evaluate


@pytest.fixture
def alert(client: TestClient, admin_headers, engine) -> dict:
    """A live RED alert raised by the engine on a genuinely delinquent loan."""
    with engine.begin() as conn:
        loan_id = _make_loan(conn, dpd=47, overdue=Decimal("145600"), missed=2)
    evaluate(client, admin_headers, loan_id)

    body = client.get(
        "/api/v1/alerts",
        params={"loan_id": loan_id, "severity": "RED"},
        headers=admin_headers,
    ).json()
    assert body["items"], "the delinquent loan should have raised a RED alert"
    return body["items"][0]


# ---------------------------------------------------------------------------
# Assignment — FR-7.8
# ---------------------------------------------------------------------------
def test_an_alert_can_be_assigned(client: TestClient, portfolio_headers, alert):
    response = client.post(
        f"/api/v1/alerts/{alert['id']}/assign",
        json={"assignee_id": 5, "note": "Field visit — Bhaktapur"},
        headers=portfolio_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["assigned_to"] == 5


def test_assignment_is_not_a_state_change(client: TestClient, portfolio_headers, alert):
    """The SLA clock measures the work, not the ownership: handing an OPEN alert to
    someone must not quietly mark it as being worked on."""
    body = client.post(
        f"/api/v1/alerts/{alert['id']}/assign",
        json={"assignee_id": 5},
        headers=portfolio_headers,
    ).json()
    assert body["status"] == "OPEN"


def test_assignment_appears_in_the_timeline(client: TestClient, portfolio_headers, alert):
    client.post(
        f"/api/v1/alerts/{alert['id']}/assign",
        json={"assignee_id": 5, "note": "Field visit — Bhaktapur"},
        headers=portfolio_headers,
    )
    timeline = client.get(
        f"/api/v1/alerts/{alert['id']}/activities", headers=portfolio_headers
    ).json()
    assert any(a["activity_type"] == "ASSIGNMENT" for a in timeline)


def test_reassignment_is_recorded_with_the_previous_owner(
    client: TestClient, portfolio_headers, alert, engine
):
    client.post(
        f"/api/v1/alerts/{alert['id']}/assign", json={"assignee_id": 5},
        headers=portfolio_headers,
    )
    client.post(
        f"/api/v1/alerts/{alert['id']}/assign", json={"assignee_id": 4},
        headers=portfolio_headers,
    )
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT before_state, after_state FROM audit_logs "
                 "WHERE action = 'ALERT_ASSIGNED' ORDER BY id DESC LIMIT 1")
        ).one()
    assert row[0]["assigned_to"] == 5
    assert row[1]["assigned_to"] == 4


def test_assignment_needs_the_assign_permission(client: TestClient, viewer_headers, alert):
    response = client.post(
        f"/api/v1/alerts/{alert['id']}/assign", json={"assignee_id": 5},
        headers=viewer_headers,
    )
    assert response.status_code == 403
    assert response.json()["error"]["details"]["required_permission"] == "alert:assign"


def test_a_closed_alert_cannot_be_reassigned(client: TestClient, admin_headers, alert):
    client.post(
        f"/api/v1/alerts/{alert['id']}/resolve",
        json={"resolution_code": "PAYMENT_RECEIVED",
              "resolution_notes": "Borrower cleared both instalments on 2026-03-02."},
        headers=admin_headers,
    )
    response = client.post(
        f"/api/v1/alerts/{alert['id']}/assign", json={"assignee_id": 5},
        headers=admin_headers,
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# The documented lifecycle — FR-7.7
# ---------------------------------------------------------------------------
def test_the_documented_happy_path(client: TestClient, admin_headers, alert):
    """OPEN → ACKNOWLEDGED → IN_PROGRESS → RESOLVED, which is what Doc 12 step 17 walks
    an audience through."""
    alert_id = alert["id"]
    assert alert["status"] == "OPEN"

    acknowledged = client.post(
        f"/api/v1/alerts/{alert_id}/acknowledge", headers=admin_headers
    )
    assert acknowledged.status_code == 200
    assert acknowledged.json()["status"] == "ACKNOWLEDGED"

    started = client.post(f"/api/v1/alerts/{alert_id}/start", headers=admin_headers)
    assert started.status_code == 200
    assert started.json()["status"] == "IN_PROGRESS"

    resolved = client.post(
        f"/api/v1/alerts/{alert_id}/resolve",
        json={"resolution_code": "PAYMENT_RECEIVED",
              "resolution_notes": "Motor controller replaced; borrower cleared arrears."},
        headers=admin_headers,
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "RESOLVED"


def test_work_can_start_without_a_separate_acknowledgement(
    client: TestClient, admin_headers, alert
):
    """An officer who picks up an alert and starts immediately should not be forced
    through a ceremonial extra click."""
    response = client.post(
        f"/api/v1/alerts/{alert['id']}/start",
        json={"note": "Calling the borrower now"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "IN_PROGRESS"


def test_a_resolved_alert_cannot_be_reopened(client: TestClient, admin_headers, alert):
    """The history is the record of how it was worked; walking it backwards would destroy
    that."""
    client.post(
        f"/api/v1/alerts/{alert['id']}/resolve",
        json={"resolution_code": "PAYMENT_RECEIVED",
              "resolution_notes": "Borrower cleared both instalments on 2026-03-02."},
        headers=admin_headers,
    )
    for endpoint in ("acknowledge", "start"):
        response = client.post(
            f"/api/v1/alerts/{alert['id']}/{endpoint}", headers=admin_headers
        )
        assert response.status_code == 409, endpoint
        assert response.json()["error"]["code"] == "INVALID_STATE_TRANSITION"


def test_an_acknowledged_alert_cannot_be_acknowledged_twice(
    client: TestClient, admin_headers, alert
):
    client.post(f"/api/v1/alerts/{alert['id']}/acknowledge", headers=admin_headers)
    second = client.post(
        f"/api/v1/alerts/{alert['id']}/acknowledge", headers=admin_headers
    )
    assert second.status_code == 409


def test_the_refusal_names_the_transitions_that_would_be_allowed(
    client: TestClient, admin_headers, alert
):
    """A 409 that does not say what *is* possible sends the caller guessing."""
    client.post(
        f"/api/v1/alerts/{alert['id']}/resolve",
        json={"resolution_code": "PAYMENT_RECEIVED",
              "resolution_notes": "Borrower cleared both instalments on 2026-03-02."},
        headers=admin_headers,
    )
    details = client.post(
        f"/api/v1/alerts/{alert['id']}/start", headers=admin_headers
    ).json()["error"]["details"]
    assert details["status"] == "RESOLVED"
    assert details["allowed"] == []


def test_a_false_positive_is_a_distinct_terminal_state(
    client: TestClient, admin_headers, alert
):
    response = client.post(
        f"/api/v1/alerts/{alert['id']}/resolve",
        json={"resolution_code": "FALSE_POSITIVE",
              "resolution_notes": "Telemetry backfill error; the vehicle was operating."},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "FALSE_POSITIVE"


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------
def test_an_alert_can_be_escalated(client: TestClient, portfolio_headers, alert):
    response = client.post(
        f"/api/v1/alerts/{alert['id']}/escalate",
        json={"reason": "Borrower unreachable for six days; recommending repossession review",
              "assignee_id": 2},
        headers=portfolio_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ESCALATED"
    assert response.json()["assigned_to"] == 2


def test_escalation_requires_a_stated_reason(client: TestClient, portfolio_headers, alert):
    """An escalation with no cause tells the next officer nothing."""
    response = client.post(
        f"/api/v1/alerts/{alert['id']}/escalate",
        json={"reason": "bad"},
        headers=portfolio_headers,
    )
    assert response.status_code == 422


def test_an_escalated_alert_can_still_be_worked_and_closed(
    client: TestClient, portfolio_headers, admin_headers, alert
):
    client.post(
        f"/api/v1/alerts/{alert['id']}/escalate",
        json={"reason": "Borrower unreachable; handing to the risk manager"},
        headers=portfolio_headers,
    )
    started = client.post(f"/api/v1/alerts/{alert['id']}/start", headers=admin_headers)
    assert started.status_code == 200

    resolved = client.post(
        f"/api/v1/alerts/{alert['id']}/resolve",
        json={"resolution_code": "RESTRUCTURED",
              "resolution_notes": "Moratorium approved by the credit committee."},
        headers=admin_headers,
    )
    assert resolved.status_code == 200


def test_escalation_is_recorded_in_the_timeline_and_the_audit(
    client: TestClient, portfolio_headers, alert, engine
):
    reason = "Borrower unreachable for six days; recommending repossession review"
    client.post(
        f"/api/v1/alerts/{alert['id']}/escalate",
        json={"reason": reason}, headers=portfolio_headers,
    )
    timeline = client.get(
        f"/api/v1/alerts/{alert['id']}/activities", headers=portfolio_headers
    ).json()
    assert any(a["activity_type"] == "ESCALATION" for a in timeline)

    with engine.begin() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM audit_logs WHERE action = 'ALERT_ESCALATED'")
        ).scalar_one()
    assert count >= 1


# ---------------------------------------------------------------------------
# Activities — the investigation trail
# ---------------------------------------------------------------------------
def test_a_call_log_can_be_recorded(client: TestClient, portfolio_headers, alert):
    """Doc 12 step 17 — the note an officer writes after speaking to the borrower."""
    response = client.post(
        f"/api/v1/alerts/{alert['id']}/activities",
        json={
            "activity_type": "CALL_LOG",
            "description": (
                "Spoke to borrower. Vehicle off-road since 18 Aug for motor controller "
                "replacement; part now fitted. Promised full clearance by 15 Sep."
            ),
        },
        headers=portfolio_headers,
    )
    assert response.status_code == 201
    assert response.json()["activity_type"] == "CALL_LOG"


def test_the_timeline_is_oldest_first(client: TestClient, portfolio_headers, alert):
    for note in ("First contact attempt", "Second contact attempt"):
        client.post(
            f"/api/v1/alerts/{alert['id']}/activities",
            json={"activity_type": "CALL_LOG", "description": note},
            headers=portfolio_headers,
        )
    timeline = client.get(
        f"/api/v1/alerts/{alert['id']}/activities", headers=portfolio_headers
    ).json()
    stamps = [a["created_at"] for a in timeline]
    assert stamps == sorted(stamps)


def test_the_timeline_survives_resolution(client: TestClient, admin_headers, alert):
    """Doc 12 step 17 — "the activity timeline retains the whole history"."""
    client.post(
        f"/api/v1/alerts/{alert['id']}/activities",
        json={"activity_type": "CALL_LOG", "description": "Borrower promised clearance"},
        headers=admin_headers,
    )
    client.post(
        f"/api/v1/alerts/{alert['id']}/resolve",
        json={"resolution_code": "PAYMENT_RECEIVED",
              "resolution_notes": "Borrower cleared both instalments on 2026-03-02."},
        headers=admin_headers,
    )
    timeline = client.get(
        f"/api/v1/alerts/{alert['id']}/activities", headers=admin_headers
    ).json()
    assert any(a["activity_type"] == "CALL_LOG" for a in timeline)
    assert any(a["activity_type"] == "STATUS_CHANGE" for a in timeline)


def test_a_note_can_be_added_after_closure(client: TestClient, admin_headers, alert):
    """What was learned afterwards is exactly the kind of thing that should not be lost."""
    client.post(
        f"/api/v1/alerts/{alert['id']}/resolve",
        json={"resolution_code": "PAYMENT_RECEIVED",
              "resolution_notes": "Borrower cleared both instalments on 2026-03-02."},
        headers=admin_headers,
    )
    response = client.post(
        f"/api/v1/alerts/{alert['id']}/activities",
        json={"activity_type": "NOTE", "description": "Payment confirmed by the branch."},
        headers=admin_headers,
    )
    assert response.status_code == 201


def test_recording_a_note_is_a_write_and_needs_more_than_read(
    client: TestClient, viewer_headers, alert
):
    """Regression: the endpoint originally accepted `alert:read`, which would have let a
    read-only auditor write into the investigation trail."""
    response = client.post(
        f"/api/v1/alerts/{alert['id']}/activities",
        json={"activity_type": "NOTE", "description": "Should not be permitted"},
        headers=viewer_headers,
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# SLA — BR-5
# ---------------------------------------------------------------------------
def test_the_sla_countdown_is_reported(client: TestClient, admin_headers, alert):
    response = client.get(f"/api/v1/alerts/{alert['id']}/sla", headers=admin_headers)
    assert response.status_code == 200

    body = response.json()
    assert body["acknowledge_seconds_remaining"] is not None
    assert body["resolve_seconds_remaining"] is not None
    assert body["acknowledge_breached"] is False


def test_acknowledging_stops_the_acknowledge_clock(
    client: TestClient, admin_headers, alert
):
    client.post(f"/api/v1/alerts/{alert['id']}/acknowledge", headers=admin_headers)
    body = client.get(f"/api/v1/alerts/{alert['id']}/sla", headers=admin_headers).json()
    assert body["acknowledge_seconds_remaining"] is None
    assert body["acknowledge_breached"] is False


def test_resolving_stops_both_clocks(client: TestClient, admin_headers, alert):
    client.post(
        f"/api/v1/alerts/{alert['id']}/resolve",
        json={"resolution_code": "PAYMENT_RECEIVED",
              "resolution_notes": "Borrower cleared both instalments on 2026-03-02."},
        headers=admin_headers,
    )
    body = client.get(f"/api/v1/alerts/{alert['id']}/sla", headers=admin_headers).json()
    assert body["resolve_seconds_remaining"] is None
    assert body["resolve_breached"] is False


def test_a_breached_acknowledge_sla_is_reported(
    client: TestClient, admin_headers, alert, engine
):
    """BR-5 — a RED alert must be acknowledged within 4 hours."""
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE risk_alerts SET acknowledge_due_at = now() - interval '1 hour' "
                 "WHERE uuid = :u"),
            {"u": alert["id"]},
        )
    body = client.get(f"/api/v1/alerts/{alert['id']}/sla", headers=admin_headers).json()
    assert body["acknowledge_breached"] is True
    assert body["acknowledge_seconds_remaining"] < 0


def test_the_red_sla_matches_the_documented_hours(client: TestClient, admin_headers, alert):
    """BR-5 — RED: acknowledge within 4 h, resolve within 3 working days."""
    body = client.get(f"/api/v1/alerts/{alert['id']}/sla", headers=admin_headers).json()
    assert 0 < body["acknowledge_seconds_remaining"] <= 4 * 3600
    assert body["resolve_seconds_remaining"] > 24 * 3600
