"""AuthService — Doc 06 §6.2, Doc 09 §9.2, PRD FR-1.1 to FR-1.7.

Everything about a credential lives here: the constant-shape failure response, the lockout
counter, refresh-token rotation with reuse detection, and the password policy. Routers only
hand over the request body.

``now`` is an argument on every method rather than a call to the clock, for the same reason
it is in the engines: a time-dependent security rule that cannot be tested at a chosen
instant is a rule nobody can prove.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import (
    AccountInactiveError,
    AccountLockedError,
    TokenReuseError,
    UnauthorizedError,
    ValidationError,
)
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    needs_rehash,
    verify_password,
)
from app.models.identity import User, UserSession
from app.repositories.identity import (
    PasswordHistoryRepository,
    PasswordResetRepository,
    UserRepository,
    UserSessionRepository,
)
from app.services import audit as audit_events
from app.services.audit import AuditService, normalise_ip

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128

#: Doc 06 §6.2 — a reset link is valid for thirty minutes.
RESET_TOKEN_TTL_MINUTES = 30

#: FR-1.2 — the number of previous passwords a new one is checked against.
PASSWORD_HISTORY_DEPTH = 5

#: Doc 09 §9.2.1 — a small local list stands in for the "top 10k breached" check. The
#: interface is what matters: the check happens, and expanding the list is data, not code.
COMMON_PASSWORDS = frozenset({
    "password", "password1", "password123", "passw0rd", "qwerty", "qwertyuiop",
    "123456", "12345678", "123456789", "1234567890", "letmein", "welcome",
    "admin", "administrator", "iloveyou", "monkey", "dragon", "football",
    "abc123", "changeme", "secret", "nepal123", "kathmandu",
})


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    """What a successful login or refresh produces."""

    user: User
    access_token: str
    refresh_token: str
    expires_in: int
    permissions: tuple[str, ...]
    session: UserSession


@dataclass(frozen=True, slots=True)
class RequestContext:
    """The caller's network identity, carried for the audit trail."""

    request_id: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None


