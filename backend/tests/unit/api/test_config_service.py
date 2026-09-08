"""ConfigService resolution and caching — Doc 02 §4.13.

The service decides *which* scorecard the engines run against. Getting that wrong is worse
than a crash: it silently scores applications against unpublished policy. These tests use
stub repositories, so the resolution rules are pinned independently of any database.
"""

from __future__ import annotations

import dataclasses
import re
from types import SimpleNamespace

import pytest

from app.core.errors import ConfigurationNotPublishedError, NotFoundError
from app.engines.config_loader import load_definition
from app.services import config as config_module
from app.services.config import ConfigService, clear_config_cache


def _component_rows(config_type: str, version: int) -> list[SimpleNamespace]:
    """Component rows shaped like the seeded ones, taken from the shipped default so the
    weights genuinely sum to 100 and ScoringConfig validation runs for real."""
    definition = load_definition(config_type, version)
    return [
        SimpleNamespace(
            component_code=c["component_code"],
            label=c["label"],
            weight=c["weight"],
            display_order=c["display_order"],
            scoring_rules=c["scoring_rules"],
            description=c.get("description"),
            is_active=True,
        )
        for c in definition["components"]
    ]


def _config_row(config_type: str, version_no: int, status: str = "ACTIVE") -> SimpleNamespace:
    definition = load_definition(config_type, version_no)
    return SimpleNamespace(
        id=version_no,
        config_type=config_type,
        version_no=version_no,
        name=definition["name"],
        status=status,
        grade_thresholds=definition["grade_thresholds"],
        decision_rules=definition.get("decision_rules"),
    )


class StubConfigRepo:
    def __init__(self, rows, components):
        self.rows = list(rows)
        self.components = components
        self.components_calls = 0

    def active(self, config_type):
        return next(
            (r for r in self.rows
             if r.config_type == config_type and r.status == "ACTIVE"),
            None,
        )

    def list_all(self, config_type=None):
        return [r for r in self.rows if config_type is None or r.config_type == config_type]

    def by_version(self, config_type, version_no):
        return next(
            (r for r in self.rows
             if r.config_type == config_type and r.version_no == version_no),
            None,
        )

    def components_for(self, config_id):
        self.components_calls += 1
        return self.components(config_id)


class StubRuleRepo:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def active_rules(self):
        return self.rows

    def all_rules(self):
        return self.rows


def service_with(rows, *, components=None, rules=()):
    """Build a ConfigService bypassing __init__ so no Session is required."""
    service = ConfigService.__new__(ConfigService)
    service.session = None
    repo = StubConfigRepo(rows, components or (lambda _cid: []))
    service.configs = repo
    service.rules = StubRuleRepo(rules)
    return service, repo


def route_components(config_id):
    return _component_rows("ROUTE", int(config_id))


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_config_cache()
    yield
    clear_config_cache()


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------
def test_an_unseeded_database_falls_back_to_the_shipped_default():
    """A fresh CI database has no rows at all; the engines must still be runnable."""
    service, _ = service_with([])
    assert service.active_config("ROUTE").version_no == 2


def test_a_seeded_database_wins_over_the_shipped_default():
    service, repo = service_with([_config_row("ROUTE", 2)], components=route_components)
    service.active_config("ROUTE")
    assert repo.components_calls == 1, "the database row must be the source, not the JSON"


def test_versions_exist_but_none_active_is_a_409_not_a_silent_default():
    """The dangerous case. Falling back here would score against policy nobody published."""
    service, _ = service_with([_config_row("ROUTE", 1, status="ARCHIVED")],
                              components=route_components)
    with pytest.raises(ConfigurationNotPublishedError) as exc:
        service.active_config("ROUTE")
    assert exc.value.status_code == 409
    assert exc.value.code == "CONFIG_NOT_PUBLISHED"
    assert exc.value.details["config_type"] == "ROUTE"


def test_an_unknown_config_type_is_rejected():
    service, _ = service_with([])
    with pytest.raises(ConfigurationNotPublishedError):
        service.active_config("WEATHER")


def test_config_type_is_case_insensitive():
    service, _ = service_with([])
    assert service.active_config("route").version_no == 2


