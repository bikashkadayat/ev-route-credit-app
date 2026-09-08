"""Scoring-configuration and risk-rule persistence.

Read-only by design at this stage: publishing a configuration is an admin workflow with
its own change-control (Doc 03 Screen 27), and nothing in the API surface built here may
mutate a published or archived version.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.catalog import ScoringConfigComponent, ScoringConfiguration
from app.models.portfolio import RiskRule
from app.models.system import SystemSetting
from app.repositories.base import BaseRepository


class ScoringConfigRepository(BaseRepository[ScoringConfiguration]):
    model = ScoringConfiguration

    def active(self, config_type: str) -> ScoringConfiguration | None:
        stmt = (
            select(ScoringConfiguration)
            .options(selectinload(ScoringConfiguration.components))
            .where(
                ScoringConfiguration.config_type == config_type,
                ScoringConfiguration.status == "ACTIVE",
            )
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def by_version(self, config_type: str, version_no: int) -> ScoringConfiguration | None:
        stmt = (
            select(ScoringConfiguration)
            .options(selectinload(ScoringConfiguration.components))
            .where(
                ScoringConfiguration.config_type == config_type,
                ScoringConfiguration.version_no == version_no,
            )
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_all(self, config_type: str | None = None) -> Sequence[ScoringConfiguration]:
        stmt = select(ScoringConfiguration).options(
            selectinload(ScoringConfiguration.components)
        )
        if config_type:
            stmt = stmt.where(ScoringConfiguration.config_type == config_type)
        stmt = stmt.order_by(
            ScoringConfiguration.config_type, ScoringConfiguration.version_no.desc()
        )
        return self.session.execute(stmt).scalars().all()

    def components_for(self, config_id: int) -> Sequence[ScoringConfigComponent]:
        stmt = (
            select(ScoringConfigComponent)
            .where(ScoringConfigComponent.config_id == config_id)
            .order_by(ScoringConfigComponent.display_order)
        )
        return self.session.execute(stmt).scalars().all()


    def create(self, **values: Any) -> ScoringConfiguration:
        return self.add(ScoringConfiguration(**values))

    def replace_components(
        self, config_id: int, components: list[dict[str, Any]]
    ) -> Sequence[ScoringConfigComponent]:
        """Swap a draft's component rows wholesale.

        Editing in place would leave orphans when a component is dropped, and a scorecard
        with a stale component would not sum to 100%.
        """
        existing = self.session.execute(
            select(ScoringConfigComponent).where(
                ScoringConfigComponent.config_id == config_id
            )
        ).scalars().all()
        for row in existing:
            self.session.delete(row)
        self.session.flush()

        created = [
            ScoringConfigComponent(
                config_id=config_id,
                component_code=c["component_code"],
                label=c.get("label") or c["component_code"].replace("_", " ").title(),
                weight=c["weight"],
                display_order=c.get("display_order", index),
                scoring_rules=c.get("scoring_rules") or {},
                description=c.get("description"),
                is_active=c.get("is_active", True),
            )
            for index, c in enumerate(components)
        ]
        self.session.add_all(created)
        self.session.flush()
        return created

    def list_settings(self, category: str | None = None) -> Sequence[SystemSetting]:
        stmt = select(SystemSetting)
        if category:
            stmt = stmt.where(SystemSetting.category == category)
        return self.session.execute(
            stmt.order_by(SystemSetting.category, SystemSetting.setting_key)
        ).scalars().all()

    def setting(self, key: str) -> SystemSetting | None:
        stmt = select(SystemSetting).where(SystemSetting.setting_key == key).limit(1)
        return self.session.execute(stmt).scalar_one_or_none()


class RiskRuleRepository(BaseRepository[RiskRule]):
    model = RiskRule

    def active_rules(self) -> Sequence[RiskRule]:
        stmt = (
            select(RiskRule)
            .options(selectinload(RiskRule.conditions))
            .where(RiskRule.is_active.is_(True))
            .order_by(RiskRule.priority, RiskRule.rule_code)
        )
        return self.session.execute(stmt).scalars().all()

    def all_rules(self) -> Sequence[RiskRule]:
        stmt = (
            select(RiskRule)
            .options(selectinload(RiskRule.conditions))
            .order_by(RiskRule.priority, RiskRule.rule_code)
        )
        return self.session.execute(stmt).scalars().all()

    def by_code(self, rule_code: str) -> RiskRule | None:
        stmt = (
            select(RiskRule)
            .options(selectinload(RiskRule.conditions))
            .where(RiskRule.rule_code == rule_code)
        )
        return self.session.execute(stmt).scalar_one_or_none()
