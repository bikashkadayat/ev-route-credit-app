"""Audit, notifications, settings and job bookkeeping — Doc 05 §5.3.36-5.3.39."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Index, Numeric, Text
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    BigInteger,
    Boolean,
    CreatedAtMixin,
    Date,
    DateTime,
    Integer,
    SmallInteger,
    String,
    TimestampMixin,
    func,
)


class AuditLog(Base):
    """Append-only. Doc 09 §9.8 — UPDATE and DELETE are blocked by a database trigger,
    and the application role is not granted DELETE."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    request_id: Mapped[str | None] = mapped_column(String(40))
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    actor_type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="USER")
    user_email: Mapped[str | None] = mapped_column(String(150))
    user_role: Mapped[str | None] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(50))
    entity_id: Mapped[int | None] = mapped_column(BigInteger)
    entity_uuid: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True))
    before_state: Mapped[dict | None] = mapped_column(JSONB)
    after_state: Mapped[dict | None] = mapped_column(JSONB)
    changed_fields: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(15), nullable=False, server_default="SUCCESS")
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("idx_audit_time", "event_time"),
        Index("idx_audit_user", "user_id", "event_time"),
        Index("idx_audit_entity", "entity_type", "entity_id", "event_time"),
        Index("idx_audit_action", "action", "event_time"),
    )


class Notification(Base, CreatedAtMixin):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    channel: Mapped[str] = mapped_column(
        String(15), nullable=False, server_default="IN_APP")
    notification_type: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    link_url: Mapped[str | None] = mapped_column(String(300))
    entity_type: Mapped[str | None] = mapped_column(String(50))
    entity_id: Mapped[int | None] = mapped_column(BigInteger)
    priority: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="NORMAL")
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_status: Mapped[str] = mapped_column(
        String(15), nullable=False, server_default="PENDING")
    delivery_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("idx_notif_user", "user_id", "is_read", "created_at"),)


class SystemSetting(Base, TimestampMixin):
    """Doc 05 §5.3.38 — operational policy the admin UI edits."""

    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    setting_key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    setting_value: Mapped[str] = mapped_column(Text, nullable=False)
    value_type: Mapped[str] = mapped_column(String(15), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    min_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    max_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    allowed_values: Mapped[dict | None] = mapped_column(JSONB)
    is_editable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    requires_restart: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false")
    updated_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))

    __table_args__ = (Index("idx_settings_category", "category"),)


class JobRun(Base):
    """Doc 02 §2.10 — every batch job records a run so a silent failure is alertable."""

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String(60), nullable=False)
    as_of_date: Mapped[date | None] = mapped_column(Date)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(15), nullable=False, server_default="RUNNING")
    records_processed: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0")
    records_failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_message: Mapped[str | None] = mapped_column(Text)
    job_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB)

    __table_args__ = (Index("idx_jobs_name", "job_name", "started_at"),)


class IdempotencyKey(Base):
    """Doc 06 §6.1.1 — replay protection on money-moving and job-creating endpoints."""

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(120), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_body: Mapped[dict | None] = mapped_column(JSONB)
    status_code: Mapped[int | None] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("idx_idem_expiry", "expires_at"),)
