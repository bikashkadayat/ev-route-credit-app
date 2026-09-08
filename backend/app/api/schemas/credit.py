"""Credit-assessment and alert schemas — Doc 06 §6.6, §6.10, §6.11."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import Field

from app.api.schemas.common import ApiModel, PublicId
from app.api.schemas.route import FactorOut, ScoreComponentOut


# ---------------------------------------------------------------------------
# Credit assessment
# ---------------------------------------------------------------------------
class CreditAssessRequest(ApiModel):
    application_id: int | None = Field(
        default=None,
        description="Defaults to the applicant's most recent application.",
    )
    as_of: date | None = Field(
        default=None, description="Business date. Defaults to today (UTC)."
    )


class KnockOutOut(ApiModel):
    code: str
    message: str
    value: Any = None
    threshold: Any = None


class VehicleEconomicsOut(ApiModel):
    energy_cost_per_km: Decimal
    daily_energy_cost: Decimal
    daily_maintenance: Decimal
    daily_driver_cost: Decimal
    daily_fixed_accrual: Decimal
    daily_gross_revenue: Decimal
    daily_net_contribution: Decimal
    seasonal_factor: Decimal
    monthly_net_contribution: Decimal
    payback_months: Decimal | None = None


class CreditAssessmentOut(ApiModel):
    id: PublicId
    application_id: int
    application_number: str | None = None
    total_score: Decimal
    grade: str
    risk_level: str
    dti_ratio: Decimal
    foir_ratio: Decimal
    disposable_income: Decimal
    knockouts_triggered: list[KnockOutOut] = Field(default_factory=list)
    components: list[ScoreComponentOut] = Field(default_factory=list)
    risk_factors: list[FactorOut] = Field(default_factory=list)
    positive_factors: list[FactorOut] = Field(default_factory=list)
    explanation: str
    vehicle_economics: VehicleEconomicsOut | None = None
    engine_version: str
    config_version: int | None = None
    is_latest: bool
    scored_at: datetime


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------
AlertStatus = Literal[
    "OPEN", "ACKNOWLEDGED", "IN_PROGRESS", "RESOLVED", "FALSE_POSITIVE", "ESCALATED",
    "SUPERSEDED",
]
Severity = Literal["YELLOW", "RED"]


class AlertSummary(ApiModel):
    id: PublicId
    alert_number: str
    severity: str
    status: str
    alert_type: str
    trigger_condition: str
    trigger_date: date
    occurrence_count: int
    loan_id: int
    assigned_to: int | None = None
    created_at: datetime


class AlertActivityOut(ApiModel):
    activity_type: str
    description: str
    performed_by: int
    created_at: datetime


class AlertDetail(AlertSummary):
    applicant_id: int
    vehicle_id: int
    rule_id: int
    trigger_metrics: dict[str, Any] = Field(default_factory=dict)
    current_metric_value: Decimal | None = None
    acknowledge_due_at: datetime | None = None
    resolve_due_at: datetime | None = None
    acknowledged_at: datetime | None = None
    resolution_code: str | None = None
    resolution_notes: str | None = None
    resolution_date: datetime | None = None
    activities: list[AlertActivityOut] = Field(default_factory=list)


class AlertEvaluateRequest(ApiModel):
    loan_id: int | None = Field(
        default=None, description="Evaluate one loan. Omit for the whole active book."
    )
    as_of: date | None = Field(
        default=None,
        description="Business date. The run is idempotent for a given date.",
    )


class LoanEvaluationOut(ApiModel):
    loan_id: int
    risk_status: str
    created: list[str] = Field(default_factory=list)
    refreshed: list[str] = Field(default_factory=list)
    superseded: list[str] = Field(default_factory=list)
    auto_resolved: list[str] = Field(default_factory=list)


class AlertEvaluationOut(ApiModel):
    as_of: date
    engine_version: str
    loans_evaluated: int
    alerts_created: int
    alerts_refreshed: int
    alerts_superseded: int
    alerts_auto_resolved: int
    results: list[LoanEvaluationOut] = Field(default_factory=list)


class AlertResolveRequest(ApiModel):
    resolution_code: Annotated[str, Field(max_length=40)]
    resolution_notes: Annotated[str, Field(min_length=10, max_length=2000)] = Field(
        description="At least 10 characters; 20 for FALSE_POSITIVE (Doc 08 §8.7)."
    )


class AlertAssignRequest(ApiModel):
    """FR-7.8 — put a named officer on the alert."""

    assignee_id: int = Field(description="The user who will work it")
    note: Annotated[str, Field(max_length=500)] | None = None


class AlertActivityRequest(ApiModel):
    """FR-7.7 — the investigation trail. Doc 12 step 17 adds a CALL_LOG."""

    activity_type: Annotated[str, Field(max_length=30)] = Field(
        description="CALL_LOG, FIELD_VISIT, NOTE, STATUS_CHANGE, ESCALATION"
    )
    description: Annotated[str, Field(min_length=3, max_length=2000)]
    metadata: dict[str, Any] | None = None


class AlertEscalateRequest(ApiModel):
    reason: Annotated[str, Field(min_length=10, max_length=2000)] = Field(
        description="Why it is being handed up; at least 10 characters."
    )
    assignee_id: int | None = None


class AlertStartWorkRequest(ApiModel):
    note: Annotated[str, Field(max_length=500)] | None = None


class SlaStateOut(ApiModel):
    """BR-5 — the countdown the queue shows."""

    acknowledge_seconds_remaining: int | None = None
    resolve_seconds_remaining: int | None = None
    acknowledge_breached: bool
    resolve_breached: bool
