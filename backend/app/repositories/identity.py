"""Users, refresh-token sessions and the audit trail — Doc 05 §5.3.1-5.3.3, §5.7.

Queries only. Lockout arithmetic, rotation policy and redaction all live in the services;
this module just reads and writes rows.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import joinedload

from app.models.identity import (
    PasswordHistory,
    PasswordResetToken,
    Permission,
    Role,
    RolePermission,
    User,
    UserSession,
)
from app.models.system import AuditLog
from app.repositories.base import BaseRepository, apply_sort


class UserRepository(BaseRepository[User]):
    model = User

    def by_email(self, email: str) -> User | None:
        """Case-insensitive by column type: ``users.email`` is CITEXT (Doc 05 §5.3.1).

        The role is eager-loaded because the caller always needs it to mint a token, and a
        lazy load here would be a second round trip on the hottest security path.
        """
        stmt = (
            select(User)
            .options(joinedload(User.role))
            .where(func.lower(User.email) == email.strip().lower())
            .where(User.deleted_at.is_(None))
            .limit(1)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def by_uuid(self, user_uuid: UUID) -> User | None:
        stmt = (
            select(User)
            .options(joinedload(User.role))
            .where(User.uuid == user_uuid)
            .where(User.deleted_at.is_(None))
            .limit(1)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def active_by_id(self, user_id: int) -> User | None:
        stmt = (
            select(User)
            .options(joinedload(User.role))
            .where(User.id == user_id)
            .where(User.deleted_at.is_(None))
            .limit(1)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def permissions_for_role(self, role_id: int) -> list[str]:
        """The permission codes a role grants, sorted so a token's claim set is stable."""
        stmt = (
            select(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role_id)
            .order_by(Permission.code)
        )
        return list(self.session.execute(stmt).scalars().all())

    def role_by_code(self, code: str) -> Role | None:
        stmt = select(Role).where(Role.code == code).limit(1)
        return self.session.execute(stmt).scalar_one_or_none()


    SORTABLE = frozenset({"created_at", "email", "full_name"})

    def list_users(
        self,
        *,
        offset: int,
        limit: int,
        role_code: str | None = None,
        is_active: bool | None = None,
        branch_code: str | None = None,
        q: str | None = None,
        sort: str | None = "full_name",
    ) -> tuple[Sequence[User], int]:
        stmt = (
            select(User)
            .options(joinedload(User.role))
            .where(User.deleted_at.is_(None))
        )
        if role_code:
            stmt = stmt.join(Role, Role.id == User.role_id).where(Role.code == role_code)
        if is_active is not None:
            stmt = stmt.where(User.is_active.is_(is_active))
        if branch_code:
            stmt = stmt.where(User.branch_code == branch_code)
        if q:
            stmt = stmt.where(User.full_name.ilike(f"%{q}%") | User.email.ilike(f"%{q}%"))
        stmt = apply_sort(stmt, User, sort, self.SORTABLE)
        return self.paginate(stmt, offset=offset, limit=limit)

    def list_roles(self) -> Sequence[Role]:
        return self.session.execute(select(Role).order_by(Role.code)).scalars().all()


