"""Configuration and portfolio schemas — Doc 06 §6.11, §6.9.

Read-only. Nothing in this API surface can publish, edit or archive a configuration:
those are admin workflows with their own change control (Doc 03 Screen 27).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import Field

from app.api.schemas.common import ApiModel, PublicId


class GradeBandOut(ApiModel):
    grade: str
    min: Decimal
    max: Decimal
    label: str | None = None
    risk_level: str | None = None
    recommendation: str | None = None


class ConfigComponentOut(ApiModel):
    component_code: str
    label: str
    weight: Decimal
    display_order: int
    is_active: bool
    description: str | None = None


class ScoringConfigSummary(ApiModel):
    config_type: str
    version_no: int
    name: str
    status: str
    published_at: datetime | None = None
    archived_at: datetime | None = None


class ScoringConfigDetail(ScoringConfigSummary):
    grade_thresholds: list[GradeBandOut] = Field(default_factory=list)
    components: list[ConfigComponentOut] = Field(default_factory=list)
    weight_total: Decimal = Field(
        description="Sum of active component weights. Always exactly 1.0000 when ACTIVE."
    )
    decision_rules: dict[str, Any] | None = None
    notes: str | None = None


class ActiveConfigOut(ApiModel):
    """`GET /config` — what the engines are currently running."""

    active_versions: dict[str, int] = Field(
        description="config_type -> version_no of the ACTIVE configuration"
    )
    engine_versions: dict[str, str]
    configurations: list[ScoringConfigSummary] = Field(default_factory=list)


class RuleConditionOut(ApiModel):
    metric_code: str
    operator: str
    threshold_value: Decimal | None = None
    threshold_value_2: Decimal | None = None
    window_days: int | None = None
    aggregation: str | None = None
    sequence_no: int


class RiskRuleOut(ApiModel):
    rule_code: str
    name: str
    description: str | None = None
    category: str
    severity: str
    condition_logic: str
    is_active: bool
    priority: int
    suppression_hours: int
    auto_resolve: bool
    sla_hours_acknowledge: int
    sla_hours_resolve: int
    recommended_action: str | None = None
    conditions: list[RuleConditionOut] = Field(default_factory=list)
    plain_language: str | None = Field(
        default=None,
        description="The rule rendered as a sentence, as shown in the admin builder",
    )


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------
class PortfolioKpis(ApiModel):
    active_loans: int
    total_disbursed: Decimal
    total_outstanding: Decimal
    total_overdue: Decimal
    npl_count: int
    par30: Decimal
    red_alerts_loans: int
    yellow_alerts_loans: int
    green_loans: int


class GradeDistributionOut(ApiModel):
    grade: str | None = None
    count: int
    outstanding: Decimal


class DpdBucketOut(ApiModel):
    bucket: str
    count: int
    outstanding: Decimal


class PortfolioSummaryOut(ApiModel):
    kpis: PortfolioKpis
    by_risk_grade: list[GradeDistributionOut] = Field(default_factory=list)
    dpd_buckets: list[DpdBucketOut] = Field(default_factory=list)
    open_alerts: dict[str, int] = Field(default_factory=dict)


class LoanSummaryOut(ApiModel):
    id: PublicId
    loan_account_number: str
    applicant_id: int
    vehicle_id: int
    route_id: int
    principal_amount: Decimal
    outstanding_principal: Decimal
    overdue_amount: Decimal
    emi_amount: Decimal
    days_past_due: int
    classification: str
    risk_status: str
    risk_grade: str
    final_risk_score: Decimal
    status: str
    assigned_officer_id: int | None = None
