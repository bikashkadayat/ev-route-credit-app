"""Configuration endpoints — Doc 06 §6.11 (read-only slice).

Deliberately read-only. Publishing, editing and archiving a configuration are admin
workflows with their own change control (Doc 03 Screen 27, Doc 09 §9.3.3); nothing routed
here can mutate a published or archived version.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.mappers import config_detail, config_summary, risk_rule_out
from app.api.schemas.common import ERROR_RESPONSES
from app.api.schemas.config import (
    ActiveConfigOut,
    RiskRuleOut,
    ScoringConfigDetail,
)
from app.core.dependencies import ConfigServiceDep
from app.core.errors import NotFoundError
from app.core.permissions import CurrentUser, require_permission

router = APIRouter(prefix="/config", tags=["Configuration"], responses=ERROR_RESPONSES)


@router.get(
    "",
    response_model=ActiveConfigOut,
    summary="Active scoring configuration",
    description=(
        "Which scorecard version each engine is running, and the engine versions "
        "themselves. Every stored assessment is stamped with these, so a historical score "
        "can always be attributed to the exact policy and code that produced it."
    ),
)
def get_active_config(
    service: ConfigServiceDep,
    _user: CurrentUser = Depends(require_permission("risk:config:read")),
) -> ActiveConfigOut:
    return ActiveConfigOut(
        active_versions=service.active_versions(),
        engine_versions=service.engine_versions(),
        configurations=[config_summary(r) for r in service.list_configurations()],
    )


@router.get(
    "/routes",
    response_model=ScoringConfigDetail,
    summary="Route scorecard",
    description=(
        "The ROUTE scorecard, ACTIVE by default. Pass `version` to read a superseded one: "
        "v1 (Class A from 80) is retained as ARCHIVED after the calibration review raised "
        "the boundary to 88 in v2, so scores produced under v1 remain explainable."
    ),
)
def get_route_config(
    service: ConfigServiceDep,
    _user: CurrentUser = Depends(require_permission("risk:config:read")),
    version: Annotated[int | None, Query(ge=1, description="Defaults to ACTIVE")] = None,
) -> ScoringConfigDetail:
    rows = service.list_configurations("ROUTE")
    if not rows:
        raise NotFoundError("scoring configuration", "ROUTE")
    if version is not None:
        match = next((r for r in rows if r.version_no == version), None)
        if match is None:
            raise NotFoundError("scoring configuration", f"ROUTE v{version}")
        return config_detail(match)
    active = next((r for r in rows if str(r.status) == "ACTIVE"), None)
    if active is None:
        raise NotFoundError("active scoring configuration", "ROUTE")
    return config_detail(active)


@router.get(
    "/risk-rules",
    response_model=list[RiskRuleOut],
    summary="Risk rule catalogue",
    description=(
        "The 22-rule early-warning catalogue with its conditions, SLAs and playbook text. "
        "Each rule is also rendered as a plain-language sentence, which is what the admin "
        "condition builder displays."
    ),
)
def get_risk_rules(
    service: ConfigServiceDep,
    _user: CurrentUser = Depends(require_permission("risk:config:read")),
    active_only: Annotated[bool, Query(description="Only enabled rules")] = False,
) -> list[RiskRuleOut]:
    rows = service.all_rules()
    if active_only:
        rows = [r for r in rows if r.is_active]

    # The readable sentence comes from the rule engine's own Rule.describe(), so the API
    # and the admin UI cannot describe a rule differently from how it evaluates.
    described = {r.rule_code: r.describe() for r in service.active_rules()}
    return [risk_rule_out(r, described.get(r.rule_code)) for r in rows]
