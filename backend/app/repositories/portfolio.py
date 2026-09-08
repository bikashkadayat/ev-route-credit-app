"""Portfolio aggregates and telemetry reads.

Loan row access lives in ``app.repositories.alert.LoanRepository`` because the alert
pipeline is its heaviest consumer; this module holds the read-side aggregates the
portfolio dashboard needs.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, select

from app.models.portfolio import BatteryMetric, Loan, RiskAlert, VehicleTelemetry
from app.repositories.base import BaseRepository


class PortfolioRepository(BaseRepository[Loan]):
    model = Loan

    def summary(self) -> dict[str, Any]:
        """KPI row for the portfolio dashboard (Doc 03 Screen 18)."""
        row = self.session.execute(
            select(
                func.count(Loan.id),
                func.coalesce(func.sum(Loan.principal_amount), 0),
                func.coalesce(func.sum(Loan.outstanding_principal), 0),
                func.coalesce(func.sum(Loan.overdue_amount), 0),
                func.count(Loan.id).filter(Loan.days_past_due > 30),
                func.count(Loan.id).filter(Loan.risk_status == "RED"),
                func.count(Loan.id).filter(Loan.risk_status == "YELLOW"),
                func.count(Loan.id).filter(Loan.risk_status == "GREEN"),
            ).where(Loan.status == "ACTIVE")
        ).one()

        outstanding = Decimal(row[2] or 0)
        npl_outstanding = Decimal(
            self.session.execute(
                select(func.coalesce(func.sum(Loan.outstanding_principal), 0)).where(
                    Loan.status == "ACTIVE", Loan.days_past_due > 30
                )
            ).scalar_one()
            or 0
        )
        par30 = (npl_outstanding / outstanding) if outstanding else Decimal("0")

        return {
            "active_loans": int(row[0]),
            "total_disbursed": Decimal(row[1] or 0),
            "total_outstanding": outstanding,
            "total_overdue": Decimal(row[3] or 0),
            "npl_count": int(row[4]),
            "red_alerts_loans": int(row[5]),
            "yellow_alerts_loans": int(row[6]),
            "green_loans": int(row[7]),
            "par30": par30.quantize(Decimal("0.0001")),
        }

    def by_risk_grade(self) -> Sequence[dict[str, Any]]:
        rows = self.session.execute(
            select(
                Loan.risk_grade,
                func.count(Loan.id),
                func.coalesce(func.sum(Loan.outstanding_principal), 0),
            )
            .where(Loan.status == "ACTIVE")
            .group_by(Loan.risk_grade)
            .order_by(Loan.risk_grade)
        ).all()
        return [
            {"grade": r[0], "count": int(r[1]), "outstanding": Decimal(r[2] or 0)}
            for r in rows
        ]

    def dpd_buckets(self) -> Sequence[dict[str, Any]]:
        """Doc 01 BR-4 buckets, computed in SQL so the API never loops over loans."""
        bucket = case(
            (Loan.days_past_due == 0, "0"),
            (Loan.days_past_due <= 30, "1-30"),
            (Loan.days_past_due <= 90, "31-90"),
            (Loan.days_past_due <= 180, "91-180"),
            else_="181+",
        )
        rows = self.session.execute(
            select(
                bucket.label("bucket"),
                func.count(Loan.id),
                func.coalesce(func.sum(Loan.outstanding_principal), 0),
            )
            .where(Loan.status == "ACTIVE")
            .group_by(bucket)
        ).all()
        order = {"0": 0, "1-30": 1, "31-90": 2, "91-180": 3, "181+": 4}
        return sorted(
            (
                {"bucket": r[0], "count": int(r[1]), "outstanding": Decimal(r[2] or 0)}
                for r in rows
            ),
            key=lambda d: order.get(str(d["bucket"]), 99),
        )

    def application_counts(self) -> dict[str, int]:
        """PRD §1.11 — the application KPI cards, counted in one pass by status."""
        from app.models.credit import LoanApplication

        rows = self.session.execute(
            select(LoanApplication.status, func.count(LoanApplication.id))
            .where(LoanApplication.deleted_at.is_(None))
            .group_by(LoanApplication.status)
        ).all()
        counts = {str(r[0]): int(r[1]) for r in rows}
        counts["total"] = sum(counts.values())
        return counts

    def route_class_distribution(self) -> Sequence[dict[str, Any]]:
        """Chart 3 — how the book is spread across corridor classes."""
        from app.models.catalog import Route

        rows = self.session.execute(
            select(Route.latest_grade, func.count(Route.id))
            .where(Route.deleted_at.is_(None))
            .where(Route.latest_grade.is_not(None))
            .group_by(Route.latest_grade)
            .order_by(Route.latest_grade)
        ).all()
        return [{"grade": r[0], "count": int(r[1])} for r in rows]

    def customer_grade_distribution(self) -> Sequence[dict[str, Any]]:
        """Chart 4 — the grade mix of the credit scores actually issued."""
        from app.models.credit import CreditScore

        rows = self.session.execute(
            select(CreditScore.grade, func.count(CreditScore.id))
            .where(CreditScore.is_latest.is_(True))
            .group_by(CreditScore.grade)
            .order_by(CreditScore.grade)
        ).all()
        return [{"grade": r[0], "count": int(r[1])} for r in rows]

    def open_alert_counts(self) -> dict[str, int]:
        rows = self.session.execute(
            select(RiskAlert.severity, func.count(RiskAlert.id))
            .where(RiskAlert.status.in_(("OPEN", "ACKNOWLEDGED", "IN_PROGRESS")))
            .group_by(RiskAlert.severity)
        ).all()
        counts = {"YELLOW": 0, "RED": 0}
        for severity, count in rows:
            counts[str(severity)] = int(count)
        return counts


class TelemetryRepository(BaseRepository[VehicleTelemetry]):
    model = VehicleTelemetry

    def series_for_vehicle(
        self, vehicle_id: int, *, start: date, end: date
    ) -> Sequence[VehicleTelemetry]:
        stmt = (
            select(VehicleTelemetry)
            .where(
                VehicleTelemetry.vehicle_id == vehicle_id,
                VehicleTelemetry.telemetry_date >= start,
                VehicleTelemetry.telemetry_date <= end,
            )
            .order_by(VehicleTelemetry.telemetry_date)
        )
        return self.session.execute(stmt).scalars().all()

    def charging_session_count(
        self, vehicle_id: int, *, start: date, end: date
    ) -> int:
        """Sessions in a window — the input to the charging-decline rules."""
        from app.models.portfolio import ChargingSession

        stmt = (
            select(func.count())
            .select_from(ChargingSession)
            .where(ChargingSession.vehicle_id == vehicle_id)
            .where(ChargingSession.session_date >= start)
            .where(ChargingSession.session_date < end)
        )
        return int(self.session.execute(stmt).scalar_one())

    def latest_battery(self, vehicle_id: int) -> BatteryMetric | None:
        stmt = (
            select(BatteryMetric)
            .where(BatteryMetric.vehicle_id == vehicle_id)
            .order_by(BatteryMetric.metric_date.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalar_one_or_none()
