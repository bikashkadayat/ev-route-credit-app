"""Deterministic mock telematics with an injectable distress arc — Doc 16 §16.10.

The generator is the reason the demo lands. It must produce, for the distressed loan, a
usage decline that visibly **precedes** the first missed payment — that gap is the
product's central claim (Doc 12, Act III).

Deterministic by construction: every value derives from SHA-256 of
(vehicle_id, date, seed). No ``random``, so the same demo runs identically every time.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.core.types import q2
from app.integrations.cib.mock import DeterministicStream
from app.integrations.telematics.base import (
    BatteryRecord,
    ChargingSessionRecord,
    DailyTelemetryBundle,
    TelematicsProvider,
    TelematicsUnavailable,
    TelemetryRecord,
)

DEFAULT_SEED = "20260908"


@dataclass(frozen=True, slots=True)
class VehicleProfile:
    """One vehicle's behavioural shape — Doc 16 §16.10."""

    vehicle_id: int
    baseline_daily_km: Decimal
    trips_per_100km: Decimal = Decimal("3.5")
    active_days_per_week: int = 6
    baseline_soh: Decimal = Decimal("98.5")
    soh_decay_per_year: Decimal = Decimal("2.0")
    revenue_per_km: Decimal = Decimal("31.5")
    route_deviation_base: Decimal = Decimal("3.0")

    # distress arc (all optional)
    decline_start: date | None = None
    decline_factor: Decimal = Decimal("1.0")        # 0.62 == 38% below baseline
    charging_decline_factor: Decimal = Decimal("1.0")
    deviation_drift: Decimal = Decimal("0")          # extra deviation once declining
    inactive_from: date | None = None                # hard stop (repossession, breakdown)
    silent_from: date | None = None                  # device stops reporting
    zero_km_dates: frozenset[date] = frozenset()

    def is_declining(self, day: date) -> bool:
        return self.decline_start is not None and day >= self.decline_start


