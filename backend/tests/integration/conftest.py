"""Integration fixtures — a real PostgreSQL 16 and a live FastAPI client.

Docker is unavailable on the development machine, so an embedded PostgreSQL (``pgserver``)
is used. It is the same PostgreSQL binary, so these are genuine integration tests against
the real schema, real constraints and real triggers — not an in-memory substitute.

The whole module is skipped when pgserver is absent, keeping the unit suite fast.
"""

from __future__ import annotations

import itertools
import shutil
import sys
import tempfile
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

pgserver = pytest.importorskip("pgserver", reason="pgserver not installed")
psycopg = pytest.importorskip("psycopg", reason="psycopg not installed")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from verify_schema import to_compat_sql  # noqa: E402

from app.core.database import configure_engine, reset_engine  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.services.config import clear_config_cache  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "db"

#: Permission sets mirroring db/seed/01_reference_data.sql, so a token issued here grants
#: exactly what the corresponding seeded role grants in production.
ROLE_PERMISSIONS: dict[str, list[str]] = {
    "SUPER_ADMIN": [
        "route:read", "route:create", "route:update", "route:delete", "route:assess",
        "applicant:read", "applicant:create", "applicant:update",
        "applicant:read_sensitive", "applicant:credit_check",
        "application:read", "application:create", "application:assess",
        "application:approve", "application:override",
        "loan:create", "repayment:create", "portfolio:read",
        "alert:read", "alert:assign", "alert:resolve",
        "risk:config:read", "risk:config:publish", "risk:waiver",
        "settings:read", "settings:update", "report:read", "audit:read",
    ],
    "RISK_MANAGER": [
        "route:read", "route:create", "route:update", "route:assess",
        "applicant:read", "applicant:create", "applicant:update",
        "applicant:credit_check", "application:read", "application:create",
        "application:assess", "application:approve", "application:override",
        "loan:create", "portfolio:read", "alert:read", "alert:assign", "alert:resolve",
        "risk:config:read", "risk:config:publish", "risk:waiver",
        "settings:read", "settings:update", "report:read", "audit:read",
    ],
    "CREDIT_OFFICER": [
        "route:read", "route:create", "route:update", "route:assess",
        "applicant:read", "applicant:create", "applicant:update",
        "applicant:credit_check", "application:read", "application:create",
        "application:assess", "loan:create", "portfolio:read", "alert:read",
        "risk:config:read", "report:read",
    ],
    "PORTFOLIO_MANAGER": [
        "route:read", "applicant:read", "application:read", "repayment:create",
        "portfolio:read", "alert:read", "alert:assign", "alert:resolve",
        "risk:config:read", "report:read",
    ],
    "VIEWER": [
        "route:read", "applicant:read", "application:read", "portfolio:read",
        "alert:read", "risk:config:read", "report:read", "audit:read",
    ],
}


@pytest.fixture(scope="session")
def pg_server() -> Iterator[str]:
    """One PostgreSQL instance for the whole session, with schema and seeds applied."""
    workdir = Path(tempfile.mkdtemp(prefix="evrca_api_"))
    server = pgserver.get_server(workdir)
    try:
        uri = server.get_uri()
        with psycopg.connect(uri, autocommit=True) as conn:
            schema_sql, _ = to_compat_sql((DB / "schema.sql").read_text(encoding="utf-8"))
            conn.execute(schema_sql)
            for seed in ("01_reference_data.sql", "02_demo_data.sql"):
                seed_sql, _ = to_compat_sql(
                    (DB / "seed" / seed).read_text(encoding="utf-8")
                )
                conn.execute(seed_sql)
            # db/schema.sql and migration 0001 are the same DDL, so stamping matches what
            # `alembic upgrade head` leaves behind and the readiness probe reports the
            # revision an operator would actually see.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS alembic_version "
                "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
            conn.execute("INSERT INTO alembic_version VALUES ('0001')")
        yield uri
    finally:
        try:
            server.cleanup()
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


@pytest.fixture(scope="session")
def engine(pg_server: str):
    eng = create_engine(
        pg_server.replace("postgresql://", "postgresql+psycopg://"), future=True
    )
    configure_engine(eng)
    yield eng
    eng.dispose()
    reset_engine()


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """The throttle counters are process-global, so one suite's logins would exhaust the
    window for the next. Cleared between tests, exactly as the config cache is; the
    throttle behaviour itself is asserted in test_api_password_security.py."""
    from app.core import ratelimit

    ratelimit.reset()
    yield
    ratelimit.reset()


