"""Telematics adapter contract — Doc 04 §4.11.

The platform consumes **daily aggregates**, not raw second-by-second pings: they answer
every credit question at a fraction of the cost, storage and privacy exposure
(Doc 13 §13.7).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

MAX_DAILY_KM = Decimal("800")
MAX_ACTIVE_HOURS = Decimal("24")


class TelematicsUnavailable(RuntimeError):
    """Provider unreachable. Monitoring degrades; it never scores a vehicle as healthy."""


class InvalidTelemetry(ValueError):
    """A record failed plausibility validation and must not be persisted."""


@dataclass(frozen=True, slots=True)
class TelemetryRecord:
    vehicle_id: int
    telemetry_date: date
    daily_km: Decimal
    trip_count: int
    active_hours: Decimal
    idle_minutes: int
    route_deviation_percent: Decimal
    avg_speed_kmph: Decimal | None = None
    max_speed_kmph: Decimal | None = None
    harsh_braking_count: int = 0
    night_driving_hours: Decimal = Decimal("0")
    geofence_exits: int = 0
    estimated_revenue: Decimal | None = None
    data_source: str = "MOCK"
    is_estimated: bool = False

    def validate(self) -> None:
        """Doc 14 story G-02 — implausible values are rejected, not stored."""
        if self.daily_km < 0 or self.daily_km > MAX_DAILY_KM:
            raise InvalidTelemetry(
                f"daily_km {self.daily_km} outside 0-{MAX_DAILY_KM} for vehicle "
                f"{self.vehicle_id} on {self.telemetry_date}"
            )
        if self.active_hours < 0 or self.active_hours > MAX_ACTIVE_HOURS:
            raise InvalidTelemetry(f"active_hours {self.active_hours} outside 0-24")
        if not (0 <= self.route_deviation_percent <= 100):
            raise InvalidTelemetry(
                f"route_deviation_percent {self.route_deviation_percent} outside 0-100"
            )
        if self.trip_count < 0:
            raise InvalidTelemetry("trip_count cannot be negative")


@dataclass(frozen=True, slots=True)
class BatteryRecord:
    vehicle_id: int
    metric_date: date
    avg_state_of_charge: Decimal | None
    min_state_of_charge: Decimal | None
    state_of_health: Decimal | None
    energy_consumed_kwh: Decimal
    charge_cycles: Decimal = Decimal("0")
    estimated_range_km: int | None = None

    def validate(self) -> None:
        for name, value in (
            ("avg_state_of_charge", self.avg_state_of_charge),
            ("min_state_of_charge", self.min_state_of_charge),
            ("state_of_health", self.state_of_health),
        ):
            if value is not None and not (0 <= value <= 100):
                raise InvalidTelemetry(f"{name} {value} outside 0-100")


@dataclass(frozen=True, slots=True)
class ChargingSessionRecord:
    vehicle_id: int
    session_date: date
    energy_delivered_kwh: Decimal
    duration_minutes: int
    charger_type: str
    soc_start: Decimal | None = None
    soc_end: Decimal | None = None
    is_home_charging: bool = False


@dataclass(frozen=True, slots=True)
class DailyTelemetryBundle:
    telemetry: tuple[TelemetryRecord, ...] = ()
    battery: tuple[BatteryRecord, ...] = ()
    charging: tuple[ChargingSessionRecord, ...] = ()
    missing_vehicle_ids: tuple[int, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)


class TelematicsProvider(ABC):
    name: str

    @abstractmethod
    async def fetch_daily(
        self, *, vehicle_ids: Sequence[int], on_date: date
    ) -> DailyTelemetryBundle:
        """Return one day of aggregates, or raise TelematicsUnavailable."""
