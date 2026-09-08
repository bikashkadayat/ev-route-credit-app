"""Service orchestration — the seam between persistence and the pure engines.

A service does four things: resolve configuration, marshal rows into the engine's frozen
input, call the engine **once**, and translate the engine's domain errors into the API
contract. These tests exercise exactly that, with stub repositories, so a change in
orchestration is caught without a database.

The engine itself is not re-tested here — it has its own suite. What is asserted is that
the service does not compute anything the engine already computes.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.errors import IncompleteDataError, NotFoundError
from app.engines.config_loader import default_config
from app.engines.route_engine import IncompleteRouteData, RouteScoringInput
from app.services.route_assessment import RouteAssessmentService


def route_row(**overrides):
    """An ORM-shaped route row. Only the attributes the adapter reads are present, which
    is itself the assertion that the adapter reads no more than these."""
    row = SimpleNamespace(
        id=1,
        route_name="Kathmandu-Dhulikhel",
        total_distance_km=Decimal("30"),
        road_type="PITCH",
        road_condition="GOOD",
        gradient_profile="ROLLING",
        pitch_road_percent=Decimal("100"),
        charging_station_count=6,
        fast_charger_count=3,
        avg_charging_distance_km=Decimal("5"),
        max_charging_gap_km=Decimal("12"),
        passenger_volume_daily=1200,
        freight_volume_daily_tons=Decimal("0"),
        estimated_daily_trips=Decimal("8"),
        avg_fare_per_trip=Decimal("950"),
        avg_freight_revenue_per_trip=Decimal("0"),
        traffic_density="HIGH",
        competition_level="MODERATE",
        seasonal_risk="LOW",
        monsoon_disruption_days=6,
        flood_landslide_risk="LOW",
        security_risk="LOW",
        electricity_tariff_per_kwh=Decimal("12"),
        estimated_daily_operating_cost=Decimal("1800"),
        deleted_at=None,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


class StubSession:
    def __init__(self, objects: dict | None = None):
        self.objects = objects or {}

    def get(self, model, identifier):
        return self.objects.get((model.__name__, identifier))


def bare_service(session=None) -> RouteAssessmentService:
    """A service with only what these tests touch; __init__ is bypassed so no Session,
    engine wiring or repository construction is required."""
    service = RouteAssessmentService.__new__(RouteAssessmentService)
    service.session = session or StubSession()
    return service


# ---------------------------------------------------------------------------
# The ORM -> engine adapter
# ---------------------------------------------------------------------------
def test_the_adapter_produces_the_engine_input_dataclass():
    payload = bare_service().build_engine_input(route_row())
    assert isinstance(payload, RouteScoringInput)
    assert payload.route_name == "Kathmandu-Dhulikhel"
    assert payload.total_distance_km == Decimal("30")


def test_numeric_columns_arrive_as_decimal_never_float():
    """Doc 07 §7.1 — the engine is Decimal end to end; a float here would reintroduce
    binary rounding into a regulated score."""
    payload = bare_service().build_engine_input(route_row())
    for field in (
        payload.total_distance_km, payload.pitch_road_percent, payload.avg_fare_per_trip,
        payload.electricity_tariff_per_kwh, payload.estimated_daily_operating_cost,
    ):
        assert isinstance(field, Decimal)


def test_a_null_charging_gap_stays_null_rather_than_becoming_zero():
    """Zero would read as "chargers everywhere"; null means "unknown" and the engine
    raises IncompleteRouteData instead of scoring a guess."""
    payload = bare_service().build_engine_input(route_row(max_charging_gap_km=None))
    assert payload.max_charging_gap_km is None


def test_the_default_reference_vehicle_is_the_documented_24kwh_140km():
    payload = bare_service().build_engine_input(route_row())
    assert payload.reference_range_km == Decimal("140")
    assert payload.reference_battery_kwh == Decimal("24")


def test_a_named_reference_vehicle_overrides_the_default():
    from app.models.catalog import VehicleModel

    model = SimpleNamespace(real_world_range_km=Decimal("380"),
                            battery_capacity_kwh=Decimal("71.7"))
    session = StubSession({(VehicleModel.__name__, 5): model})
    payload = bare_service(session).build_engine_input(route_row(), 5)
    assert payload.reference_range_km == Decimal("380")
    assert payload.reference_battery_kwh == Decimal("71.7")


def test_an_unknown_reference_vehicle_is_a_404_not_a_silent_default():
    with pytest.raises(NotFoundError) as exc:
        bare_service().build_engine_input(route_row(), 999)
    assert exc.value.status_code == 404
    assert exc.value.code == "VEHICLE_MODEL_NOT_FOUND"


def test_the_adapter_is_the_only_translation_and_computes_no_score():
    """A component score computed here would be a second implementation of the engine."""
    payload = bare_service().build_engine_input(route_row())
    assert not hasattr(payload, "total_score")
    assert not hasattr(payload, "grade")


# ---------------------------------------------------------------------------
# Error translation
# ---------------------------------------------------------------------------
class FailingEngine:
    """Stands in for the engine so the translation is tested, not the engine."""

    def __init__(self, fields):
        self.fields = fields
        self.calls = 0

    def score(self, payload, config):
        self.calls += 1
        raise IncompleteRouteData(self.fields)


class CountingEngine:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def score(self, payload, config):
        self.calls += 1
        return self.result


class StubConfigService:
    def __init__(self, config):
        self.config = config

    def active_config(self, config_type):
        return self.config


def assess_service(engine, *, persisted=None) -> RouteAssessmentService:
    service = bare_service()
    service.engine = engine
    service.config_service = StubConfigService(default_config("ROUTE"))
    service.get_route = lambda route_id: route_row(id=route_id)  # type: ignore[method-assign]
    service._persist = lambda *a, **k: persisted  # type: ignore[method-assign]
    return service


def test_the_engines_incomplete_data_error_becomes_a_422_listing_every_field():
    """The engine knows nothing about HTTP; the service owns the translation."""
    engine = FailingEngine(["max_charging_gap_km", "avg_fare_per_trip"])
    with pytest.raises(IncompleteDataError) as exc:
        assess_service(engine).assess(1, assessed_by=1)

    assert exc.value.status_code == 422
    assert exc.value.code == "INCOMPLETE_DATA"
    assert [d["field"] for d in exc.value.details] == [
        "max_charging_gap_km", "avg_fare_per_trip"
    ]


def test_the_original_engine_error_is_chained_for_the_log():
    engine = FailingEngine(["max_charging_gap_km"])
    with pytest.raises(IncompleteDataError) as exc:
        assess_service(engine).assess(1, assessed_by=1)
    assert isinstance(exc.value.__cause__, IncompleteRouteData)


def test_the_engine_is_called_exactly_once_per_assessment():
    """Twice would be two chances to diverge, and double the cost of the expensive path."""
    result = SimpleNamespace(total_score=Decimal("90.12"), grade="A")
    engine = CountingEngine(result)
    service = assess_service(engine, persisted=SimpleNamespace(id=1))
    outcome = service.assess(1, assessed_by=1)

    assert engine.calls == 1
    assert outcome.score is result, "the service must return the engine's own result"


def test_the_service_returns_the_engine_result_unmodified():
    """Any post-hoc adjustment here would be policy applied outside the versioned
    scorecard, and would not be reproducible from the stored config version."""
    result = SimpleNamespace(total_score=Decimal("87.99"), grade="B")
    service = assess_service(CountingEngine(result), persisted=SimpleNamespace(id=1))
    outcome = service.assess(1, assessed_by=1)
    assert outcome.score.total_score == Decimal("87.99")
    assert outcome.score.grade == "B"


def test_configuration_is_resolved_before_the_engine_runs():
    """Ordering matters: an unpublished scorecard must 409 before anything is scored."""
    calls: list[str] = []

    class RecordingConfig(StubConfigService):
        def active_config(self, config_type):
            calls.append("config")
            return self.config

    class RecordingEngine(CountingEngine):
        def score(self, payload, config):
            calls.append("engine")
            return super().score(payload, config)

    service = assess_service(
        RecordingEngine(SimpleNamespace(grade="A", total_score=Decimal("90.12"))),
        persisted=SimpleNamespace(),
    )
    service.config_service = RecordingConfig(default_config("ROUTE"))
    service.assess(1, assessed_by=1)
    assert calls == ["config", "engine"]


# ---------------------------------------------------------------------------
# Derived route economics
# ---------------------------------------------------------------------------
def test_derived_route_economics_are_a_convenience_not_an_engine_input():
    """These columns exist for list views and reports. The engine recomputes them from the
    same formulas, so the stored value can never influence a score."""
    derived = RouteAssessmentService._derive_economics(
        {
            "estimated_daily_trips": Decimal("8"),
            "avg_fare_per_trip": Decimal("950"),
            "avg_freight_revenue_per_trip": Decimal("0"),
            "total_distance_km": Decimal("30"),
            "electricity_tariff_per_kwh": Decimal("12"),
            "gradient_profile": "ROLLING",
        }
    )
    assert derived["estimated_daily_revenue"] == Decimal("7600.00")
    assert derived["estimated_daily_energy_cost"] > 0

    payload = bare_service().build_engine_input(route_row())
    assert not hasattr(payload, "estimated_daily_revenue")


def test_derived_economics_survive_a_route_with_no_revenue_fields():
    derived = RouteAssessmentService._derive_economics({})
    assert derived["estimated_daily_revenue"] == Decimal("0.00")
    assert derived["estimated_daily_energy_cost"] == Decimal("0.00")


# ---------------------------------------------------------------------------
# Alert snapshot assembly
# ---------------------------------------------------------------------------
def test_the_alert_snapshot_leaves_absent_telemetry_absent():
    """Doc 08 §8.4 — a loan with no monitoring snapshot must produce nulls, not zeros. A
    zero avg_daily_km would fire the idle-vehicle rule on a vehicle nobody has measured."""
    from app.services.alert import AlertService

    service = AlertService.__new__(AlertService)
    service.snapshots = SimpleNamespace(latest_for_loan=lambda loan_id, as_of: None)
    loan = SimpleNamespace(
        id=1, days_past_due=0, installments_overdue=0,
        overdue_amount=Decimal("0"), emi_amount=Decimal("52000"),
    )

    snapshot = service.build_snapshot(loan, date(2026, 1, 31))
    assert snapshot.get("AVG_DAILY_KM_7D") is None
    assert snapshot.get("ZERO_KM_STREAK") is None
    assert snapshot.get("DAYS_PAST_DUE") == 0, "loan-level facts are always known"


def test_the_alert_snapshot_carries_loan_level_facts_through():
    from app.services.alert import AlertService

    service = AlertService.__new__(AlertService)
    service.snapshots = SimpleNamespace(latest_for_loan=lambda loan_id, as_of: None)
    loan = SimpleNamespace(
        id=1, days_past_due=47, installments_overdue=2,
        overdue_amount=Decimal("104000"), emi_amount=Decimal("52000"),
    )

    snapshot = service.build_snapshot(loan, date(2026, 1, 31))
    assert snapshot.get("DAYS_PAST_DUE") == 47
    assert snapshot.get("CONSECUTIVE_MISSED_EMI") == 2
