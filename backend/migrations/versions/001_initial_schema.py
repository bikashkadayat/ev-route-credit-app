"""Initial schema — applies db/schema.sql verbatim.

Revision ID: 0001
Revises:
Create Date: 2026-09-08

WHY THE RAW FILE RATHER THAN AUTOGENERATE
-----------------------------------------
``db/schema.sql`` is the documented authority for the schema (Doc 05), and it contains
things SQLAlchemy's autogenerate cannot express: declarative partitioning, the append-only
guard triggers, the deferred weight-validation constraint trigger, generated columns,
partial unique indexes and the materialised views. Re-expressing those in Python would
create a second source of truth that silently drifts.

So migration 001 executes the file, and
``tests/integration/test_model_schema_parity.py`` proves the ORM matches the result.
Subsequent migrations are ordinary Alembic operations.
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA_SQL = Path(__file__).resolve().parents[3].parent / "db" / "schema.sql"

DROP_ALL = """
DROP MATERIALIZED VIEW IF EXISTS mv_geographic_exposure CASCADE;
DROP MATERIALIZED VIEW IF EXISTS mv_repayment_performance CASCADE;
DROP MATERIALIZED VIEW IF EXISTS mv_route_risk_distribution CASCADE;
DROP MATERIALIZED VIEW IF EXISTS mv_application_funnel CASCADE;
DROP MATERIALIZED VIEW IF EXISTS mv_portfolio_summary CASCADE;

DROP TABLE IF EXISTS idempotency_keys, job_runs, system_settings, notifications,
    audit_logs, alert_activities, risk_alerts, risk_rule_conditions, risk_rules,
    loan_monitoring_snapshots, maintenance_events, charging_sessions, battery_metrics,
    vehicle_telemetry, repayments, repayment_schedules, loans, applicant_documents,
    underwriting_decisions, loan_assessments, credit_score_components, credit_scores,
    loan_applications, route_score_components, route_assessments,
    scoring_config_components, scoring_configurations, charging_stations, routes,
    vehicles, vehicle_models, credit_bureau_reports, existing_obligations,
    password_history, password_reset_tokens,
    applicant_financials, applicants, user_sessions, users, role_permissions,
    permissions, roles CASCADE;

DROP FUNCTION IF EXISTS refresh_dashboard_views() CASCADE;
DROP FUNCTION IF EXISTS create_monthly_partitions(date, integer) CASCADE;
DROP FUNCTION IF EXISTS trg_validate_config_weights() CASCADE;
DROP FUNCTION IF EXISTS trg_bump_version() CASCADE;
DROP FUNCTION IF EXISTS trg_block_update_delete() CASCADE;
DROP FUNCTION IF EXISTS trg_set_updated_at() CASCADE;

DROP TYPE IF EXISTS alert_status_enum, operator_enum, severity_enum, rule_category_enum,
    installment_status_enum, loan_classification_enum, loan_status_enum, decision_enum,
    application_status_enum, config_status_enum, config_type_enum, route_status_enum,
    risk_level_enum, competition_enum, traffic_enum, gradient_enum, road_condition_enum,
    road_type_enum, route_type_enum, vehicle_status_enum, vehicle_category_enum,
    document_type_enum, id_type_enum, gender_enum, applicant_status_enum,
    applicant_type_enum CASCADE;
"""


def upgrade() -> None:
    if not SCHEMA_SQL.exists():  # pragma: no cover - packaging guard
        raise FileNotFoundError(f"schema file not found: {SCHEMA_SQL}")
    op.execute(SCHEMA_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    """Full teardown. Used by CI (`downgrade base` then `upgrade head`) and by local
    resets; never relied upon in production, where recovery is forward-fixing only
    (Doc 02 §2.14)."""
    op.execute(DROP_ALL)
