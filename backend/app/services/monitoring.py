"""MonitoringService — per-loan telemetry view and the dashboard KPIs.

Doc 06 §6.8 `/loans/{id}/monitoring` and §6.9 `/dashboard/summary`, PRD §1.11.

The derived figures — 7- and 30-day baselines, percentage change, inferred revenue — are
computed here from stored daily aggregates rather than in SQL, because the rule engine
consumes the same numbers and the two must not drift. What SQL does own is the aggregation
across the book: a dashboard that loaded every loan into Python would stop working at the
first thousand.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.core.types import ZERO, D, q2
from app.repositories.alert import AlertRepository, MonitoringSnapshotRepository
from app.repositories.loan import LoanBookRepository
from app.repositories.portfolio import PortfolioRepository, TelemetryRepository
from app.risk.behaviour import behaviour_band, behaviour_score

DEFAULT_WINDOW_DAYS = 90


@dataclass(frozen=True, slots=True)
class UsageTrend:
    """The derived picture the monitoring tab shows — FR-6.3, FR-6.4."""

    avg_daily_km_7d: Decimal | None
    avg_daily_km_30d: Decimal | None
    baseline_daily_km: Decimal | None
    usage_change_percent: Decimal | None
    active_days_30d: int
    zero_km_streak_days: int
    charging_sessions_30d: int
    charging_change_percent: Decimal | None
    estimated_revenue_30d: Decimal | None
    latest_state_of_health: Decimal | None
    days_since_last_telemetry: int | None
    behaviour_score: Decimal | None
    behaviour_band: str | None


class MonitoringService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.loans = LoanBookRepository(session)
        self.telemetry = TelemetryRepository(session)
        self.snapshots = MonitoringSnapshotRepository(session)
        self.alerts = AlertRepository(session)
        self.portfolio = PortfolioRepository(session)
        self._metrics: Any = None

    # -- per-loan ----------------------------------------------------------
    def loan_monitoring(
        self,
        loan_id: int,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict[str, Any]:
        """Doc 06 §6.8 — the telemetry series plus the derived trends."""
        loan = self.loans.get(loan_id)
        if loan is None:
            raise NotFoundError("loan", loan_id)

        end = date_to or datetime.now(UTC).date()
        start = date_from or (end - timedelta(days=DEFAULT_WINDOW_DAYS))

        series = self.telemetry.series_for_vehicle(loan.vehicle_id, start=start, end=end)
        snapshot = self.snapshots.latest_for_loan(loan.id, end)

        # Reuse the alert pipeline's own snapshot builder so the behaviour score shown
        # here is the one the rules evaluated, not a second reading of the same data.
        from app.services.alert import AlertService

        self._metrics = AlertService(self.session).build_snapshot(loan, end)
        trend = self._trend(series, snapshot, end)

        return {
            "loan_id": loan.uuid,
            "from": start,
            "to": end,
            "baseline_daily_km": trend.baseline_daily_km,
            "current": trend,
            "series": [
                {
                    "date": row.telemetry_date,
                    "daily_km": row.daily_km,
                    "trip_count": row.trip_count,
                    "active_hours": row.active_hours,
                    "idle_minutes": row.idle_minutes,
                    "avg_speed_kmph": row.avg_speed_kmph,
                    "route_deviation_percent": row.route_deviation_percent,
                    "estimated_revenue": row.estimated_revenue,
                }
                for row in series
            ],
        }

    def _trend(self, series: Any, snapshot: Any, as_of: date) -> UsageTrend:
        """FR-6.3/6.4 — rolling windows over the stored daily aggregates.

        Computed from the series rather than read off the snapshot so the endpoint still
        answers for a loan the nightly job has not reached yet; where a snapshot exists,
        the same arithmetic produced it.
        """
        rows = list(series)
        last_7 = [r for r in rows if (as_of - r.telemetry_date).days < 7]
        last_30 = [r for r in rows if (as_of - r.telemetry_date).days < 30]
        baseline_window = [r for r in rows if 30 <= (as_of - r.telemetry_date).days < 90]

        avg_7 = _mean(r.daily_km for r in last_7)
        avg_30 = _mean(r.daily_km for r in last_30)
        baseline = _mean(r.daily_km for r in baseline_window) or (
            D(snapshot.baseline_daily_km) if snapshot and snapshot.baseline_daily_km else None
        )

        change = None
        if baseline and baseline > ZERO and avg_7 is not None:
            change = q2((avg_7 - baseline) / baseline * Decimal("100"))

        active_days = sum(1 for r in last_30 if r.daily_km and D(r.daily_km) > ZERO)
        revenue_30 = _total(r.estimated_revenue for r in last_30)

        score = None
        band = None
        if self._metrics is not None:
            # The behaviour score is a pure function of the metric snapshot; the service
            # only assembles that snapshot, exactly as the alert pipeline does.
            score = behaviour_score(self._metrics)
            band = behaviour_band(score)

        return UsageTrend(
            avg_daily_km_7d=avg_7,
            avg_daily_km_30d=avg_30,
            baseline_daily_km=baseline,
            usage_change_percent=change,
            active_days_30d=active_days,
            zero_km_streak_days=self._zero_streak(rows, as_of),
            charging_sessions_30d=(
                int(snapshot.charging_sessions_30d) if snapshot else 0
            ),
            charging_change_percent=(
                D(snapshot.charging_change_percent)
                if snapshot and snapshot.charging_change_percent is not None
                else None
            ),
            estimated_revenue_30d=revenue_30,
            latest_state_of_health=(
                D(snapshot.latest_state_of_health)
                if snapshot and snapshot.latest_state_of_health is not None
                else None
            ),
            days_since_last_telemetry=(
                (as_of - rows[-1].telemetry_date).days if rows else None
            ),
            behaviour_score=score,
            behaviour_band=band,
        )

    @staticmethod
    def _zero_streak(rows: list[Any], as_of: date) -> int:
        """Consecutive zero-kilometre days ending at the business date.

        Counted backwards from today rather than as the longest streak anywhere in the
        window: a vehicle that stood still in June and is running now is not idle.
        """
        by_date = {r.telemetry_date: r for r in rows}
        streak = 0
        cursor = as_of - timedelta(days=1)
        while cursor in by_date:
            row = by_date[cursor]
            if row.daily_km is not None and D(row.daily_km) > ZERO:
                break
            streak += 1
            cursor -= timedelta(days=1)
        return streak

    # -- dashboard ---------------------------------------------------------
    def dashboard_summary(self, *, as_of: date | None = None) -> dict[str, Any]:
        """PRD §1.11 — the ten KPI cards and the distribution charts.

        Every figure is aggregated in SQL, so response time does not grow with the book.
        """
        business_date = as_of or datetime.now(UTC).date()
        kpis = self.portfolio.summary()
        applications = self.portfolio.application_counts()
        alert_counts = self.portfolio.open_alert_counts()

        return {
            "as_of": business_date,
            "kpis": {
                "total_applications": applications.get("total", 0),
                "approved": applications.get("APPROVED", 0),
                "rejected": applications.get("REJECTED", 0),
                "manual_reviews": applications.get("PENDING_DECISION", 0),
                "active_loans": kpis["active_loans"],
                "total_portfolio_value": kpis["total_disbursed"],
                "outstanding_amount": kpis["total_outstanding"],
                "overdue_amount": kpis["total_overdue"],
                "yellow_alerts": alert_counts.get("YELLOW", 0),
                "red_alerts": alert_counts.get("RED", 0),
            },
            "portfolio_by_grade": list(self.portfolio.by_risk_grade()),
            "dpd_buckets": list(self.portfolio.dpd_buckets()),
            "route_class_distribution": list(self.portfolio.route_class_distribution()),
            "customer_grade_distribution": list(
                self.portfolio.customer_grade_distribution()
            ),
            "par30": kpis["par30"],
        }


def _mean(values: Any) -> Decimal | None:
    present = [D(v) for v in values if v is not None]
    return q2(sum(present, start=ZERO) / Decimal(len(present))) if present else None


def _total(values: Any) -> Decimal | None:
    present = [D(v) for v in values if v is not None]
    return q2(sum(present, start=ZERO)) if present else None


