"""Administration endpoints — FR-9.1 to FR-9.7, Doc 06 §6.11.

Every write here changes policy, so every write is audited and every one names the
permission it requires. The governing rule the endpoints enforce: a published scorecard is
immutable. Changing policy means drafting a new version and publishing it, which is what
keeps a score taken last quarter explainable today.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.mappers import risk_rule_out
from app.api.schemas.admin import (
    AdminUserOut,
    AuditEventOut,
    ConfigDraftCreate,
    ConfigDraftUpdate,
    ConfigOut,
    JobRunHistoryOut,
    PublishRequest,
    RoleOut,
    RuleToggleRequest,
    RuleUpdateRequest,
    SettingOut,
    SettingUpdate,
    UserCreate,
    UserStatusRequest,
    WeightCheckOut,
    WeightCheckRequest,
)
from app.api.schemas.common import ERROR_RESPONSES, Page
from app.api.schemas.config import RiskRuleOut
from app.core.dependencies import (
    AdminServiceDep,
    PaginationParams,
    ServicingJobServiceDep,
)
from app.core.permissions import CurrentUser, require_permission

router = APIRouter(prefix="/admin", tags=["Admin"], responses=ERROR_RESPONSES)


def _user_id(user: CurrentUser) -> int:
    try:
        return int(user.id)
    except (TypeError, ValueError):
        return 1


# ---------------------------------------------------------------------------
# Scoring configuration — FR-9.1, FR-9.2
# ---------------------------------------------------------------------------
@router.get(
    "/scoring-configs",
    response_model=list[ConfigOut],
    summary="List scoring configurations",
    description="Every version of every scorecard, whatever its status.",
)
def list_configs(
    service: AdminServiceDep,
    _user: CurrentUser = Depends(require_permission("risk:config:read")),
    config_type: Annotated[str | None, Query(description="ROUTE, CUSTOMER or FINAL")] = None,
) -> list[ConfigOut]:
    return [ConfigOut.model_validate(r) for r in service.list_configs(config_type)]


@router.post(
    "/scoring-configs/validate-weights",
    response_model=WeightCheckOut,
    summary="Check weights sum to 100%",
    description=(
        "FR-9.1. The editor calls this as an administrator types, so the running total is "
        "visible before anything is saved. It never writes; publishing is where a bad "
        "total becomes an error."
    ),
)
def validate_weights(
    payload: WeightCheckRequest,
    service: AdminServiceDep,
    # Part of the publish workflow, not a public calculator: whoever is drafting a
    # scorecard is the only person who needs its running weight total.
    _user: CurrentUser = Depends(require_permission("risk:config:publish")),
) -> WeightCheckOut:
    result = service.validate_weights(
        [c.model_dump() for c in payload.components]
    )
    return WeightCheckOut(**result.to_dict())


@router.post(
    "/scoring-configs",
    response_model=ConfigOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a configuration draft",
    description=(
        "FR-9.2. A new version starts as a DRAFT, editable until it is published. The "
        "version number is allocated by the server."
    ),
)
def create_draft(
    payload: ConfigDraftCreate,
    service: AdminServiceDep,
    user: CurrentUser = Depends(require_permission("risk:config:publish")),
) -> ConfigOut:
    return ConfigOut.model_validate(
        service.create_draft(payload.model_dump(), created_by=_user_id(user))
    )


@router.patch(
    "/scoring-configs/{config_id}",
    response_model=ConfigOut,
    summary="Edit a draft",
    description=(
        "Only a DRAFT can be edited. A published version is immutable, which is what makes "
        "a stored score reproducible — 409 otherwise."
    ),
)
def update_draft(
    config_id: int,
    payload: ConfigDraftUpdate,
    service: AdminServiceDep,
    user: CurrentUser = Depends(require_permission("risk:config:publish")),
) -> ConfigOut:
    return ConfigOut.model_validate(
        service.update_draft(
            config_id,
            payload.model_dump(exclude_unset=True),
            updated_by=_user_id(user),
        )
    )


@router.post(
    "/scoring-configs/{config_id}/publish",
    response_model=ConfigOut,
    summary="Publish a configuration",
    description=(
        "FR-9.2. Makes the draft ACTIVE and archives whatever was active before, so the "
        "superseded version stays queryable and its scores stay explainable.\n\n"
        "Refuses with `WEIGHTS_NOT_100` if the active component weights do not sum to "
        "exactly 100%: a scorecard that did not would make every score it produced wrong."
    ),
)
def publish_config(
    config_id: int,
    service: AdminServiceDep,
    payload: PublishRequest | None = None,
    user: CurrentUser = Depends(require_permission("risk:config:publish")),
) -> ConfigOut:
    request = payload or PublishRequest()
    return ConfigOut.model_validate(
        service.publish(config_id, published_by=_user_id(user), note=request.note)
    )


# ---------------------------------------------------------------------------
# Risk rules — FR-9.4
# ---------------------------------------------------------------------------
@router.get(
    "/risk-rules",
    response_model=list[RiskRuleOut],
    summary="List risk rules",
    description="The full catalogue with conditions, SLAs and playbook text.",
)
def list_rules(
    service: AdminServiceDep,
    _user: CurrentUser = Depends(require_permission("risk:config:read")),
) -> list[RiskRuleOut]:
    return [risk_rule_out(r) for r in service.list_rules()]


@router.post(
    "/risk-rules/{rule_id}/toggle",
    response_model=RiskRuleOut,
    summary="Enable or disable a rule",
    description=(
        "FR-7.9, FR-9.4. A rule is switched off, never deleted: every alert it ever raised "
        "points at this row and must stay explainable."
    ),
)
def toggle_rule(
    rule_id: int,
    payload: RuleToggleRequest,
    service: AdminServiceDep,
    user: CurrentUser = Depends(require_permission("risk:config:publish")),
) -> RiskRuleOut:
    return risk_rule_out(
        service.set_rule_enabled(
            rule_id,
            enabled=payload.enabled,
            user_id=_user_id(user),
            reason=payload.reason,
        )
    )


@router.patch(
    "/risk-rules/{rule_id}",
    response_model=RiskRuleOut,
    summary="Edit a rule",
    description=(
        "FR-9.4. Thresholds, severity, SLAs and playbook text are policy and are editable. "
        "The rule code is not: it is stamped on every alert ever raised."
    ),
)
def update_rule(
    rule_id: int,
    payload: RuleUpdateRequest,
    service: AdminServiceDep,
    user: CurrentUser = Depends(require_permission("risk:config:publish")),
) -> RiskRuleOut:
    return risk_rule_out(
        service.update_rule(
            rule_id, payload.model_dump(exclude_unset=True), user_id=_user_id(user)
        )
    )


# ---------------------------------------------------------------------------
# Settings — FR-9.3, FR-9.7
# ---------------------------------------------------------------------------
@router.get(
    "/settings",
    response_model=list[SettingOut],
    summary="List system settings",
    description=(
        "FR-9.3 and FR-9.7 — loan rules, underwriting gates, alert SLAs and general "
        "settings. Each row carries its own bounds, so the editor knows what it may accept."
    ),
)
def list_settings(
    service: AdminServiceDep,
    _user: CurrentUser = Depends(require_permission("settings:read")),
    category: Annotated[str | None, Query(description="GENERAL, LOAN_RULES, …")] = None,
) -> list[SettingOut]:
    return [SettingOut.model_validate(r) for r in service.list_settings(category)]


@router.put(
    "/settings/{setting_key}",
    response_model=SettingOut,
    summary="Update a setting",
    description=(
        "The stored minimum and maximum are enforced here, so a typo cannot set the LTV "
        "ceiling to 800% and quietly widen every approval. Non-editable settings are 409."
    ),
)
def update_setting(
    setting_key: str,
    payload: SettingUpdate,
    service: AdminServiceDep,
    user: CurrentUser = Depends(require_permission("settings:update")),
) -> SettingOut:
    return SettingOut.model_validate(
        service.update_setting(
            setting_key, payload.setting_value, user_id=_user_id(user)
        )
    )


# ---------------------------------------------------------------------------
# Users and roles — FR-9.5
# ---------------------------------------------------------------------------
@router.get(
    "/users",
    response_model=Page[AdminUserOut],
    summary="List users",
)
def list_users(
    pagination: PaginationParams,
    service: AdminServiceDep,
    _user: CurrentUser = Depends(require_permission("settings:read")),
    role_code: str | None = None,
    is_active: bool | None = None,
    branch_code: str | None = None,
    q: Annotated[str | None, Query(description="Name or email")] = None,
) -> Page[AdminUserOut]:
    rows, total = service.list_users(
        offset=pagination.offset,
        limit=pagination.limit,
        role_code=role_code,
        is_active=is_active,
        branch_code=branch_code,
        q=q,
    )
    return Page.build(
        [_user_out(r) for r in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.post(
    "/users",
    response_model=AdminUserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user",
    description=(
        "FR-9.5. Exactly one role per user. The initial password is validated against the "
        "same policy as any other, and the account is forced to change it on first login "
        "(FR-1.7)."
    ),
)
def create_user(
    payload: UserCreate,
    service: AdminServiceDep,
    user: CurrentUser = Depends(require_permission("settings:update")),
) -> AdminUserOut:
    return _user_out(service.create_user(payload.model_dump(), created_by=_user_id(user)))


@router.get("/users/{user_uuid}", response_model=AdminUserOut, summary="Get a user")
def get_user(
    user_uuid: UUID,
    service: AdminServiceDep,
    _user: CurrentUser = Depends(require_permission("settings:read")),
) -> AdminUserOut:
    return _user_out(service.get_user(user_uuid))


@router.post(
    "/users/{user_uuid}/status",
    response_model=AdminUserOut,
    summary="Activate or deactivate a user",
    description=(
        "FR-9.5, Doc 09 §9.2.4. Deactivation revokes every session immediately — a "
        "departing employee holding a valid refresh token for seven days is the reason "
        "this is more than a flag."
    ),
)
def set_user_status(
    user_uuid: UUID,
    payload: UserStatusRequest,
    service: AdminServiceDep,
    user: CurrentUser = Depends(require_permission("settings:update")),
) -> AdminUserOut:
    return _user_out(
        service.set_user_active(
            user_uuid,
            active=payload.active,
            user_id=_user_id(user),
            reason=payload.reason,
        )
    )


@router.get(
    "/roles",
    response_model=list[RoleOut],
    summary="List roles and their permissions",
    description=(
        "What each role may do, read from `role_permissions`. This is the same source the "
        "token claims are minted from, so the screen cannot disagree with enforcement."
    ),
)
def list_roles(
    service: AdminServiceDep,
    _user: CurrentUser = Depends(require_permission("settings:read")),
) -> list[RoleOut]:
    return [
        RoleOut(
            code=r.code,
            name=r.name,
            description=r.description,
            is_system=bool(r.is_system),
            permissions=service.role_permissions(r.code),
        )
        for r in service.list_roles()
    ]


# ---------------------------------------------------------------------------
# Audit — FR-9.6
# ---------------------------------------------------------------------------
@router.get(
    "/audit-logs",
    response_model=Page[AuditEventOut],
    summary="Search the audit trail",
    description=(
        "FR-9.6. Read-only and newest first. The table is append-only in the database and "
        "this API exposes no write path.\n\n"
        "Entries never carry a password, token or raw national identifier: the writer "
        "redacts by key name before anything is stored."
    ),
)
def list_audit_events(
    pagination: PaginationParams,
    service: AdminServiceDep,
    _user: CurrentUser = Depends(require_permission("audit:read")),
    action: str | None = None,
    entity_type: str | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    actor_id: UUID | None = None,
    since: date | None = None,
) -> Page[AuditEventOut]:
    rows, total = service.list_audit_events(
        offset=pagination.offset,
        limit=pagination.limit,
        action=action,
        entity_type=entity_type,
        status=status_filter,
        actor_uuid=actor_id,
        since=since,
    )
    return Page.build(
        [AuditEventOut.model_validate(r) for r in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get(
    "/jobs",
    response_model=list[JobRunHistoryOut],
    summary="Recent job runs",
    description=(
        "TRD §2.10. What ran, when, with what outcome. A night with no successful run is "
        "the failure this exists to make visible."
    ),
)
def list_job_runs(
    service: ServicingJobServiceDep,
    _user: CurrentUser = Depends(require_permission("settings:read")),
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> list[JobRunHistoryOut]:
    return [
        JobRunHistoryOut.model_validate(r) for r in service.recent_runs(limit=limit)
    ]


def _user_out(row: Any) -> AdminUserOut:
    role = getattr(row, "role", None)
    return AdminUserOut(
        id=row.uuid,
        email=row.email,
        full_name=row.full_name,
        role_code=role.code if role else None,
        role_name=role.name if role else None,
        branch_code=row.branch_code,
        employee_code=row.employee_code,
        is_active=bool(row.is_active),
        must_change_password=bool(row.must_change_password),
        last_login_at=row.last_login_at,
        created_at=row.created_at,
    )
