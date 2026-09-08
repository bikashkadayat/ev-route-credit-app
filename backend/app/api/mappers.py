"""ORM / engine result -> response schema mapping.

Presentation only: reshaping and masking, never a calculation. Keeping it here rather than
inline in the routers means a router body stays "authorise, call service, return", and the
same mapping is reused by every endpoint that returns the shape.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.api.schemas.application import (
    ApplicationDetail,
    ApplicationSummary,
    AssessmentSummary,
    DecisionOut,
)
from app.api.schemas.auth import RoleOut, UserOut
from app.api.schemas.config import (
    ConfigComponentOut,
    RiskRuleOut,
    RuleConditionOut,
    ScoringConfigDetail,
    ScoringConfigSummary,
)
from app.api.schemas.credit import CreditAssessmentOut, VehicleEconomicsOut
from app.api.schemas.customer import CustomerDetail, FinancialProfileOut, ObligationOut
from app.api.schemas.route import (
    FactorOut,
    RouteAssessmentOut,
    ScoreComponentOut,
    SubFactorOut,
)
from app.engines.base import ScoreResult
from app.services.customer_assessment import CustomerService


# ---------------------------------------------------------------------------
# Route assessment
# ---------------------------------------------------------------------------
def score_components(score: ScoreResult) -> list[ScoreComponentOut]:
    return [
        ScoreComponentOut(
            code=c.code,
            label=c.label,
            normalized_score=c.normalized_score,
            weight=c.weight,
            weighted_score=c.weighted_score,
            raw_inputs=c.raw_inputs,
            explanation=c.explanation,
            sub_factors=[
                SubFactorOut(
                    code=s.code,
                    value=str(s.value) if s.value is not None else None,
                    score=s.score,
                    weight=s.weight,
                    contribution=s.contribution,
                )
                for s in c.sub_factors
            ],
        )
        for c in score.components
    ]


def factors(items: Sequence[Any]) -> list[FactorOut]:
    return [
        FactorOut(
            code=f.code, type=f.type, severity=f.severity, message=f.message
        )
        for f in items
    ]


def _stored_factors(rows: Any) -> list[FactorOut]:
    """Factors read back from JSONB carry the same keys the engine emitted."""
    return [
        FactorOut(
            code=r.get("code", ""),
            type=r.get("type", "RISK"),
            severity=r.get("severity"),
            message=r.get("message", ""),
        )
        for r in (rows or [])
    ]


def route_assessment_from_result(result: Any) -> RouteAssessmentOut:
    """Fresh assessment: use the engine result, which carries the sub-factor detail."""
    assessment, score, route = result.assessment, result.score, result.route
    return RouteAssessmentOut(
        id=assessment.uuid,
        route_id=route.uuid,
        route_name=route.route_name,
        total_score=score.total_score,
        grade=score.grade,
        risk_level=score.risk_level,
        charging_adequacy=str(score.extras.get("charging_adequacy", "")),
        revenue_potential=str(score.extras.get("revenue_potential", "")),
        recommendation=str(score.extras.get("recommendation", "")),
        components=score_components(score),
        risk_factors=factors(score.risk_factors),
        positive_factors=factors(score.positive_factors),
        explanation=score.explanation,
        estimated_monthly_revenue=assessment.estimated_monthly_revenue,
        estimated_monthly_profit=assessment.estimated_monthly_profit,
        engine_version=score.engine_version,
        config_version=score.config_version_no,
        assessed_at=assessment.assessed_at,
    )


def route_assessment_from_row(assessment: Any) -> RouteAssessmentOut:
    """Historical assessment read from the database."""
    return RouteAssessmentOut(
        id=assessment.uuid,
        route_id=None,
        total_score=assessment.total_score,
        grade=assessment.grade,
        risk_level=assessment.risk_level,
        charging_adequacy=assessment.charging_adequacy,
        revenue_potential=assessment.revenue_potential,
        recommendation=assessment.recommendation,
        components=[
            ScoreComponentOut(
                code=c.component_code,
                label=c.label,
                normalized_score=c.normalized_score,
                weight=c.weight,
                weighted_score=c.weighted_score,
                raw_inputs=c.raw_inputs or {},
                explanation=c.explanation,
            )
            for c in sorted(assessment.components, key=lambda c: c.display_order)
        ],
        risk_factors=_stored_factors(assessment.risk_factors),
        positive_factors=_stored_factors(assessment.positive_factors),
        explanation=assessment.explanation,
        estimated_monthly_revenue=assessment.estimated_monthly_revenue,
        estimated_monthly_profit=assessment.estimated_monthly_profit,
        engine_version=assessment.engine_version,
        config_version=None,
        assessed_at=assessment.assessed_at,
    )


# ---------------------------------------------------------------------------
# Customer
# ---------------------------------------------------------------------------
def customer_detail(applicant: Any, financial: Any | None) -> CustomerDetail:
    return CustomerDetail(
        id=applicant.uuid,
        applicant_code=applicant.applicant_code,
        applicant_type=str(applicant.applicant_type),
        full_name=applicant.full_name,
        phone=applicant.phone,
        province=applicant.province,
        district=applicant.district,
        status=str(applicant.status),
        created_at=applicant.created_at,
        updated_at=applicant.updated_at,
        id_type=str(applicant.id_type),
        id_number_masked=CustomerService.mask_identifier(applicant.id_number_enc),
        date_of_birth=applicant.date_of_birth,
        gender=str(applicant.gender) if applicant.gender else None,
        email=applicant.email,
        alt_phone=applicant.alt_phone,
        municipality=applicant.municipality,
        ward_no=applicant.ward_no,
        address_line=applicant.address_line,
        total_experience_years=applicant.total_experience_years,
        driving_experience_years=applicant.driving_experience_years,
        commercial_driving_years=applicant.commercial_driving_years,
        business_experience_years=applicant.business_experience_years,
        licence_category=applicant.licence_category,
        licence_expiry=applicant.licence_expiry,
        has_previous_ev_experience=applicant.has_previous_ev_experience,
        company_reg_number=applicant.company_reg_number,
        fleet_size=applicant.fleet_size,
        current_financial_profile=(
            FinancialProfileOut.model_validate(financial) if financial else None
        ),
        obligations=[
            ObligationOut.model_validate(o) for o in (applicant.obligations or [])
        ],
    )


# ---------------------------------------------------------------------------
# Credit assessment
# ---------------------------------------------------------------------------
def credit_assessment_from_result(result: Any) -> CreditAssessmentOut:
    row, score = result.credit_score, result.score
    economics = result.economics
    return CreditAssessmentOut(
        id=row.uuid,
        application_id=row.application_id,
        application_number=result.application.application_number,
        total_score=row.total_score,
        grade=row.grade,
        risk_level=row.risk_level,
        dti_ratio=row.dti_ratio,
        foir_ratio=row.foir_ratio,
        disposable_income=row.disposable_income,
        knockouts_triggered=row.knockouts_triggered or [],
        components=score_components(score),
        risk_factors=factors(score.risk_factors),
        positive_factors=factors(score.positive_factors),
        explanation=row.explanation,
        vehicle_economics=(
            VehicleEconomicsOut(**economics.to_dict()) if economics else None
        ),
        engine_version=row.engine_version,
        config_version=score.config_version_no,
        is_latest=row.is_latest,
        scored_at=row.scored_at,
    )


def credit_assessment_from_row(row: Any) -> CreditAssessmentOut:
    return CreditAssessmentOut(
        id=row.uuid,
        application_id=row.application_id,
        total_score=row.total_score,
        grade=row.grade,
        risk_level=row.risk_level,
        dti_ratio=row.dti_ratio,
        foir_ratio=row.foir_ratio,
        disposable_income=row.disposable_income,
        knockouts_triggered=row.knockouts_triggered or [],
        components=[
            ScoreComponentOut(
                code=c.component_code,
                label=c.label,
                normalized_score=c.normalized_score,
                weight=c.weight,
                weighted_score=c.weighted_score,
                raw_inputs=c.raw_inputs or {},
                explanation=c.explanation,
            )
            for c in sorted(row.components, key=lambda c: c.display_order)
        ],
        risk_factors=_stored_factors(row.risk_factors),
        positive_factors=_stored_factors(row.positive_factors),
        explanation=row.explanation,
        engine_version=row.engine_version,
        is_latest=row.is_latest,
        scored_at=row.scored_at,
    )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def config_summary(row: Any) -> ScoringConfigSummary:
    return ScoringConfigSummary(
        config_type=str(row.config_type),
        version_no=row.version_no,
        name=row.name,
        status=str(row.status),
        published_at=row.published_at,
        archived_at=row.archived_at,
    )


def config_detail(row: Any) -> ScoringConfigDetail:
    from decimal import Decimal

    components = sorted(row.components, key=lambda c: c.display_order)
    return ScoringConfigDetail(
        config_type=str(row.config_type),
        version_no=row.version_no,
        name=row.name,
        status=str(row.status),
        published_at=row.published_at,
        archived_at=row.archived_at,
        grade_thresholds=row.grade_thresholds or [],
        components=[ConfigComponentOut.model_validate(c) for c in components],
        weight_total=sum(
            (Decimal(c.weight) for c in components if c.is_active), Decimal("0")
        ),
        decision_rules=row.decision_rules,
        notes=row.notes,
    )


def risk_rule_out(rule: Any, plain_language: str | None = None) -> RiskRuleOut:
    return RiskRuleOut(
        rule_code=rule.rule_code,
        name=rule.name,
        description=rule.description,
        category=str(rule.category),
        severity=str(rule.severity),
        condition_logic=rule.condition_logic,
        is_active=rule.is_active,
        priority=rule.priority,
        suppression_hours=rule.suppression_hours,
        auto_resolve=rule.auto_resolve,
        sla_hours_acknowledge=rule.sla_hours_acknowledge,
        sla_hours_resolve=rule.sla_hours_resolve,
        recommended_action=rule.recommended_action,
        conditions=[
            RuleConditionOut(
                metric_code=c.metric_code,
                operator=str(c.operator),
                threshold_value=c.threshold_value,
                threshold_value_2=c.threshold_value_2,
                window_days=c.window_days,
                aggregation=c.aggregation,
                sequence_no=c.sequence_no,
            )
            for c in sorted(rule.conditions, key=lambda c: c.sequence_no)
        ],
        plain_language=plain_language,
    )


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
def auth_user_out(user: Any, permissions: Sequence[str]) -> UserOut:
    """The caller's own identity. Carries no password material of any kind."""
    return UserOut(
        id=user.uuid,
        email=user.email,
        full_name=user.full_name,
        role=RoleOut(
            code=user.role.code if user.role else "",
            name=user.role.name if user.role else "",
        ),
        permissions=list(permissions),
        must_change_password=bool(user.must_change_password),
        branch_code=user.branch_code,
        last_login_at=user.last_login_at,
    )


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
def application_summary(row: Any) -> ApplicationSummary:
    return ApplicationSummary(
        id=row.uuid,
        application_number=row.application_number,
        status=str(row.status),
        requested_amount=row.requested_amount,
        requested_tenure_months=row.requested_tenure_months,
        proposed_interest_rate=row.proposed_interest_rate,
        branch_code=row.branch_code,
        submitted_at=row.submitted_at,
        decided_at=row.decided_at,
        created_at=row.created_at,
    )


