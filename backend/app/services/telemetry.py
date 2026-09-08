"""TelemetryService — ingestion and baseline computation — FR-6.1 to FR-6.6.

The adapter fetches; this service validates, persists and derives. The split matters
because "the vehicle stopped moving" is an underwriting signal, and a provider that sent
one implausible reading must not be able to raise an alert with it.

What is derived here (FR-6.3, FR-6.4, FR-6.6) is written into
``loan_monitoring_snapshots``, which is exactly what the rule engine reads. One
computation, one stored answer — the alert, the monitoring screen and the behaviour score
cannot disagree about how far a vehicle drove.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.types import ZERO, D, q2
from app.integrations.registry import build_telematics_provider
from app.integrations.telematics.base import (
    BatteryRecord,
    InvalidTelemetry,
    TelemetryRecord,
)
from app.models.portfolio import BatteryMetric, VehicleTelemetry
from app.repositories.alert import LoanRepository, MonitoringSnapshotRepository
from app.repositories.portfolio import TelemetryRepository

#: FR-6.3 — the rolling windows the rule catalogue is written against.
SHORT_WINDOW_DAYS = 7
LONG_WINDOW_DAYS = 30
#: The baseline is the month *before* the long window, so a decline is measured against
#: normal operation rather than against itself.
BASELINE_START_DAYS = 30
BASELINE_END_DAYS = 90


@dataclass(frozen=True, slots=True)
class IngestResult:
    as_of: date
    vehicles_polled: int
    telemetry_rows: int
    battery_rows: int
    rejected: tuple[str, ...]
    missing_vehicles: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "vehicles_polled": self.vehicles_polled,
            "telemetry_rows": self.telemetry_rows,
            "battery_rows": self.battery_rows,
            "rejected": list(self.rejected),
            "missing_vehicles": list(self.missing_vehicles),
        }


class TelemetryService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.telemetry = TelemetryRepository(session)
        self.snapshots = MonitoringSnapshotRepository(session)
        self.loans = LoanRepository(session)

    # ------------------------------------------------------------------
    # Ingestion — FR-6.1, FR-6.2
    # ------------------------------------------------------------------
    def ingest_day(
        self, *, as_of: date | None = None, profiles: Any = None
    ) -> IngestResult:
        """Pull one day of aggregates for every financed vehicle.

        Idempotent by primary key ``(vehicle_id, telemetry_date)``: re-running for the same
        day overwrites rather than double-counts, which is what makes a retry after a
        partial night safe.

        ``profiles`` overrides the behavioural profiles handed to the mock provider. It is
        how a caller reproduces a specific scenario — and how a live adapter, which has no
        profiles at all, simply ignores it.
        """
        business_date = as_of or datetime.now(UTC).date()
        loans = list(self.loans.active_loans())
        vehicle_ids = [loan.vehicle_id for loan in loans]
        by_vehicle = {loan.vehicle_id: loan.id for loan in loans}

        if not vehicle_ids:
            return IngestResult(business_date, 0, 0, 0, (), ())

        provider = build_telematics_provider(
            settings.telematics_provider,
            anchor=business_date,
            profiles=profiles or self._profiles_for(vehicle_ids, business_date),
        )
        bundle = asyncio.run(
            provider.fetch_daily(vehicle_ids=vehicle_ids, on_date=business_date)
        )

        rejected: list[str] = []
        telemetry_rows = 0
        for record in bundle.telemetry:
            try:
                # A provider that sends an implausible reading must not be able to raise an
                # alert with it (Doc 14 G-02).
                record.validate()
            except InvalidTelemetry as exc:
                rejected.append(str(exc))
                continue
            self._upsert_telemetry(record, by_vehicle.get(record.vehicle_id))
            telemetry_rows += 1

        battery_rows = 0
        for record in bundle.battery:
            try:
                record.validate()
            except InvalidTelemetry as exc:
                rejected.append(str(exc))
                continue
            self._upsert_battery(record)
            battery_rows += 1

        self.session.flush()
        return IngestResult(
            as_of=business_date,
            vehicles_polled=len(vehicle_ids),
            telemetry_rows=telemetry_rows,
            battery_rows=battery_rows,
            rejected=tuple(rejected),
            missing_vehicles=tuple(bundle.missing_vehicle_ids),
        )

    @staticmethod
    def _profiles_for(vehicle_ids: list[int], as_of: date) -> Any:
        """Behavioural profiles covering every vehicle being polled.

        The mock ships a scripted fleet (Doc 16 §16.10) that carries the demo's distress
        arc, and returns nothing for a vehicle it does not recognise. That is right for the
        demo and wrong for everything else: a vehicle financed through the API would report
        no telemetry at all, and FR-6.1 is "per financed vehicle", not "per demo vehicle".

        So the scripted profiles are kept as they are, and any other vehicle gets a plain
        one derived deterministically from its id — same vehicle, same behaviour, every
        run. A real provider would simply return whatever the device sent.
        """
        if settings.telematics_provider != "mock":
            return None

        from app.integrations.telematics.mock import VehicleProfile, demo_profiles

        scripted = {p.vehicle_id: p for p in demo_profiles(as_of)}
        for vehicle_id in vehicle_ids:
            if vehicle_id in scripted:
                continue
            # 110-190 km/day, stable across runs because it is a function of the id alone.
            baseline = Decimal(110 + (vehicle_id * 17) % 80)
            scripted[vehicle_id] = VehicleProfile(vehicle_id, baseline)
        return tuple(scripted.values())

    def _upsert_telemetry(self, record: TelemetryRecord, loan_id: int | None) -> None:
        existing = self.session.get(
            VehicleTelemetry, (record.vehicle_id, record.telemetry_date)
        )
        values = {
            "loan_id": loan_id,
            "daily_km": record.daily_km,
            "trip_count": record.trip_count,
            "avg_speed_kmph": record.avg_speed_kmph,
            "max_speed_kmph": record.max_speed_kmph,
            "active_hours": record.active_hours,
            "idle_minutes": record.idle_minutes,
            "route_deviation_percent": record.route_deviation_percent,
            "harsh_braking_count": record.harsh_braking_count,
            "night_driving_hours": record.night_driving_hours,
            "geofence_exits": record.geofence_exits,
            "estimated_revenue": record.estimated_revenue,
            "data_source": record.data_source,
            "is_estimated": record.is_estimated,
        }
        if existing is None:
            self.session.add(
                VehicleTelemetry(
                    vehicle_id=record.vehicle_id,
                    telemetry_date=record.telemetry_date,
                    **values,
                )
            )
        else:
            for key, value in values.items():
                setattr(existing, key, value)

    def _upsert_battery(self, record: BatteryRecord) -> None:
        existing = self.session.get(
            BatteryMetric, (record.vehicle_id, record.metric_date)
        )
        values = {
            "avg_state_of_charge": record.avg_state_of_charge,
            "min_state_of_charge": record.min_state_of_charge,
            "state_of_health": record.state_of_health,
            "energy_consumed_kwh": record.energy_consumed_kwh,
            "charge_cycles": record.charge_cycles,
            "estimated_range_km": record.estimated_range_km,
        }
        if existing is None:
            self.session.add(
                BatteryMetric(
                    vehicle_id=record.vehicle_id,
                    metric_date=record.metric_date,
                    **values,
                )
            )
        else:
            for key, value in values.items():
                setattr(existing, key, value)

    # ------------------------------------------------------------------
    # Baselines — FR-6.3, FR-6.4, FR-6.6
    # ------------------------------------------------------------------
    def rebuild_snapshots(self, *, as_of: date | None = None) -> int:
        """Recompute the monitoring snapshot every rule reads.

        Idempotent: a snapshot is a pure function of the stored telemetry and the business
        date, so a second run for the same date writes the same numbers.
        """
        business_date = as_of or datetime.now(UTC).date()
        count = 0
        for loan in self.loans.active_loans():
            self.snapshots.upsert(
                loan_id=loan.id,
                snapshot_date=business_date,
                **self.derive(loan, business_date),
            )
            count += 1
        self.session.flush()
        return count

    def derive(self, loan: Any, as_of: date) -> dict[str, Any]:
        """FR-6.3/6.4/6.6 — the derived metrics, from the stored daily aggregates."""
        rows = list(
            self.telemetry.series_for_vehicle(
                loan.vehicle_id,
                start=as_of - timedelta(days=BASELINE_END_DAYS),
                end=as_of,
            )
        )
        by_date = {r.telemetry_date: r for r in rows}

        def window(start_days: int, end_days: int) -> list[Any]:
            return [
                r for r in rows
                if start_days <= (as_of - r.telemetry_date).days < end_days
            ]

        last_7 = window(0, SHORT_WINDOW_DAYS)
        last_30 = window(0, LONG_WINDOW_DAYS)
        baseline_rows = window(BASELINE_START_DAYS, BASELINE_END_DAYS)

        avg_7 = _mean(r.daily_km for r in last_7)
        avg_30 = _mean(r.daily_km for r in last_30)
        baseline = _mean(r.daily_km for r in baseline_rows)

        revenue_30 = _sum(r.estimated_revenue for r in last_30)
        baseline_revenue = _sum(r.estimated_revenue for r in baseline_rows)
        # The baseline window is 60 days against the current 30, so it is halved to make
        # the comparison like for like.
        projected_revenue = (
            q2(baseline_revenue / Decimal("2")) if baseline_revenue else None
        )

        battery = self.telemetry.latest_battery(loan.vehicle_id)
        charging_30 = self.telemetry.charging_session_count(
            loan.vehicle_id,
            start=as_of - timedelta(days=LONG_WINDOW_DAYS),
            end=as_of,
        )
        charging_baseline = self.telemetry.charging_session_count(
            loan.vehicle_id,
            start=as_of - timedelta(days=BASELINE_END_DAYS),
            end=as_of - timedelta(days=BASELINE_START_DAYS),
        )

        return {
            "avg_daily_km_7d": avg_7,
            "avg_daily_km_30d": avg_30,
            "avg_daily_km_90d": _mean(r.daily_km for r in rows),
            "baseline_daily_km": baseline,
            "usage_change_percent": _pct_change(avg_7, baseline),
            "active_days_30d": sum(
                1 for r in last_30 if r.daily_km is not None and D(r.daily_km) > ZERO
            ),
            "zero_km_streak_days": _zero_streak(by_date, as_of),
            "avg_trips_7d": _mean(r.trip_count for r in last_7),
            "avg_trips_30d": _mean(r.trip_count for r in last_30),
            "charging_sessions_30d": charging_30,
            "charging_change_percent": _pct_change(
                Decimal(charging_30),
                # The baseline window is twice as long, so halve it.
                q2(Decimal(charging_baseline) / Decimal("2"))
                if charging_baseline
                else None,
            ),
            "estimated_revenue_30d": revenue_30,
            "revenue_change_percent": _pct_change(revenue_30, projected_revenue),
            "avg_route_deviation_percent": _mean(
                r.route_deviation_percent for r in last_30
            ),
            "latest_state_of_health": (
                D(battery.state_of_health)
                if battery and battery.state_of_health is not None
                else None
            ),
            # FR-6.6 — silence is a signal, never read as good news.
            #
            # The column is NOT NULL DEFAULT 0 (Doc 05 §5.3.29), so it cannot express
            # "never reported at all". A loan with no telemetry therefore records 0, the
            # schema's own default, rather than a number this service invented; the
            # silence rules would otherwise fire on every loan booked before its device
            # was fitted. The distinction is worth a column and is noted as a gap.
            "days_since_last_telemetry": (
                (as_of - max(by_date)).days if by_date else 0
            ),
            "days_past_due": int(loan.days_past_due or 0),
        }


def _mean(values: Any) -> Decimal | None:
    present = [D(v) for v in values if v is not None]
    return q2(sum(present, start=ZERO) / Decimal(len(present))) if present else None


def _sum(values: Any) -> Decimal | None:
    present = [D(v) for v in values if v is not None]
    return q2(sum(present, start=ZERO)) if present else None


def _pct_change(current: Decimal | None, baseline: Decimal | None) -> Decimal | None:
    """None when there is nothing to compare against.

    Returning zero for "no baseline" would read as "no change" and quietly suppress every
    usage rule on a loan too new to have one.
    """
    if current is None or baseline is None or baseline <= ZERO:
        return None
    return q2((current - baseline) / baseline * Decimal("100"))


def _zero_streak(by_date: dict[date, Any], as_of: date) -> int:
    """Consecutive zero-kilometre days ending yesterday.

    Counted backwards from the business date rather than as the longest streak in the
    window: a vehicle that stood still in June and is running now is not idle.
    """
    streak = 0
    cursor = as_of - timedelta(days=1)
    while cursor in by_date:
        row = by_date[cursor]
        if row.daily_km is not None and D(row.daily_km) > ZERO:
            break
        streak += 1
        cursor -= timedelta(days=1)
    return streak
