"""Alert evaluation and lifecycle — end to end.

The rule engine is pure and has its own suite. What is tested here is everything the API
layer adds around it: that a run is idempotent for a business date, that a live alert is
refreshed rather than duplicated, that a cleared condition auto-resolves, that a
higher-severity sibling supersedes a lower one, and that the acknowledge/resolve lifecycle
enforces its own preconditions.

Loans have no public write endpoint, so they are inserted with SQL. Every assertion still
goes through the API.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.integration.conftest import (
    AS_OF,
)
from tests.integration.conftest import (
    make_loan as _make_loan,
)
from tests.integration.conftest import (
    snapshot as _snapshot,
)


@pytest.fixture
def healthy_loan(engine) -> int:
    with engine.begin() as conn:
        loan_id = _make_loan(conn)
        _snapshot(
            conn, loan_id, AS_OF,
            avg_daily_km_7d=210, avg_daily_km_30d=205, baseline_daily_km=200,
            usage_change_percent=2, active_days_30d=26, zero_km_streak_days=0,
            charging_sessions_30d=48, charging_change_percent=0,
            estimated_revenue_30d=190000, revenue_change_percent=1,
            avg_route_deviation_percent=4, latest_state_of_health=97,
            days_since_last_telemetry=0, days_past_due=0,
        )
    return loan_id


@pytest.fixture
def delinquent_loan(engine) -> int:
    """35 days past due with two missed instalments — the RED arrears case."""
    with engine.begin() as conn:
        loan_id = _make_loan(
            conn, dpd=35, overdue=Decimal("145600"), missed=2
        )
        _snapshot(
            conn, loan_id, AS_OF,
            avg_daily_km_7d=180, avg_daily_km_30d=190, baseline_daily_km=200,
            usage_change_percent=-5, active_days_30d=24, zero_km_streak_days=0,
            charging_sessions_30d=44, charging_change_percent=-4,
            estimated_revenue_30d=175000, revenue_change_percent=-3,
            avg_route_deviation_percent=6, latest_state_of_health=95,
            days_since_last_telemetry=0, days_past_due=35,
        )
    return loan_id


def evaluate(client: TestClient, headers, loan_id: int, as_of: str = AS_OF) -> dict:
    response = client.post(
        "/api/v1/alerts/evaluate",
        json={"loan_id": loan_id, "as_of": as_of},
        headers=headers,
    )
    assert response.status_code == 202, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
def test_a_healthy_loan_raises_nothing(client: TestClient, admin_headers, healthy_loan):
    """A rule catalogue that fires on a good loan is worse than useless — it trains
    officers to ignore alerts."""
    run = evaluate(client, admin_headers, healthy_loan)
    assert run["loans_evaluated"] == 1
    assert run["alerts_created"] == 0
    assert run["results"][0]["risk_status"] == "GREEN"


def test_a_delinquent_loan_raises_alerts_and_turns_red(
    client: TestClient, admin_headers, delinquent_loan
):
    run = evaluate(client, admin_headers, delinquent_loan)
    assert run["alerts_created"] >= 1
    assert run["results"][0]["risk_status"] == "RED"


def test_the_arrears_rules_that_fire_are_the_documented_ones(
    client: TestClient, admin_headers, delinquent_loan
):
    """35 DPD and two missed instalments: over 30 days, consecutive misses, and a material
    overdue amount. The 1-30 day rule must not also fire — the bands are exclusive."""
    created = set(evaluate(client, admin_headers, delinquent_loan)["results"][0]["created"])
    assert "EMI_OVERDUE_30_PLUS" in created
    assert "EMI_MISSED_CONSECUTIVE" in created
    assert "OVERDUE_AMOUNT_MATERIAL" in created
    assert "EMI_OVERDUE_1_30" not in created


def test_the_run_reports_the_engine_version_and_business_date(
    client: TestClient, admin_headers, healthy_loan
):
    run = evaluate(client, admin_headers, healthy_loan)
    assert run["as_of"] == AS_OF
    assert run["engine_version"].startswith("rule-engine@")


def test_re_running_for_the_same_date_refreshes_rather_than_duplicates(
    client: TestClient, admin_headers, delinquent_loan
):
    """Doc 08 §8.6 — an officer must not find the same alert three times because a job
    ran three times."""
    first = evaluate(client, admin_headers, delinquent_loan)
    second = evaluate(client, admin_headers, delinquent_loan)

    assert first["alerts_created"] > 0
    assert second["alerts_created"] == 0
    assert second["alerts_refreshed"] == first["alerts_created"]


def test_the_alert_count_does_not_grow_on_re_evaluation(
    client: TestClient, admin_headers, delinquent_loan
):
    evaluate(client, admin_headers, delinquent_loan)
    after_first = client.get(
        "/api/v1/alerts", params={"loan_id": delinquent_loan}, headers=admin_headers
    ).json()["total"]

    evaluate(client, admin_headers, delinquent_loan)
    after_second = client.get(
        "/api/v1/alerts", params={"loan_id": delinquent_loan}, headers=admin_headers
    ).json()["total"]

    assert after_first == after_second


def test_a_cleared_condition_auto_resolves_its_alert(
    client: TestClient, admin_headers, engine, delinquent_loan
):
    """Doc 08 §8.7 — the queue has to empty itself when the borrower catches up, or it
    fills with stale work nobody trusts."""
    evaluate(client, admin_headers, delinquent_loan)

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE loans SET days_past_due = 0, overdue_amount = 0,"
                " installments_overdue = 0 WHERE id = :i"
            ),
            {"i": delinquent_loan},
        )

    run = evaluate(client, admin_headers, delinquent_loan)
    assert run["alerts_auto_resolved"] >= 1
    assert run["results"][0]["risk_status"] == "GREEN"


def test_evaluating_the_whole_book_covers_every_active_loan(
    client: TestClient, admin_headers, healthy_loan, delinquent_loan
):
    response = client.post(
        "/api/v1/alerts/evaluate", json={"as_of": AS_OF}, headers=admin_headers
    )
    assert response.status_code == 202
    assert response.json()["loans_evaluated"] >= 2


def test_evaluating_an_unknown_loan_is_a_404(client: TestClient, admin_headers):
    response = client.post(
        "/api/v1/alerts/evaluate", json={"loan_id": 999_999}, headers=admin_headers
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "LOAN_NOT_FOUND"


def test_a_loan_with_no_telemetry_raises_no_telemetry_alerts(
    client: TestClient, admin_headers, engine
):
    """Doc 08 §8.4 — an unmeasured vehicle must not look like a stationary one."""
    with engine.begin() as conn:
        loan_id = _make_loan(conn)

    created = set(evaluate(client, admin_headers, loan_id)["results"][0]["created"])
    telemetry_rules = {
        "VEHICLE_INACTIVE_7D", "VEHICLE_INACTIVE_3D", "USAGE_COLLAPSE",
        "USAGE_DROP_MODERATE", "LOW_ACTIVE_DAYS", "BATTERY_HEALTH_CRITICAL",
        "TELEMETRY_SILENT_5D", "TELEMETRY_SILENT_2D",
    }
    assert not (created & telemetry_rules), f"fired on missing data: {created & telemetry_rules}"


# ---------------------------------------------------------------------------
# Alert reads and lifecycle
# ---------------------------------------------------------------------------
@pytest.fixture
def open_alert(client: TestClient, admin_headers, delinquent_loan) -> dict:
    evaluate(client, admin_headers, delinquent_loan)
    body = client.get(
        "/api/v1/alerts",
        params={"loan_id": delinquent_loan, "severity": "RED"},
        headers=admin_headers,
    ).json()
    assert body["items"], "the delinquent loan should have raised a RED alert"
    return body["items"][0]


def test_an_alert_carries_what_an_officer_needs_to_act(
    client: TestClient, admin_headers, open_alert
):
    response = client.get(f"/api/v1/alerts/{open_alert['id']}", headers=admin_headers)
    assert response.status_code == 200

    body = response.json()
    assert body["alert_number"]
    assert body["severity"] == "RED"
    assert body["status"] == "OPEN"
    assert body["trigger_condition"], "the alert must say what fired"
    assert body["resolve_due_at"], "an SLA without a due time is not an SLA"


def test_an_unknown_alert_is_a_404(client: TestClient, admin_headers):
    response = client.get(
        "/api/v1/alerts/00000000-0000-0000-0000-000000000000", headers=admin_headers
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ALERT_NOT_FOUND"


def test_acknowledging_stops_the_first_sla_clock(
    client: TestClient, admin_headers, open_alert
):
    response = client.post(
        f"/api/v1/alerts/{open_alert['id']}/acknowledge", headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ACKNOWLEDGED"
    assert response.json()["acknowledged_at"]


def test_resolving_requires_a_code_and_a_real_explanation(
    client: TestClient, admin_headers, open_alert
):
    response = client.post(
        f"/api/v1/alerts/{open_alert['id']}/resolve",
        json={"resolution_code": "PAYMENT_RECEIVED", "resolution_notes": "ok"},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_a_false_positive_needs_a_longer_justification(
    client: TestClient, admin_headers, open_alert
):
    """Doc 08 §8.7 — closing an alert as a false positive is how a rule gets quietly
    disabled in practice, so it costs more to write."""
    short = client.post(
        f"/api/v1/alerts/{open_alert['id']}/resolve",
        json={"resolution_code": "FALSE_POSITIVE", "resolution_notes": "not real at all"},
        headers=admin_headers,
    )
    assert short.status_code == 422

    long_enough = client.post(
        f"/api/v1/alerts/{open_alert['id']}/resolve",
        json={
            "resolution_code": "FALSE_POSITIVE",
            "resolution_notes": "Telemetry backfill error; the vehicle was operating normally.",
        },
        headers=admin_headers,
    )
    assert long_enough.status_code == 200


def test_an_alert_can_be_resolved_with_notes(client: TestClient, admin_headers, open_alert):
    response = client.post(
        f"/api/v1/alerts/{open_alert['id']}/resolve",
        json={
            "resolution_code": "PAYMENT_RECEIVED",
            "resolution_notes": "Borrower cleared both instalments on 2026-03-02.",
        },
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "RESOLVED"
    assert response.json()["resolution_code"] == "PAYMENT_RECEIVED"


def test_resolving_a_closed_alert_is_a_409(client: TestClient, admin_headers, open_alert):
    body = {
        "resolution_code": "PAYMENT_RECEIVED",
        "resolution_notes": "Borrower cleared both instalments on 2026-03-02.",
    }
    assert client.post(
        f"/api/v1/alerts/{open_alert['id']}/resolve", json=body, headers=admin_headers
    ).status_code == 200

    second = client.post(
        f"/api/v1/alerts/{open_alert['id']}/resolve", json=body, headers=admin_headers
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "INVALID_STATE_TRANSITION"


# ---------------------------------------------------------------------------
# Listing and authorisation
# ---------------------------------------------------------------------------
def test_alerts_can_be_filtered_by_severity(
    client: TestClient, admin_headers, delinquent_loan
):
    evaluate(client, admin_headers, delinquent_loan)
    body = client.get(
        "/api/v1/alerts", params={"severity": "RED"}, headers=admin_headers
    ).json()
    assert body["items"]
    assert all(a["severity"] == "RED" for a in body["items"])


def test_the_alert_queue_is_paginated(client: TestClient, admin_headers, delinquent_loan):
    evaluate(client, admin_headers, delinquent_loan)
    body = client.get(
        "/api/v1/alerts", params={"page_size": 1}, headers=admin_headers
    ).json()
    assert len(body["items"]) <= 1
    assert body["total"] >= 1


def test_a_viewer_may_read_alerts_but_not_evaluate_them(
    client: TestClient, viewer_headers, healthy_loan
):
    """Evaluation writes alerts across the book; reading the queue does not."""
    assert client.get("/api/v1/alerts", headers=viewer_headers).status_code == 200

    denied = client.post(
        "/api/v1/alerts/evaluate", json={"as_of": AS_OF}, headers=viewer_headers
    )
    assert denied.status_code == 403


def test_a_viewer_may_not_resolve_an_alert(client: TestClient, viewer_headers, open_alert):
    response = client.post(
        f"/api/v1/alerts/{open_alert['id']}/resolve",
        json={"resolution_code": "PAYMENT_RECEIVED",
              "resolution_notes": "Borrower cleared both instalments."},
        headers=viewer_headers,
    )
    assert response.status_code == 403


def test_a_portfolio_manager_may_work_the_queue(
    client: TestClient, portfolio_headers, open_alert
):
    assert client.post(
        f"/api/v1/alerts/{open_alert['id']}/acknowledge", headers=portfolio_headers
    ).status_code == 200


def test_an_anonymous_alert_request_is_401(client: TestClient):
    assert client.get("/api/v1/alerts").status_code == 401


# ---------------------------------------------------------------------------
# Portfolio read side
# ---------------------------------------------------------------------------
def test_the_portfolio_summary_aggregates_the_book(
    client: TestClient, admin_headers, healthy_loan, delinquent_loan
):
    response = client.get("/api/v1/portfolio/summary", headers=admin_headers)
    assert response.status_code == 200

    kpis = response.json()["kpis"]
    assert kpis["active_loans"] >= 2
    assert Decimal(kpis["total_outstanding"]) > 0


def test_the_loan_book_can_be_filtered_by_arrears(
    client: TestClient, admin_headers, delinquent_loan
):
    body = client.get(
        "/api/v1/portfolio", params={"min_dpd": 30}, headers=admin_headers
    ).json()
    assert body["items"]
    assert all(row["days_past_due"] >= 30 for row in body["items"])


def test_the_loan_book_is_worst_first(client: TestClient, admin_headers, delinquent_loan,
                                      healthy_loan):
    items = client.get(
        "/api/v1/portfolio", params={"page_size": 50}, headers=admin_headers
    ).json()["items"]
    dpds = [row["days_past_due"] for row in items]
    assert dpds == sorted(dpds, reverse=True)


def test_a_viewer_may_read_the_portfolio(client: TestClient, viewer_headers):
    assert client.get("/api/v1/portfolio/summary", headers=viewer_headers).status_code == 200


def test_the_business_date_is_an_input_not_a_clock_read(
    client: TestClient, admin_headers, engine
):
    """A snapshot dated after the business date must not be visible to that run."""
    with engine.begin() as conn:
        loan_id = _make_loan(conn)
        _snapshot(conn, loan_id, "2026-03-31", zero_km_streak_days=30,
                  active_days_30d=0, days_since_last_telemetry=30)

    early = evaluate(client, admin_headers, loan_id, as_of="2026-01-31")
    assert early["results"][0]["created"] == []

    later = evaluate(client, admin_headers, loan_id, as_of=str(date(2026, 4, 15)))
    assert "VEHICLE_INACTIVE_7D" in set(later["results"][0]["created"])


def test_an_escalating_loan_supersedes_the_lower_severity_alert(
    client: TestClient, admin_headers, engine
):
    """Doc 08 §8.6.1 — one actionable item per family. When a borrower slips past 90 days
    both arrears rules fire; the queue must show the 90+ alert and close the 30+ one as
    SUPERSEDED rather than leaving two live items describing the same arrears."""
    with engine.begin() as conn:
        loan_id = _make_loan(conn, dpd=35, overdue=Decimal("145600"), missed=2)

    first = evaluate(client, admin_headers, loan_id)
    assert "EMI_OVERDUE_30_PLUS" in set(first["results"][0]["created"])

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE loans SET days_past_due = 100, overdue_amount = 291200,"
                " installments_overdue = 4 WHERE id = :i"
            ),
            {"i": loan_id},
        )

    result = evaluate(client, admin_headers, loan_id)["results"][0]
    assert "EMI_OVERDUE_90_PLUS" in set(result["created"])
    assert "EMI_OVERDUE_30_PLUS" in set(result["superseded"])

    statuses = {
        a["status"]
        for a in client.get(
            "/api/v1/alerts", params={"loan_id": loan_id, "page_size": 50},
            headers=admin_headers,
        ).json()["items"]
    }
    assert "SUPERSEDED" in statuses


def test_a_superseded_alert_leaves_one_live_item_for_the_family(
    client: TestClient, admin_headers, engine
):
    with engine.begin() as conn:
        loan_id = _make_loan(conn, dpd=35, overdue=Decimal("145600"), missed=2)
    evaluate(client, admin_headers, loan_id)

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE loans SET days_past_due = 100 WHERE id = :i"), {"i": loan_id}
        )
    evaluate(client, admin_headers, loan_id)

    live = client.get(
        "/api/v1/alerts",
        params={"loan_id": loan_id, "status": "OPEN", "page_size": 50},
        headers=admin_headers,
    ).json()["items"]

    # Supersession is per family, so the three arrears-ladder rules collapse to one live
    # item. The consecutive-miss and overdue-amount rules are separate signals and are
    # meant to stay: they tell the officer different things.
    ladder = [a for a in live if "Days Past Due" in (a["trigger_condition"] or "")]
    assert len(ladder) == 1, f"ladder not collapsed: {[a['trigger_condition'] for a in ladder]}"
    assert "> 90" in ladder[0]["trigger_condition"]
