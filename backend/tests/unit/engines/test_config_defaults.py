"""Configuration integrity and anti-drift — Doc 01 FR-9.1, Doc 05 §5.3.16.

Two guarantees:
  1. A configuration whose weights do not sum to exactly 100% cannot be constructed.
  2. The canonical JSON defaults and the SQL seed cannot drift apart.
"""

from __future__ import annotations

import itertools
import json
import pathlib
import re
from decimal import Decimal as Dc

import pytest

from app.engines.base import ConfigurationError, ScoringConfig
from app.engines.config_loader import (
    CONFIG_DEFAULTS_DIR,
    DEFAULT_FILES,
    default_config,
    load_default_definition,
)

SEED_SQL = (
    pathlib.Path(__file__).resolve().parents[4] / "db" / "seed" / "01_reference_data.sql"
)


@pytest.mark.parametrize("config_type", sorted(DEFAULT_FILES))
def test_default_configuration_weights_sum_to_one(config_type):
    config = default_config(config_type)
    total = sum(c.weight for c in config.active_components)
    assert total == Dc("1.0000"), f"{config_type} weights sum to {total}"


@pytest.mark.parametrize("config_type", sorted(DEFAULT_FILES))
def test_default_configuration_grade_bands_cover_zero_to_one_hundred(config_type):
    config = default_config(config_type)
    bands = sorted(config.grade_bands, key=lambda b: b.min)
    assert bands[0].min == Dc("0")
    assert bands[-1].max == Dc("100")
    for lower, upper in itertools.pairwise(bands):
        assert lower.max < upper.min


def test_publishing_weights_that_do_not_total_one_hundred_is_refused():
    """Doc 06 §6.11 WEIGHTS_NOT_100 — the same rule the API and the DB trigger enforce."""
    definition = load_default_definition("ROUTE")
    broken = [dict(c) for c in definition["components"]]
    broken[0]["weight"] = 0.25  # 30% -> 25%, total becomes 0.95

    with pytest.raises(ConfigurationError) as exc:
        ScoringConfig.from_rows(
            id=None, config_type="ROUTE", version_no=99, name="broken",
            grade_thresholds=definition["grade_thresholds"], components=broken,
        )
    assert "0.9500" in str(exc.value)


def test_inactive_components_are_excluded_from_the_weight_sum():
    definition = load_default_definition("ROUTE")
    components = [dict(c) for c in definition["components"]]
    components.append({
        "component_code": "RETIRED_COMPONENT", "label": "Retired", "weight": 0.2,
        "display_order": 9, "is_active": False,
        "scoring_rules": {"type": "PASSTHROUGH", "input": "route_score"},
    })
    config = ScoringConfig.from_rows(
        id=None, config_type="ROUTE", version_no=2, name="with retired component",
        grade_thresholds=definition["grade_thresholds"], components=components,
    )
    assert len(config.active_components) == 5


def test_overlapping_grade_bands_are_refused():
    definition = load_default_definition("ROUTE")
    with pytest.raises(ConfigurationError, match="overlap"):
        ScoringConfig.from_rows(
            id=None, config_type="ROUTE", version_no=3, name="overlapping",
            grade_thresholds=[
                {"grade": "A", "min": 60, "max": 100, "label": "A", "risk_level": "LOW"},
                {"grade": "B", "min": 0, "max": 79.99, "label": "B", "risk_level": "MED"},
            ],
            components=definition["components"],
        )


def test_a_configuration_with_no_components_is_refused():
    with pytest.raises(ConfigurationError, match="no components"):
        ScoringConfig.from_rows(
            id=None, config_type="ROUTE", version_no=4, name="empty",
            grade_thresholds=[{"grade": "A", "min": 0, "max": 100, "label": "A",
                               "risk_level": "LOW"}],
            components=[],
        )


# ---------------------------------------------------------------------------
# Anti-drift: the JSON defaults and the SQL seed must agree
# ---------------------------------------------------------------------------
def test_seed_sql_exists():
    assert SEED_SQL.exists(), f"reference seed not found at {SEED_SQL}"


@pytest.mark.parametrize("config_type", sorted(DEFAULT_FILES))
def test_sql_seed_carries_the_same_component_weights(config_type):
    """The SQL seed is a psql-only restore path for the same configuration.

    If someone edits a weight in one place and not the other, the deployed scorecard
    depends on how the database was populated. This test makes that impossible.
    """
    sql = SEED_SQL.read_text(encoding="utf-8")
    definition = load_default_definition(config_type)

    for component in definition["components"]:
        code = component["component_code"]
        weight = Dc(str(component["weight"])).quantize(Dc("0.0001"))
        pattern = rf"\('{re.escape(code)}','[^']*',\s*({weight}),"
        assert re.search(pattern, sql), (
            f"{config_type} component {code} has weight {weight} in "
            f"{DEFAULT_FILES[config_type]} but that weight was not found in the SQL seed"
        )


def test_default_json_files_are_valid_json_and_declare_their_type():
    for config_type, filename in DEFAULT_FILES.items():
        data = json.loads((CONFIG_DEFAULTS_DIR / filename).read_text(encoding="utf-8"))
        assert data["config_type"] == config_type
        assert data["version_no"] >= 1
        assert data["components"]


def test_config_rule_lookup_reads_dotted_paths(final_config):
    assert final_config.rule("approve.min_final_score") == 75
    assert final_config.rule("structuring.absolute_ltv_ceiling") == 80
    assert final_config.rule("nonexistent.path") is None
    assert final_config.decimal_rule("approve.min_dscr", Dc("0")) == Dc("1.3")
