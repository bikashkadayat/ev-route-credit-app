"""Telemetry ingestion, baselines and the monitoring view — FR-6.1 to FR-6.6.

This is the half of the product the demo calls "the gap": the vehicle tells you in week two
what repayment-only monitoring finds out six weeks later. None of it is reachable unless
telemetry actually lands and the baselines are actually derived, which is what these test.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.integration.conftest import make_loan

INGEST_DATE = date(2026, 5, 20)


@pytest.fixture
def loan_id(engine) -> int:
    with engine.begin() as conn:
        return make_loan(conn)


def ingest(client: TestClient, admin_headers, on: date) -> dict:
    response = client.post(
        "/api/v1/admin/jobs/run", params={"as_of": on.isoformat()}, headers=admin_headers
    )
    assert response.status_code == 202, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Ingestion — FR-6.1, FR-6.2
# ---------------------------------------------------------------------------
def test_a_days_telemetry_is_ingested(client: TestClient, admin_headers, loan_id, engine):
    ingest(client, admin_headers, INGEST_DATE)
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT count(*) FROM vehicle_telemetry v "
                 "JOIN loans l ON l.vehicle_id = v.vehicle_id "
                 "WHERE l.id = :i AND v.telemetry_date = :d"),
            {"i": loan_id, "d": INGEST_DATE},
        ).scalar_one()
    assert rows == 1


def test_the_documented_metrics_are_all_captured(
    client: TestClient, admin_headers, loan_id, engine
):
    """FR-6.1 names them: km, trips, average speed, active hours, idle and deviation."""
    ingest(client, admin_headers, INGEST_DATE)
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT daily_km, trip_count, avg_speed_kmph, active_hours, "
                 "idle_minutes, route_deviation_percent, estimated_revenue "
                 "FROM vehicle_telemetry v JOIN loans l ON l.vehicle_id = v.vehicle_id "
                 "WHERE l.id = :i AND v.telemetry_date = :d"),
            {"i": loan_id, "d": INGEST_DATE},
        ).one()
    assert all(value is not None for value in row)


def test_battery_metrics_are_captured(client: TestClient, admin_headers, loan_id, engine):
    """FR-6.2 — state of charge, state of health and energy consumed."""
    ingest(client, admin_headers, INGEST_DATE)
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT avg_state_of_charge, state_of_health, energy_consumed_kwh "
                 "FROM battery_metrics b JOIN loans l ON l.vehicle_id = b.vehicle_id "
                 "WHERE l.id = :i AND b.metric_date = :d"),
            {"i": loan_id, "d": INGEST_DATE},
        ).one()
    assert row[1] is not None
    assert Decimal(row[2]) >= 0


def test_ingestion_is_idempotent(client: TestClient, admin_headers, loan_id, engine):
    """A retry after a partial night must overwrite, not double-count the day's kilometres."""
    ingest(client, admin_headers, INGEST_DATE)
    with engine.begin() as conn:
        first = conn.execute(
            text("SELECT daily_km FROM vehicle_telemetry v "
                 "JOIN loans l ON l.vehicle_id = v.vehicle_id "
                 "WHERE l.id = :i AND v.telemetry_date = :d"),
            {"i": loan_id, "d": INGEST_DATE},
        ).scalar_one()

    ingest(client, admin_headers, INGEST_DATE)
    with engine.begin() as conn:
        rows, second = conn.execute(
            text("SELECT count(*), max(daily_km) FROM vehicle_telemetry v "
                 "JOIN loans l ON l.vehicle_id = v.vehicle_id "
                 "WHERE l.id = :i AND v.telemetry_date = :d"),
            {"i": loan_id, "d": INGEST_DATE},
        ).one()
    assert rows == 1
    assert Decimal(second) == Decimal(first)


def test_the_mock_provider_is_deterministic(
    client: TestClient, admin_headers, loan_id, engine
):
    """A demo that told a different story on every run would be useless."""
    ingest(client, admin_headers, INGEST_DATE)
    with engine.begin() as conn:
        first = conn.execute(
            text("SELECT daily_km, trip_count FROM vehicle_telemetry v "
                 "JOIN loans l ON l.vehicle_id = v.vehicle_id "
                 "WHERE l.id = :i AND v.telemetry_date = :d"),
            {"i": loan_id, "d": INGEST_DATE},
        ).one()
        conn.execute(
            text("DELETE FROM vehicle_telemetry v USING loans l "
                 "WHERE l.vehicle_id = v.vehicle_id AND l.id = :i"),
            {"i": loan_id},
        )

    ingest(client, admin_headers, INGEST_DATE)
    with engine.begin() as conn:
        second = conn.execute(
            text("SELECT daily_km, trip_count FROM vehicle_telemetry v "
                 "JOIN loans l ON l.vehicle_id = v.vehicle_id "
                 "WHERE l.id = :i AND v.telemetry_date = :d"),
            {"i": loan_id, "d": INGEST_DATE},
        ).one()
    assert first == second


