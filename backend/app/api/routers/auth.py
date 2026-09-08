"""Authentication endpoints — Doc 06 §6.2, PRD FR-1.1 to FR-1.7.

Thin, like every other router: read the request, build the network context, call
``AuthService``, map the result. No credential comparison, no lockout arithmetic and no
token minting happens here.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.mappers import auth_user_out
from app.api.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    RefreshRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserOut,
)
from app.api.schemas.common import ERROR_RESPONSES
from app.core import ratelimit
from app.core.dependencies import AuthServiceDep
from app.core.permissions import CurrentUser, get_current_user
from app.services.auth import RequestContext

router = APIRouter(prefix="/auth", tags=["Authentication"], responses=ERROR_RESPONSES)


def request_context(request: Request) -> RequestContext:
    """The caller's network identity, for the audit trail (Doc 09 §9.8)."""
    return RequestContext(
        request_id=getattr(request.state, "request_id", None),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("User-Agent"),
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Sign in",
    description=(
        "Issues a short-lived access token and a rotating refresh token. An unknown email "
        "and a wrong password produce the identical response, so the endpoint cannot be "
        "used to discover which accounts exist. Five failures within the window lock the "
        "account for 15 minutes and return 423 with the remaining time."
    ),
    responses={
        **ERROR_RESPONSES,
        423: {"description": "Account temporarily locked; details carry retry_after_seconds"},
    },
)
def login(
    payload: LoginRequest,
    request: Request,
    service: AuthServiceDep,
) -> TokenResponse:
    # Doc 09 §9.6 — five per minute per IP. The per-account lockout alone would not stop a
    # run spread across many accounts, because each account would only see one failure.
    ratelimit.check("login", _client_identity(request), ratelimit.LOGIN_LIMIT)
    result = service.login(
        email=payload.email,
        password=payload.password,
        context=request_context(request),
    )
    return TokenResponse(
        access_token=result.access_token,
        expires_in=result.expires_in,
        refresh_token=result.refresh_token,
        user=auth_user_out(result.user, result.permissions),
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate the session",
    description=(
        "Returns a new access **and** refresh token. The presented token is marked "
        "rotated; presenting it again is treated as theft — the whole session family is "
        "revoked and a SECURITY_REFRESH_REUSE audit event is written."
    ),
)
def refresh(
    payload: RefreshRequest,
    request: Request,
    service: AuthServiceDep,
) -> TokenResponse:
    result = service.refresh(
        refresh_token=payload.refresh_token, context=request_context(request)
    )
    return TokenResponse(
        access_token=result.access_token,
        expires_in=result.expires_in,
        refresh_token=result.refresh_token,
        user=auth_user_out(result.user, result.permissions),
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out of this session",
    description="Revokes the presented refresh token and its family.",
)
def logout(
    payload: RefreshRequest,
    request: Request,
    service: AuthServiceDep,
) -> Response:
    service.logout(refresh_token=payload.refresh_token, context=request_context(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out everywhere",
    description="Revokes every session for the authenticated user.",
)
def logout_all(
    request: Request,
    service: AuthServiceDep,
    user: CurrentUser = Depends(get_current_user),
) -> Response:
    row = service.user_by_uuid(UUID(user.id))
    service.logout_all(user_id=row.id, context=request_context(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/me",
    response_model=UserOut,
    summary="The signed-in user",
    description=(
        "Permissions are read from the database rather than echoed from the token, so a "
        "permission an administrator has just revoked is visible before the token expires."
    ),
)
def me(
    service: AuthServiceDep,
    user: CurrentUser = Depends(get_current_user),
) -> UserOut:
    row, permissions = service.profile(UUID(user.id))
    return auth_user_out(row, permissions)


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change your password",
    description=(
        "Requires the current password. The new one must be at least 12 characters with "
        "upper, lower, digit and symbol, must not be a common password and must differ "
        "from the current one. Every other session is revoked on success."
    ),
)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    service: AuthServiceDep,
    user: CurrentUser = Depends(get_current_user),
) -> Response:
    row = service.user_by_uuid(UUID(user.id))
    service.change_password(
        user_id=row.id,
        current_password=payload.current_password,
        new_password=payload.new_password,
        context=request_context(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/forgot-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Request a password reset",
    description=(
        "Issues a single-use token valid for 30 minutes and retires any earlier one.\n\n"
        "Always returns 204 whether or not the email is registered, and does the same work "
        "either way, so the endpoint cannot be used to discover accounts. The token is "
        "never in the response — putting it there, even outside production, would make a "
        "registered address distinguishable from an unregistered one.\n\n"
        "Delivery is by email, the documented external dependency (Doc 01 D-4)."
    ),
)
def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    service: AuthServiceDep,
) -> Response:
    ratelimit.check(
        "password-reset-request", _client_identity(request),
        ratelimit.RESET_REQUEST_LIMIT,
    )
    service.request_password_reset(
        email=payload.email, context=request_context(request)
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/reset-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Redeem a reset token",
    description=(
        "FR-1.6. The token is single-use and expires after 30 minutes. Every failure — "
        "unknown, expired or already redeemed — returns the same message, so the endpoint "
        "cannot be used to probe which tokens exist.\n\n"
        "The new password is held to the full policy, including the rule that it may not "
        "be one of the last five. Every session is revoked on success."
    ),
)
def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    service: AuthServiceDep,
) -> Response:
    ratelimit.check(
        "password-reset-redeem", _client_identity(request),
        ratelimit.RESET_REDEEM_LIMIT,
    )
    service.reset_password(
        token=payload.token,
        new_password=payload.new_password,
        context=request_context(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _client_identity(request: Request) -> str:
    """The rate-limit bucket key.

    The direct peer, not a forwarded header: an attacker controls X-Forwarded-For unless a
    trusted proxy overwrites it, and trusting it here would let one host present itself as
    thousands.
    """
    return request.client.host if request.client else "unknown"