class MockTelematicsProvider(TelematicsProvider):
    name = "MOCK_TELEMATICS"

    def __init__(
        self,
        profiles: Sequence[VehicleProfile],
        *,
        seed: str = DEFAULT_SEED,
        unavailable: bool = False,
    ) -> None:
        self.profiles = {p.vehicle_id: p for p in profiles}
        self.seed = seed
        self.unavailable = unavailable

    async def fetch_daily(
        self, *, vehicle_ids: Sequence[int], on_date: date
    ) -> DailyTelemetryBundle:
        if self.unavailable:
            raise TelematicsUnavailable("mock telematics provider is configured as unavailable")
        return self.build_daily(vehicle_ids=vehicle_ids, on_date=on_date)

    # Synchronous core so tests need no event loop.
    def build_daily(
        self, *, vehicle_ids: Sequence[int], on_date: date
    ) -> DailyTelemetryBundle:
        telemetry: list[TelemetryRecord] = []
        battery: list[BatteryRecord] = []
        charging: list[ChargingSessionRecord] = []
        missing: list[int] = []

        for vehicle_id in vehicle_ids:
            profile = self.profiles.get(vehicle_id)
            if profile is None:
                missing.append(vehicle_id)
                continue
            if profile.silent_from is not None and on_date >= profile.silent_from:
                # The device has stopped reporting. Silence is itself a signal
                # (TELEMETRY_SILENT_*): we emit nothing rather than emitting zeros,
                # because a zero would look like a stationary vehicle rather than a
                # missing feed.
                missing.append(vehicle_id)
                continue

            record = self._telemetry_for(profile, on_date)
            telemetry.append(record)
            battery.append(self._battery_for(profile, on_date, record))
            charging.extend(self._charging_for(profile, on_date, record))

        return DailyTelemetryBundle(
            telemetry=tuple(telemetry),
            battery=tuple(battery),
            charging=tuple(charging),
            missing_vehicle_ids=tuple(missing),
            meta={"provider": self.name, "seed": self.seed, "date": on_date.isoformat()},
        )

    def series(
        self, vehicle_id: int, start: date, end: date
    ) -> tuple[TelemetryRecord, ...]:
        """Convenience for seeding and tests: a whole date range for one vehicle."""
        out: list[TelemetryRecord] = []
        day = start
        while day <= end:
            bundle = self.build_daily(vehicle_ids=[vehicle_id], on_date=day)
            out.extend(bundle.telemetry)
            day += timedelta(days=1)
        return tuple(out)

    # ------------------------------------------------------------------
    def _stream(self, profile: VehicleProfile, day: date, salt: str) -> DeterministicStream:
        return DeterministicStream(f"{self.seed}:{salt}:{profile.vehicle_id}:{day.isoformat()}")

    def _is_rest_day(self, profile: VehicleProfile, day: date) -> bool:
        """A fixed weekly rest day, derived from the vehicle id so it is stable."""
        if profile.active_days_per_week >= 7:
            return False
        rest_days = 7 - profile.active_days_per_week
        offset = profile.vehicle_id % 7
        return ((day.toordinal() + offset) % 7) < rest_days

    def _telemetry_for(self, profile: VehicleProfile, day: date) -> TelemetryRecord:
        stream = self._stream(profile, day, "tel")

        inactive = (
            day in profile.zero_km_dates
            or (profile.inactive_from is not None and day >= profile.inactive_from)
            or self._is_rest_day(profile, day)
        )

        if inactive:
            record = TelemetryRecord(
                vehicle_id=profile.vehicle_id, telemetry_date=day, daily_km=Decimal("0"),
                trip_count=0, active_hours=Decimal("0"), idle_minutes=0,
                route_deviation_percent=Decimal("0"), avg_speed_kmph=None,
                estimated_revenue=Decimal("0"),
            )
            record.validate()
            return record

        # +/- 12% day-to-day variation around the (possibly declined) baseline
        variation = Decimal(str(0.88 + stream.fraction() * 0.24))
        base = profile.baseline_daily_km
        if profile.is_declining(day):
            base *= profile.decline_factor
        daily_km = q2(base * variation)

        trips = int((daily_km * profile.trips_per_100km / Decimal("100")).to_integral_value())
        active_hours = q2(daily_km / Decimal("22"))  # ~22 km/h effective urban average
        deviation = profile.route_deviation_base + (
            profile.deviation_drift if profile.is_declining(day) else Decimal("0")
        )

        record = TelemetryRecord(
            vehicle_id=profile.vehicle_id,
            telemetry_date=day,
            daily_km=daily_km,
            trip_count=max(trips, 1),
            active_hours=min(active_hours, Decimal("18")),
            idle_minutes=stream.integer(30, 180),
            route_deviation_percent=q2(min(deviation, Decimal("100"))),
            avg_speed_kmph=q2(Decimal("18") + Decimal(str(stream.fraction() * 10))),
            max_speed_kmph=q2(Decimal("45") + Decimal(str(stream.fraction() * 25))),
            harsh_braking_count=stream.integer(0, 6),
            night_driving_hours=q2(Decimal(str(stream.fraction())) * Decimal("2")),
            geofence_exits=stream.integer(0, 2),
            estimated_revenue=q2(daily_km * profile.revenue_per_km),
        )
        record.validate()
        return record

    def _battery_for(
        self, profile: VehicleProfile, day: date, record: TelemetryRecord
    ) -> BatteryRecord:
        stream = self._stream(profile, day, "bat")
        # Linear degradation from the profile's baseline, anchored on the epoch so the
        # value is a pure function of the date.
        years = Decimal(day.toordinal() - date(2026, 1, 1).toordinal()) / Decimal("365")
        soh = profile.baseline_soh - profile.soh_decay_per_year * max(years, Decimal("0"))
        soh = max(soh, Decimal("50"))

        energy = q2(record.daily_km / Decimal("5.2"))  # ~5.2 km per kWh
        avg_soc = Decimal("45") + Decimal(str(stream.fraction() * 30))
        min_soc = max(avg_soc - Decimal(str(15 + stream.fraction() * 20)), Decimal("5"))

        battery = BatteryRecord(
            vehicle_id=profile.vehicle_id, metric_date=day,
            avg_state_of_charge=q2(avg_soc), min_state_of_charge=q2(min_soc),
            state_of_health=q2(soh), energy_consumed_kwh=energy,
            charge_cycles=q2(energy / Decimal("40")),
            estimated_range_km=int(soh / Decimal("100") * Decimal("380")),
        )
        battery.validate()
        return battery

    def _charging_for(
        self, profile: VehicleProfile, day: date, record: TelemetryRecord
    ) -> tuple[ChargingSessionRecord, ...]:
        if record.daily_km == 0:
            return ()
        stream = self._stream(profile, day, "chg")
        expected = record.daily_km / Decimal("160")
        if profile.is_declining(day):
            expected *= profile.charging_decline_factor
        sessions = max(int(expected.to_integral_value()), 1 if stream.fraction() > 0.25 else 0)

        out: list[ChargingSessionRecord] = []
        for _ in range(sessions):
            energy = q2(Decimal("12") + Decimal(str(stream.fraction() * 30)))
            out.append(
                ChargingSessionRecord(
                    vehicle_id=profile.vehicle_id, session_date=day,
                    energy_delivered_kwh=energy,
                    duration_minutes=stream.integer(25, 110),
                    charger_type=stream.choice(["DC_FAST", "AC", "HOME"]),
                    soc_start=q2(Decimal("15") + Decimal(str(stream.fraction() * 25))),
                    soc_end=q2(Decimal("75") + Decimal(str(stream.fraction() * 20))),
                    is_home_charging=stream.fraction() < 0.3,
                )
            )
        return tuple(out)


