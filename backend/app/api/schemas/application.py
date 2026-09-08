"""Application and loan schemas — Doc 06 §6.6, §6.8.

Request models carry only what a client may set. Everything derived — the application
number, the on-road price snapshot, the financial profile version, every score — is
produced by a service and appears on the response only.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.api.schemas.common import ApiModel, PublicId
from app.api.schemas.route import FactorOut, ScoreComponentOut

ApplicationStatus = Literal[
    "DRAFT", "SUBMITTED", "UNDER_ASSESSMENT", "PENDING_DECISION", "APPROVED",
    "REJECTED", "WITHDRAWN", "DISBURSED", "EXPIRED",
]
DecisionValue = Literal["APPROVE", "MANUAL_REVIEW", "REJECT"]


class ApplicationCreate(ApiModel):
    """Doc 06 §6.6. Identifiers are the public UUIDs, never row ids."""

    applicant_id: UUID
    vehicle_id: UUID
    route_id: UUID
    requested_amount: Annotated[Decimal, Field(gt=0, le=100_000_000)]
    requested_tenure_months: Annotated[int, Field(ge=6, le=120)]
    proposed_interest_rate: Annotated[Decimal, Field(gt=0, lt=40)]
    down_payment_amount: Annotated[Decimal, Field(ge=0)]
    expected_daily_km: Annotated[Decimal, Field(gt=0, le=800)]
    expected_operating_days_month: Annotated[int, Field(ge=1, le=31)] = 26
    purpose: Annotated[str, Field(max_length=200)] | None = None
    branch_code: Annotated[str, Field(max_length=20)] | None = None


class ApplicationUpdate(ApiModel):
    """PATCH — a partial edit, and only while the application is a DRAFT."""

    requested_amount: Annotated[Decimal, Field(gt=0, le=100_000_000)] | None = None
    requested_tenure_months: Annotated[int, Field(ge=6, le=120)] | None = None
    proposed_interest_rate: Annotated[Decimal, Field(gt=0, lt=40)] | None = None
    down_payment_amount: Annotated[Decimal, Field(ge=0)] | None = None
    expected_daily_km: Annotated[Decimal, Field(gt=0, le=800)] | None = None
    expected_operating_days_month: Annotated[int, Field(ge=1, le=31)] | None = None
    purpose: Annotated[str, Field(max_length=200)] | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> ApplicationUpdate:
        if not self.model_dump(exclude_unset=True):
            raise ValueError("Supply at least one field to change")
        return self


class ApplicationSummary(ApiModel):
    id: PublicId
    application_number: str
    status: str
    requested_amount: Decimal
    requested_tenure_months: int
    proposed_interest_rate: Decimal
    branch_code: str | None = None
    submitted_at: datetime | None = None
    decided_at: datetime | None = None
    created_at: datetime


class ApplicationDetail(ApplicationSummary):
    applicant_id: UUID | None = None
    applicant_name: str | None = None
    vehicle_id: UUID | None = None
    route_id: UUID | None = None
    route_name: str | None = None
    financial_profile_version: int | None = None
    down_payment_amount: Decimal
    vehicle_on_road_price: Decimal
    expected_daily_km: Decimal
    expected_operating_days_month: int
    purpose: str | None = None
    latest_assessment: AssessmentSummary | None = None
    decisions: list[DecisionOut] = Field(default_factory=list)
    updated_at: datetime


class AssessRequest(ApiModel):
    force_route_reassessment: bool = Field(
        default=False,
        description=(
            "Re-score the corridor even if a recent assessment exists. A route is normally "
            "scored once and reused by every application that runs on it."
        ),
    )


class ReasonOut(ApiModel):
    code: str
    type: str
    impact: str
    message: str
    metric: dict[str, Any] | None = None


class KnockOutOut(ApiModel):
    code: str
    message: str
    value: Any = None
    threshold: Any = None


class RouteLegOut(ApiModel):
    assessment_id: UUID
    score: Decimal
    grade: str
    is_reused: bool = Field(
        description="True when an existing, non-stale assessment was used"
    )
    assessed_at: datetime


class CustomerLegOut(ApiModel):
    credit_score_id: UUID
    total_score: Decimal
    grade: str
    risk_level: str
    dti_ratio: Decimal
    foir_ratio: Decimal
    disposable_income: Decimal
    components: list[ScoreComponentOut] = Field(default_factory=list)


class VehicleEconomicsLegOut(ApiModel):
    score: Decimal
    energy_cost_per_km: Decimal
    daily_net_contribution: Decimal
    monthly_net_contribution: Decimal
    payback_months: Decimal | None = None


class StructureOut(ApiModel):
    recommended_amount: Decimal
    max_ltv_percent: Decimal
    applied_ltv_percent: Decimal
    recommended_tenure_months: int
    recommended_interest_rate: Decimal
    estimated_emi: Decimal
    dscr: Decimal | None = None
    foir_post_loan: Decimal | None = None
    binding_constraint: str | None = None
    is_viable: bool


class FinalLegOut(ApiModel):
    final_score: Decimal
    risk_grade: str
    risk_level: str
    weights: dict[str, float] = Field(default_factory=dict)
    recommendation: DecisionValue
    structure: StructureOut
    reasons: list[ReasonOut] = Field(default_factory=list)
    failed_gates: list[str] = Field(default_factory=list)
    components: list[ScoreComponentOut] = Field(default_factory=list)
    risk_factors: list[FactorOut] = Field(default_factory=list)
    positive_factors: list[FactorOut] = Field(default_factory=list)


class AssessmentSummary(ApiModel):
    id: PublicId
    final_score: Decimal
    risk_grade: str
    risk_level: str
    recommendation: str
    recommended_amount: Decimal
    estimated_emi: Decimal
    is_latest: bool
    assessed_at: datetime


class ApplicationAssessmentOut(ApiModel):
    """`POST /applications/{id}/assess` — Doc 06 §6.6."""

    application_id: UUID
    assessment_id: UUID
    status: str
    knockouts_triggered: list[KnockOutOut] = Field(default_factory=list)
    route: RouteLegOut
    customer: CustomerLegOut
    vehicle_economics: VehicleEconomicsLegOut
    final: FinalLegOut
    config_versions: dict[str, int] = Field(default_factory=dict)
    engine_versions: dict[str, str] = Field(default_factory=dict)
    assessed_at: datetime


class DecisionRequest(ApiModel):
    final_decision: DecisionValue
    approved_amount: Annotated[Decimal, Field(gt=0)] | None = None
    approved_tenure_months: Annotated[int, Field(ge=6, le=120)] | None = None
    approved_interest_rate: Annotated[Decimal, Field(gt=0, lt=40)] | None = None
    conditions: list[Annotated[str, Field(max_length=300)]] = Field(default_factory=list)
    override_justification: Annotated[str, Field(max_length=2000)] | None = Field(
        default=None,
        description=(
            "Required, and at least 20 characters, when the decision differs from the "
            "system recommendation."
        ),
    )


class DecisionOut(ApiModel):
    id: PublicId
    system_recommendation: str
    final_decision: str
    is_override: bool
    override_justification: str | None = None
    approved_amount: Decimal | None = None
    approved_tenure_months: int | None = None
    approved_interest_rate: Decimal | None = None
    conditions: list[str] = Field(default_factory=list)
    decided_by: int
    decided_at: datetime
    second_approver_id: int | None = None


class WithdrawRequest(ApiModel):
    reason: Annotated[str, Field(max_length=300)] | None = None


ApplicationDetail.model_rebuild()
