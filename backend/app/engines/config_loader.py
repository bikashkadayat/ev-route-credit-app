"""Loading a ScoringConfig from its canonical JSON definition or from database rows.

The JSON files in ``config_defaults/`` are the **single source of truth** for the shipped
scorecards. The seeding CLI inserts them into ``scoring_configurations`` /
``scoring_config_components``; the engine tests load them directly.
``tests/unit/engines/test_config_defaults.py`` asserts that the SQL seed carries the same
weights, so the two cannot drift.

Versioning
----------
Every published scorecard version keeps its own file. ``ACTIVE_VERSION`` names the one
that is currently live; superseded versions stay on disk so a historical score can be
recomputed and byte-matched (Doc 02 §2.4.4). ROUTE v1 → v2 is the worked example: the
Class A boundary moved from 80 to 88 after a calibration review, with every component
weight and curve unchanged.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.engines.base import ScoringConfig

CONFIG_DEFAULTS_DIR = Path(__file__).parent / "config_defaults"

#: Every scorecard version shipped with the product, oldest first.
AVAILABLE_VERSIONS: dict[str, dict[int, str]] = {
    "ROUTE": {1: "route_v1.json", 2: "route_v2.json"},
    "CUSTOMER": {1: "customer_v1.json"},
    "FINAL": {1: "final_v1.json"},
}

#: The version that is ACTIVE on a fresh install.
ACTIVE_VERSION: dict[str, int] = {"ROUTE": 2, "CUSTOMER": 1, "FINAL": 1}

#: Convenience map used by the seed generator and the drift test.
DEFAULT_FILES: dict[str, str] = {
    config_type: AVAILABLE_VERSIONS[config_type][version]
    for config_type, version in ACTIVE_VERSION.items()
}


def load_definition(config_type: str, version: int | None = None) -> dict[str, Any]:
    """Read one scorecard definition. ``version=None`` means the active one."""
    key = config_type.upper()
    if key not in AVAILABLE_VERSIONS:
        raise KeyError(f"No configuration for type {config_type!r}")
    resolved = ACTIVE_VERSION[key] if version is None else version
    filename = AVAILABLE_VERSIONS[key].get(resolved)
    if filename is None:
        raise KeyError(f"{key} has no version {resolved}")
    data: dict[str, Any] = json.loads(
        (CONFIG_DEFAULTS_DIR / filename).read_text(encoding="utf-8")
    )
    return data


def load_default_definition(config_type: str) -> dict[str, Any]:
    """Backwards-compatible alias for the active definition."""
    return load_definition(config_type)


def build_config(definition: dict[str, Any], config_id: int | None = None) -> ScoringConfig:
    return ScoringConfig.from_rows(
        id=config_id,
        config_type=definition["config_type"],
        version_no=definition["version_no"],
        name=definition["name"],
        grade_thresholds=definition["grade_thresholds"],
        components=definition["components"],
        decision_rules=definition.get("decision_rules"),
    )


@lru_cache(maxsize=16)
def default_config(config_type: str, version: int | None = None) -> ScoringConfig:
    """The shipped scorecard for a config type. Cached; ScoringConfig is frozen."""
    return build_config(load_definition(config_type, version))


def config_from_db_rows(config_row: Any, component_rows: list[Any]) -> ScoringConfig:
    """Build a ScoringConfig from ORM rows (used by ConfigService)."""
    return ScoringConfig.from_rows(
        id=config_row.id,
        config_type=(
            config_row.config_type.value
            if hasattr(config_row.config_type, "value")
            else config_row.config_type
        ),
        version_no=config_row.version_no,
        name=config_row.name,
        grade_thresholds=config_row.grade_thresholds,
        components=[
            {
                "component_code": c.component_code,
                "label": c.label,
                "weight": c.weight,
                "display_order": c.display_order,
                "scoring_rules": c.scoring_rules,
                "description": c.description,
                "is_active": c.is_active,
            }
            for c in component_rows
        ],
        decision_rules=config_row.decision_rules,
    )