# ---------------------------------------------------------------------------
# Baselines — FR-6.3, FR-6.4, FR-6.6
# ---------------------------------------------------------------------------
@pytest.fixture
def with_history(client: TestClient, admin_headers, loan_id, engine) -> int:
    """Ninety days of telemetry, so the 30-day window has a real baseline behind it."""
    from sqlalchemy.orm import Session

    # An ordinary, always-reporting profile for this loan's vehicle. Ids are assigned by
    # the database, so a test vehicle can land on one of the scripted demo profiles — and
    # some of those go deliberately silent, which is right for the demo and useless as a
    # baseline fixture.
    from app.integrations.telematics.mock import VehicleProfile
    from app.services.telemetry import TelemetryService

    with Session(engine) as session:
        service = TelemetryService(session)
        vehicle_id = _vehicle_id(engine, loan_id)
        profiles = (VehicleProfile(vehicle_id, Decimal("150")),)
        for offset in range(90, -1, -1):
            service.ingest_day(
                as_of=INGEST_DATE - timedelta(days=offset), profiles=profiles
            )
        session.commit()

    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT count(*) FROM vehicle_telemetry v "
                 "JOIN loans l ON l.vehicle_id = v.vehicle_id WHERE l.id = :i"),
            {"i": loan_id},
        ).scalar_one()
    # Not necessarily all 91: a vehicle that lands on one of the scripted demo profiles
    # may have a silence arc, and the mock emits nothing while a device is silent — which
    # is the correct behaviour, since silence is itself the signal. What the baseline
    # window needs is enough history behind it.
    assert rows >= 60, f"the history fixture wrote only {rows} days"
    return loan_id


def test_the_snapshot_carries_the_rolling_windows(
    client: TestClient, admin_headers, with_history, engine
):
    """FR-6.3 — 7-day and 30-day averages against a 30-90 day baseline."""
    from sqlalchemy.orm import Session

    from app.services.telemetry import TelemetryService

    with Session(engine) as session:
        TelemetryService(session).rebuild_snapshots(as_of=INGEST_DATE)
        session.commit()

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT avg_daily_km_7d, avg_daily_km_30d, baseline_daily_km, "
                 "usage_change_percent, active_days_30d "
                 "FROM loan_monitoring_snapshots WHERE loan_id = :i AND snapshot_date = :d"),
            {"i": with_history, "d": INGEST_DATE},
        ).one()
    assert row[0] is not None and row[1] is not None
    assert row[2] is not None, "no baseline derived from the 30-90 day window"
    assert row[3] is not None, "no percentage change without a baseline to compare to"
    assert row[4] > 0


def test_an_inferred_monthly_revenue_is_derived(
    client: TestClient, admin_headers, with_history, engine
):
    """FR-6.4 — revenue inferred from kilometres and the corridor's fare characteristics."""
    from sqlalchemy.orm import Session

    from app.services.telemetry import TelemetryService

    with Session(engine) as session:
        TelemetryService(session).rebuild_snapshots(as_of=INGEST_DATE)
        session.commit()

    with engine.begin() as conn:
        revenue = conn.execute(
            text("SELECT estimated_revenue_30d FROM loan_monitoring_snapshots "
                 "WHERE loan_id = :i AND snapshot_date = :d"),
            {"i": with_history, "d": INGEST_DATE},
        ).scalar_one()
    assert revenue is not None and Decimal(revenue) > 0


def test_a_loan_with_no_history_gets_nulls_not_zeros(
    client: TestClient, admin_headers, loan_id, engine
):
    """Doc 08 §8.4 — a zero would read as "stationary vehicle" and fire the idle rules on
    a loan nobody has measured yet."""
    from sqlalchemy.orm import Session

    from app.services.telemetry import TelemetryService

    with Session(engine) as session:
        TelemetryService(session).rebuild_snapshots(as_of=INGEST_DATE)
        session.commit()

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT avg_daily_km_7d, baseline_daily_km, usage_change_percent "
                 "FROM loan_monitoring_snapshots WHERE loan_id = :i AND snapshot_date = :d"),
            {"i": loan_id, "d": INGEST_DATE},
        ).one()
    assert row[1] is None
    assert row[2] is None


def test_rebuilding_the_snapshot_is_idempotent(
    client: TestClient, admin_headers, with_history, engine
):
    from sqlalchemy.orm import Session

    from app.services.telemetry import TelemetryService

    def snapshot() -> tuple:
        with engine.begin() as conn:
            return conn.execute(
                text("SELECT avg_daily_km_7d, baseline_daily_km, usage_change_percent, "
                     "active_days_30d FROM loan_monitoring_snapshots "
                     "WHERE loan_id = :i AND snapshot_date = :d"),
                {"i": with_history, "d": INGEST_DATE},
            ).one()

    with Session(engine) as session:
        TelemetryService(session).rebuild_snapshots(as_of=INGEST_DATE)
        session.commit()
    first = snapshot()

    with Session(engine) as session:
        TelemetryService(session).rebuild_snapshots(as_of=INGEST_DATE)
        session.commit()
    assert snapshot() == first