class UserSessionRepository(BaseRepository[UserSession]):
    model = UserSession

    def by_token_hash(self, token_hash: str) -> UserSession | None:
        stmt = select(UserSession).where(
            UserSession.refresh_token_hash == token_hash
        ).limit(1)
        return self.session.execute(stmt).scalar_one_or_none()

    def create(
        self,
        *,
        user_id: int,
        refresh_token_hash: str,
        expires_at: datetime,
        family_id: UUID | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> UserSession:
        row = UserSession(
            user_id=user_id,
            refresh_token_hash=refresh_token_hash,
            expires_at=expires_at,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        if family_id is not None:
            row.family_id = family_id
        return self.add(row)

    def live_for_user(self, user_id: int) -> Sequence[UserSession]:
        stmt = (
            select(UserSession)
            .where(UserSession.user_id == user_id)
            .where(UserSession.revoked_at.is_(None))
            .order_by(UserSession.issued_at.desc())
        )
        return self.session.execute(stmt).scalars().all()

    def revoke_family(self, family_id: UUID, *, at: datetime, reason: str) -> int:
        """Doc 09 §9.2.3 — reuse detection revokes the whole family, not one row."""
        result = self.session.execute(
            update(UserSession)
            .where(UserSession.family_id == family_id)
            .where(UserSession.revoked_at.is_(None))
            .values(revoked_at=at, revoked_reason=reason)
        )
        self.session.flush()
        return int(result.rowcount or 0)

    def revoke_all_for_user(
        self, user_id: int, *, at: datetime, reason: str, except_family: UUID | None = None
    ) -> int:
        stmt = (
            update(UserSession)
            .where(UserSession.user_id == user_id)
            .where(UserSession.revoked_at.is_(None))
        )
        if except_family is not None:
            stmt = stmt.where(UserSession.family_id != except_family)
        result = self.session.execute(stmt.values(revoked_at=at, revoked_reason=reason))
        self.session.flush()
        return int(result.rowcount or 0)

    def purge_expired(self, *, before: datetime) -> int:
        """Used by the ``purge_expired_sessions`` job (TRD §2.10)."""
        rows = self.session.execute(
            select(UserSession).where(UserSession.expires_at < before)
        ).scalars().all()
        for row in rows:
            self.session.delete(row)
        self.session.flush()
        return len(rows)


class PasswordResetRepository(BaseRepository[PasswordResetToken]):
    """FR-1.6 — single-use reset tokens, stored as hashes only."""

    model = PasswordResetToken

    def create(self, **values: Any) -> PasswordResetToken:
        return self.add(PasswordResetToken(**values))

    def by_token_hash(self, token_hash: str) -> PasswordResetToken | None:
        stmt = select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash
        ).limit(1)
        return self.session.execute(stmt).scalar_one_or_none()

    def invalidate_outstanding(self, user_id: int, *, at: datetime) -> int:
        """Issuing a new token retires any earlier one.

        Two live tokens would mean an old email still works after the user asked again,
        which is exactly the window an attacker who saw the first one wants.
        """
        result = self.session.execute(
            update(PasswordResetToken)
            .where(PasswordResetToken.user_id == user_id)
            .where(PasswordResetToken.used_at.is_(None))
            .values(used_at=at)
        )
        self.session.flush()
        return int(result.rowcount or 0)


class PasswordHistoryRepository(BaseRepository[PasswordHistory]):
    """FR-1.2 — the hashes a new password is checked against."""

    model = PasswordHistory

    def recent(self, user_id: int, *, limit: int = 5) -> Sequence[PasswordHistory]:
        stmt = (
            select(PasswordHistory)
            .where(PasswordHistory.user_id == user_id)
            .order_by(PasswordHistory.changed_at.desc(), PasswordHistory.id.desc())
            .limit(limit)
        )
        return self.session.execute(stmt).scalars().all()

    def record(self, *, user_id: int, password_hash: str, at: datetime) -> PasswordHistory:
        return self.add(
            PasswordHistory(
                user_id=user_id, password_hash=password_hash, changed_at=at
            )
        )

    def prune(self, user_id: int, *, keep: int = 5) -> int:
        """Keep only what the policy needs. Retaining more hashes than the rule checks is
        storing password material for no purpose."""
        rows = list(self.recent(user_id, limit=keep + 50))
        stale = rows[keep:]
        for row in stale:
            self.session.delete(row)
        self.session.flush()
        return len(stale)


class AuditRepository(BaseRepository[AuditLog]):
    """Append-only: this repository deliberately exposes no update or delete."""

    model = AuditLog

    def record(self, **values: Any) -> AuditLog:
        row = AuditLog(**values)
        self.session.add(row)
        self.session.flush()
        return row

    def list_events(
        self,
        *,
        offset: int,
        limit: int,
        action: str | None = None,
        user_id: int | None = None,
        entity_type: str | None = None,
        status: str | None = None,
        since: datetime | None = None,
    ) -> tuple[Sequence[AuditLog], int]:
        stmt = select(AuditLog)
        if action:
            stmt = stmt.where(AuditLog.action == action)
        if user_id is not None:
            stmt = stmt.where(AuditLog.user_id == user_id)
        if entity_type:
            stmt = stmt.where(AuditLog.entity_type == entity_type)
        if status:
            stmt = stmt.where(AuditLog.status == status)
        if since is not None:
            stmt = stmt.where(AuditLog.event_time >= since)
        stmt = stmt.order_by(AuditLog.event_time.desc(), AuditLog.id.desc())
        return self.paginate(stmt, offset=offset, limit=limit)

    def count_recent_by_action(
        self, *, action: str, user_id: int, since: datetime
    ) -> int:
        """Doc 09 §9.8 — feeds the "10 PERMISSION_DENIED in 5 minutes" alert."""
        stmt = (
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == action)
            .where(AuditLog.user_id == user_id)
            .where(AuditLog.event_time >= since)
        )
        return int(self.session.execute(stmt).scalar_one())
