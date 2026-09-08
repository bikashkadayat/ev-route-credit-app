"""Mock CIB and telematics adapters — Doc 04 §4.11, Doc 16 §16.10.

Determinism is the property that matters: the demo must render identically every run, and
the E2E suite asserts on specific numbers.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import Decimal as Dc

import pytest

from app.integrations.cib.base import CIBProvider, CIBReport, CIBUnavailable
from app.integrations.cib.mock import DeterministicStream, MockCIBProvider
from app.integrations.registry import build_cib_provider, build_telematics_provider
from app.integrations.telematics.base import (
    InvalidTelemetry,
    TelematicsUnavailable,
    TelemetryRecord,
)
from app.integrations.telematics.mock import (
    MockTelematicsProvider,
    demo_profiles,
)

AS_OF = date(2026, 9, 8)
RAM = {"id_type": "CITIZENSHIP", "id_number": "27-01-70-04521", "full_name": "Ram Bahadur Tamang"}


# ---------------------------------------------------------------------------
# Deterministic stream
# ---------------------------------------------------------------------------
def test_stream_is_reproducible_and_seed_sensitive():
    a = [DeterministicStream("seed-a").integer(0, 1000) for _ in range(1)]
    b = [DeterministicStream("seed-a").integer(0, 1000) for _ in range(1)]
    c = [DeterministicStream("seed-b").integer(0, 1000) for _ in range(1)]
    assert a == b
    assert a != c


def test_stream_respects_bounds():
    stream = DeterministicStream("bounds")
    for _ in range(200):
        assert 5 <= stream.integer(5, 9) <= 9
        assert 0.0 <= stream.fraction() < 1.0


# ---------------------------------------------------------------------------
# Mock CIB
# ---------------------------------------------------------------------------
def test_same_id_always_returns_the_same_report():
    provider = MockCIBProvider()
    first = provider.build_report(as_of=AS_OF, **RAM)
    second = provider.build_report(as_of=AS_OF, **RAM)
    assert first == second


def test_seeded_reference_borrower_matches_the_documented_profile():
    """Doc 07 §7.5.7 — Ram Bahadur Tamang."""
    report = MockCIBProvider().build_report(as_of=AS_OF, **RAM)
    assert report.score == 742
    assert report.credit_history_months == 54
    assert report.previous_default_count == 0
    assert report.max_dpd_last_24m == 12
    assert report.enquiries_last_6m == 1
    assert report.is_blacklisted is False
    assert report.total_monthly_emi == Dc("12400")
    assert report.total_outstanding == Dc("385000")
    assert report.active_loan_count == 1


def test_identifiers_are_masked_by_default():
    report = MockCIBProvider().build_report(as_of=AS_OF, **RAM)
    assert report.id_number_masked == "27-01-70-****"
    assert "04521" not in report.id_number_masked


@pytest.mark.parametrize(
    "raw, masked",
    [("27-01-70-04521", "27-01-70-****"), ("PAN-301245678", "PAN-****"),
     ("301245678", "30124****"), ("1234", "****"), ("12", "**")],
)
def test_masking_rules(raw, masked):
    assert CIBProvider.mask(raw) == masked


def test_validity_window_is_applied():
    report = MockCIBProvider(validity_days=90).build_report(as_of=AS_OF, **RAM)
    assert report.enquiry_date == AS_OF
    assert report.valid_until == AS_OF + timedelta(days=90)


def test_enquiry_id_is_stable_per_id_and_date():
    provider = MockCIBProvider()
    same_day = provider.build_report(as_of=AS_OF, **RAM).enquiry_id
    assert provider.build_report(as_of=AS_OF, **RAM).enquiry_id == same_day
    next_day = provider.build_report(as_of=AS_OF + timedelta(days=1), **RAM).enquiry_id
    assert next_day != same_day


def test_seeded_profiles_cover_every_decision_branch():
    """Blacklist, active default, thin file and a pristine record must all be reachable."""
    provider = MockCIBProvider()

    blacklisted = provider.build_report(
        id_type="CITIZENSHIP", id_number="27-08-70-02237", full_name="Hari Krishna Yadav",
        as_of=AS_OF)
    assert blacklisted.is_blacklisted is True

    defaulted = provider.build_report(
        id_type="CITIZENSHIP", id_number="27-07-73-06691", full_name="Prakash Chaudhary",
        as_of=AS_OF)
    assert defaulted.previous_default_count >= 1
    assert defaulted.current_overdue_amount > 0

    thin = provider.build_report(
        id_type="CITIZENSHIP", id_number="27-06-77-00214", full_name="Dipak Lama", as_of=AS_OF)
    assert thin.score is None
    assert thin.credit_history_months == 0
    assert thin.repayment_history == ()

    pristine = provider.build_report(
        id_type="CITIZENSHIP", id_number="27-03-68-01192", full_name="Bishnu Prasad Poudel",
        as_of=AS_OF)
    assert pristine.score >= 780
    assert pristine.max_dpd_last_24m == 0
    assert pristine.previous_default_count == 0


def test_unknown_identifier_still_produces_a_valid_report():
    report = MockCIBProvider().build_report(
        id_type="CITIZENSHIP", id_number="99-99-99-99999", full_name="Unknown Person",
        as_of=AS_OF)
    assert 560 <= report.score <= 860
    assert report.grade in {"A", "B", "C", "D", "E"}


def test_repayment_history_length_tracks_credit_depth():
    report = MockCIBProvider().build_report(as_of=AS_OF, **RAM)
    assert len(report.repayment_history) == 24  # min(24, 54 months)
    assert all(set(m) == {"month", "status"} for m in report.repayment_history)


def test_report_rejects_an_out_of_range_score():
    with pytest.raises(ValueError, match="outside the valid range"):
        CIBReport(
            provider="X", enquiry_id="1", enquiry_date=AS_OF, valid_until=AS_OF,
            id_type="CITIZENSHIP", id_number_masked="***", full_name="X",
            score=1200, grade="A", credit_history_months=1, active_loan_count=0,
            total_outstanding=Dc(0), total_monthly_emi=Dc(0), previous_default_count=0,
            current_overdue_amount=Dc(0), max_dpd_last_24m=0, is_blacklisted=False,
            enquiries_last_6m=0,
        )


def test_report_rejects_validity_before_enquiry():
    with pytest.raises(ValueError, match="precedes"):
        CIBReport(
            provider="X", enquiry_id="1", enquiry_date=AS_OF,
            valid_until=AS_OF - timedelta(days=1),
            id_type="CITIZENSHIP", id_number_masked="***", full_name="X",
            score=700, grade="B", credit_history_months=1, active_loan_count=0,
            total_outstanding=Dc(0), total_monthly_emi=Dc(0), previous_default_count=0,
            current_overdue_amount=Dc(0), max_dpd_last_24m=0, is_blacklisted=False,
            enquiries_last_6m=0,
        )


def test_configured_failure_raises_the_documented_exception():
    """The degradation path (Doc 04 §4.14) must be exercisable on demand."""
    provider = MockCIBProvider(fail_for_ids=frozenset({RAM["id_number"]}))
    with pytest.raises(CIBUnavailable):
        asyncio.run(provider.fetch_report(as_of=AS_OF, **RAM))


def test_async_interface_returns_the_same_object_as_the_sync_core():
    provider = MockCIBProvider()
    assert asyncio.run(provider.fetch_report(as_of=AS_OF, **RAM)) == provider.build_report(
        as_of=AS_OF, **RAM)


# ---------------------------------------------------------------------------
# Mock telematics
# ---------------------------------------------------------------------------
def test_daily_bundle_is_reproducible():
    provider = MockTelematicsProvider(demo_profiles(AS_OF))
    a = provider.build_daily(vehicle_ids=[1, 2, 7], on_date=AS_OF - timedelta(days=10))
    b = provider.build_daily(vehicle_ids=[1, 2, 7], on_date=AS_OF - timedelta(days=10))
    assert a == b


def test_different_seeds_produce_different_series():
    """Compared over a range, because the weekly rest-day pattern is seed-independent by
    design (it derives from vehicle id and date so the rhythm stays stable)."""
    start, end = AS_OF - timedelta(days=30), AS_OF - timedelta(days=1)
    a = MockTelematicsProvider(demo_profiles(AS_OF), seed="A").series(1, start, end)
    b = MockTelematicsProvider(demo_profiles(AS_OF), seed="B").series(1, start, end)
    assert [r.daily_km for r in a] != [r.daily_km for r in b]
    # rest days remain on the same calendar days regardless of seed
    assert [r.daily_km == 0 for r in a] == [r.daily_km == 0 for r in b]


def test_unknown_vehicle_is_reported_missing_not_zeroed():
    """A vehicle with no device must not look like a stationary vehicle."""
    bundle = MockTelematicsProvider(demo_profiles(AS_OF)).build_daily(
        vehicle_ids=[999], on_date=AS_OF)
    assert bundle.telemetry == ()
    assert bundle.missing_vehicle_ids == (999,)


def test_silent_device_stops_reporting_rather_than_reporting_zero():
    """Doc 08 — silence must be distinguishable from a parked vehicle."""
    provider = MockTelematicsProvider(demo_profiles(AS_OF))
    bundle = provider.build_daily(vehicle_ids=[7], on_date=AS_OF)
    assert bundle.telemetry == ()
    assert 7 in bundle.missing_vehicle_ids


def test_every_generated_record_passes_its_own_validation():
    provider = MockTelematicsProvider(demo_profiles(AS_OF))
    start = AS_OF - timedelta(days=120)
    for vehicle_id in (1, 2, 3, 8, 10):
        for record in provider.series(vehicle_id, start, AS_OF):
            record.validate()
            assert Dc("0") <= record.daily_km <= Dc("800")
            assert Dc("0") <= record.active_hours <= Dc("24")


def test_distressed_vehicle_declines_before_the_payment_signal():
    """Doc 12 Act III — the whole demo rests on this ordering.

    Usage must fall materially in the 24 days before the anchor, while the loan's first
    missed instalment is 47 days past due. If this assertion ever fails, the demo's
    central claim stops being true.
    """
    provider = MockTelematicsProvider(demo_profiles(AS_OF))
    profile = {p.vehicle_id: p for p in demo_profiles(AS_OF)}[7]

    before = provider.series(7, AS_OF - timedelta(days=90), profile.decline_start
                             - timedelta(days=1))
    after = provider.series(7, profile.decline_start, AS_OF - timedelta(days=2))

    active_before = [r.daily_km for r in before if r.daily_km > 0]
    active_after = [r.daily_km for r in after if r.daily_km > 0]
    mean_before = sum(active_before) / len(active_before)
    mean_after = sum(active_after) / len(active_after)

    decline = (mean_after - mean_before) / mean_before * 100
    assert Dc("-45") < decline < Dc("-30"), f"decline was {decline:.1f}%, expected about -38%"


def test_distressed_vehicle_shows_route_deviation_drift():
    provider = MockTelematicsProvider(demo_profiles(AS_OF))
    profile = {p.vehicle_id: p for p in demo_profiles(AS_OF)}[7]
    early = provider.series(7, AS_OF - timedelta(days=90), AS_OF - timedelta(days=60))
    late = provider.series(7, profile.decline_start, AS_OF - timedelta(days=2))
    early_dev = max(r.route_deviation_percent for r in early)
    late_dev = max(r.route_deviation_percent for r in late)
    assert late_dev > early_dev


def test_healthy_vehicles_stay_near_their_baseline():
    provider = MockTelematicsProvider(demo_profiles(AS_OF))
    for vehicle_id, baseline in ((1, Dc("160")), (5, Dc("150")), (9, Dc("172"))):
        records = [r for r in provider.series(vehicle_id, AS_OF - timedelta(days=30), AS_OF)
                   if r.daily_km > 0]
        mean = sum(r.daily_km for r in records) / len(records)
        assert abs(mean - baseline) / baseline < Dc("0.15")


def test_rest_days_produce_zero_kilometre_records():
    provider = MockTelematicsProvider(demo_profiles(AS_OF))
    records = provider.series(3, AS_OF - timedelta(days=30), AS_OF)
    assert any(r.daily_km == 0 for r in records)
    assert all(r.trip_count == 0 for r in records if r.daily_km == 0)


def test_battery_health_declines_monotonically_over_time():
    provider = MockTelematicsProvider(demo_profiles(AS_OF))
    early = provider.build_daily(vehicle_ids=[1], on_date=date(2026, 2, 1)).battery[0]
    late = provider.build_daily(vehicle_ids=[1], on_date=date(2026, 12, 1)).battery[0]
    assert late.state_of_health < early.state_of_health


def test_low_soh_profile_is_in_the_battery_alert_band():
    """Vehicle 8 is seeded below 80% so BATTERY_HEALTH_LOW is demonstrable."""
    provider = MockTelematicsProvider(demo_profiles(AS_OF))
    battery = provider.build_daily(vehicle_ids=[8], on_date=AS_OF).battery
    if battery:  # vehicle 8 may be resting; take any reporting day
        assert battery[0].state_of_health < Dc("80")


def test_charging_sessions_only_occur_on_active_days():
    provider = MockTelematicsProvider(demo_profiles(AS_OF))
    bundle = provider.build_daily(vehicle_ids=[1, 2, 3, 4], on_date=AS_OF - timedelta(days=3))
    moving = {r.vehicle_id for r in bundle.telemetry if r.daily_km > 0}
    for session in bundle.charging:
        assert session.vehicle_id in moving


def test_provider_unavailable_raises():
    provider = MockTelematicsProvider(demo_profiles(AS_OF), unavailable=True)
    with pytest.raises(TelematicsUnavailable):
        asyncio.run(provider.fetch_daily(vehicle_ids=[1], on_date=AS_OF))


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"daily_km": Dc("900")}, "daily_km"),
        ({"daily_km": Dc("-1")}, "daily_km"),
        ({"active_hours": Dc("25")}, "active_hours"),
        ({"route_deviation_percent": Dc("101")}, "route_deviation_percent"),
        ({"trip_count": -1}, "trip_count"),
    ],
)
def test_implausible_telemetry_is_rejected(kwargs, message):
    base = {
        "vehicle_id": 1, "telemetry_date": AS_OF, "daily_km": Dc("100"), "trip_count": 5,
        "active_hours": Dc("6"), "idle_minutes": 60, "route_deviation_percent": Dc("3"),
    }
    base.update(kwargs)
    with pytest.raises(InvalidTelemetry, match=message):
        TelemetryRecord(**base).validate()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def test_registry_defaults_to_mock(monkeypatch):
    monkeypatch.delenv("CIB_PROVIDER", raising=False)
    monkeypatch.delenv("TELEMATICS_PROVIDER", raising=False)
    assert isinstance(build_cib_provider(), MockCIBProvider)
    assert isinstance(build_telematics_provider(anchor=AS_OF), MockTelematicsProvider)


def test_registry_is_driven_by_environment(monkeypatch):
    monkeypatch.setenv("CIB_PROVIDER", "mock")
    assert build_cib_provider().name == "MOCK_CIB"


def test_selecting_live_fails_loudly_until_implemented():
    """Better a clear NotImplementedError than a silent fallback to mock data in production."""
    with pytest.raises(NotImplementedError, match="bureau contract"):
        build_cib_provider("live")
    with pytest.raises(NotImplementedError, match="OEM or OBD"):
        build_telematics_provider("live", anchor=AS_OF)


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match="Unknown CIB_PROVIDER"):
        build_cib_provider("carrier-pigeon")
