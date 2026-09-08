"""Metric provider registry — Doc 08 §8.3.

Nineteen metrics. Adding a twentieth is a provider function plus a seed row: no engine
change, no migration (Doc 08: "Adding a metric = write one provider function + register
it + insert a seed row").

Providers are **pure functions of a raw monitoring row**. Fetching that row is the only
part of monitoring that touches the database, and it lives in the service layer.

Null semantics: a provider returning ``None`` means "unknown", and the engine treats an
unknown metric as a condition that does not fire.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.core.types import ZERO, D, safe_div
from app.risk.models import MetricSnapshot


@dataclass(frozen=True, slots=True)
class LoanSnapshotRow:
    """Raw monitoring inputs for one loan on one business date.

    Mirrors `loan_monitoring_snapshots` joined to `loans` (Doc 05 §5.3.31). Every field is
    optional because a loan in its first month has no baseline and a vehicle with no
    device has no telemetry.
    """

    loan_id: int
    as_of: date

    # repayment
    days_past_due: int | None = None
    consecutive_missed_emi: int | None = None
    overdue_amount: Decimal | None = None
    partial_payment_count_90d: int | None = None
    emi_amount: Decimal | None = None

    # usage
    avg_daily_km_7d: Decimal | None = None
    avg_daily_km_30d: Decimal | None = None
    baseline_daily_km: Decimal | None = None
    active_days_30d: int | None = None
    zero_km_streak_days: int | None = None

    # revenue
    estimated_revenue_30d: Decimal | None = None
    projected_revenue_30d: Decimal | None = None
    monthly_net_contribution: Decimal | None = None

    # charging / battery
    charging_sessions_30d: int | None = None
    baseline_charging_sessions_30d: Decimal | None = None
    latest_state_of_health: Decimal | None = None
    min_state_of_charge_7d: Decimal | None = None

    # route / data quality
    avg_route_deviation_percent: Decimal | None = None
    days_since_last_telemetry: int | None = None
    maintenance_downtime_30d: int | None = None

    extras: Mapping[str, Decimal | None] = field(default_factory=dict)


MetricProvider = Callable[[LoanSnapshotRow], Decimal | None]
METRIC_REGISTRY: dict[str, MetricProvider] = {}


def metric(code: str) -> Callable[[MetricProvider], MetricProvider]:
    def wrapper(fn: MetricProvider) -> MetricProvider:
        if code in METRIC_REGISTRY:
            raise ValueError(f"Metric {code!r} is already registered")
        METRIC_REGISTRY[code] = fn
        return fn
    return wrapper


def _d(value: object | None) -> Decimal | None:
    return None if value is None else D(value)


def _pct_change(current: Decimal | None, baseline: Decimal | None) -> Decimal | None:
    """Percentage change against a baseline. Negative means decline."""
    if current is None or baseline is None or baseline == ZERO:
        return None
    return (current - baseline) / baseline * Decimal("100")


# ---------------------------------------------------------------------------
# Repayment
# ---------------------------------------------------------------------------
@metric("DAYS_PAST_DUE")
def days_past_due(row: LoanSnapshotRow) -> Decimal | None:
    return _d(row.days_past_due)


@metric("CONSECUTIVE_MISSED_EMI")
def consecutive_missed_emi(row: LoanSnapshotRow) -> Decimal | None:
    return _d(row.consecutive_missed_emi)


@metric("OVERDUE_AMOUNT")
def overdue_amount(row: LoanSnapshotRow) -> Decimal | None:
    return _d(row.overdue_amount)


@metric("PARTIAL_PAYMENT_COUNT_90D")
def partial_payment_count_90d(row: LoanSnapshotRow) -> Decimal | None:
    return _d(row.partial_payment_count_90d)


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
@metric("USAGE_CHANGE_PCT")
def usage_change_pct(row: LoanSnapshotRow) -> Decimal | None:
    """7-day average against the established baseline. No baseline ⇒ no signal."""
    return _pct_change(row.avg_daily_km_7d, row.baseline_daily_km)


@metric("USAGE_CHANGE_PCT_30D")
def usage_change_pct_30d(row: LoanSnapshotRow) -> Decimal | None:
    return _pct_change(row.avg_daily_km_30d, row.baseline_daily_km)


@metric("ZERO_KM_STREAK")
def zero_km_streak(row: LoanSnapshotRow) -> Decimal | None:
    return _d(row.zero_km_streak_days)


@metric("ACTIVE_DAYS_30D")
def active_days_30d(row: LoanSnapshotRow) -> Decimal | None:
    return _d(row.active_days_30d)


@metric("AVG_DAILY_KM_7D")
def avg_daily_km_7d(row: LoanSnapshotRow) -> Decimal | None:
    return _d(row.avg_daily_km_7d)


# ---------------------------------------------------------------------------
# Revenue
# ---------------------------------------------------------------------------
@metric("REVENUE_CHANGE_PCT")
def revenue_change_pct(row: LoanSnapshotRow) -> Decimal | None:
    """Inferred 30-day revenue against the projection made at underwriting."""
    return _pct_change(row.estimated_revenue_30d, row.projected_revenue_30d)


@metric("DSCR_ACTUAL")
def dscr_actual(row: LoanSnapshotRow) -> Decimal | None:
    if row.monthly_net_contribution is None or not row.emi_amount:
        return None
    return safe_div(row.monthly_net_contribution, row.emi_amount)


# ---------------------------------------------------------------------------
# Charging and battery
# ---------------------------------------------------------------------------
@metric("CHARGING_CHANGE_PCT")
def charging_change_pct(row: LoanSnapshotRow) -> Decimal | None:
    return _pct_change(_d(row.charging_sessions_30d), row.baseline_charging_sessions_30d)


@metric("CHARGING_SESSIONS_30D")
def charging_sessions_30d(row: LoanSnapshotRow) -> Decimal | None:
    return _d(row.charging_sessions_30d)


@metric("BATTERY_SOH")
def battery_soh(row: LoanSnapshotRow) -> Decimal | None:
    return _d(row.latest_state_of_health)


@metric("BATTERY_MIN_SOC_7D")
def battery_min_soc_7d(row: LoanSnapshotRow) -> Decimal | None:
    return _d(row.min_state_of_charge_7d)


# ---------------------------------------------------------------------------
# Route and data quality
# ---------------------------------------------------------------------------
@metric("ROUTE_DEVIATION_PCT")
def route_deviation_pct(row: LoanSnapshotRow) -> Decimal | None:
    return _d(row.avg_route_deviation_percent)


@metric("DAYS_SINCE_TELEMETRY")
def days_since_telemetry(row: LoanSnapshotRow) -> Decimal | None:
    return _d(row.days_since_last_telemetry)


@metric("MAINTENANCE_DOWNTIME_30D")
def maintenance_downtime_30d(row: LoanSnapshotRow) -> Decimal | None:
    return _d(row.maintenance_downtime_30d)


@metric("LTV_CURRENT")
def ltv_current(row: LoanSnapshotRow) -> Decimal | None:
    """Phase 2 — needs a depreciation model. Present so rules can reference it."""
    return _d(row.extras.get("LTV_CURRENT"))


# ---------------------------------------------------------------------------
# Snapshot assembly
# ---------------------------------------------------------------------------
def build_metric_snapshot(row: LoanSnapshotRow) -> MetricSnapshot:
    """Run every registered provider once. Pure."""
    values: dict[str, Decimal | None] = {}
    for code, provider in METRIC_REGISTRY.items():
        values[code] = provider(row)
    return MetricSnapshot(loan_id=row.loan_id, as_of=row.as_of, values=values)


def registered_metrics() -> tuple[str, ...]:
    return tuple(sorted(METRIC_REGISTRY))
