"""Loans, repayments, telemetry, rules and alerts — Doc 05 §5.3.24-5.3.35."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Index, Numeric, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    BigInteger,
    Boolean,
    CreatedAtMixin,
    Date,
    DateTime,
    OptimisticLockMixin,
    SmallInteger,
    String,
    TimestampMixin,
    enum_column,
    external_uuid,
    money,
    ratio,
    score,
    small_money,
)


class Loan(Base, TimestampMixin):
    __tablename__ = "loans"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[UUID] = external_uuid()
    loan_account_number: Mapped[str] = mapped_column(String(25), nullable=False, unique=True)
    application_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("loan_applications.id", ondelete="RESTRICT"),
        nullable=False, unique=True)
    applicant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("applicants.id", ondelete="RESTRICT"), nullable=False)
    vehicle_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("vehicles.id", ondelete="RESTRICT"), nullable=False)
    route_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("routes.id", ondelete="RESTRICT"), nullable=False)
    principal_amount: Mapped[Decimal] = money(nullable=False)
    interest_rate: Mapped[Decimal] = score(nullable=False)
    tenure_months: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    emi_amount: Mapped[Decimal] = small_money(nullable=False)
    down_payment: Mapped[Decimal] = money(nullable=False)
    ltv_percent: Mapped[Decimal] = score(nullable=False)
    disbursement_date: Mapped[date] = mapped_column(Date, nullable=False)
    first_emi_date: Mapped[date] = mapped_column(Date, nullable=False)
    maturity_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_interest: Mapped[Decimal] = money(nullable=False)
    total_payable: Mapped[Decimal] = money(nullable=False)
    outstanding_principal: Mapped[Decimal] = money(nullable=False)
    total_paid: Mapped[Decimal] = money(nullable=False, server_default="0")
    overdue_amount: Mapped[Decimal] = money(nullable=False, server_default="0")
    days_past_due: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    installments_paid: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    installments_overdue: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    classification: Mapped[str] = enum_column(
        "loan_classification_enum", "PERFORMING", "WATCHLIST", "SUBSTANDARD", "DOUBTFUL",
        "LOSS", nullable=False, server_default="PERFORMING")
    risk_status: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="GREEN")
    final_risk_score: Mapped[Decimal] = score(nullable=False)
    risk_grade: Mapped[str] = mapped_column(String(2), nullable=False)
    monitoring_status: Mapped[str] = mapped_column(
        String(25), nullable=False, server_default="ACTIVE")
    status: Mapped[str] = enum_column(
        "loan_status_enum", "ACTIVE", "CLOSED", "FORECLOSED", "WRITTEN_OFF", "RESTRUCTURED",
        nullable=False, server_default="ACTIVE")
    assigned_officer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"))
    branch_code: Mapped[str | None] = mapped_column(String(20))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False)

    schedule: Mapped[list[RepaymentSchedule]] = relationship(
        back_populates="loan", cascade="all, delete-orphan",
        order_by="RepaymentSchedule.installment_no")
    repayments: Mapped[list[Repayment]] = relationship(back_populates="loan")
    alerts: Mapped[list[RiskAlert]] = relationship(back_populates="loan")

    __table_args__ = (
        UniqueConstraint("uuid", name="uq_loan_uuid"),
        Index("idx_loans_applicant", "applicant_id"),
        Index("idx_loans_status_risk", "status", "risk_status"),
        Index("idx_loans_class", "classification"),
        Index("idx_loans_officer", "assigned_officer_id", "status"),
        Index("idx_loans_route", "route_id"),
        Index("idx_loans_disb_date", "disbursement_date"),
    )


class RepaymentSchedule(Base, TimestampMixin):
    __tablename__ = "repayment_schedules"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    loan_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("loans.id", ondelete="CASCADE"), nullable=False)
    installment_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    opening_balance: Mapped[Decimal] = money(nullable=False)
    principal_due: Mapped[Decimal] = small_money(nullable=False)
    interest_due: Mapped[Decimal] = small_money(nullable=False)
    total_due: Mapped[Decimal] = small_money(nullable=False)
    closing_balance: Mapped[Decimal] = money(nullable=False)
    principal_paid: Mapped[Decimal] = small_money(nullable=False, server_default="0")
    interest_paid: Mapped[Decimal] = small_money(nullable=False, server_default="0")
    penalty_due: Mapped[Decimal] = small_money(nullable=False, server_default="0")
    penalty_paid: Mapped[Decimal] = small_money(nullable=False, server_default="0")
    total_paid: Mapped[Decimal] = small_money(nullable=False, server_default="0")
    paid_date: Mapped[date | None] = mapped_column(Date)
    days_past_due: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    status: Mapped[str] = enum_column(
        "installment_status_enum", "PENDING", "PAID", "PARTIAL", "OVERDUE", "WAIVED",
        nullable=False, server_default="PENDING")

    loan: Mapped[Loan] = relationship(back_populates="schedule")

    __table_args__ = (
        UniqueConstraint("loan_id", "installment_no", name="uq_sched"),
        Index("idx_sched_loan_due", "loan_id", "due_date"),
        Index("idx_sched_due_status", "due_date", "status"),
    )


class Repayment(Base, CreatedAtMixin):
    """Immutable — Doc 05 §5.3.26."""

    __tablename__ = "repayments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[UUID] = external_uuid()
    loan_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("loans.id", ondelete="RESTRICT"), nullable=False)
    schedule_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("repayment_schedules.id", ondelete="RESTRICT"))
    receipt_number: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_paid: Mapped[Decimal] = small_money(nullable=False)
    principal_component: Mapped[Decimal] = small_money(nullable=False, server_default="0")
    interest_component: Mapped[Decimal] = small_money(nullable=False, server_default="0")
    penalty_component: Mapped[Decimal] = small_money(nullable=False, server_default="0")
    payment_mode: Mapped[str] = mapped_column(String(25), nullable=False)
    reference_number: Mapped[str | None] = mapped_column(String(60))
    days_late: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    is_advance: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    idempotency_key: Mapped[str | None] = mapped_column(String(80))
    remarks: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False)

    loan: Mapped[Loan] = relationship(back_populates="repayments")

    __table_args__ = (
        UniqueConstraint("uuid", name="uq_repay_uuid"),
        Index("idx_repay_loan", "loan_id", "payment_date"),
        Index("idx_repay_sched", "schedule_id"),
        Index("idx_repay_date", "payment_date"),
    )


class VehicleTelemetry(Base, CreatedAtMixin):
    """Partitioned monthly by telemetry_date — Doc 05 §5.3.27."""

    __tablename__ = "vehicle_telemetry"

    vehicle_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("vehicles.id", ondelete="CASCADE"), primary_key=True)
    telemetry_date: Mapped[date] = mapped_column(Date, primary_key=True)
    loan_id: Mapped[int | None] = mapped_column(BigInteger)
    daily_km: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), nullable=False, server_default="0")
    trip_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    avg_speed_kmph: Mapped[Decimal | None] = score()
    max_speed_kmph: Mapped[Decimal | None] = score()
    active_hours: Mapped[Decimal] = score(nullable=False, server_default="0")
    idle_minutes: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    route_deviation_percent: Mapped[Decimal] = score(nullable=False, server_default="0")
    harsh_braking_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    night_driving_hours: Mapped[Decimal] = score(nullable=False, server_default="0")
    geofence_exits: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    estimated_revenue: Mapped[Decimal | None] = small_money()
    data_source: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="MOCK")
    is_estimated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false")

    __table_args__ = (
        Index("idx_tel_loan_date", "loan_id", "telemetry_date"),
        {"postgresql_partition_by": "RANGE (telemetry_date)"},
    )


class BatteryMetric(Base, CreatedAtMixin):
    __tablename__ = "battery_metrics"

    vehicle_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("vehicles.id", ondelete="CASCADE"), primary_key=True)
    metric_date: Mapped[date] = mapped_column(Date, primary_key=True)
    loan_id: Mapped[int | None] = mapped_column(BigInteger)
    avg_state_of_charge: Mapped[Decimal | None] = score()
    min_state_of_charge: Mapped[Decimal | None] = score()
    state_of_health: Mapped[Decimal | None] = score()
    estimated_range_km: Mapped[int | None] = mapped_column(SmallInteger)
    energy_consumed_kwh: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), nullable=False, server_default="0")
    efficiency_km_per_kwh: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    charge_cycles: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), nullable=False, server_default="0")
    battery_temp_avg_c: Mapped[Decimal | None] = score()
    fault_codes: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("idx_bm_loan_date", "loan_id", "metric_date"),
        Index("idx_bm_soh", "state_of_health"),
        {"postgresql_partition_by": "RANGE (metric_date)"},
    )


class ChargingSession(Base, CreatedAtMixin):
    __tablename__ = "charging_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_date: Mapped[date] = mapped_column(Date, primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    loan_id: Mapped[int | None] = mapped_column(BigInteger)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_minutes: Mapped[int | None] = mapped_column(SmallInteger)
    energy_delivered_kwh: Mapped[Decimal] = mapped_column(
        Numeric(8, 2), nullable=False, server_default="0")
    soc_start: Mapped[Decimal | None] = score()
    soc_end: Mapped[Decimal | None] = score()
    charger_type: Mapped[str | None] = mapped_column(String(20))
    charging_station_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("charging_stations.id", ondelete="SET NULL"))
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    is_home_charging: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false")

    __table_args__ = (
        Index("idx_chs_vehicle", "vehicle_id", "session_date"),
        Index("idx_chs_loan", "loan_id", "session_date"),
        {"postgresql_partition_by": "RANGE (session_date)"},
    )


class LoanMonitoringSnapshot(Base, CreatedAtMixin):
    """Derived nightly — Doc 05 §5.3.31. Feeds the metric registry."""

    __tablename__ = "loan_monitoring_snapshots"

    loan_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("loans.id", ondelete="CASCADE"), primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    avg_daily_km_7d: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    avg_daily_km_30d: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    avg_daily_km_90d: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    baseline_daily_km: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    usage_change_percent: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    active_days_30d: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    zero_km_streak_days: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    avg_trips_7d: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    avg_trips_30d: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    charging_sessions_30d: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    charging_change_percent: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    estimated_revenue_30d: Mapped[Decimal | None] = small_money()
    revenue_change_percent: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    avg_route_deviation_percent: Mapped[Decimal | None] = score()
    latest_state_of_health: Mapped[Decimal | None] = score()
    days_since_last_telemetry: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    days_past_due: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    behaviour_score: Mapped[Decimal | None] = score()

    __table_args__ = (
        Index("idx_snap_date", "snapshot_date"),
        Index("idx_snap_usage", "usage_change_percent"),
    )


class RiskRule(Base, TimestampMixin):
    __tablename__ = "risk_rules"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rule_code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = enum_column(
        "rule_category_enum", "REPAYMENT", "USAGE", "REVENUE", "BATTERY", "ROUTE",
        "DATA_QUALITY", nullable=False)
    severity: Mapped[str] = enum_column(
        "severity_enum", "YELLOW", "RED", nullable=False)
    condition_logic: Mapped[str] = mapped_column(
        String(5), nullable=False, server_default="ALL")
    evaluation_frequency: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="DAILY")
    suppression_hours: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="24")
    auto_resolve: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true")
    assign_to_role_id: Mapped[int | None] = mapped_column(
        SmallInteger, ForeignKey("roles.id"))
    sla_hours_acknowledge: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="48")
    sla_hours_resolve: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="240")
    recommended_action: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    priority: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="100")
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    updated_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))

    conditions: Mapped[list[RiskRuleCondition]] = relationship(
        back_populates="rule", cascade="all, delete-orphan",
        order_by="RiskRuleCondition.sequence_no")

    __table_args__ = (
        Index("idx_rules_active", "is_active", "priority"),
        Index("idx_rules_category", "category", "severity"),
    )


class RiskRuleCondition(Base, CreatedAtMixin):
    __tablename__ = "risk_rule_conditions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rule_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("risk_rules.id", ondelete="CASCADE"), nullable=False)
    metric_code: Mapped[str] = mapped_column(String(50), nullable=False)
    operator: Mapped[str] = enum_column(
        "operator_enum", "GT", "GTE", "LT", "LTE", "EQ", "NEQ", "BETWEEN", "IN",
        nullable=False)
    threshold_value: Mapped[Decimal | None] = ratio()
    threshold_value_2: Mapped[Decimal | None] = ratio()
    threshold_values: Mapped[dict | None] = mapped_column(JSONB)
    window_days: Mapped[int | None] = mapped_column(SmallInteger)
    aggregation: Mapped[str | None] = mapped_column(String(20))
    sequence_no: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="1")

    rule: Mapped[RiskRule] = relationship(back_populates="conditions")

    __table_args__ = (
        Index("idx_cond_rule", "rule_id", "sequence_no"),
        Index("idx_cond_metric", "metric_code"),
    )


class RiskAlert(Base, TimestampMixin, OptimisticLockMixin):
    __tablename__ = "risk_alerts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[UUID] = external_uuid()
    alert_number: Mapped[str] = mapped_column(String(25), nullable=False, unique=True)
    loan_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("loans.id", ondelete="CASCADE"), nullable=False)
    applicant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("applicants.id", ondelete="RESTRICT"), nullable=False)
    vehicle_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("vehicles.id", ondelete="RESTRICT"), nullable=False)
    rule_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("risk_rules.id", ondelete="RESTRICT"), nullable=False)
    alert_type: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = enum_column("severity_enum", "YELLOW", "RED", nullable=False)
    trigger_condition: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_metrics: Mapped[dict] = mapped_column(JSONB, nullable=False)
    trigger_date: Mapped[date] = mapped_column(Date, nullable=False)
    current_metric_value: Mapped[Decimal | None] = ratio()
    occurrence_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="1")
    status: Mapped[str] = enum_column(
        "alert_status_enum", "OPEN", "ACKNOWLEDGED", "IN_PROGRESS", "RESOLVED",
        "FALSE_POSITIVE", "ESCALATED", "SUPERSEDED", nullable=False, server_default="OPEN")
    assigned_to: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    acknowledge_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolve_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_code: Mapped[str | None] = mapped_column(String(40))
    resolution_notes: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    resolution_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by_alert_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("risk_alerts.id", ondelete="SET NULL"))
    last_evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()")

    loan: Mapped[Loan] = relationship(back_populates="alerts")
    activities: Mapped[list[AlertActivity]] = relationship(
        back_populates="alert", cascade="all, delete-orphan",
        order_by="AlertActivity.created_at")

    __table_args__ = (
        UniqueConstraint("uuid", name="uq_alert_uuid"),
        Index("idx_alerts_queue", "status", "severity", "trigger_date"),
        Index("idx_alerts_assignee", "assigned_to", "status"),
        Index("idx_alerts_loan", "loan_id", "trigger_date"),
    )


class AlertActivity(Base, CreatedAtMixin):
    __tablename__ = "alert_activities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    alert_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("risk_alerts.id", ondelete="CASCADE"), nullable=False)
    activity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    activity_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB)
    performed_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False)

    alert: Mapped[RiskAlert] = relationship(back_populates="activities")

    __table_args__ = (Index("idx_activity_alert", "alert_id", "created_at"),)
