"""Assessment, loan, schedule and repayment mapping — Doc 06 §6.6, §6.8.

Split from ``app.api.mappers`` by domain rather than by kind: the underwriting and
servicing shapes are the largest in the API and have nothing to do with route or config
responses. Same rule applies — presentation only. Every figure below was produced by a
pure engine and is reshaped here, never recomputed.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from app.api.mappers import factors, score_components
from app.api.schemas.application import (
    ApplicationAssessmentOut,
    CustomerLegOut,
    FinalLegOut,
    KnockOutOut,
    ReasonOut,
    RouteLegOut,
    StructureOut,
    VehicleEconomicsLegOut,
)
from app.api.schemas.loan import (
    AllocationLineOut,
    AllocationOut,
    LoanDetailOut,
    LoanPositionOut,
    LoanSummaryOut,
    RepaymentOut,
    RepaymentPostedOut,
    ScheduleRowOut,
    ScheduleSummaryOut,
)


def application_assessment_out(result: Any) -> ApplicationAssessmentOut:
    """`POST /applications/{id}/assess`."""
    final = result.final
    credit = result.credit_result
    score = credit.score
    extras = score.extras or {}
    economics = final.economics
    structure = final.structure
    route = result.route_assessment

    return ApplicationAssessmentOut(
        application_id=result.application.uuid,
        assessment_id=result.assessment.uuid,
        status=str(result.application.status),
        knockouts_triggered=[
            KnockOutOut(
                code=k.get("code", ""),
                message=k.get("message", ""),
                value=k.get("value"),
                threshold=k.get("threshold"),
            )
            for k in (extras.get("knockouts_triggered") or [])
        ],
        route=RouteLegOut(
            assessment_id=route.uuid,
            score=route.total_score,
            grade=str(route.grade),
            is_reused=result.route_reused,
            assessed_at=route.assessed_at,
        ),
        customer=CustomerLegOut(
            credit_score_id=result.credit_score.uuid,
            total_score=score.total_score,
            grade=score.grade,
            risk_level=score.risk_level,
            dti_ratio=Decimal(str(extras.get("dti_ratio") or 0)),
            foir_ratio=Decimal(str(extras.get("foir_ratio") or 0)),
            disposable_income=Decimal(str(extras.get("disposable_income") or 0)),
            components=score_components(score),
        ),
        vehicle_economics=VehicleEconomicsLegOut(
            score=result.assessment.vehicle_economics_score,
            energy_cost_per_km=economics.energy_cost_per_km,
            daily_net_contribution=economics.daily_net_contribution,
            monthly_net_contribution=economics.monthly_net_contribution,
            payback_months=economics.payback_months,
        ),
        final=FinalLegOut(
            final_score=final.score.total_score,
            risk_grade=final.score.grade,
            risk_level=final.score.risk_level,
            weights=final.score.extras.get("weights", {}),
            recommendation=final.outcome.decision,
            structure=StructureOut(
                recommended_amount=structure.recommended_amount,
                max_ltv_percent=structure.max_ltv_percent,
                applied_ltv_percent=structure.applied_ltv_percent,
                recommended_tenure_months=structure.recommended_tenure_months,
                recommended_interest_rate=structure.recommended_interest_rate,
                estimated_emi=structure.estimated_emi,
                dscr=structure.dscr,
                foir_post_loan=structure.foir_post_loan,
                binding_constraint=structure.binding_constraint,
                is_viable=structure.is_viable,
            ),
            reasons=[
                ReasonOut(
                    code=r.code,
                    type=r.type,
                    impact=r.impact,
                    message=r.message,
                    metric=r.metric,
                )
                for r in final.score.extras.get("_reasons", ())
            ],
            failed_gates=list(final.outcome.failed_gates),
            components=score_components(final.score),
            risk_factors=factors(final.score.risk_factors),
            positive_factors=factors(final.score.positive_factors),
        ),
        config_versions=result.config_versions,
        engine_versions=result.engine_versions,
        assessed_at=result.assessment.assessed_at,
    )


# ---------------------------------------------------------------------------
# Loans
# ---------------------------------------------------------------------------
def schedule_row_out(row: Any) -> ScheduleRowOut:
    return ScheduleRowOut(
        installment_no=row.installment_no,
        due_date=row.due_date,
        opening_balance=row.opening_balance,
        principal_due=row.principal_due,
        interest_due=row.interest_due,
        total_due=row.total_due,
        closing_balance=row.closing_balance,
        principal_paid=row.principal_paid,
        interest_paid=row.interest_paid,
        penalty_due=row.penalty_due,
        penalty_paid=row.penalty_paid,
        total_paid=row.total_paid,
        paid_date=row.paid_date,
        days_past_due=row.days_past_due,
        status=str(row.status),
    )


def schedule_summary_out(rows: Sequence[Any]) -> ScheduleSummaryOut:
    return ScheduleSummaryOut(
        installment_count=len(rows),
        first_due=rows[0].due_date if rows else None,
        last_due=rows[-1].due_date if rows else None,
        total_principal=sum((Decimal(r.principal_due) for r in rows), Decimal("0")),
        total_interest=sum((Decimal(r.interest_due) for r in rows), Decimal("0")),
        total_payable=sum((Decimal(r.total_due) for r in rows), Decimal("0")),
    )


def loan_summary_out(row: Any) -> LoanSummaryOut:
    return LoanSummaryOut(
        id=row.uuid,
        loan_account_number=row.loan_account_number,
        principal_amount=row.principal_amount,
        interest_rate=row.interest_rate,
        tenure_months=row.tenure_months,
        emi_amount=row.emi_amount,
        outstanding_principal=row.outstanding_principal,
        overdue_amount=row.overdue_amount,
        total_paid=row.total_paid,
        days_past_due=row.days_past_due,
        installments_paid=row.installments_paid,
        installments_overdue=row.installments_overdue,
        classification=str(row.classification),
        risk_status=row.risk_status,
        risk_grade=row.risk_grade,
        final_risk_score=row.final_risk_score,
        status=str(row.status),
        disbursement_date=row.disbursement_date,
        maturity_date=row.maturity_date,
    )


def loan_detail_out(
    row: Any,
    *,
    application: Any = None,
    applicant: Any = None,
    route: Any = None,
    schedule: Sequence[Any] = (),
) -> LoanDetailOut:
    return LoanDetailOut(
        **loan_summary_out(row).model_dump(),
        application_id=application.uuid if application else None,
        applicant_id=applicant.uuid if applicant else None,
        route_id=route.uuid if route else None,
        ltv_percent=row.ltv_percent,
        down_payment=row.down_payment,
        total_interest=row.total_interest,
        total_payable=row.total_payable,
        first_emi_date=row.first_emi_date,
        assigned_officer_id=row.assigned_officer_id,
        branch_code=row.branch_code,
        schedule_summary=schedule_summary_out(schedule) if schedule else None,
        created_at=row.created_at,
    )


def repayment_out(row: Any) -> RepaymentOut:
    return RepaymentOut(
        id=row.uuid,
        receipt_number=row.receipt_number,
        payment_date=row.payment_date,
        amount_paid=row.amount_paid,
        principal_component=row.principal_component,
        interest_component=row.interest_component,
        penalty_component=row.penalty_component,
        payment_mode=row.payment_mode,
        reference_number=row.reference_number,
        days_late=row.days_late,
        is_advance=row.is_advance,
        created_at=row.created_at,
    )


def repayment_posted_out(posted: Any) -> RepaymentPostedOut:
    allocation = posted.allocation
    position = posted.position
    return RepaymentPostedOut(
        repayment=repayment_out(posted.repayment),
        allocation=AllocationOut(
            penalty=allocation.penalty_component,
            interest=allocation.interest_component,
            principal=allocation.principal_component,
            allocated=allocation.allocated,
            unallocated=allocation.unallocated,
            settled_installments=list(allocation.settled_installments),
            lines=[
                AllocationLineOut(
                    installment_no=line.installment_no,
                    penalty=line.penalty,
                    interest=line.interest,
                    principal=line.principal,
                    total=line.total,
                )
                for line in allocation.lines
            ],
        ),
        loan=LoanPositionOut(
            days_past_due=position.days_past_due,
            overdue_amount=position.overdue_amount,
            outstanding_principal=position.outstanding_principal,
            outstanding_interest=position.outstanding_interest,
            total_paid=position.total_paid,
            installments_paid=position.installments_paid,
            installments_overdue=position.installments_overdue,
            classification=position.classification,
            risk_status=position.risk_status,
        ),
        affected_installments=[schedule_row_out(r) for r in posted.affected],
    )
