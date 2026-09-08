"""Rule-catalogue coverage guards.

These exist because of a real defect found during implementation: USAGE_DROP_MODERATE was
bounded at -35% and USAGE_COLLAPSE at -50%, leaving a **15-point blind spot**. A vehicle
losing 38% of its usage — which is exactly the demo pack's distressed loan — raised no
alert at all. The bands are now contiguous, and these tests make sure nobody reopens the
hole by nudging a threshold.

The general principle: for any monitored signal with a graded YELLOW/RED pair, the union
of the bands must cover the whole deteriorating range with no gap and no silent overlap.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as Dc

import pytest

from app.risk.engine import actionable, evaluate_rules
from app.risk.loader import default_rules
from app.risk.models import MetricSnapshot

RULES = default_rules()
AS_OF = date(2026, 9, 8)


def _fired(**values) -> set[str]:
    snapshot = MetricSnapshot(
        loan_id=1, as_of=AS_OF,
        values={k: (None if v is None else Dc(str(v))) for k, v in values.items()},
    )
    return {o.rule_code for o in actionable(evaluate_rules(RULES, snapshot))}


# ---------------------------------------------------------------------------
# Usage decline — the band that was broken
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "pct", [-20, -25, -30, -34.9, -35, -38.4, -40, -45, -49.9, -50, -55, -70, -100]
)
def test_every_material_usage_decline_raises_exactly_one_alert(pct):
    fired = _fired(USAGE_CHANGE_PCT=pct, ACTIVE_DAYS_30D=19)
    usage_alerts = fired & {"USAGE_DROP_MODERATE", "USAGE_COLLAPSE"}
    assert len(usage_alerts) == 1, (
        f"usage decline of {pct}% raised {usage_alerts or 'nothing'}; the YELLOW and RED "
        f"bands must tile the range with exactly one alert at every point"
    )


@pytest.mark.parametrize("pct", [-19.9, -10, 0, 5, 20])
def test_immaterial_usage_change_raises_nothing(pct):
    assert not (_fired(USAGE_CHANGE_PCT=pct, ACTIVE_DAYS_30D=19)
                & {"USAGE_DROP_MODERATE", "USAGE_COLLAPSE"})


def test_the_distressed_demo_loan_raises_a_usage_alert():
    """Doc 16 §16.11 seeds AL-2026-000452 = USAGE_DROP_MODERATE on loan 087 at -38.4%.

    Under the original catalogue this alert was impossible to produce. It is the single
    clearest example of why band coverage needs a test rather than a review.
    """
    assert "USAGE_DROP_MODERATE" in _fired(USAGE_CHANGE_PCT=-38.4, ACTIVE_DAYS_30D=19)


def test_severity_escalates_at_the_collapse_boundary():
    assert _fired(USAGE_CHANGE_PCT=-49.99, ACTIVE_DAYS_30D=19) & {"USAGE_DROP_MODERATE"}
    assert _fired(USAGE_CHANGE_PCT=-50, ACTIVE_DAYS_30D=19) & {"USAGE_COLLAPSE"}


# ---------------------------------------------------------------------------
# The other graded pairs — proven contiguous, so they stay contiguous
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("pct", [-20, -30, -39.9, -40, -50, -80])
def test_revenue_decline_bands_are_contiguous(pct):
    fired = _fired(REVENUE_CHANGE_PCT=pct)
    assert len(fired & {"REVENUE_DECLINE", "REVENUE_COLLAPSE"}) == 1, (
        f"revenue decline of {pct}% is uncovered or double-covered"
    )


@pytest.mark.parametrize("pct", [-30, -45, -59.9, -60, -75, -100])
def test_charging_decline_bands_are_contiguous(pct):
    fired = _fired(CHARGING_CHANGE_PCT=pct)
    assert len(fired & {"CHARGING_DROP_MODERATE", "CHARGING_DROP_SEVERE"}) == 1


@pytest.mark.parametrize("dpd", [1, 15, 30, 31, 60, 90, 91, 200])
def test_arrears_bands_cover_every_delinquent_day(dpd):
    fired = _fired(DAYS_PAST_DUE=dpd)
    arrears = fired & {"EMI_OVERDUE_1_30", "EMI_OVERDUE_30_PLUS", "EMI_OVERDUE_90_PLUS"}
    assert arrears, f"{dpd} days past due raised no arrears alert"


@pytest.mark.parametrize("streak", [3, 4, 6, 7, 14])
def test_inactivity_bands_cover_every_streak_length(streak):
    fired = _fired(ZERO_KM_STREAK=streak)
    assert fired & {"VEHICLE_INACTIVE_3D", "VEHICLE_INACTIVE_7D"}


@pytest.mark.parametrize("soh", [69.9, 70, 75, 79.99, 80.01])
def test_battery_bands_have_no_gap_below_eighty(soh):
    fired = _fired(BATTERY_SOH=soh)
    battery = fired & {"BATTERY_HEALTH_LOW", "BATTERY_HEALTH_CRITICAL"}
    if soh < 80:
        assert len(battery) == 1, f"SOH {soh}% is uncovered or double-covered"
    else:
        assert not battery


@pytest.mark.parametrize("days", [2, 3, 4, 5, 10])
def test_telemetry_silence_bands_cover_every_gap_length(days):
    assert _fired(DAYS_SINCE_TELEMETRY=days) & {"TELEMETRY_SILENT_2D", "TELEMETRY_SILENT_5D"}


@pytest.mark.parametrize("pct", [30, 45, 59.99, 60, 90])
def test_route_deviation_bands_are_contiguous(pct):
    fired = _fired(ROUTE_DEVIATION_PCT=pct)
    assert fired & {"ROUTE_DEVIATION_MODERATE", "ROUTE_ABANDONED"}