@pytest.fixture(autouse=True)
def _reset_config_cache():
    """The scorecard cache is process-local; clear it between tests so one test's
    configuration can never leak into another's assertions."""
    clear_config_cache()
    yield
    clear_config_cache()


@pytest.fixture(scope="session")
def client(engine) -> Iterator[TestClient]:
    from app.main import create_app

    with TestClient(create_app(), raise_server_exceptions=False) as test_client:
        yield test_client


def token_for(role: str, *, user_id: int = 1, permissions: list[str] | None = None) -> str:
    return create_access_token(
        subject=str(user_id),
        email=f"{role.lower()}@bank.com.np",
        role=role,
        permissions=permissions if permissions is not None else ROLE_PERMISSIONS[role],
    )


def auth(role: str, *, user_id: int = 1) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_for(role, user_id=user_id)}"}


@pytest.fixture
def admin_headers() -> dict[str, str]:
    return auth("SUPER_ADMIN")


@pytest.fixture
def officer_headers() -> dict[str, str]:
    return auth("CREDIT_OFFICER", user_id=3)


@pytest.fixture
def viewer_headers() -> dict[str, str]:
    return auth("VIEWER", user_id=6)


@pytest.fixture
def portfolio_headers() -> dict[str, str]:
    return auth("PORTFOLIO_MANAGER", user_id=4)