def demo_profiles(anchor: date) -> tuple[VehicleProfile, ...]:
    """The Doc 16 §16.10 behavioural profiles, anchored to a demo date.

    Vehicle 7 is the distressed loan LN-2026-000087: its usage decline starts 24 days
    before the anchor, while the first missed instalment is 47 days past due — so the
    telematics signal precedes the payment signal, which is the whole point.
    """
    return (
        # healthy
        VehicleProfile(1, Decimal("160"), baseline_soh=Decimal("98.8")),
        VehicleProfile(5, Decimal("150"), baseline_soh=Decimal("98.2")),
        VehicleProfile(9, Decimal("172"), baseline_soh=Decimal("99.0")),
        # normal
        VehicleProfile(2, Decimal("120")),
        VehicleProfile(3, Decimal("95"), trips_per_100km=Decimal("9.0"),
                       revenue_per_km=Decimal("18.0")),
        VehicleProfile(4, Decimal("130")),
        VehicleProfile(6, Decimal("140")),
        # mild decline
        VehicleProfile(8, Decimal("110"), decline_start=anchor - timedelta(days=30),
                       decline_factor=Decimal("0.79"), baseline_soh=Decimal("80.9"),
                       soh_decay_per_year=Decimal("3.5"), active_days_per_week=5),
        VehicleProfile(10, Decimal("125"), decline_start=anchor - timedelta(days=26),
                       decline_factor=Decimal("0.81"), active_days_per_week=5),
        # distressed — LN-2026-000087
        VehicleProfile(
            7, Decimal("150.2"),
            decline_start=anchor - timedelta(days=24),
            decline_factor=Decimal("0.616"),        # -38.4% against baseline
            charging_decline_factor=Decimal("0.667"),  # -33% charging sessions
            deviation_drift=Decimal("11"),          # deviation rises to ~14%
            active_days_per_week=5,
            baseline_soh=Decimal("96.4"),
            zero_km_dates=frozenset({
                anchor - timedelta(days=12), anchor - timedelta(days=11),
                anchor - timedelta(days=5), anchor - timedelta(days=4),
            }),
            silent_from=anchor - timedelta(days=1),  # feed goes quiet 2 days before today
        ),
    )
