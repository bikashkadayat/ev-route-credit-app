"""The ORM must match the schema the database is actually built from.

``db/schema.sql`` is the authority (Doc 05). These tests build a real PostgreSQL from it,
reflect the result, and compare it to ``Base.metadata``. Without this, the models are just
a hopeful description of the database.

Docker is unavailable on the development machine, so an embedded PostgreSQL 16 is used
(``pgserver``). The contrib extensions it lacks are handled by the same compatibility
rewrite the verification script uses — see ``scripts/verify_schema.py`` for exactly what
that changes and why it does not affect table or column shape.

Skipped automatically when pgserver is not installed, so the unit suite stays fast.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

pgserver = pytest.importorskip("pgserver", reason="pgserver not installed")
psycopg = pytest.importorskip("psycopg", reason="psycopg not installed")

from sqlalchemy import create_engine, inspect  # noqa: E402
from verify_schema import to_compat_sql  # noqa: E402

from app.models import Base  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "db"

# Columns whose ORM type deliberately differs from the DDL because the embedded build
# lacks the extension. Both are CITEXT in a real deployment and TEXT here.
CITEXT_COLUMNS = {("users", "email"), ("applicants", "email")}


@pytest.fixture(scope="module")
def live_schema():
    """A real PostgreSQL built from db/schema.sql, plus both seeds."""
    workdir = Path(tempfile.mkdtemp(prefix="evrca_parity_"))
    server = pgserver.get_server(workdir)
    try:
        uri = server.get_uri()
        with psycopg.connect(uri, autocommit=True) as conn:
            schema_sql, _ = to_compat_sql((DB / "schema.sql").read_text(encoding="utf-8"))
            conn.execute(schema_sql)
            for seed in ("01_reference_data.sql", "02_demo_data.sql"):
                text, _ = to_compat_sql(
                    (DB / "seed" / seed).read_text(encoding="utf-8")
                )
                conn.execute(text)
        engine = create_engine(uri.replace("postgresql://", "postgresql+psycopg://"))
        yield engine
        engine.dispose()
    finally:
        try:
            server.cleanup()
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


@pytest.fixture(scope="module")
def db_tables(live_schema) -> dict[str, set[str]]:
    inspector = inspect(live_schema)
    out: dict[str, set[str]] = {}
    for name in inspector.get_table_names(schema="public"):
        # partitions are created by create_monthly_partitions(), not modelled
        if any(name.startswith(p) for p in
               ("vehicle_telemetry_", "battery_metrics_", "charging_sessions_")):
            continue
        out[name] = {c["name"] for c in inspector.get_columns(name, schema="public")}
    return out


# ---------------------------------------------------------------------------
def test_every_model_has_a_table_in_the_database(db_tables):
    missing = sorted(set(Base.metadata.tables) - set(db_tables))
    assert not missing, (
        f"the ORM declares tables the schema does not create: {missing}"
    )


def test_every_table_in_the_database_has_a_model(db_tables):
    missing = sorted(set(db_tables) - set(Base.metadata.tables))
    assert not missing, (
        f"the schema creates tables the ORM does not model: {missing}. "
        f"Add them to app/models/ or the API will not be able to reach them."
    )


def test_table_count_matches():
    assert len(Base.metadata.tables) == 40


@pytest.mark.parametrize("table_name", sorted(Base.metadata.tables))
def test_model_columns_exist_in_the_database(table_name, db_tables):
    model_columns = {c.name for c in Base.metadata.tables[table_name].columns}
    db_columns = db_tables[table_name]
    missing = sorted(model_columns - db_columns)
    assert not missing, (
        f"{table_name}: the ORM declares columns the schema does not have: {missing}"
    )


@pytest.mark.parametrize("table_name", sorted(Base.metadata.tables))
def test_database_columns_are_all_modelled(table_name, db_tables):
    model_columns = {c.name for c in Base.metadata.tables[table_name].columns}
    db_columns = db_tables[table_name]
    missing = sorted(db_columns - model_columns)
    assert not missing, (
        f"{table_name}: the schema has columns the ORM does not model: {missing}"
    )


def test_nullability_matches_for_every_column(live_schema, db_tables):
    """A column the ORM thinks is optional but the database requires will fail at INSERT
    time in production rather than at test time here."""
    inspector = inspect(live_schema)
    mismatches: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        db_columns = {
            c["name"]: c for c in inspector.get_columns(table_name, schema="public")
        }
        for column in table.columns:
            db_column = db_columns.get(column.name)
            if db_column is None:
                continue
            # a generated column is NOT NULL in the DB but never written by the ORM
            if db_column.get("computed") is not None:
                continue
            if column.nullable != db_column["nullable"]:
                mismatches.append(
                    f"{table_name}.{column.name}: ORM nullable={column.nullable}, "
                    f"database nullable={db_column['nullable']}"
                )
    assert not mismatches, "nullability drift:\n  " + "\n  ".join(mismatches)


def test_primary_keys_match(live_schema):
    inspector = inspect(live_schema)
    mismatches: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        db_pk = set(inspector.get_pk_constraint(table_name, schema="public")
                    ["constrained_columns"])
        orm_pk = {c.name for c in table.primary_key.columns}
        if db_pk != orm_pk:
            mismatches.append(f"{table_name}: ORM {sorted(orm_pk)} vs database {sorted(db_pk)}")
    assert not mismatches, "primary-key drift:\n  " + "\n  ".join(mismatches)


def test_foreign_key_targets_match(live_schema):
    """Every FK the ORM declares must exist in the database with the same target."""
    inspector = inspect(live_schema)
    mismatches: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        db_fks = {
            (tuple(fk["constrained_columns"]), fk["referred_table"])
            for fk in inspector.get_foreign_keys(table_name, schema="public")
        }
        for constraint in table.foreign_key_constraints:
            key = (
                tuple(c.name for c in constraint.columns),
                next(iter(constraint.elements)).column.table.name,
            )
            if key not in db_fks:
                mismatches.append(f"{table_name}: {key[0]} -> {key[1]} not in the database")
    assert not mismatches, "foreign-key drift:\n  " + "\n  ".join(mismatches)


def test_seeded_data_is_queryable_through_the_orm(live_schema):
    """End-to-end proof that the mapping works, not just that the names line up."""
    from sqlalchemy.orm import Session

    from app.models import RiskRule, Role, Route, ScoringConfiguration

    with Session(live_schema) as session:
        assert session.query(Role).count() == 6
        assert session.query(RiskRule).count() == 22

        active = (
            session.query(ScoringConfiguration)
            .filter(ScoringConfiguration.config_type == "ROUTE",
                    ScoringConfiguration.status == "ACTIVE")
            .one()
        )
        assert active.version_no == 2
        assert active.grade_thresholds[0]["min"] == 88
        assert len(active.components) == 5
        assert sum(c.weight for c in active.components) == 1

        ktm = session.query(Route).filter(Route.route_code == "RT-KTM-DHU-001").one()
        assert str(ktm.latest_score) == "90.12"
        assert ktm.latest_grade == "A"
        assert len(ktm.assessments) == 1
        assert len(ktm.assessments[0].components) == 5
        assert ktm.stations and len(ktm.stations) == 6


def test_relationship_navigation_works(live_schema):
    from sqlalchemy.orm import Session

    from app.models import RiskRule

    with Session(live_schema) as session:
        rule = session.query(RiskRule).filter(
            RiskRule.rule_code == "USAGE_DROP_MODERATE").one()
        assert len(rule.conditions) == 3
        assert [c.sequence_no for c in rule.conditions] == [1, 2, 3]
        # the coverage-gap fix must be what the database actually holds
        upper = next(c for c in rule.conditions if c.operator == "GT")
        assert upper.threshold_value == -50
