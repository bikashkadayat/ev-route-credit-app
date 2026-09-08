"""ScoringService — the stateless calculators — Doc 06 §6.7.

These power the "what-if" controls: an officer drags the amount or tenure slider and sees
DSCR, EMI, FOIR and LTV move before committing to anything. Nothing here writes a row.

That is the whole point, and it is also the risk. A what-if that used different arithmetic
from the persisted assessment would show an officer one number and store another, so these
call **the same engines with the same active configuration** — the only difference is that
the result is returned instead of saved.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import IncompleteDataError
from app.engines.base import ScoreResult
from app.engines.customer_engine import CustomerScoringEngine, CustomerScoringInput
from app.engines.emi import calculate_emi, total_interest
from app.engines.final_engine import FinalAssessment, FinalRiskEngine, FinalScoringInput
from app.engines.route_engine import (
    DEFAULT_REFERENCE_BATTERY_KWH,
    DEFAULT_REFERENCE_RANGE_KM,
    IncompleteRouteData,
    RouteScoringEngine,
    RouteScoringInput,
)
from app.engines.vehicle_economics import (
    VehicleEconomics,
    VehicleEconomicsInput,
    calculate_vehicle_economics,
)
from app.services.config import ConfigService


@dataclass(frozen=True, slots=True)
class EmiBreakdown:
    emi: Decimal
    total_interest: Decimal
    total_payable: Decimal


class ScoringService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.config_service = ConfigService(session)
        self.route_engine = RouteScoringEngine()
        self.customer_engine = CustomerScoringEngine()
        self.final_engine = FinalRiskEngine()

    # -- route -------------------------------------------------------------
    def score_route(self, payload: dict[str, Any]) -> ScoreResult:
        """The same engine and configuration `/routes/{id}/assess` uses, unpersisted."""
        config = self.config_service.active_config("ROUTE")
        try:
            return self.route_engine.score(self._route_input(payload), config)
        except IncompleteRouteData as exc:
            raise IncompleteDataError(
                "The route data is incomplete for scoring", exc.fields
            ) from exc

    @staticmethod
    def _route_input(payload: dict[str, Any]) -> RouteScoringInput:
        def dec(key: str, default: str = "0") -> Decimal:
            value = payload.get(key)
            return Decimal(str(value)) if value is not None else Decimal(default)

        return RouteScoringInput(
            route_name=str(payload.get("route_name") or "what-if"),
            total_distance_km=dec("total_distance_km"),
            road_type=str(payload["road_type"]),
            road_condition=str(payload["road_condition"]),
            gradient_profile=str(payload.get("gradient_profile") or "FLAT"),
            pitch_road_percent=dec("pitch_road_percent", "100"),
            charging_station_count=int(payload.get("charging_station_count") or 0),
            fast_charger_count=int(payload.get("fast_charger_count") or 0),
            avg_charging_distance_km=dec("avg_charging_distance_km"),
            max_charging_gap_km=(
                Decimal(str(payload["max_charging_gap_km"]))
                if payload.get("max_charging_gap_km") is not None
                else None
            ),
            passenger_volume_daily=int(payload.get("passenger_volume_daily") or 0),
            freight_volume_daily_tons=dec("freight_volume_daily_tons"),
            estimated_daily_trips=dec("estimated_daily_trips"),
            avg_fare_per_trip=dec("avg_fare_per_trip"),
            avg_freight_revenue_per_trip=dec("avg_freight_revenue_per_trip"),
            traffic_density=str(payload["traffic_density"]),
            competition_level=str(payload["competition_level"]),
            seasonal_risk=str(payload.get("seasonal_risk") or "LOW"),
            monsoon_disruption_days=int(payload.get("monsoon_disruption_days") or 0),
            flood_landslide_risk=str(payload.get("flood_landslide_risk") or "LOW"),
            security_risk=str(payload.get("security_risk") or "LOW"),
            electricity_tariff_per_kwh=dec("electricity_tariff_per_kwh", "12"),
            estimated_daily_operating_cost=dec("estimated_daily_operating_cost"),
            reference_range_km=(
                Decimal(str(payload["reference_range_km"]))
                if payload.get("reference_range_km") is not None
                else DEFAULT_REFERENCE_RANGE_KM
            ),
            reference_battery_kwh=(
                Decimal(str(payload["reference_battery_kwh"]))
                if payload.get("reference_battery_kwh") is not None
                else DEFAULT_REFERENCE_BATTERY_KWH
            ),
        )

    # -- customer ----------------------------------------------------------
    def score_customer(self, payload: dict[str, Any]) -> ScoreResult:
        config = self.config_service.active_config("CUSTOMER")
        return self.customer_engine.score(self._customer_input(payload), config)

    def _customer_input(self, payload: dict[str, Any]) -> CustomerScoringInput:
        def dec(key: str, default: str = "0") -> Decimal:
            value = payload.get(key)
            return Decimal(str(value)) if value is not None else Decimal(default)

        economics = (
            self.vehicle_economics(payload["economics"])
            if payload.get("economics")
            else None
        )
        proposed_emi = (
            Decimal(str(payload["proposed_emi"]))
            if payload.get("proposed_emi") is not None
            else calculate_emi(
                dec("requested_amount"),
                dec("proposed_interest_rate", "13"),
                int(payload.get("requested_tenure_months") or 60),
            )
        )
        return CustomerScoringInput(
            applicant_type=str(payload.get("applicant_type") or "OWNER_DRIVER"),
            age_years=_opt_decimal(payload.get("age_years")),
            age_at_maturity=_opt_decimal(payload.get("age_at_maturity")),
            bureau_score=_opt_decimal(payload.get("bureau_score")),
            credit_history_months=int(payload.get("credit_history_months") or 0),
            previous_default_count=int(payload.get("previous_default_count") or 0),
            max_dpd_last_24m=int(payload.get("max_dpd_last_24m") or 0),
            enquiries_last_6m=int(payload.get("enquiries_last_6m") or 0),
            current_overdue_amount=dec("current_overdue_amount"),
            is_blacklisted=bool(payload.get("is_blacklisted", False)),
            total_monthly_income=dec("total_monthly_income"),
            total_monthly_expenses=dec("total_monthly_expenses"),
            total_existing_emi=dec("total_existing_emi"),
            existing_loan_count=int(payload.get("existing_loan_count") or 0),
            overdue_obligation_count=int(payload.get("overdue_obligation_count") or 0),
            avg_bank_balance_6m=dec("avg_bank_balance_6m"),
            income_proof_type=str(payload.get("income_proof_type") or "SELF_DECLARED"),
            income_verified=bool(payload.get("income_verified", False)),
            requested_amount=dec("requested_amount"),
            requested_tenure_months=int(payload.get("requested_tenure_months") or 60),
            down_payment=dec("down_payment"),
            vehicle_on_road_price=dec("vehicle_on_road_price"),
            proposed_emi=proposed_emi,
            driving_experience_years=dec("driving_experience_years"),
            commercial_driving_years=dec("commercial_driving_years"),
            business_experience_years=dec("business_experience_years"),
            fleet_size=int(payload.get("fleet_size") or 0),
            has_previous_ev_experience=bool(
                payload.get("has_previous_ev_experience", False)
            ),
            licence_category=payload.get("licence_category"),
            licence_months_remaining=_opt_decimal(payload.get("licence_months_remaining")),
            economics=economics,
            route_grade=payload.get("route_grade"),
            route_score=_opt_decimal(payload.get("route_score")),
            extras={
                "proposed_interest_rate": str(payload.get("proposed_interest_rate") or 13)
            },
        )

    # -- vehicle economics -------------------------------------------------
    @staticmethod
    def vehicle_economics(payload: dict[str, Any]) -> VehicleEconomics:
        def dec(key: str, default: str = "0") -> Decimal:
            value = payload.get(key)
            return Decimal(str(value)) if value is not None else Decimal(default)

        return calculate_vehicle_economics(
            VehicleEconomicsInput(
                battery_capacity_kwh=dec("battery_capacity_kwh"),
                real_world_range_km=dec("real_world_range_km", "1"),
                on_road_price=dec("on_road_price"),
                maintenance_cost_per_km=dec("maintenance_cost_per_km", "0.55"),
                battery_warranty_years=int(payload.get("battery_warranty_years") or 0),
                daily_km=dec("daily_km"),
                operating_days_per_month=int(payload.get("operating_days_month") or 26),
                daily_trips=dec("daily_trips"),
                avg_revenue_per_trip=dec("avg_revenue_per_trip"),
                electricity_tariff_per_kwh=dec("electricity_tariff_per_kwh", "12"),
                gradient_profile=str(payload.get("gradient_profile") or "FLAT"),
                monsoon_disruption_days=int(payload.get("monsoon_disruption_days") or 0),
                driver_monthly_wage=dec("driver_monthly_wage"),
                annual_insurance=dec("annual_insurance"),
                annual_permit_tax=dec("annual_permit_tax"),
                down_payment=dec("down_payment"),
                tenure_months=int(payload.get("tenure_months") or 60),
            )
        )

    # -- final -------------------------------------------------------------
    def score_final(self, payload: dict[str, Any]) -> FinalAssessment:
        """Doc 06 §6.7 — the what-if behind the amount and tenure sliders."""
        config = self.config_service.active_config("FINAL")

        vehicle = dict(payload.get("vehicle") or {})
        operation = dict(payload.get("operation") or {})
        loan = dict(payload.get("loan") or {})
        borrower = dict(payload.get("borrower") or {})
        grades = dict(payload.get("grades") or {})

        economics = self.vehicle_economics({
            **vehicle, **operation,
            "down_payment": loan.get("down_payment", 0),
            "tenure_months": loan.get("tenure_months", 60),
        })

        customer_inputs = self._customer_input({
            **borrower,
            "requested_amount": loan.get("requested_amount", 0),
            "requested_tenure_months": loan.get("tenure_months", 60),
            "proposed_interest_rate": loan.get("interest_rate", 13),
            "down_payment": loan.get("down_payment", 0),
            "vehicle_on_road_price": vehicle.get("on_road_price", 0),
            "route_grade": grades.get("route_grade"),
            "route_score": payload.get("route_score"),
        })

        return self.final_engine.assess(
            FinalScoringInput(
                route_score=Decimal(str(payload["route_score"])),
                route_grade=str(grades.get("route_grade") or "B"),
                customer_score=Decimal(str(payload["customer_score"])),
                customer_grade=str(grades.get("customer_grade") or "B"),
                vehicle_economics_score=self._economics_score(
                    payload, customer_inputs
                ),
                economics=economics,
                customer_inputs=customer_inputs,
                battery_warranty_months=int(vehicle.get("battery_warranty_years") or 0) * 12,
            ),
            config,
        )

    def _economics_score(
        self, payload: dict[str, Any], customer_inputs: CustomerScoringInput
    ) -> Decimal:
        """The vehicle-economics component, taken from the customer scorecard.

        Doc 06 §6.7 does not ask the caller for this figure, and it must not be
        re-derived with a second formula: it is the VEHICLE_ECONOMICS component the
        customer engine already computes, so the what-if and the persisted assessment
        cannot disagree.
        """
        supplied = payload.get("vehicle_economics_score")
        if supplied is not None:
            return Decimal(str(supplied))

        customer_config = self.config_service.active_config("CUSTOMER")
        scored = self.customer_engine.score(customer_inputs, customer_config)
        component = next(
            (c for c in scored.components if c.code == "VEHICLE_ECONOMICS"), None
        )
        return Decimal(component.normalized_score) if component else Decimal("0")

    # -- EMI ---------------------------------------------------------------
    @staticmethod
    def emi(
        principal: Decimal, annual_rate: Decimal, tenure_months: int
    ) -> EmiBreakdown:
        """Doc 06 §6.7 utility. The same function the schedule generator uses."""
        payment = calculate_emi(principal, annual_rate, tenure_months)
        interest = total_interest(principal, payment, tenure_months)
        return EmiBreakdown(
            emi=payment,
            total_interest=interest,
            total_payable=(principal + interest).quantize(Decimal("0.01")),
        )


def _opt_decimal(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None
