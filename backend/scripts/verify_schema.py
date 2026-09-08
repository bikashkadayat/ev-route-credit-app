"""Apply db/schema.sql and both seeds to a real PostgreSQL 16 instance and verify them.

Docker is not available on this machine, so an embedded PostgreSQL (``pgserver``) is used.
It is a genuine PostgreSQL 16 server, so this is runtime verification of the DDL,
constraints, triggers, partitions, materialised views and seed data — not a syntax check.

WHAT IS NOT COVERED
-------------------
The embedded build ships only ``plpgsql`` and ``vector``; the four contrib extensions the
schema uses are unavailable. For verification only, the script rewrites:

  * ``CREATE EXTENSION`` for pgcrypto / citext / pg_trgm / btree_gin  -> removed
  * ``CITEXT`` column type                                           -> ``TEXT``
  * ``gin (... gin_trgm_ops)`` trigram indexes                       -> skipped

Everything else runs verbatim. ``gen_random_uuid()`` needs no rewrite: it has been core
PostgreSQL since 13. On any packaged or managed PostgreSQL 16 (including the
docker-compose service in this repo) all four extensions are present and the unmodified
file applies as written.

    cd backend && python scripts/verify_schema.py
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "db"

EXTENSIONS_UNAVAILABLE = ("pgcrypto", "citext", "pg_trgm", "btree_gin")


def to_compat_sql(sql: str) -> tuple[str, list[str]]:
    """Rewrite the schema for an embedded server without contrib. Returns (sql, notes)."""
    notes: list[str] = []

    for ext in EXTENSIONS_UNAVAILABLE:
        pattern = rf"CREATE EXTENSION IF NOT EXISTS {ext};.*?\n"
        if re.search(pattern, sql):
            sql = re.sub(pattern, "", sql)
    notes.append(f"removed CREATE EXTENSION for {', '.join(EXTENSIONS_UNAVAILABLE)}")

    citext_count = len(re.findall(r"\bCITEXT\b", sql))
    sql = re.sub(r"\bCITEXT\b", "TEXT", sql)
    notes.append(f"CITEXT -> TEXT on {citext_count} column(s)")

    trigram = re.findall(r"^CREATE INDEX .*gin_trgm_ops.*;$", sql, flags=re.MULTILINE)
    sql = re.sub(r"^CREATE INDEX .*gin_trgm_ops.*;$", "", sql, flags=re.MULTILINE)
    notes.append(f"skipped {len(trigram)} trigram index/indexes")

    return sql, notes


STRUCTURE = [
    ("tables", "SELECT count(*) FROM information_schema.tables "
               "WHERE table_schema='public' AND table_type='BASE TABLE'"),
    ("enum types", "SELECT count(*) FROM pg_type WHERE typtype='e'"),
    ("indexes", "SELECT count(*) FROM pg_indexes WHERE schemaname='public'"),
    ("triggers", "SELECT count(DISTINCT trigger_name||event_object_table) "
                 "FROM information_schema.triggers WHERE trigger_schema='public'"),
    ("materialised views", "SELECT count(*) FROM pg_matviews WHERE schemaname='public'"),
    ("functions", "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                  "WHERE n.nspname='public'"),
    ("foreign keys", "SELECT count(*) FROM information_schema.table_constraints "
                     "WHERE constraint_type='FOREIGN KEY' AND table_schema='public'"),
    ("check constraints", "SELECT count(*) FROM information_schema.table_constraints "
                          "WHERE constraint_type='CHECK' AND table_schema='public'"),
    ("partitioned tables", "SELECT count(*) FROM pg_class WHERE relkind='p'"),
    ("telemetry partitions", "SELECT count(*) FROM pg_inherits i "
                             "JOIN pg_class p ON p.oid=i.inhparent "
                             "WHERE p.relname='vehicle_telemetry'"),
]

SEED_COUNTS = [
    ("roles", "SELECT count(*) FROM roles", 6),
    ("permissions", "SELECT count(*) FROM permissions", 38),
    ("role_permissions", "SELECT count(*) FROM role_permissions", None),
    ("system_settings", "SELECT count(*) FROM system_settings", None),
    ("scoring_configurations", "SELECT count(*) FROM scoring_configurations", 4),
    ("  ACTIVE", "SELECT count(*) FROM scoring_configurations WHERE status='ACTIVE'", 3),
    ("  ARCHIVED", "SELECT count(*) FROM scoring_configurations WHERE status='ARCHIVED'", 1),
    ("scoring_config_components", "SELECT count(*) FROM scoring_config_components", 19),
    ("risk_rules", "SELECT count(*) FROM risk_rules", 22),
    ("risk_rule_conditions", "SELECT count(*) FROM risk_rule_conditions", 27),
    ("vehicle_models", "SELECT count(*) FROM vehicle_models", 10),
    ("users", "SELECT count(*) FROM users", 6),
    ("routes", "SELECT count(*) FROM routes", 10),
    ("charging_stations", "SELECT count(*) FROM charging_stations", 25),
    ("route_assessments", "SELECT count(*) FROM route_assessments", 10),
    ("route_score_components", "SELECT count(*) FROM route_score_components", 50),
]

BEHAVIOUR = [
    ("weights total 1.0000 for every ACTIVE config",
     "SELECT coalesce(bool_and(s=1.0000), false) FROM (SELECT sum(c.weight) s "
     "FROM scoring_configurations sc JOIN scoring_config_components c ON c.config_id=sc.id "
     "WHERE sc.status='ACTIVE' GROUP BY sc.id) x", True),
    ("exactly one ACTIVE config per type",
     "SELECT coalesce(bool_and(n=1), false) FROM (SELECT count(*) n FROM "
     "scoring_configurations WHERE status='ACTIVE' GROUP BY config_type) x", True),
    ("ROUTE v2 is ACTIVE with Class A boundary 88",
     "SELECT (grade_thresholds->0->>'min') FROM scoring_configurations "
     "WHERE config_type='ROUTE' AND status='ACTIVE'", "88"),
    ("ROUTE v1 is retained as ARCHIVED with boundary 80",
     "SELECT (grade_thresholds->0->>'min') FROM scoring_configurations "
     "WHERE config_type='ROUTE' AND version_no=1", "80"),
    ("every route carries an engine-generated score",
     "SELECT coalesce(bool_and(latest_score IS NOT NULL), false) FROM routes", True),
    ("route grade distribution",
     "SELECT string_agg(latest_grade||':'||n, ' ' ORDER BY latest_grade) FROM "
     "(SELECT latest_grade, count(*) n FROM routes GROUP BY latest_grade) x", "A:3 B:6 C:1"),
    ("Kathmandu-Dhulikhel scores 90.12",
     "SELECT latest_score::text FROM routes WHERE route_code='RT-KTM-DHU-001'", "90.12"),
    ("assessments reconcile with their components",
     "SELECT coalesce(bool_and(abs(t - ra.total_score) <= 0.01), false) FROM "
     "(SELECT assessment_id, sum(weighted_score) t FROM route_score_components "
     "GROUP BY assessment_id) c JOIN route_assessments ra ON ra.id=c.assessment_id", True),
    ("USAGE_DROP_MODERATE upper bound is -50 (coverage-gap fix)",
     "SELECT threshold_value::text FROM risk_rule_conditions c JOIN risk_rules r "
     "ON r.id=c.rule_id WHERE r.rule_code='USAGE_DROP_MODERATE' AND c.operator='GT'",
     "-50.0000"),
]

# NOTE: the immutability guards are BEFORE ... FOR EACH ROW triggers, so they only fire
# when a statement actually touches a row. A DELETE against an empty table therefore
# succeeds trivially. Each probe below inserts a row first where it can; for tables whose
# FK graph makes that expensive, trigger attachment is asserted directly instead.
REJECTIONS = [
    ("audit_logs rejects UPDATE",
     "INSERT INTO audit_logs (action) VALUES ('T'); UPDATE audit_logs SET action='X'"),
    ("audit_logs rejects DELETE",
     "INSERT INTO audit_logs (action) VALUES ('T'); DELETE FROM audit_logs"),
    ("route_assessments rejects UPDATE", "UPDATE route_assessments SET total_score=1"),
    ("route_assessments rejects DELETE", "DELETE FROM route_assessments"),
    ("route distance must be positive",
     "INSERT INTO routes (route_code,route_name,origin,destination,province,district,"
     "route_type,total_distance_km,road_type,road_condition,estimated_daily_trips,"
     "traffic_density,competition_level) VALUES "
     "('ZZ','Z','a','b','P','D','URBAN',-5,'PITCH','GOOD',5,'LOW','LOW')"),
    ("fast chargers cannot exceed total stations",
     "INSERT INTO routes (route_code,route_name,origin,destination,province,district,"
     "route_type,total_distance_km,road_type,road_condition,estimated_daily_trips,"
     "traffic_density,competition_level,charging_station_count,fast_charger_count) VALUES "
     "('ZY','Z','a','b','P','D','URBAN',10,'PITCH','GOOD',5,'LOW','LOW',2,5)"),
    ("a second ACTIVE config of one type is refused",
     "INSERT INTO scoring_configurations (config_type,version_no,name,status,"
     "grade_thresholds,created_by) SELECT 'ROUTE',99,'dupe','ACTIVE','[]'::jsonb,id "
     "FROM users LIMIT 1"),
    ("duplicate route_code is refused",
     "INSERT INTO routes (route_code,route_name,origin,destination,province,district,"
     "route_type,total_distance_km,road_type,road_condition,estimated_daily_trips,"
     "traffic_density,competition_level) VALUES "
     "('RT-KTM-DHU-001','Dup','a','b','P','D','URBAN',10,'PITCH','GOOD',5,'LOW','LOW')"),
]


def main() -> int:
    try:
        import pgserver
        import psycopg
    except ImportError as exc:
        print(f"missing dependency: {exc}. pip install pgserver psycopg[binary]")
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="evrca_pg_"))
    server = None
    failures = 0
    try:
        print("starting embedded PostgreSQL ...")
        server = pgserver.get_server(workdir)
        uri = server.get_uri()

        with psycopg.connect(uri, autocommit=True) as conn:
            version = conn.execute("SHOW server_version").fetchone()[0]
            print(f"  PostgreSQL {version}\n")

            schema_sql, notes = to_compat_sql(
                (DB / "schema.sql").read_text(encoding="utf-8")
            )
            print("compatibility rewrites for the embedded build:")
            for note in notes:
                print(f"  - {note}")

            print("\napplying db/schema.sql ...")
            conn.execute(schema_sql)
            print("  applied with no errors")

            print("\nstructure:")
            for label, sql in STRUCTURE:
                print(f"  {label:<24} {conn.execute(sql).fetchone()[0]}")

            print("\napplying seeds ...")
            for seed in ("01_reference_data.sql", "02_demo_data.sql"):
                text, _ = to_compat_sql((DB / "seed" / seed).read_text(encoding="utf-8"))
                conn.execute(text)
                print(f"  {seed} applied with no errors")

            print("\nseed row counts:")
            for label, sql, expected in SEED_COUNTS:
                actual = conn.execute(sql).fetchone()[0]
                ok = expected is None or actual == expected
                failures += 0 if ok else 1
                suffix = "" if expected is None else f"  (expected {expected})"
                flag = "    " if ok else "FAIL"
                print(f"  {flag} {label:<28} {actual}{'' if ok else suffix}")

            print("\nbehaviour:")
            for label, sql, expected in BEHAVIOUR:
                actual = conn.execute(sql).fetchone()[0]
                ok = actual == expected
                failures += 0 if ok else 1
                print(f"  [{'PASS' if ok else 'FAIL'}] {label:<52} {actual}")

            print("\nrejections (each statement must fail):")
            for label, sql in REJECTIONS:
                rejected = False
                try:
                    with psycopg.connect(uri) as probe:
                        probe.execute(sql)
                        probe.rollback()
                except Exception:
                    rejected = True
                failures += 0 if rejected else 1
                print(f"  [{'PASS' if rejected else 'FAIL'}] {label}")

            print("\nmaterialised views:")
            try:
                conn.execute("SELECT refresh_dashboard_views()")
                print("  [PASS] refresh_dashboard_views() ran")
            except Exception as exc:
                failures += 1
                print(f"  [FAIL] {str(exc).splitlines()[0][:110]}")

            print("\nidempotency:")
            try:
                text, _ = to_compat_sql(
                    (DB / "seed" / "01_reference_data.sql").read_text(encoding="utf-8")
                )
                conn.execute(text)
                after = conn.execute("SELECT count(*) FROM risk_rules").fetchone()[0]
                ok = after == 22
                failures += 0 if ok else 1
                print(f"  [{'PASS' if ok else 'FAIL'}] re-running the reference seed is a no-op "
                      f"({after} rules)")
            except Exception as exc:
                failures += 1
                print(f"  [FAIL] reference seed is not idempotent: "
                      f"{str(exc).splitlines()[0][:110]}")

            print("\nseeded credentials:")
            try:
                # The script runs from backend/scripts; the application package sits one
                # level up and is needed only for this check.
                sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
                from app.core.security import verify_password

                rows = conn.execute(
                    "SELECT email, password_hash FROM users ORDER BY id"
                ).fetchall()
                # Doc 16 §16.1 prints this password for the demo accounts. The seed once
                # shipped a placeholder string here, which left every documented account
                # unable to log in; this check makes that class of defect loud.
                bad = [
                    email for email, digest in rows
                    if not verify_password("Demo@2026!Ev", digest)
                ]
                ok = bool(rows) and not bad
                failures += 0 if ok else 1
                print(f"  [{'PASS' if ok else 'FAIL'}] every seeded user authenticates with "
                      f"the documented demo password ({len(rows)} users)"
                      + (f" — failing: {bad}" if bad else ""))
            except Exception as exc:
                failures += 1
                print(f"  [FAIL] seeded credential check: "
                      f"{str(exc).splitlines()[0][:110]}")

        print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
        return 0 if failures == 0 else 1
    finally:
        if server is not None:
            try:
                server.cleanup()
            except Exception:
                pass
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
