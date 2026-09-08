"""AdminService — configuration, rules, settings, users and the audit reader.

FR-9.1 to FR-9.7, Doc 06 §6.11, Doc 03 Screens 26-30.

The governing idea across all of it: **policy is data, and published policy is immutable.**
A scorecard is drafted, validated, published as a new version, and never edited again — so
a score taken last quarter can still be explained by loading the version stamped on it. The
same applies to a risk rule: it is enabled, disabled or superseded, not silently rewritten
under assessments that already used it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import (
    ConflictError,
    DuplicateError,
    NotFoundError,
    ValidationError,
)
from app.core.security import hash_password
from app.models.catalog import ScoringConfiguration
from app.models.portfolio import RiskRule
from app.models.system import SystemSetting
from app.repositories.config import RiskRuleRepository, ScoringConfigRepository
from app.repositories.identity import AuditRepository, UserRepository
from app.services import audit as audit_events
from app.services.audit import AuditService
from app.services.auth import validate_password_strength
from app.services.config import clear_config_cache

WEIGHT_TOLERANCE = Decimal("0.00005")


@dataclass(frozen=True, slots=True)
class WeightValidation:
    """FR-9.1 — what the editor shows live as an administrator types."""

    total: Decimal
    is_valid: bool
    components: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": float(self.total),
            "total_percent": float(self.total * Decimal("100")),
            "is_valid": self.is_valid,
            "components": list(self.components),
        }


class AdminService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.configs = ScoringConfigRepository(session)
        self.rules = RiskRuleRepository(session)
        self.users = UserRepository(session)
        self.audit_repo = AuditRepository(session)
        self.audit = AuditService(session)

    # ------------------------------------------------------------------
    # Scoring configuration — FR-9.1, FR-9.2
    # ------------------------------------------------------------------
    def list_configs(self, config_type: str | None = None) -> Any:
        return self.configs.list_all(config_type.upper() if config_type else None)

    def get_config(self, config_id: int) -> ScoringConfiguration:
        row = self.configs.get(config_id)
        if row is None:
            raise NotFoundError("scoring configuration", config_id)
        return row

    @staticmethod
    def validate_weights(components: list[dict[str, Any]]) -> WeightValidation:
        """FR-9.1 — the sum must be exactly 1.0000 before anything can be published.

        Returned rather than raised, because the editor calls this on every keystroke to
        show a running total; publishing is where it becomes an error.
        """
        total = sum(
            (Decimal(str(c.get("weight", 0))) for c in components if c.get("is_active", True)),
            start=Decimal("0"),
        )
        return WeightValidation(
            total=total,
            is_valid=abs(total - Decimal("1")) <= WEIGHT_TOLERANCE,
            components=tuple(
                {
                    "component_code": c.get("component_code"),
                    "weight": float(Decimal(str(c.get("weight", 0)))),
                    "weight_percent": float(
                        Decimal(str(c.get("weight", 0))) * Decimal("100")
                    ),
                }
                for c in components
            ),
        )

    def create_draft(
        self, payload: dict[str, Any], *, created_by: int
    ) -> ScoringConfiguration:
        """FR-9.2 — a new version starts as a DRAFT and is editable until published."""
        config_type = str(payload["config_type"]).upper()
        components = list(payload.get("components") or [])
        if not components:
            raise ValidationError(
                "A configuration needs at least one component",
                details=[{"field": "components", "code": "EMPTY",
                          "message": "Supply the component weights"}],
            )

        next_version = self._next_version(config_type)
        row = self.configs.create(
            config_type=config_type,
            version_no=next_version,
            name=payload.get("name") or f"{config_type} v{next_version}",
            status="DRAFT",
            grade_thresholds=payload["grade_thresholds"],
            decision_rules=payload.get("decision_rules"),
            notes=payload.get("notes"),
            created_by=created_by,
        )
        self.configs.replace_components(row.id, components)
        self.session.flush()

        self.audit.record(
            action="CONFIG_DRAFT_CREATED",
            user_id=created_by,
            entity_type="scoring_configuration",
            entity_id=row.id,
            after={"config_type": config_type, "version_no": next_version,
                   "status": "DRAFT"},
        )
        return row

    def _next_version(self, config_type: str) -> int:
        existing = self.configs.list_all(config_type)
        return max((int(r.version_no) for r in existing), default=0) + 1

    def update_draft(
        self, config_id: int, payload: dict[str, Any], *, updated_by: int
    ) -> ScoringConfiguration:
        """A published version is immutable — that is what makes a stored score
        reproducible. Only a DRAFT can be edited."""
        row = self.get_config(config_id)
        if str(row.status) != "DRAFT":
            raise ConflictError(
                f"A {row.status} configuration cannot be edited. Create a new draft "
                f"instead — published policy is immutable so historical scores stay "
                f"reproducible.",
                details={"status": str(row.status)},
            )

        if "grade_thresholds" in payload:
            row.grade_thresholds = payload["grade_thresholds"]
        if "decision_rules" in payload:
            row.decision_rules = payload["decision_rules"]
        if "name" in payload:
            row.name = payload["name"]
        if "notes" in payload:
            row.notes = payload["notes"]
        if payload.get("components"):
            self.configs.replace_components(row.id, list(payload["components"]))
        self.session.flush()

        self.audit.record(
            action="CONFIG_DRAFT_UPDATED",
            user_id=updated_by,
            entity_type="scoring_configuration",
            entity_id=row.id,
            after={"version_no": row.version_no, "status": "DRAFT"},
        )
        return row

    def publish(
        self, config_id: int, *, published_by: int, note: str | None = None
    ) -> ScoringConfiguration:
        """FR-9.2 — publish a draft, archiving whatever was active.

        The weight check is enforced here rather than only in the editor: a configuration
        whose weights do not sum to 100% would make every score it produced wrong, and the
        engine refuses to load one, so it must never reach ACTIVE.
        """
        row = self.get_config(config_id)
        if str(row.status) != "DRAFT":
            raise ConflictError(
                f"Only a DRAFT can be published; this one is {row.status}",
                details={"status": str(row.status)},
            )

        components = [
            {
                "component_code": c.component_code,
                "weight": c.weight,
                "is_active": c.is_active,
            }
            for c in self.configs.components_for(row.id)
        ]
        validation = self.validate_weights(components)
        if not validation.is_valid:
            raise ValidationError(
                f"Component weights sum to {validation.total * 100:.2f}%, not 100%",
                code="WEIGHTS_NOT_100",
                details=[{"field": "components", "code": "WEIGHTS_NOT_100",
                          "message": "Active weights must sum to exactly 100%"}],
            )

        now = datetime.now(UTC)
        previous = self.configs.active(str(row.config_type))
        if previous is not None:
            previous.status = "ARCHIVED"
            previous.archived_at = now

        row.status = "ACTIVE"
        row.published_by = published_by
        row.published_at = now
        if note:
            row.notes = note
        self.session.flush()

        # The scorecard cache is keyed by (type, version), so a new version is a new key;
        # clearing is belt-and-braces for the archived row's status change.
        clear_config_cache()

        self.audit.record(
            action=audit_events.CONFIG_PUBLISHED,
            user_id=published_by,
            entity_type="scoring_configuration",
            entity_id=row.id,
            before={"previous_active_version": previous.version_no if previous else None},
            after={
                "config_type": str(row.config_type),
                "version_no": row.version_no,
                "status": "ACTIVE",
                "weight_total": float(validation.total),
                "note": note,
            },
        )
        return row

    # ------------------------------------------------------------------
    # Risk rules — FR-9.4
    # ------------------------------------------------------------------
    def list_rules(self) -> Any:
        return self.rules.all_rules()

    def get_rule(self, rule_id: int) -> RiskRule:
        row = self.rules.get(rule_id)
        if row is None:
            raise NotFoundError("risk rule", rule_id)
        return row

    def set_rule_enabled(
        self, rule_id: int, *, enabled: bool, user_id: int, reason: str | None = None
    ) -> RiskRule:
        """FR-7.9 / FR-9.4 — a rule is enabled or disabled, never quietly deleted.

        Disabling rather than removing keeps every alert the rule ever raised explainable:
        the row it points at still exists.
        """
        row = self.get_rule(rule_id)
        before = bool(row.is_active)
        if before == enabled:
            raise ConflictError(
                f"Rule {row.rule_code} is already {'enabled' if enabled else 'disabled'}",
                details={"rule_code": row.rule_code, "is_active": before},
            )

        row.is_active = enabled
        self.session.flush()
        self.audit.record(
            action="RISK_RULE_TOGGLED",
            user_id=user_id,
            entity_type="risk_rule",
            entity_id=row.id,
            before={"is_active": before},
            after={"is_active": enabled, "rule_code": row.rule_code, "reason": reason},
        )
        return row

    def update_rule(
        self, rule_id: int, payload: dict[str, Any], *, user_id: int
    ) -> RiskRule:
        """FR-9.4 — thresholds, severity and playbook text are editable policy.

        The rule *code* is not: it is stamped on every alert ever raised, and changing it
        would orphan them.
        """
        row = self.get_rule(rule_id)
        before = _rule_snapshot(row)

        editable = {
            "name", "description", "severity", "priority", "suppression_hours",
            "auto_resolve", "sla_hours_acknowledge", "sla_hours_resolve",
            "recommended_action", "condition_logic",
        }
        rejected = sorted(set(payload) - editable)
        if rejected:
            raise ValidationError(
                "These fields cannot be changed on a rule",
                details=[{"field": f, "code": "IMMUTABLE",
                          "message": "Fixed for the life of the rule"} for f in rejected],
            )

        for field, value in payload.items():
            setattr(row, field, value)
        self.session.flush()

        self.audit.record(
            action="RISK_RULE_UPDATED",
            user_id=user_id,
            entity_type="risk_rule",
            entity_id=row.id,
            before=before,
            after=_rule_snapshot(row),
        )
        return row

    # ------------------------------------------------------------------
    # System settings — FR-9.3, FR-9.7
    # ------------------------------------------------------------------
    def list_settings(self, category: str | None = None) -> Any:
        return self.configs.list_settings(category)

    def update_setting(
        self, key: str, value: str, *, user_id: int
    ) -> SystemSetting:
        """FR-9.3, FR-9.7 — loan rules and operational settings are data.

        The stored min/max bounds are enforced here, so a typo cannot set the LTV ceiling
        to 800% and quietly widen every approval.
        """
        row = self.configs.setting(key)
        if row is None:
            raise NotFoundError("system setting", key)
        if not row.is_editable:
            raise ConflictError(
                f"{key} is not editable at runtime",
                details={"setting_key": key},
            )

        before = row.setting_value
        self._validate_setting(row, value)
        row.setting_value = value
        row.updated_by = user_id
        self.session.flush()

        self.audit.record(
            action="SETTING_UPDATED",
            user_id=user_id,
            entity_type="system_setting",
            entity_id=row.id,
            before={"setting_key": key, "setting_value": before},
            after={"setting_key": key, "setting_value": value},
        )
        return row

    @staticmethod
    def _validate_setting(row: SystemSetting, value: str) -> None:
        value_type = str(row.value_type)
        if value_type == "NUMBER":
            try:
                number = Decimal(value)
            except Exception as exc:
                raise ValidationError(
                    f"{row.setting_key} must be a number",
                    details=[{"field": "setting_value", "code": "NOT_A_NUMBER",
                              "message": "Numeric value required"}],
                ) from exc
            if row.min_value is not None and number < Decimal(row.min_value):
                raise ValidationError(
                    f"{row.setting_key} must be at least {row.min_value}",
                    details=[{"field": "setting_value", "code": "BELOW_MINIMUM",
                              "message": f"Minimum {row.min_value}"}],
                )
            if row.max_value is not None and number > Decimal(row.max_value):
                raise ValidationError(
                    f"{row.setting_key} must be at most {row.max_value}",
                    details=[{"field": "setting_value", "code": "ABOVE_MAXIMUM",
                              "message": f"Maximum {row.max_value}"}],
                )
        elif value_type == "BOOLEAN" and value.lower() not in {"true", "false"}:
            raise ValidationError(
                f"{row.setting_key} must be true or false",
                details=[{"field": "setting_value", "code": "NOT_A_BOOLEAN",
                          "message": "true or false"}],
            )
        elif value_type == "JSON":
            import json

            try:
                json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValidationError(
                    f"{row.setting_key} must be valid JSON",
                    details=[{"field": "setting_value", "code": "INVALID_JSON",
                              "message": str(exc)[:120]}],
                ) from exc

    # ------------------------------------------------------------------
    # Users — FR-9.5
    # ------------------------------------------------------------------
    def list_users(self, **filters: Any) -> Any:
        return self.users.list_users(**filters)

    def get_user(self, user_uuid: Any) -> Any:
        row = self.users.by_uuid(user_uuid)
        if row is None:
            raise NotFoundError("user", user_uuid)
        return row

    def create_user(self, payload: dict[str, Any], *, created_by: int) -> Any:
        """FR-9.5, FR-1.7 — an admin-created account must change its password on first use.

        The initial password is validated against the same policy as any other, so an
        administrator cannot seed a weak one.
        """
        email = str(payload["email"]).strip().lower()
        if self.users.by_email(email) is not None:
            raise DuplicateError(
                "A user with this email already exists", details={"email": email}
            )

        role = self.users.role_by_code(str(payload["role_code"]))
        if role is None:
            raise NotFoundError("role", payload["role_code"])

        password = str(payload["password"])
        validate_password_strength(password, email=email)

        row = self.users.add(
            self.users.model(
                email=email,
                password_hash=hash_password(password),
                full_name=payload["full_name"],
                phone=payload.get("phone"),
                employee_code=payload.get("employee_code"),
                role_id=role.id,
                branch_code=payload.get("branch_code"),
                is_active=True,
                must_change_password=True,
                created_by=created_by,
            )
        )
        self.audit.record(
            action="USER_CREATED",
            user_id=created_by,
            entity_type="user",
            entity_id=row.id,
            entity_uuid=row.uuid,
            after={"email": email, "role": role.code, "branch_code": row.branch_code},
        )
        return row

    def set_user_active(
        self, user_uuid: Any, *, active: bool, user_id: int, reason: str | None = None
    ) -> Any:
        """FR-9.5, Doc 09 §9.2.4 — deactivation revokes every session immediately.

        A departing employee who keeps a valid refresh token for seven days is the whole
        reason this is not just a flag.
        """
        row = self.get_user(user_uuid)
        if bool(row.is_active) == active:
            raise ConflictError(
                f"User is already {'active' if active else 'inactive'}",
                details={"is_active": bool(row.is_active)},
            )

        row.is_active = active
        row.updated_by = user_id
        self.session.flush()

        revoked = 0
        if not active:
            from app.repositories.identity import UserSessionRepository

            revoked = UserSessionRepository(self.session).revoke_all_for_user(
                row.id, at=datetime.now(UTC), reason="USER_DEACTIVATED"
            )

        self.audit.record(
            action=audit_events.USER_DEACTIVATED if not active else "USER_ACTIVATED",
            user_id=user_id,
            entity_type="user",
            entity_id=row.id,
            entity_uuid=row.uuid,
            before={"is_active": not active},
            after={"is_active": active, "sessions_revoked": revoked, "reason": reason},
        )
        return row

    def list_roles(self) -> Any:
        return self.users.list_roles()

    def role_permissions(self, role_code: str) -> list[str]:
        role = self.users.role_by_code(role_code)
        if role is None:
            raise NotFoundError("role", role_code)
        return self.users.permissions_for_role(role.id)

    # ------------------------------------------------------------------
    # Audit reader — FR-9.6
    # ------------------------------------------------------------------
    def list_audit_events(
        self,
        *,
        offset: int,
        limit: int,
        action: str | None = None,
        entity_type: str | None = None,
        status: str | None = None,
        actor_uuid: Any = None,
        since: date | None = None,
    ) -> tuple[Any, int]:
        """FR-9.6 — read-only. The table is append-only in the database, so there is no
        write path here to abuse."""
        user_id = None
        if actor_uuid is not None:
            actor = self.users.by_uuid(actor_uuid)
            if actor is None:
                raise NotFoundError("user", actor_uuid)
            user_id = actor.id

        since_dt = (
            datetime.combine(since, datetime.min.time(), tzinfo=UTC) if since else None
        )
        return self.audit_repo.list_events(
            offset=offset,
            limit=limit,
            action=action,
            entity_type=entity_type,
            status=status,
            user_id=user_id,
            since=since_dt,
        )


def _rule_snapshot(row: RiskRule) -> dict[str, Any]:
    return {
        "rule_code": row.rule_code,
        "name": row.name,
        "severity": str(row.severity),
        "priority": row.priority,
        "is_active": bool(row.is_active),
        "suppression_hours": row.suppression_hours,
        "auto_resolve": bool(row.auto_resolve),
        "sla_hours_acknowledge": row.sla_hours_acknowledge,
        "sla_hours_resolve": row.sla_hours_resolve,
    }