def test_days_since_last_telemetry_is_tracked(
    client: TestClient, admin_headers, with_history, engine
):
    """FR-6.6 — silence is a signal. Never read as good news."""
    from sqlalchemy.orm import Session

    from app.services.telemetry import TelemetryService

    with Session(engine) as session:
        TelemetryService(session).rebuild_snapshots(as_of=INGEST_DATE + timedelta(days=4))
        session.commit()

    with engine.begin() as conn:
        days = conn.execute(
            text("SELECT days_since_last_telemetry FROM loan_monitoring_snapshots "
                 "WHERE loan_id = :i ORDER BY snapshot_date DESC LIMIT 1"),
            {"i": with_history},
        ).scalar_one()
    assert days == 4


# ---------------------------------------------------------------------------
# The monitoring endpoint — FR-6.5
# ---------------------------------------------------------------------------
def test_the_monitoring_view_returns_the_series_and_the_trends(
    client: TestClient, admin_headers, with_history, engine
):
    loan_uuid = _loan_uuid(engine, with_history)
    response = client.get(
        f"/api/v1/loans/{loan_uuid}/monitoring",
        params={"from": (INGEST_DATE - timedelta(days=60)).isoformat(),
                "to": INGEST_DATE.isoformat()},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["series"], "no telemetry series returned"
    assert body["current"]["avg_daily_km_7d"] is not None
    assert body["current"]["active_days_30d"] > 0


def test_the_series_carries_every_documented_daily_field(
    client: TestClient, admin_headers, with_history, engine
):
    loan_uuid = _loan_uuid(engine, with_history)
    body = client.get(
        f"/api/v1/loans/{loan_uuid}/monitoring", headers=admin_headers,
        params={"from": (INGEST_DATE - timedelta(days=10)).isoformat(),
                "to": INGEST_DATE.isoformat()},
    ).json()
    row = body["series"][0]
    for field in ("date", "daily_km", "trip_count", "active_hours",
                  "route_deviation_percent", "estimated_revenue"):
        assert field in row


def test_the_behaviour_score_is_reported(
    client: TestClient, admin_headers, with_history, engine
):
    """Doc 12 step 15 — the behaviour band next to the number."""
    from sqlalchemy.orm import Session

    from app.services.telemetry import TelemetryService

    with Session(engine) as session:
        TelemetryService(session).rebuild_snapshots(as_of=INGEST_DATE)
        session.commit()

    loan_uuid = _loan_uuid(engine, with_history)
    body = client.get(
        f"/api/v1/loans/{loan_uuid}/monitoring", headers=admin_headers
    ).json()
    assert body["current"]["behaviour_score"] is not None
    assert body["current"]["behaviour_band"] in {"HEALTHY", "WATCH", "STRESSED"}


def test_monitoring_needs_the_portfolio_permission(
    client: TestClient, viewer_headers, loan_id, engine
):
    loan_uuid = _loan_uuid(engine, loan_id)
    assert client.get(
        f"/api/v1/loans/{loan_uuid}/monitoring", headers=viewer_headers
    ).status_code == 200  # a viewer holds portfolio:read


def test_monitoring_an_unknown_loan_is_a_404(client: TestClient, admin_headers):
    assert client.get(
        "/api/v1/loans/00000000-0000-0000-0000-000000000000/monitoring",
        headers=admin_headers,
    ).status_code == 404


# ---------------------------------------------------------------------------
# The nightly chain
# ---------------------------------------------------------------------------
def test_the_nightly_pass_runs_telemetry_before_the_rules(
    client: TestClient, admin_headers, loan_id
):
    """The order is not cosmetic: the rule engine reads the baselines, so evaluating
    before they are rebuilt would raise alerts against yesterday's position."""
    body = ingest(client, admin_headers, INGEST_DATE)
    names = [run["job_name"] for run in body["runs"]]
    assert names == [
        "ingest_telemetry",
        "rebuild_usage_baselines",
        "recompute_dpd_and_outstanding",
        "recompute_classification",
        "evaluate_risk_rules",
    ]


def test_every_job_in_the_chain_records_a_run(
    client: TestClient, admin_headers, loan_id, engine
):
    ingest(client, admin_headers, INGEST_DATE)
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT job_name, status FROM job_runs WHERE as_of_date = :d"),
            {"d": INGEST_DATE},
        ).all()
    assert {"ingest_telemetry", "rebuild_usage_baselines"} <= {r[0] for r in rows}
    assert all(r[1] == "SUCCESS" for r in rows)


def _loan_uuid(engine, loan_id: int) -> str:
    with engine.begin() as conn:
        return str(
            conn.execute(
                text("SELECT uuid FROM loans WHERE id = :i"), {"i": loan_id}
            ).scalar_one()
        )


def _vehicle_id(engine, loan_id: int) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text("SELECT vehicle_id FROM loans WHERE id = :i"), {"i": loan_id}
        ).scalar_one()