@pytest.fixture
def seeded_route_id(client: TestClient, admin_headers: dict[str, str]) -> str:
    """The Kathmandu-Dhulikhel corridor from the demo pack."""
    response = client.get(
        "/api/v1/routes", params={"q": "Dhulikhel"}, headers=admin_headers
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert items, "demo seed is missing the Kathmandu-Dhulikhel route"
    return items[0]["id"]


#: Routes are unique on (origin, destination), so each test needs its own pair.
_ROUTE_SEQUENCE = itertools.count(1)


@pytest.fixture
def route_payload() -> dict:
    """A valid, complete route body with endpoints unique to this test."""
    n = next(_ROUTE_SEQUENCE)
    return {
        "route_name": f"Test Corridor {n:03d}",
        "origin": f"Testpur{n:03d}",
        "destination": f"Trialganj{n:03d}",
        "province": "Bagmati",
        "district": "Kathmandu",
        "route_type": "SUBURBAN",
        "total_distance_km": "24.0",
        "road_type": "PITCH",
        "road_condition": "GOOD",
        "gradient_profile": "FLAT",
        "pitch_road_percent": "100",
        "charging_station_count": 5,
        "fast_charger_count": 2,
        "avg_charging_distance_km": "4.8",
        "max_charging_gap_km": "9.0",
        "passenger_volume_daily": 1400,
        "freight_volume_daily_tons": "0",
        "estimated_daily_trips": "9",
        "avg_fare_per_trip": "420",
        "avg_freight_revenue_per_trip": "0",
        "traffic_density": "HIGH",
        "competition_level": "MODERATE",
        "operator_count": 40,
        "seasonal_risk": "LOW",
        "monsoon_disruption_days": 5,
        "flood_landslide_risk": "LOW",
        "security_risk": "LOW",
        "electricity_tariff_per_kwh": "12",
        "estimated_daily_operating_cost": "1500",
        "permit_required": False,
    }


@pytest.fixture
def customer_payload() -> dict:
    return {
        "applicant_type": "OWNER_DRIVER",
        "full_name": "Test Applicant One",
        "date_of_birth": "1992-03-14",
        "gender": "MALE",
        "id_type": "CITIZENSHIP",
        "id_number": "27-99-70-00001",
        "phone": "+9779841110001",
        "province": "Bagmati",
        "district": "Kathmandu",
        "municipality": "Kathmandu Metropolitan",
        "ward_no": 16,
        "total_experience_years": "9",
        "driving_experience_years": "9",
        "commercial_driving_years": "6",
        "licence_category": "B",
        "licence_expiry": "2029-03-10",
        "has_previous_ev_experience": False,
    }

# ---------------------------------------------------------------------------
# Shared loan fixtures
#
# These live here rather than in a test module because two suites build loans, and a
# module-level counter imported across test files can end up duplicated — pytest may hold
# the same file as two module objects, each with its own sequence, which collides on the
# account-number unique constraint.
# ---------------------------------------------------------------------------
AS_OF = "2026-02-28"

_LOAN_SEQUENCE = itertools.count(1)


def make_loan(conn, *, dpd: int = 0, overdue: Decimal = Decimal("0"),
               missed: int = 0) -> int:
    """A minimal ACTIVE loan on a seeded route, with a real applicant and vehicle."""
    number = next(_LOAN_SEQUENCE)
    route_id = conn.execute(
        text("SELECT id FROM routes ORDER BY id LIMIT 1")
    ).scalar_one()
    model_id = conn.execute(
        text("SELECT id FROM vehicle_models ORDER BY id LIMIT 1")
    ).scalar_one()
    user_id = conn.execute(text("SELECT id FROM users ORDER BY id LIMIT 1")).scalar_one()

    applicant_id = conn.execute(
        text(
            "INSERT INTO applicants (applicant_code, applicant_type, full_name,"
            " id_type, id_number_hash, id_number_enc, phone, province, district,"
            " municipality, status)"
            " VALUES (:c, 'OWNER_DRIVER', :n, 'CITIZENSHIP', :h, :e, :p,"
            " 'Bagmati', 'Kathmandu', 'Kathmandu Metropolitan', 'ACTIVE')"
            " RETURNING id"
        ),
        {
            "c": f"APL-LOAN-{number:05d}",
            "n": f"Loan Holder {number:03d}",
            "h": f"hash-loan-{number:05d}",
            "e": f"27-55-70-{number:05d}".encode(),
            "p": f"+97798412{number:05d}",
        },
    ).scalar_one()
    financial_id = conn.execute(
        text(
            "INSERT INTO applicant_financials (applicant_id, version_no, is_current,"
            " monthly_income, monthly_household_expenses) VALUES (:a, 1, true, 120000, 50000)"
            " RETURNING id"
        ),
        {"a": applicant_id},
    ).scalar_one()
    vehicle_id = conn.execute(
        text(
            "INSERT INTO vehicles (vehicle_model_id, purchase_price, status)"
            " VALUES (:m, 4600000, 'FINANCED') RETURNING id"
        ),
        {"m": model_id},
    ).scalar_one()
    application_id = conn.execute(
        text(
            "INSERT INTO loan_applications (application_number, applicant_id,"
            " financial_profile_id, vehicle_id, route_id, requested_amount,"
            " requested_tenure_months, proposed_interest_rate, down_payment_amount,"
            " vehicle_on_road_price, expected_daily_km, status)"
            " VALUES (:n, :a, :f, :v, :r, 3200000, 60, 13.0, 1400000, 4600000, 240,"
            " 'APPROVED') RETURNING id"
        ),
        {
            "n": f"APP-LOAN-{number:05d}",
            "a": applicant_id,
            "f": financial_id,
            "v": vehicle_id,
            "r": route_id,
        },
    ).scalar_one()

    return conn.execute(
        text(
            "INSERT INTO loans (loan_account_number, application_id, applicant_id,"
            " vehicle_id, route_id, principal_amount, interest_rate, tenure_months,"
            " emi_amount, down_payment, ltv_percent, disbursement_date, first_emi_date,"
            " maturity_date, total_interest, total_payable, outstanding_principal,"
            " overdue_amount, days_past_due, installments_overdue, risk_status,"
            " final_risk_score, risk_grade, status, created_by)"
            " VALUES (:n, :app, :a, :v, :r, 3200000, 13.0, 60, 72800, 1400000, 69.57,"
            " DATE '2025-03-01', DATE '2025-04-01', DATE '2030-03-01', 1168000, 4368000,"
            " 3000000, :od, :dpd, :missed, 'GREEN', 78.5, 'B', 'ACTIVE', :u)"
            " RETURNING id"
        ),
        {
            "n": f"LN-TEST-{number:05d}",
            "app": application_id,
            "a": applicant_id,
            "v": vehicle_id,
            "r": route_id,
            "od": overdue,
            "dpd": dpd,
            "missed": missed,
            "u": user_id,
        },
    ).scalar_one()


def snapshot(conn, loan_id: int, snapshot_date: str, **metrics) -> None:
    columns = ", ".join(metrics)
    values = ", ".join(f":{k}" for k in metrics)
    conn.execute(
        text(
            f"INSERT INTO loan_monitoring_snapshots (loan_id, snapshot_date, {columns})"
            f" VALUES (:loan_id, :snapshot_date, {values})"
            " ON CONFLICT (loan_id, snapshot_date) DO NOTHING"
        ),
        {"loan_id": loan_id, "snapshot_date": snapshot_date, **metrics},
    )