def assessment_summary(row: Any) -> AssessmentSummary | None:
    if row is None:
        return None
    return AssessmentSummary(
        id=row.uuid,
        final_score=row.final_score,
        risk_grade=row.risk_grade,
        risk_level=row.risk_level,
        recommendation=str(row.recommendation),
        recommended_amount=row.recommended_amount,
        estimated_emi=row.estimated_emi,
        is_latest=row.is_latest,
        assessed_at=row.assessed_at,
    )


def decision_out(row: Any) -> DecisionOut:
    return DecisionOut(
        id=row.uuid,
        system_recommendation=str(row.system_recommendation),
        final_decision=str(row.final_decision),
        is_override=bool(row.is_override),
        override_justification=row.override_justification,
        approved_amount=row.approved_amount,
        approved_tenure_months=row.approved_tenure_months,
        approved_interest_rate=row.approved_interest_rate,
        conditions=list(row.conditions or []),
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        second_approver_id=row.second_approver_id,
    )


def application_detail(
    row: Any,
    *,
    applicant: Any = None,
    route: Any = None,
    vehicle: Any = None,
    financial_version: int | None = None,
    latest_assessment: Any = None,
    decisions: Sequence[Any] = (),
) -> ApplicationDetail:
    """Presentation only. The nested objects are passed in by the service; this never
    fetches anything itself."""
    return ApplicationDetail(
        **application_summary(row).model_dump(),
        applicant_id=applicant.uuid if applicant else None,
        applicant_name=applicant.full_name if applicant else None,
        vehicle_id=vehicle.uuid if vehicle else None,
        route_id=route.uuid if route else None,
        route_name=route.route_name if route else None,
        financial_profile_version=financial_version,
        down_payment_amount=row.down_payment_amount,
        vehicle_on_road_price=row.vehicle_on_road_price,
        expected_daily_km=row.expected_daily_km,
        expected_operating_days_month=row.expected_operating_days_month,
        purpose=row.purpose,
        latest_assessment=assessment_summary(latest_assessment),
        decisions=[decision_out(d) for d in decisions],
        updated_at=row.updated_at,
    )
