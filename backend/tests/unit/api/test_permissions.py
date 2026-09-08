"""Authorisation — Doc 09 §9.3.

The point of ``require_permission`` is that authorisation is declared once, in the
endpoint signature. These tests pin the behaviour it must have for that to be trustworthy:
a missing or malformed credential is 401, a valid credential without the permission is
403, and the required permission is introspectable so the coverage test below can read it
off the route table.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request

from app.core.errors import PermissionDeniedError, UnauthorizedError
from app.core.permissions import (
    CurrentUser,
    get_current_user,
    require_any_permission,
    require_permission,
)
from app.core.security import create_access_token


def make_request(headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": raw, "method": "GET", "path": "/"})


def user(*permissions: str) -> CurrentUser:
    return CurrentUser(
        id="7", email="a@b.np", role="TESTER", permissions=frozenset(permissions)
    )


def test_has_and_require_agree():
    caller = user("route:read")
    assert caller.has("route:read")
    caller.require("route:read")


def test_require_raises_permission_denied_naming_the_permission():
    with pytest.raises(PermissionDeniedError) as exc:
        user("route:read").require("route:assess")
    assert exc.value.status_code == 403
    assert exc.value.code == "PERMISSION_DENIED"
    assert exc.value.details["required_permission"] == "route:assess"


def test_dependency_returns_the_caller_when_permitted():
    dependency = require_permission("route:read")
    assert dependency(user("route:read")).id == "7"


def test_dependency_rejects_a_caller_without_the_permission():
    dependency = require_permission("route:assess")
    with pytest.raises(PermissionDeniedError):
        dependency(user("route:read"))


def test_dependency_exposes_the_permission_it_enforces():
    """Read by the route-coverage test; also what makes an access-control review possible
    without executing anything."""
    assert require_permission("alert:resolve").required_permission == "alert:resolve"


def test_any_permission_admits_a_caller_holding_one_of_them():
    dependency = require_any_permission("alert:assign", "alert:resolve")
    assert dependency(user("alert:resolve")).id == "7"


def test_any_permission_rejects_a_caller_holding_none():
    dependency = require_any_permission("alert:assign", "alert:resolve")
    with pytest.raises(PermissionDeniedError):
        dependency(user("route:read"))


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": ""},
        {"Authorization": "Bearer"},
        {"Authorization": "Basic abc123"},
        {"Authorization": "Token abc123"},
    ],
    ids=["absent", "empty", "no-token", "basic", "wrong-scheme"],
)
def test_missing_or_malformed_credentials_are_401(headers):
    with pytest.raises(UnauthorizedError) as exc:
        get_current_user(make_request(headers))
    assert exc.value.status_code == 401


def test_a_garbage_token_is_401_not_500():
    with pytest.raises(UnauthorizedError):
        get_current_user(make_request({"Authorization": "Bearer not.a.jwt"}))


def test_claims_become_the_current_user():
    token = create_access_token(
        subject="42", email="risk@bank.com.np", role="RISK_MANAGER",
        permissions=["route:read", "route:assess"],
    )
    caller = get_current_user(make_request({"Authorization": f"Bearer {token}"}))
    assert caller.id == "42"
    assert caller.role == "RISK_MANAGER"
    assert caller.has("route:assess")
    assert not caller.has("risk:waiver")


def test_the_resolved_user_is_cached_on_the_request():
    """The audit middleware reads it back rather than decoding the token twice."""
    token = create_access_token(
        subject="42", email="a@b.np", role="VIEWER", permissions=["route:read"]
    )
    request = make_request({"Authorization": f"Bearer {token}"})
    resolved = get_current_user(request)
    assert request.state.current_user is resolved


# ---------------------------------------------------------------------------
# Every mutating endpoint is guarded
# ---------------------------------------------------------------------------
def _app_routes():
    from app.main import create_app

    app: FastAPI = create_app()
    return [r for r in app.routes if hasattr(r, "methods")]


#: Doc 06 §6.13 — probes and the metrics scrape carry no user identity, so they are
#: unauthenticated by specification and restricted by network placement instead.
UNAUTHENTICATED_PATHS = {"/health", "/health/live", "/health/ready", "/metrics", "/"}

#: Doc 06 §6.14 lists these with permission "—": they are how a caller *obtains* a
#: credential, so requiring one would be circular. They are still not open — the three
#: below are anonymous by specification, and the rest of /auth requires authentication,
#: which the companion test asserts.
ANONYMOUS_BY_SPEC = {
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/logout",
    "/api/v1/auth/forgot-password",
    # Redeeming a reset token *is* the credential: the token proves the holder controls
    # the mailbox, so requiring a session first would be circular. Rate-limited instead.
    "/api/v1/auth/reset-password",
}

#: Authenticated but not permission-scoped: they act on the caller's own account, so the
#: identity *is* the authorisation (Doc 06 §6.2).
SELF_SERVICE_PATHS = {
    "/api/v1/auth/me",
    "/api/v1/auth/logout-all",
    "/api/v1/auth/change-password",
}


def _is_infrastructure(path: str) -> bool:
    return "openapi" in path or path.endswith(("/docs", "/redoc", "/docs/oauth2-redirect"))


def test_every_business_endpoint_declares_a_permission():
    """A new router that forgets its dependency fails here rather than shipping open."""
    unguarded = []
    for route in _app_routes():
        if route.path in UNAUTHENTICATED_PATHS or _is_infrastructure(route.path):
            continue
        if route.path in ANONYMOUS_BY_SPEC or route.path in SELF_SERVICE_PATHS:
            continue
        source = "".join(
            str(getattr(d.call, "required_permission", "")) for d in route.dependant.dependencies
        )
        if not source:
            unguarded.append(f"{sorted(route.methods)} {route.path}")
    assert not unguarded, f"endpoints without an authorisation dependency: {unguarded}"


def test_the_anonymous_allowlist_stays_closed():
    """The exemption above is a list, not a rule. Any new unguarded endpoint has to be
    added here deliberately, which is the point at which someone asks whether it should
    be open at all."""
    exempt = set()
    for route in _app_routes():
        if route.path in UNAUTHENTICATED_PATHS or _is_infrastructure(route.path):
            continue
        guarded = any(
            getattr(d.call, "required_permission", None) for d in route.dependant.dependencies
        )
        if not guarded:
            exempt.add(route.path)
    assert exempt <= (ANONYMOUS_BY_SPEC | SELF_SERVICE_PATHS), (
        f"unguarded endpoints not on the allowlist: {sorted(exempt - ANONYMOUS_BY_SPEC - SELF_SERVICE_PATHS)}"
    )


def test_self_service_endpoints_still_require_authentication():
    """They carry no permission, so the only thing standing between them and the public
    internet is the ``get_current_user`` dependency. Assert it is there."""
    from app.core.permissions import get_current_user

    for route in _app_routes():
        if route.path not in SELF_SERVICE_PATHS:
            continue
        calls = {d.call for d in route.dependant.dependencies}
        assert get_current_user in calls, f"{route.path} does not authenticate"


def test_write_endpoints_never_require_only_a_read_permission():
    """A POST guarded by ``*:read`` would be an escalation; catch it mechanically."""
    offenders = []
    for route in _app_routes():
        if not (route.methods & {"POST", "PUT", "PATCH", "DELETE"}):
            continue
        required = [
            getattr(d.call, "required_permission", None)
            for d in route.dependant.dependencies
        ]
        flat = [p for p in required if isinstance(p, str)]
        if flat and all(p.endswith(":read") for p in flat):
            offenders.append(f"{sorted(route.methods)} {route.path} -> {flat}")
    assert not offenders, f"write endpoints guarded by a read permission: {offenders}"