def test_a_specific_version_can_be_resolved_for_replay():
    """Doc 02 §2.4.4 — an archived version must stay loadable or historical scores stop
    being explainable."""
    service, _ = service_with([_config_row("ROUTE", 1, status="ARCHIVED")],
                              components=route_components)
    archived = service.config_version("ROUTE", 1)
    assert archived.version_no == 1


def test_the_archived_v1_still_carries_the_pre_calibration_class_a_boundary():
    service, _ = service_with(
        [_config_row("ROUTE", 2), _config_row("ROUTE", 1, status="ARCHIVED")],
        components=route_components,
    )
    active = service.active_config("ROUTE")
    assert next(b.min for b in active.grade_bands if b.grade == "A") == 88
    v1 = service.config_version("ROUTE", 1)
    assert next(b.min for b in v1.grade_bands if b.grade == "A") == 80


def test_an_unknown_version_is_a_404():
    service, _ = service_with([_config_row("ROUTE", 2)], components=route_components)
    with pytest.raises(NotFoundError) as exc:
        service.config_version("ROUTE", 99)
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------
def test_the_second_resolution_is_served_from_cache():
    service, repo = service_with([_config_row("ROUTE", 2)], components=route_components)
    first = service.active_config("ROUTE")
    second = service.active_config("ROUTE")
    assert first is second
    assert repo.components_calls == 1, "components were re-read despite a cache hit"


def test_the_cache_is_keyed_by_version_so_two_versions_coexist():
    service, _ = service_with(
        [_config_row("ROUTE", 2), _config_row("ROUTE", 1, status="ARCHIVED")],
        components=route_components,
    )
    active = service.active_config("ROUTE")
    archived = service.config_version("ROUTE", 1)
    assert active is not archived
    assert ("ROUTE", 2) in config_module._CONFIG_CACHE
    assert ("ROUTE", 1) in config_module._CONFIG_CACHE


def test_publishing_a_new_version_needs_no_invalidation():
    """A new version is a new cache key, so a stale entry can never be served as active."""
    service, repo = service_with([_config_row("ROUTE", 2)], components=route_components)
    assert service.active_config("ROUTE").version_no == 2

    repo.rows[0].status = "ARCHIVED"
    published = _config_row("ROUTE", 2)
    published.id = 3
    published.version_no = 3
    repo.rows.insert(0, published)
    repo.components = lambda _cid: _component_rows("ROUTE", 2)

    assert service.active_config("ROUTE").version_no == 3


def test_clearing_the_cache_forces_a_re_read():
    service, repo = service_with([_config_row("ROUTE", 2)], components=route_components)
    service.active_config("ROUTE")
    clear_config_cache()
    service.active_config("ROUTE")
    assert repo.components_calls == 2


def test_a_cached_config_cannot_be_mutated_by_a_caller():
    """It is shared across requests, so immutability is what makes the cache safe."""
    service, _ = service_with([_config_row("ROUTE", 2)], components=route_components)
    config = service.active_config("ROUTE")
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.version_no = 99  # type: ignore[misc]


def test_the_cache_never_serves_one_config_type_for_another():
    service, _ = service_with(
        [_config_row("ROUTE", 2), _config_row("CUSTOMER", 1)],
        components=lambda cid: _component_rows("ROUTE" if cid == 2 else "CUSTOMER", cid),
    )
    assert service.active_config("ROUTE").config_type == "ROUTE"
    assert service.active_config("CUSTOMER").config_type == "CUSTOMER"


# ---------------------------------------------------------------------------
# Rules and versions
# ---------------------------------------------------------------------------
def test_an_unseeded_database_falls_back_to_the_shipped_rule_catalogue():
    service, _ = service_with([])
    assert len(service.active_rules()) == 22, "the documented catalogue is 22 rules"


def test_active_versions_reports_every_config_type():
    service, _ = service_with([])
    assert set(service.active_versions()) == {"ROUTE", "CUSTOMER", "FINAL"}


def test_engine_versions_are_semantic_and_complete():
    """The API reports these; they are also stamped onto every stored assessment."""
    versions = ConfigService.engine_versions()
    assert set(versions) == {"route", "customer", "final", "rules"}
    for name, value in versions.items():
        assert re.fullmatch(r"[a-z-]+@\d+\.\d+\.\d+", value), f"{name}={value!r}"