def validate_password_strength(password: str, *, email: str | None = None) -> None:
    """Doc 09 §9.2.1. Raises ValidationError listing **every** failure at once, so a user
    is not sent round the loop four times to learn four rules."""
    problems: list[dict[str, str]] = []

    def fail(code: str, message: str) -> None:
        problems.append({"field": "new_password", "code": code, "message": message})

    if len(password) < MIN_PASSWORD_LENGTH:
        fail("TOO_SHORT", f"At least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        fail("TOO_LONG", f"At most {MAX_PASSWORD_LENGTH} characters")
    if not re.search(r"[A-Z]", password):
        fail("NO_UPPERCASE", "At least one uppercase letter")
    if not re.search(r"[a-z]", password):
        fail("NO_LOWERCASE", "At least one lowercase letter")
    if not re.search(r"\d", password):
        fail("NO_DIGIT", "At least one digit")
    if not re.search(r"[^A-Za-z0-9]", password):
        fail("NO_SYMBOL", "At least one symbol")
    if password.lower() in COMMON_PASSWORDS:
        fail("TOO_COMMON", "This password appears in a list of common passwords")
    if email and password.lower() == email.split("@")[0].lower():
        fail("CONTAINS_IDENTITY", "The password must not be your username")

    if problems:
        raise ValidationError("The password does not meet the policy", details=problems)


class AuthService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.sessions = UserSessionRepository(session)
        self.audit = AuditService(session)
        self.reset_tokens = PasswordResetRepository(session)
        self.password_history = PasswordHistoryRepository(session)

    # -- login -------------------------------------------------------------
    def login(
        self,
        *,
        email: str,
        password: str,
        context: RequestContext | None = None,
        now: datetime | None = None,
    ) -> AuthenticatedSession:
        """Doc 06 §6.2. Unknown email and wrong password are indistinguishable to the
        caller — same code, same message, same work done."""
        moment = now or datetime.now(UTC)
        ctx = context or RequestContext()
        user = self.users.by_email(email)

        if user is None:
            # Hash anyway so the response time does not reveal whether the account exists.
            verify_password(password, _DUMMY_HASH)
            self._record_failure(None, email, ctx, moment, "UNKNOWN_EMAIL")
            raise UnauthorizedError("Email or password is incorrect")

        remaining = self.lock_seconds_remaining(user, moment)
        if remaining > 0:
            self._record_failure(user, email, ctx, moment, "LOCKED")
            raise AccountLockedError(remaining)

        if not verify_password(password, user.password_hash):
            self._register_failed_attempt(user, ctx, moment)
            raise UnauthorizedError("Email or password is incorrect")

        if not user.is_active:
            # Checked *after* the password so an attacker cannot enumerate which accounts
            # exist by watching for a different error on a wrong password.
            self._record_failure(user, email, ctx, moment, "INACTIVE")
            raise AccountInactiveError("This account has been deactivated")

        return self._issue(user, ctx, moment, reset_failures=True)

    def lock_seconds_remaining(self, user: User, now: datetime) -> int:
        """Zero once the lockout has elapsed. The stored ``locked_until`` is left in place
        as evidence; expiry is decided here, against the supplied instant."""
        if user.locked_until is None:
            return 0
        locked_until = _aware(user.locked_until)
        if locked_until <= now:
            return 0
        return int((locked_until - now).total_seconds())

    def _register_failed_attempt(
        self, user: User, ctx: RequestContext, now: datetime
    ) -> None:
        """Doc 09 §9.2.2 — 5 failures inside the window locks the account for 15 minutes.

        The counter and its audit row must survive the 401 that follows, so this commits
        deliberately. It is the one place a service commits, and it does so because the
        request transaction is about to be rolled back by the error handler.
        """
        user.failed_login_attempts = int(user.failed_login_attempts or 0) + 1
        locked = user.failed_login_attempts >= settings.max_failed_login_attempts
        if locked:
            user.locked_until = now + timedelta(minutes=settings.lockout_minutes)
            user.failed_login_attempts = 0

        self._record_failure(user, user.email, ctx, now, "BAD_PASSWORD")
        if locked:
            self.audit.record(
                action=audit_events.ACCOUNT_LOCKED,
                user_id=user.id,
                user_email=user.email,
                user_role=_role_code(user),
                entity_type="user",
                entity_id=user.id,
                entity_uuid=user.uuid,
                after={"locked_until": user.locked_until.isoformat()},
                request_id=ctx.request_id,
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
                status="FAILURE",
                error_message=(
                    f"Locked after {settings.max_failed_login_attempts} failed attempts"
                ),
                event_time=now,
            )
        self.session.commit()

    def _record_failure(
        self,
        user: User | None,
        email: str,
        ctx: RequestContext,
        now: datetime,
        reason: str,
    ) -> None:
        self.audit.record(
            action=audit_events.LOGIN_FAILURE,
            user_id=user.id if user else None,
            user_email=email,
            user_role=_role_code(user) if user else None,
            entity_type="user",
            entity_id=user.id if user else None,
            request_id=ctx.request_id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            status="FAILURE",
            error_message=reason,
            event_time=now,
        )
        if user is None:
            # Nothing else in this request will commit, and the failure must be recorded.
            self.session.commit()

    def _issue(
        self,
        user: User,
        ctx: RequestContext,
        now: datetime,
        *,
        reset_failures: bool = False,
        family_id: UUID | None = None,
        action: str = audit_events.LOGIN_SUCCESS,
    ) -> AuthenticatedSession:
        permissions = tuple(self.users.permissions_for_role(user.role_id))

        if reset_failures:
            user.failed_login_attempts = 0
            user.locked_until = None
            user.last_login_at = now
        if needs_rehash(user.password_hash):
            # Argon2 parameters were raised since this hash was made; the plaintext is not
            # available here, so this only flags it. Rehash happens on the next change.
            pass

        refresh_token = generate_refresh_token()
        session_row = self.sessions.create(
            user_id=user.id,
            refresh_token_hash=hash_refresh_token(refresh_token),
            expires_at=now + timedelta(days=settings.refresh_token_expire_days),
            family_id=family_id,
            ip_address=normalise_ip(ctx.ip_address),
            user_agent=ctx.user_agent,
        )

        access_token = create_access_token(
            subject=str(user.uuid),
            email=user.email,
            role=_role_code(user) or "",
            permissions=list(permissions),
            now=now,
        )

        self.audit.record(
            action=action,
            user_id=user.id,
            user_email=user.email,
            user_role=_role_code(user),
            entity_type="user",
            entity_id=user.id,
            entity_uuid=user.uuid,
            request_id=ctx.request_id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            event_time=now,
        )

        return AuthenticatedSession(
            user=user,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.access_token_expire_minutes * 60,
            permissions=permissions,
            session=session_row,
        )

    # -- refresh -----------------------------------------------------------
    def refresh(
        self,
        *,
        refresh_token: str,
        context: RequestContext | None = None,
        now: datetime | None = None,
    ) -> AuthenticatedSession:
        """Doc 09 §9.2.3 — rotation with reuse detection.

        Presenting an already-rotated token means it was captured: the whole family is
        revoked and a SECURITY_REFRESH_REUSE event is written. That turns a stolen refresh
        token from a persistent backdoor into a single-use event that trips an alarm.
        """
        moment = now or datetime.now(UTC)
        ctx = context or RequestContext()
        row = self.sessions.by_token_hash(hash_refresh_token(refresh_token))

        if row is None:
            raise UnauthorizedError("The refresh token is not recognised")

        if row.rotated_at is not None:
            self.sessions.revoke_family(
                row.family_id, at=moment, reason="REUSE_DETECTED"
            )
            self.audit.record(
                action=audit_events.SECURITY_REFRESH_REUSE,
                user_id=row.user_id,
                entity_type="user_session",
                entity_id=row.id,
                after={"family_id": str(row.family_id)},
                request_id=ctx.request_id,
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
                status="FAILURE",
                error_message="A rotated refresh token was presented again",
                event_time=moment,
            )
            self.session.commit()
            raise TokenReuseError(
                "This session has been revoked because a used token was presented again"
            )

        if row.revoked_at is not None:
            raise UnauthorizedError("This session has been revoked")

        if _aware(row.expires_at) <= moment:
            raise UnauthorizedError("This session has expired")

        if self._absolute_timeout_reached(row, moment):
            self.sessions.revoke_family(
                row.family_id, at=moment, reason="ABSOLUTE_TIMEOUT"
            )
            raise UnauthorizedError("This session has reached its maximum lifetime")

        user = self.users.active_by_id(row.user_id)
        if user is None or not user.is_active:
            raise AccountInactiveError("This account is no longer active")

        row.rotated_at = moment
        self.session.flush()

        return self._issue(
            user,
            ctx,
            moment,
            family_id=row.family_id,
            action=audit_events.TOKEN_REFRESHED,
        )

    def _absolute_timeout_reached(self, row: UserSession, now: datetime) -> bool:
        """Doc 09 §9.2.4 — 12 hours regardless of activity, measured from the family's
        first token, not from the most recent rotation."""
        limit = timedelta(hours=settings.session_absolute_timeout_hours)
        return _aware(row.issued_at) + limit <= now

    # -- logout ------------------------------------------------------------
    def logout(
        self,
        *,
        refresh_token: str,
        context: RequestContext | None = None,
        now: datetime | None = None,
    ) -> int:
        """Revokes the presented token and its family (Doc 09 §9.2.4)."""
        moment = now or datetime.now(UTC)
        ctx = context or RequestContext()
        row = self.sessions.by_token_hash(hash_refresh_token(refresh_token))
        if row is None:
            # Already gone. Report success: a logout that 404s tells an attacker which
            # tokens are real, and the caller's intent is satisfied either way.
            return 0

        revoked = self.sessions.revoke_family(row.family_id, at=moment, reason="LOGOUT")
        self.audit.record(
            action=audit_events.LOGOUT,
            user_id=row.user_id,
            entity_type="user_session",
            entity_id=row.id,
            after={"sessions_revoked": revoked},
            request_id=ctx.request_id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            event_time=moment,
        )
        return revoked

    def logout_all(
        self,
        *,
        user_id: int,
        context: RequestContext | None = None,
        now: datetime | None = None,
    ) -> int:
        moment = now or datetime.now(UTC)
        ctx = context or RequestContext()
        revoked = self.sessions.revoke_all_for_user(
            user_id, at=moment, reason="LOGOUT_ALL"
        )
        self.audit.record(
            action=audit_events.LOGOUT,
            user_id=user_id,
            entity_type="user",
            entity_id=user_id,
            after={"sessions_revoked": revoked, "scope": "ALL"},
            request_id=ctx.request_id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            event_time=moment,
        )
        return revoked

    # -- password ----------------------------------------------------------
    def change_password(
        self,
        *,
        user_id: int,
        current_password: str,
        new_password: str,
        context: RequestContext | None = None,
        now: datetime | None = None,
    ) -> User:
        """Doc 06 §6.2 — validates the policy, then revokes every other session because
        the old credential may already be in someone else's hands."""
        moment = now or datetime.now(UTC)
        ctx = context or RequestContext()
        user = self.users.active_by_id(user_id)
        if user is None:
            raise UnauthorizedError("This account no longer exists")

        if not verify_password(current_password, user.password_hash):
            self.audit.record(
                action=audit_events.PASSWORD_CHANGED,
                user_id=user.id,
                user_email=user.email,
                user_role=_role_code(user),
                entity_type="user",
                entity_id=user.id,
                request_id=ctx.request_id,
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
                status="FAILURE",
                error_message="Current password did not match",
                event_time=moment,
            )
            self.session.commit()
            raise UnauthorizedError("The current password is incorrect")

        validate_password_strength(new_password, email=user.email)
        self._reject_reused_password(user, new_password)
        self._set_password(user, new_password, moment)
        self.session.flush()

        revoked = self.sessions.revoke_all_for_user(
            user.id, at=moment, reason="PASSWORD_CHANGED"
        )
        self.audit.record(
            action=audit_events.PASSWORD_CHANGED,
            user_id=user.id,
            user_email=user.email,
            user_role=_role_code(user),
            entity_type="user",
            entity_id=user.id,
            entity_uuid=user.uuid,
            after={"sessions_revoked": revoked},
            request_id=ctx.request_id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            event_time=moment,
        )
        return user

    def request_password_reset(
        self,
        *,
        email: str,
        context: RequestContext | None = None,
        now: datetime | None = None,
    ) -> str | None:
        """FR-1.6 — issue a single-use, 30-minute reset token.

        Returns the plaintext token so a transport can send it; only its SHA-256 hash is
        stored, so a database copy cannot be turned back into a working link. The endpoint
        behaves identically for a registered and an unregistered address — an unknown email
        does the same work and returns the same 204 — because anything else turns this into
        an account-enumeration oracle.

        Delivery by email is the documented external dependency (Doc 01 D-4, SMTP is a
        Phase 5 item). The token is returned to the caller here; the endpoint decides
        whether it may ever be exposed.
        """
        moment = now or datetime.now(UTC)
        ctx = context or RequestContext()
        user = self.users.by_email(email)

        token: str | None = None
        if user is not None and user.is_active:
            # A previous outstanding token is retired: two live links would mean an older
            # email still works after the user asked again.
            self.reset_tokens.invalidate_outstanding(user.id, at=moment)
            token = generate_refresh_token()
            self.reset_tokens.create(
                user_id=user.id,
                token_hash=hash_refresh_token(token),
                issued_at=moment,
                expires_at=moment + timedelta(minutes=RESET_TOKEN_TTL_MINUTES),
                requested_ip=normalise_ip(ctx.ip_address),
            )

        self.audit.record(
            action=audit_events.PASSWORD_RESET_REQUESTED,
            user_id=user.id if user else None,
            user_email=email,
            user_role=_role_code(user) if user else None,
            entity_type="user",
            entity_id=user.id if user else None,
            request_id=ctx.request_id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            status="SUCCESS" if token else "FAILURE",
            error_message=None if token else "No active account for this address",
            event_time=moment,
        )
        self.session.commit()
        return token

    def reset_password(
        self,
        *,
        token: str,
        new_password: str,
        context: RequestContext | None = None,
        now: datetime | None = None,
    ) -> User:
        """FR-1.6 — redeem a reset token exactly once.

        Every failure mode returns the same message. A caller who cannot tell an expired
        token from an unknown one cannot use this endpoint to probe which tokens exist.
        """
        moment = now or datetime.now(UTC)
        ctx = context or RequestContext()
        row = self.reset_tokens.by_token_hash(hash_refresh_token(token))

        if row is None or row.used_at is not None or _aware(row.expires_at) <= moment:
            self.audit.record(
                action=audit_events.PASSWORD_CHANGED,
                entity_type="password_reset_token",
                entity_id=row.id if row else None,
                request_id=ctx.request_id,
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
                status="FAILURE",
                error_message="Reset token invalid, expired or already used",
                event_time=moment,
            )
            self.session.commit()
            raise UnauthorizedError(
                "This reset link is no longer valid. Request a new one.",
                code="TOKEN_EXPIRED",
            )

        user = self.users.active_by_id(row.user_id)
        if user is None or not user.is_active:
            raise AccountInactiveError("This account is no longer active")

        validate_password_strength(new_password, email=user.email)
        self._reject_reused_password(user, new_password)

        row.used_at = moment
        self._set_password(user, new_password, moment)
        self.session.flush()

        revoked = self.sessions.revoke_all_for_user(
            user.id, at=moment, reason="PASSWORD_RESET"
        )
        self.audit.record(
            action=audit_events.PASSWORD_CHANGED,
            user_id=user.id,
            user_email=user.email,
            user_role=_role_code(user),
            entity_type="user",
            entity_id=user.id,
            entity_uuid=user.uuid,
            after={"via": "RESET_TOKEN", "sessions_revoked": revoked},
            request_id=ctx.request_id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            event_time=moment,
        )
        return user

    def _reject_reused_password(self, user: User, new_password: str) -> None:
        """FR-1.2 — the last five hashes are kept so a password cannot be recycled."""
        if verify_password(new_password, user.password_hash):
            raise ValidationError(
                "The new password must differ from the current one",
                details=[{"field": "new_password", "code": "SAME_AS_CURRENT",
                          "message": "Choose a password you have not just used"}],
            )
        for previous in self.password_history.recent(
            user.id, limit=PASSWORD_HISTORY_DEPTH
        ):
            if verify_password(new_password, previous.password_hash):
                raise ValidationError(
                    f"This password was used recently. Choose one you have not used in "
                    f"your last {PASSWORD_HISTORY_DEPTH} passwords.",
                    details=[{"field": "new_password", "code": "RECENTLY_USED",
                              "message": f"Not one of your last {PASSWORD_HISTORY_DEPTH}"}],
                )

    def _set_password(self, user: User, new_password: str, moment: datetime) -> None:
        """Store the new hash and retain the old one for the reuse check."""
        self.password_history.record(
            user_id=user.id, password_hash=user.password_hash, at=moment
        )
        user.password_hash = hash_password(new_password)
        user.password_changed_at = moment
        user.must_change_password = False
        self.password_history.prune(user.id, keep=PASSWORD_HISTORY_DEPTH)

    # -- profile -----------------------------------------------------------
    # -- profile -----------------------------------------------------------
    def profile(self, user_uuid: UUID) -> tuple[User, tuple[str, ...]]:
        """``GET /auth/me`` — the user plus the permissions their role currently grants.

        Read from the database rather than echoed from the token, so a permission changed
        by an administrator is visible before the token expires.
        """
        user = self.users.by_uuid(user_uuid)
        if user is None or not user.is_active:
            raise UnauthorizedError("This account is no longer active")
        return user, tuple(self.users.permissions_for_role(user.role_id))

    def user_by_uuid(self, user_uuid: UUID) -> User:
        user = self.users.by_uuid(user_uuid)
        if user is None:
            raise UnauthorizedError("This account no longer exists")
        return user


#: A real Argon2id hash of a value nobody knows, so the unknown-email path does the same
#: work as the wrong-password path (Doc 09 §9.2.1 constant-time comparison).
_DUMMY_HASH = hash_password("evrca-timing-equaliser-do-not-use")


def _role_code(user: Any) -> str | None:
    role = getattr(user, "role", None)
    return getattr(role, "code", None) if role is not None else None


def _aware(value: datetime) -> datetime:
    """PostgreSQL returns TIMESTAMPTZ as aware; a naive value from a test is treated as
    UTC so comparisons never raise."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
