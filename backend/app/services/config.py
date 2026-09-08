"""ConfigService — resolves the active scorecard and rule catalogue for the engines.

Two responsibilities, both about *not* letting policy drift:

1. **Resolution.** The database is the authority. If a configuration row exists it is used;
   the shipped JSON default is a fallback for a database that has not been seeded yet
   (fresh CI, a unit test), never a silent override of published policy.
2. **Caching.** Keyed by ``(config_type, version_no)``. ``ScoringConfig`` is a frozen
   dataclass, so a cached instance cannot be mutated by a caller — the engines receive an
   immutable object and the cache cannot be poisoned.

The cache is process-local and version-keyed, so publishing a new version simply produces
a new key; nothing needs invalidating and a stale version can never be served as active
(Doc 02 §4.13).
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.core.errors import ConfigurationNotPublishedError
from app.engines.base import ScoringConfig
from app.engines.config_loader import config_from_db_rows, default_config
from app.repositories.config import RiskRuleRepository, ScoringConfigRepository
from app.risk.loader import default_rules, rules_from_db_rows
from app.risk.models import Rule

CONFIG_TYPES = ("ROUTE", "CUSTOMER", "FINAL")

#: Process-local cache. Key is (config_type, version_no) so a published version is a new
#: key rather than an invalidation.
_CONFIG_CACHE: dict[tuple[str, int], ScoringConfig] = {}


def clear_config_cache() -> None:
    """Used by tests and by the admin publish flow after a version changes."""
    _CONFIG_CACHE.clear()


class ConfigService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.configs = ScoringConfigRepository(session)
        self.rules = RiskRuleRepository(session)

    # -- scorecards --------------------------------------------------------
    def active_config(self, config_type: str) -> ScoringConfig:
        """The ACTIVE scorecard for a type.

        Raises ``ConfigurationNotPublishedError`` (409) rather than silently falling back
        to a default when the database has configurations but none is active — a lender
        must never score against a scorecard nobody published.
        """
        config_type = config_type.upper()
        if config_type not in CONFIG_TYPES:
            raise ConfigurationNotPublishedError(config_type)

        row = self.configs.active(config_type)
        if row is not None:
            return self._from_row(row)

        # No ACTIVE row. If the table holds *no* configuration of this type at all the
        # database is simply unseeded, so fall back to the shipped default; if versions
        # exist but none is active, that is a real policy gap and must surface.
        if self.configs.list_all(config_type):
            raise ConfigurationNotPublishedError(config_type)
        return default_config(config_type)

    def config_version(self, config_type: str, version_no: int) -> ScoringConfig:
        """A specific version — used to reproduce a historical score (Doc 02 §2.4.4)."""
        row = self.configs.by_version(config_type.upper(), version_no)
        if row is None:
            from app.core.errors import NotFoundError

            raise NotFoundError(
                "scoring configuration", f"{config_type} v{version_no}"
            )
        return self._from_row(row)

    def _from_row(self, row) -> ScoringConfig:
        key = (str(row.config_type), int(row.version_no))
        cached = _CONFIG_CACHE.get(key)
        if cached is not None:
            return cached
        components = self.configs.components_for(row.id)
        config = config_from_db_rows(row, list(components))
        _CONFIG_CACHE[key] = config
        return config

    def active_versions(self) -> dict[str, int]:
        """Stamped onto assessments and reported by the readiness probe."""
        out: dict[str, int] = {}
        for config_type in CONFIG_TYPES:
            row = self.configs.active(config_type)
            if row is not None:
                out[config_type] = int(row.version_no)
            else:
                out[config_type] = default_config(config_type).version_no
        return out

    def list_configurations(self, config_type: str | None = None) -> Sequence:
        return self.configs.list_all(config_type.upper() if config_type else None)

    @staticmethod
    def engine_versions() -> dict[str, str]:
        """The semantic versions of the code the engines are running.

        Lives here rather than in the router so the API layer never imports an engine —
        the boundary asserted by
        ``test_engine_purity.py::test_api_layer_never_imports_a_pure_engine``.
        """
        from app.engines.customer_engine import CustomerScoringEngine
        from app.engines.final_engine import FinalRiskEngine
        from app.engines.route_engine import RouteScoringEngine
        from app.risk.engine import ENGINE_VERSION as RULE_ENGINE_VERSION

        return {
            "route": RouteScoringEngine.engine_version,
            "customer": CustomerScoringEngine.engine_version,
            "final": FinalRiskEngine.engine_version,
            "rules": RULE_ENGINE_VERSION,
        }

    # -- rule catalogue ----------------------------------------------------
    def active_rules(self) -> tuple[Rule, ...]:
        """The rule catalogue the risk engine evaluates.

        Same fallback logic: seeded database wins, shipped catalogue covers an unseeded
        one so unit tests and a fresh CI run behave identically.
        """
        rows = self.rules.active_rules()
        if not rows:
            return default_rules()
        return rules_from_db_rows(list(rows))

    def all_rules(self) -> Sequence:
        return self.rules.all_rules()
