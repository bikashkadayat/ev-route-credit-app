"""Signal families and supersession — Doc 08 §8.6.1.

Rules within one family describe the same underlying phenomenon at different intensities.
When a higher-severity member fires, live lower-severity members are closed as SUPERSEDED
so the officer's queue shows one actionable item and the history still shows the
escalation path.
"""

from __future__ import annotations

from collections.abc import Mapping

#: family -> members, ascending intensity
FAMILIES: Mapping[str, tuple[str, ...]] = {
    "REPAYMENT_DELINQUENCY": (
        "EMI_OVERDUE_1_30", "EMI_OVERDUE_30_PLUS", "EMI_OVERDUE_90_PLUS",
    ),
    "VEHICLE_INACTIVITY": ("VEHICLE_INACTIVE_3D", "VEHICLE_INACTIVE_7D"),
    "USAGE_DECLINE": ("USAGE_DROP_MODERATE", "USAGE_COLLAPSE"),
    "REVENUE_DECLINE": ("REVENUE_DECLINE", "REVENUE_COLLAPSE"),
    "BATTERY_DEGRADATION": ("BATTERY_HEALTH_LOW", "BATTERY_HEALTH_CRITICAL"),
    "ROUTE_DEVIATION": ("ROUTE_DEVIATION_MODERATE", "ROUTE_ABANDONED"),
    "TELEMETRY_GAP": ("TELEMETRY_SILENT_2D", "TELEMETRY_SILENT_5D"),
    "CHARGING_DECLINE": ("CHARGING_DROP_MODERATE", "CHARGING_DROP_SEVERE"),
}

#: rule_code -> (family, intensity index)
_MEMBERSHIP: dict[str, tuple[str, int]] = {
    code: (family, index)
    for family, members in FAMILIES.items()
    for index, code in enumerate(members)
}


def family_of(rule_code: str) -> str | None:
    entry = _MEMBERSHIP.get(rule_code)
    return entry[0] if entry else None


def intensity_of(rule_code: str) -> int:
    """Higher means more intense. Rules outside any family rank 0."""
    entry = _MEMBERSHIP.get(rule_code)
    return entry[1] if entry else 0


def supersedes(candidate: str, incumbent: str) -> bool:
    """True when ``candidate`` should close ``incumbent`` as SUPERSEDED."""
    a, b = _MEMBERSHIP.get(candidate), _MEMBERSHIP.get(incumbent)
    if a is None or b is None:
        return False
    return a[0] == b[0] and a[1] > b[1]
