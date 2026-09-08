"""Administration schemas — FR-9.1 to FR-9.7, Doc 03 Screens 26-30."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, EmailStr, Field

from app.api.schemas.common import ApiModel, PublicId

ConfigType = Literal["ROUTE", "CUSTOMER", "FINAL"]
Severity = Literal["YELLOW", "RED"]


# ---------------------------------------------------------------------------
# Scoring configuration — FR-9.1, FR-9.2
# ---------------------------------------------------------------------------
class ComponentDraft(ApiModel):
    component_code: Annotated[str, Field(min_length=2, max_length=40)]
    label: Annotated[str, Field(max_length=80)] | None = None
    weight: Annotated[Decimal, Field(ge=0, le=1)] = Field(
        description="A fraction, not a percentage. The active weights must total 1.0000."
    )
    display_order: Annotated[int, Field(ge=0)] | None = None
    scoring_rules: dict[str, Any] = Field(default_factory=dict)
    description: Annotated[str, Field(max_length=500)] | None = None
    is_active: bool = True


class ConfigDraftCreate(ApiModel):
    config_type: ConfigType
    name: Annotated[str, Field(max_length=120)] | None = None
    grade_thresholds: list[dict[str, Any]]
    decision_rules: dict[str, Any] | None = None
    notes: Annotated[str, Field(max_length=2000)] | None = None
    components: list[ComponentDraft]


class ConfigDraftUpdate(ApiModel):
    name: Annotated[str, Field(max_length=120)] | None = None
    grade_thresholds: list[dict[str, Any]] | None = None
    decision_rules: dict[str, Any] | None = None
    notes: Annotated[str, Field(max_length=2000)] | None = None
    components: list[ComponentDraft] | None = None


class PublishRequest(ApiModel):
    note: Annotated[str, Field(max_length=2000)] | None = Field(
        default=None, description="Change note recorded on the published version."
    )


class WeightCheckRequest(ApiModel):
    """FR-9.1 — what the editor posts on every keystroke to show a running total."""

    components: list[ComponentDraft]


class WeightCheckOut(ApiModel):
    total: float
    total_percent: float
    is_valid: bool
    components: list[dict[str, Any]] = Field(default_factory=list)


class ConfigOut(ApiModel):
    id: int
    config_type: str
    version_no: int
    name: str
    status: str
    notes: str | None = None
    published_at: datetime | None = None
    archived_at: datetime | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Risk rules — FR-9.4
# ---------------------------------------------------------------------------
class RuleToggleRequest(ApiModel):
    enabled: bool
    reason: Annotated[str, Field(max_length=500)] | None = None


class RuleUpdateRequest(ApiModel):
    """The rule *code* is deliberately absent: it is stamped on every alert ever raised.

    Extra fields are rejected rather than ignored. Silently dropping an attempt to rename a
    rule would leave the administrator believing it had worked.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True, extra="forbid")

    name: Annotated[str, Field(max_length=120)] | None = None
    description: Annotated[str, Field(max_length=2000)] | None = None
    severity: Severity | None = None
    priority: Annotated[int, Field(ge=1, le=999)] | None = None
    suppression_hours: Annotated[int, Field(ge=0, le=8760)] | None = None
    auto_resolve: bool | None = None
    sla_hours_acknowledge: Annotated[int, Field(ge=1, le=8760)] | None = None
    sla_hours_resolve: Annotated[int, Field(ge=1, le=8760)] | None = None
    recommended_action: Annotated[str, Field(max_length=2000)] | None = None


# ---------------------------------------------------------------------------
# Settings — FR-9.3, FR-9.7
# ---------------------------------------------------------------------------
class SettingOut(ApiModel):
    setting_key: str
    setting_value: str
    value_type: str
    category: str
    label: str
    description: str | None = None
    min_value: Decimal | None = None
    max_value: Decimal | None = None
    is_editable: bool
    requires_restart: bool
    updated_at: datetime


class SettingUpdate(ApiModel):
    setting_value: Annotated[str, Field(min_length=1, max_length=2000)]


# ---------------------------------------------------------------------------
# Users and roles — FR-9.5
# ---------------------------------------------------------------------------
class UserCreate(ApiModel):
    email: Annotated[EmailStr, Field(max_length=150)]
    full_name: Annotated[str, Field(min_length=2, max_length=120)]
    role_code: Annotated[str, Field(max_length=40)]
    password: Annotated[str, Field(min_length=12, max_length=128)] = Field(
        description=(
            "Initial password. The account is forced to change it on first login "
            "(FR-1.7), and it is validated against the same policy as any other."
        )
    )
    phone: Annotated[str, Field(max_length=20)] | None = None
    employee_code: Annotated[str, Field(max_length=30)] | None = None
    branch_code: Annotated[str, Field(max_length=20)] | None = None


class UserStatusRequest(ApiModel):
    active: bool
    reason: Annotated[str, Field(max_length=500)] | None = None


class AdminUserOut(ApiModel):
    id: PublicId
    email: str
    full_name: str
    role_code: str | None = None
    role_name: str | None = None
    branch_code: str | None = None
    employee_code: str | None = None
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None = None
    created_at: datetime


class RoleOut(ApiModel):
    code: str
    name: str
    description: str | None = None
    is_system: bool
    permissions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Audit — FR-9.6
# ---------------------------------------------------------------------------
class AuditEventOut(ApiModel):
    """Read-only. Never carries a password, token or raw identifier: the writer redacts
    by key name before anything is stored."""

    event_time: datetime
    action: str
    status: str
    user_email: str | None = None
    user_role: str | None = None
    entity_type: str | None = None
    entity_id: int | None = None
    changed_fields: list[str] | None = None
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
    request_id: str | None = None
    ip_address: str | None = None
    error_message: str | None = None


class JobRunHistoryOut(ApiModel):
    job_name: str
    as_of_date: date | None = None
    started_at: datetime
    finished_at: datetime | None = None
    status: str
    records_processed: int
    error_message: str | None = None
