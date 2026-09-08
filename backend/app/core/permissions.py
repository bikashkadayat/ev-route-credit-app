"""Authorisation — Doc 02 §2.8.2, Doc 09 §9.3.

``require_permission`` is the *only* place an endpoint's authorisation is expressed, and
it appears in the endpoint signature so an auditor can read authorisation straight off the
router file. No router ever inspects a role by hand.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import Depends, Request

from app.core.errors import PermissionDeniedError, UnauthorizedError
from app.core.security import decode_access_token


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """The authenticated caller, built from verified token claims."""

    id: str
    email: str
    role: str
    permissions: frozenset[str] = field(default_factory=frozenset)
    branch_code: str | None = None

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    def require(self, permission: str) -> None:
        if not self.has(permission):
            raise PermissionDeniedError(permission)

    @property
    def is_head_office(self) -> bool:
        return self.branch_code in (None, "HO")


def _extract_bearer(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise UnauthorizedError("Missing or malformed Authorization header")
    return token


def get_current_user(request: Request) -> CurrentUser:
    """Resolve the caller from the Bearer token. Raises 401 when absent or invalid."""
    claims = decode_access_token(_extract_bearer(request))
    user = CurrentUser(
        id=str(claims.get("sub", "")),
        email=str(claims.get("email", "")),
        role=str(claims.get("role", "")),
        permissions=frozenset(claims.get("permissions") or ()),
        branch_code=claims.get("branch_code"),
    )
    # Cached on the request so the audit middleware and services can read it without
    # decoding the token a second time.
    request.state.current_user = user
    return user


def require_permission(permission: str) -> Callable[..., CurrentUser]:
    """Build a dependency that admits only callers holding ``permission``.

        @router.post("/routes", dependencies=[Depends(require_permission("route:create"))])

    or, when the handler needs the caller:

        user: CurrentUser = Depends(require_permission("route:create"))
    """

    def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        user.require(permission)
        return user

    dependency.__name__ = f"require_{permission.replace(':', '_')}"
    dependency.required_permission = permission  # type: ignore[attr-defined]
    return dependency


def require_any_permission(*permissions: str) -> Callable[..., CurrentUser]:
    """Admits a caller holding at least one of the listed permissions."""

    def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not any(user.has(p) for p in permissions):
            raise PermissionDeniedError(" or ".join(permissions))
        return user

    dependency.__name__ = "require_any_" + "_".join(
        p.replace(":", "_") for p in permissions
    )
    dependency.required_permission = permissions  # type: ignore[attr-defined]
    return dependency
