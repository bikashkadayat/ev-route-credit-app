"""AuditService — the append-only trail required by FR-9.6 and Doc 09 §9.8.

Three properties matter, and each is a deliberate design decision:

1. **Same transaction as the change.** The writer only ever ``flush()``es; the request's
   session owns the commit. An audit row therefore cannot exist for a rolled-back change,
   and cannot be missing for a committed one. The single exception is a *failed* login,
   where the business fact being recorded (the incremented attempt counter) has to survive
   the 401 — ``AuthService`` commits that unit of work explicitly and says why.
2. **Append-only.** ``AuditRepository`` exposes no update or delete, a database trigger
   blocks both, and the application role is not granted DELETE. Three layers, because the
   value of an audit log is exactly its resistance to the compromised application.
3. **Redacted by key name.** Doc 02 §2.11 forbids passwords, tokens, national identifiers
   and account numbers in any log. Redaction is applied to before/after states here rather
   than at each call site, so forgetting it is not possible.
"""

from __future__ import annotations

import ipaddress
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.repositories.identity import AuditRepository

# -- security events named in Doc 09 §9.8 ------------------------------------------
LOGIN_SUCCESS = "LOGIN_SUCCESS"
LOGIN_FAILURE = "LOGIN_FAILURE"
ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
PASSWORD_CHANGED = "PASSWORD_CHANGED"  # noqa: S105 - an event name, not a credential
PASSWORD_RESET_REQUESTED = "PASSWORD_RESET_REQUESTED"  # noqa: S105 - event name
SECURITY_REFRESH_REUSE = "SECURITY_REFRESH_REUSE"
PERMISSION_DENIED = "PERMISSION_DENIED"
SENSITIVE_FIELD_VIEWED = "SENSITIVE_FIELD_VIEWED"
DATA_EXPORTED = "DATA_EXPORTED"
CONFIG_PUBLISHED = "CONFIG_PUBLISHED"
DECISION_OVERRIDE = "DECISION_OVERRIDE"
USER_DEACTIVATED = "USER_DEACTIVATED"
RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
LOGOUT = "LOGOUT"
TOKEN_REFRESHED = "TOKEN_REFRESHED"  # noqa: S105 - event name

SECURITY_EVENTS = frozenset({
    LOGIN_SUCCESS, LOGIN_FAILURE, ACCOUNT_LOCKED, PASSWORD_CHANGED,
    PASSWORD_RESET_REQUESTED, SECURITY_REFRESH_REUSE, PERMISSION_DENIED,
    SENSITIVE_FIELD_VIEWED, DATA_EXPORTED, CONFIG_PUBLISHED, DECISION_OVERRIDE,
    USER_DEACTIVATED, RATE_LIMIT_EXCEEDED, LOGOUT, TOKEN_REFRESHED,
})

#: Substrings that make a field name sensitive. Matching on the *name* rather than the
#: value is what lets this run before anyone has to think about it (Doc 02 §2.11).
REDACTED_KEY_PARTS = (
    "password", "token", "secret", "id_number", "id_number_enc", "id_number_hash",
    "pan_number", "citizenship", "account_number", "raw_response", "authorization",
    "api_key", "private_key", "refresh",
)

REDACTED = "[REDACTED]"


def redact(value: Any) -> Any:
    """Recursively blank out sensitive values by key name.

    Applied to dicts and lists of dicts; scalars pass through, because a bare string has no
    name to judge it by and the caller is responsible for not passing a secret as one.
    """
    if isinstance(value, dict):
        return {
            key: (REDACTED if _is_sensitive(str(key)) else redact(inner))
            for key, inner in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(item) for item in value]
    return value


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in REDACTED_KEY_PARTS)


def normalise_ip(value: str | None) -> str | None:
    """``audit_logs.ip_address`` and ``user_sessions.ip_address`` are INET columns, so a
    value that is not an IP address makes PostgreSQL reject the whole write.

    That matters more than it looks: the caller's host is not always an address. It is
    ``testclient`` under Starlette's test transport, absent over a unix socket, and can be
    a hostname behind a proxy. Losing the address is acceptable; losing the audit row, or
    failing the login that the row describes, is not.
    """
    if not value:
        return None
    candidate = value.split("%")[0].strip()
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return candidate


def changed_fields(before: dict[str, Any] | None, after: dict[str, Any] | None) -> list[str]:
    """The field names that actually differ — what a reviewer reads first."""
    if not before or not after:
        return sorted(after or before or {})
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


class AuditService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.audit = AuditRepository(session)

    def record(
        self,
        *,
        action: str,
        actor: Any | None = None,
        user_id: int | None = None,
        user_email: str | None = None,
        user_role: str | None = None,
        entity_type: str | None = None,
        entity_id: int | None = None,
        entity_uuid: UUID | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        request_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        status: str = "SUCCESS",
        error_message: str | None = None,
        actor_type: str = "USER",
        event_time: datetime | None = None,
    ) -> Any:
        """Write one audit row. ``actor`` may be a ``CurrentUser`` or a ``User`` row; its
        identity fields are used unless overridden explicitly."""
        if actor is not None:
            user_email = user_email or getattr(actor, "email", None)
            user_role = user_role or _role_code(actor)
            if user_id is None:
                user_id = _actor_id(actor)

        safe_before = redact(before) if before is not None else None
        safe_after = redact(after) if after is not None else None

        values: dict[str, Any] = {
            "action": action,
            "actor_type": actor_type,
            "user_id": user_id,
            "user_email": user_email,
            "user_role": user_role,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "entity_uuid": entity_uuid,
            "before_state": safe_before,
            "after_state": safe_after,
            "changed_fields": changed_fields(safe_before, safe_after) or None,
            "request_id": request_id,
            "ip_address": normalise_ip(ip_address),
            "user_agent": user_agent,
            "status": status,
            "error_message": error_message,
        }
        if event_time is not None:
            values["event_time"] = event_time
        return self.audit.record(**values)

    def recent_failures(self, *, action: str, user_id: int, since: datetime) -> int:
        return self.audit.count_recent_by_action(
            action=action, user_id=user_id, since=since
        )


def _actor_id(actor: Any) -> int | None:
    """A ``CurrentUser`` carries the id as a string claim; a ``User`` row as an int."""
    raw = getattr(actor, "id", None)
    if isinstance(raw, int):
        return raw
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _role_code(actor: Any) -> str | None:
    role = getattr(actor, "role", None)
    if role is None:
        return None
    return role if isinstance(role, str) else getattr(role, "code", None)
