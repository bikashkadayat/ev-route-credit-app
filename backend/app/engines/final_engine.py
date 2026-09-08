"""Combined Financing Risk Engine — Doc 07 §7.5. `final-engine@1.0.0`

Final Risk Score = route x w_route + customer x w_customer + vehicle economics x w_vehicle

Orchestrates, in order: vehicle economics -> combined score -> structuring ->
decision matrix -> reasons. Pure: it consumes already-computed route and customer
results rather than reaching for them.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.core.types import ZERO, D, q2, safe_div
from app.engines.base import (
    ComponentResult,
    Factor,
    ScoreResult,
    ScoringConfig,
    ScoringEngine,
)
from app.engines.customer_engine import CustomerScoringInput, derive_customer_inputs
from app.engines.decision_matrix import DecisionOutcome, build_reasons, decide
from app.engines.emi import calculate_emi
from app.engines.knockouts import KnockOut
from app.engines.normalizer import normalize
from app.engines.structuring import LoanStructure, StructuringInput, structure_loan
from app.engines.vehicle_economics import VehicleEconomics

VEHICLE_ECONOMICS_COMPONENT = "VEHICLE_ECONOMICS"


@dataclass(frozen=True, slots=True)
class FinalScoringInput:
    route_score: Decimal
    route_grade: str
    customer_score: Decimal
    customer_grade: str
    vehicle_economics_score: Decimal
    economics: VehicleEconomics
    customer_inputs: CustomerScoringInput
    is_thin_file: bool = False
    knockouts: tuple[KnockOut, ...] = ()
    battery_warranty_months: int = 0


@dataclass(frozen=True, slots=True)
class FinalAssessment:
    score: ScoreResult
    structure: LoanStructure
    outcome: DecisionOutcome
    economics: VehicleEconomics
    requested_emi: Decimal
    requested_dscr: Decimal | None
    requested_foir: Decimal | None
    requested_ltv: Decimal | None

    @property
    def recommendation(self) -> str:
        return self.outcome.decision

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.score.to_dict(),
            "vehicle_economics": self.economics.to_dict(),
            "requested": {
                "emi": float(self.requested_emi),
                "dscr": float(self.requested_dscr) if self.requested_dscr is not None else None,
                "foir_post_loan": (
                    float(self.requested_foir) if self.requested_foir is not None else None
                ),
                "ltv_percent": (
                    float(self.requested_ltv) if self.requested_ltv is not None else None
                ),
            },
            "recommended": self.structure.to_dict(),
            "recommendation": self.outcome.decision,
            "reasons": [r.to_dict() for r in self.score.extras.get("_reasons", ())],
            "failed_gates": list(self.outcome.failed_gates),
        }


class FinalRiskEngine(ScoringEngine):
    engine_version = "final-engine@1.0.0"
    config_type = "FINAL"

    def score(self, payload: FinalScoringInput, config: ScoringConfig) -> ScoreResult:
        """The weighted combination only. ``assess`` adds structuring and the decision."""
        inputs = {
            "route_score": payload.route_score,
            "customer_score": payload.customer_score,
            "vehicle_economics_score": payload.vehicle_economics_score,
        }
        components: list[ComponentResult] = []
        for cfg in config.active_components:
            normalized = normalize(cfg.scoring_rules, inputs)
            components.append(
                self.make_component(
                    cfg, normalized,
                    raw_inputs={k: float(D(v)) for k, v in inputs.items()},
                    explanation=(
                        f"{cfg.label} contributes {q2(normalized)} x {cfg.weight} = "
                        f"{q2(normalized * cfg.weight)} to the final score."
                    ),
                )
            )
        total = self.aggregate(components)
        band = config.band_for(total)
        return ScoreResult(
            total_score=total,
            grade=band.grade,
            grade_label=band.label,
            risk_level=band.risk_level,
            components=tuple(components),
            risk_factors=(),
            positive_factors=(),
            explanation=(
                f"Final risk score {total}/100 ({band.label}): route "
                f"{payload.route_score} x {_w(config, 'ROUTE_SCORE')}, customer "
                f"{payload.customer_score} x {_w(config, 'CUSTOMER_SCORE')}, vehicle economics "
                f"{payload.vehicle_economics_score} x {_w(config, 'VEHICLE_ECONOMICS_SCORE')}."
            ),
            config_version_id=config.id,
            config_version_no=config.version_no,
            engine_version=self.engine_version,
            extras={
                "weights": {
                    "route": float(_w(config, "ROUTE_SCORE")),
                    "customer": float(_w(config, "CUSTOMER_SCORE")),
                    "vehicle": float(_w(config, "VEHICLE_ECONOMICS_SCORE")),
                },
                "contributions": {c.code: float(c.weighted_score) for c in components},
            },
        )

    def assess(
        self,
        payload: FinalScoringInput,
        config: ScoringConfig,
        route_factors: tuple[Factor, ...] = (),
        customer_factors: tuple[Factor, ...] = (),
    ) -> FinalAssessment:
        """Full pipeline: combined score -> structuring -> decision -> reasons."""
        ci = payload.customer_inputs
        derived = derive_customer_inputs(ci)

        # Metrics on the *requested* structure, for the requested-vs-recommended table
        requested_emi = (
            calculate_emi(ci.requested_amount, _requested_rate(ci), ci.requested_tenure_months)
            if ci.requested_amount > ZERO
            else ZERO
        )
        requested_dscr = (
            payload.economics.dscr(requested_emi) if requested_emi > ZERO else None
        )
        requested_foir = safe_div(ci.total_existing_emi + requested_emi, ci.total_monthly_income)
        requested_ltv = safe_div(
            ci.requested_amount * Decimal("100"), ci.vehicle_on_road_price
        )

        # Knock-outs short-circuit everything downstream.
        if payload.knockouts:
            outcome = decide(
                final_score=ZERO, customer_grade=payload.customer_grade,
                route_grade=payload.route_grade, dscr=requested_dscr,
                foir_post_loan=requested_foir, is_thin_file=payload.is_thin_file,
                knockouts=payload.knockouts, config=config,
            )
            band = config.grade_bands[-1]
            score = ScoreResult(
                total_score=ZERO, grade=band.grade, grade_label=band.label,
                risk_level=band.risk_level, components=(), risk_factors=(), positive_factors=(),
                explanation="Declined at eligibility screening; no scoring performed.",
                config_version_id=config.id, config_version_no=config.version_no,
                engine_version=self.engine_version,
                extras={"_reasons": outcome.reasons, "knockouts_triggered":
                        [k.to_dict() for k in payload.knockouts]},
            )
            empty = LoanStructure(
                recommended_amount=ZERO, max_ltv_percent=ZERO, applied_ltv_percent=ZERO,
                recommended_tenure_months=0, recommended_interest_rate=ZERO, estimated_emi=ZERO,
                dscr=None, foir_post_loan=None, total_interest=ZERO,
                binding_constraint="KNOCKOUT", amount_by_ltv=ZERO,
                amount_by_emi_capacity=ZERO, emi_capacity=ZERO, is_viable=False,
            )
            return FinalAssessment(
                score, empty, outcome, payload.economics,
                requested_emi, requested_dscr, requested_foir, requested_ltv,
            )

        combined = self.score(payload, config)

        structure = structure_loan(
            StructuringInput(
                grade=combined.grade,
                requested_amount=ci.requested_amount,
                requested_tenure_months=ci.requested_tenure_months,
                vehicle_on_road_price=ci.vehicle_on_road_price,
                total_monthly_income=ci.total_monthly_income,
                total_existing_emi=ci.total_existing_emi,
                monthly_net_contribution=payload.economics.monthly_net_contribution,
                battery_warranty_months=payload.battery_warranty_months,
            ),
            config,
        )

        outcome = decide(
            final_score=combined.total_score,
            customer_grade=payload.customer_grade,
            route_grade=payload.route_grade,
            dscr=requested_dscr,
            foir_post_loan=requested_foir,
            is_thin_file=payload.is_thin_file,
            structure_viable=structure.is_viable,
            config=config,
        )

        reasons = build_reasons(
            outcome=outcome,
            final_score=combined.total_score,
            route_score=payload.route_score,
            route_grade=payload.route_grade,
            customer_grade=payload.customer_grade,
            dscr=requested_dscr,
            foir_post_loan=requested_foir,
            disposable_to_emi=derived.get("disposable_to_emi_ratio"),
            bureau_score=ci.bureau_score,
            down_payment_ratio=derived.get("down_payment_ratio"),
            route_factors=route_factors,
            customer_factors=customer_factors,
            config=config,
        )

        enriched = ScoreResult(
            total_score=combined.total_score, grade=combined.grade,
            grade_label=combined.grade_label, risk_level=combined.risk_level,
            components=combined.components, risk_factors=combined.risk_factors,
            positive_factors=combined.positive_factors, explanation=combined.explanation,
            config_version_id=combined.config_version_id,
            config_version_no=combined.config_version_no,
            engine_version=combined.engine_version,
            extras={**combined.extras, "_reasons": reasons,
                    "knockouts_triggered": []},
        )
        return FinalAssessment(
            enriched, structure, outcome, payload.economics,
            requested_emi, requested_dscr, requested_foir, requested_ltv,
        )


def _w(config: ScoringConfig, code: str) -> Decimal:
    try:
        return config.component(code).weight
    except Exception:  # pragma: no cover - configuration guarantees the component exists
        return ZERO


def _requested_rate(ci: CustomerScoringInput) -> Decimal:
    return D(ci.extras.get("proposed_interest_rate", "13.0"))
