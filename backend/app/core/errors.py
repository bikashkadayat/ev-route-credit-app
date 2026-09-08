"""Typed domain exceptions and the single API error envelope — Doc 06 §6.1.3.

Every non-2xx response has exactly one shape. Services raise typed exceptions; the
handlers registered in ``app.main`` translate them. Routers never build error payloads
by hand, which is what keeps the envelope consistent across 40-odd endpoints.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any

from fastapi import Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# Doc 06 §6.1.5 — the closed set of application error codes.
ERROR_CODES = frozenset({
    "VALIDATION_ERROR", "INVALID_CREDENTIALS", "ACCOUNT_LOCKED", "ACCOUNT_INACTIVE",
    "TOKEN_EXPIRED", "TOKEN_REUSE_DETECTED", "PERMISSION_DENIED", "RESOURCE_NOT_FOUND",
    "DUPLICATE_RESOURCE", "STALE_VERSION", "INVALID_STATE_TRANSITION",
    "CONFIG_NOT_PUBLISHED", "WEIGHTS_NOT_100", "ROUTE_ASSESSMENT_STALE",
    "KNOCKOUT_TRIGGERED", "POLICY_LIMIT_EXCEEDED", "OVERRIDE_JUSTIFICATION_REQUIRED",
    "EXTERNAL_PROVIDER_UNAVAILABLE", "IDEMPOTENCY_KEY_REUSED", "RATE_LIMIT_EXCEEDED",
    "INCOMPLETE_DATA", "INTERNAL_ERROR", "SERVICE_UNAVAILABLE",
})


class AppError(Exception):
    """Base for every error the API deliberately produces."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "VALIDATION_ERROR"

    def __init__(
        self,
        message: str,
        *,
        details: Any = None,
        code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details if details is not None else {}
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code

    def to_payload(self, request_id: str | None = None) -> dict[str, Any]:
        return error_payload(
            code=self.code,
            message=self.message,
            details=self.details,
            request_id=request_id,
        )


# ---------------------------------------------------------------------------
# 4xx
# ---------------------------------------------------------------------------
class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "RESOURCE_NOT_FOUND"

    def __init__(self, resource: str, identifier: Any = None) -> None:
        message = f"{resource} was not found"
        super().__init__(message, details={"resource": resource, "id": str(identifier)}
                         if identifier is not None else {"resource": resource})
        # Doc 09 §9.3.2 — a resource outside the caller's scope is a 404, never a 403,
        # so the API does not confirm that an id exists.
        self.code = f"{resource.upper().replace(' ', '_')}_NOT_FOUND"


class DuplicateError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "DUPLICATE_RESOURCE"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "INVALID_STATE_TRANSITION"


class StaleVersionError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "STALE_VERSION"


class ValidationError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "VALIDATION_ERROR"


class IncompleteDataError(AppError):
    """A record exists but lacks fields an operation requires (Doc 06 §6.3)."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "INCOMPLETE_DATA"

    def __init__(self, message: str, missing_fields: list[str]) -> None:
        super().__init__(
            message,
            details=[
                {"field": f, "code": "MISSING", "message": "This field is required"}
                for f in missing_fields
            ],
        )


class UnauthorizedError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "INVALID_CREDENTIALS"


class PermissionDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "PERMISSION_DENIED"

    def __init__(self, permission: str) -> None:
        super().__init__(
            f"This action requires the '{permission}' permission",
            details={"required_permission": permission},
        )


class AccountLockedError(AppError):
    """Doc 06 §6.2 — 423 with the remaining lockout, so a client can show a countdown
    instead of inviting the user to keep guessing."""

    status_code = status.HTTP_423_LOCKED
    code = "ACCOUNT_LOCKED"

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            "This account is temporarily locked after too many failed sign-in attempts",
            details={"retry_after_seconds": retry_after_seconds},
        )
        self.retry_after_seconds = retry_after_seconds


class AccountInactiveError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "ACCOUNT_INACTIVE"


class TokenReuseError(AppError):
    """Doc 09 §9.2.3 — a rotated refresh token presented again means it was stolen. The
    whole family is revoked before this is raised."""

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "TOKEN_REUSE_DETECTED"


class ConfigurationNotPublishedError(AppError):
    """No ACTIVE scoring configuration exists for a type (Doc 06 §6.3)."""

    status_code = status.HTTP_409_CONFLICT
    code = "CONFIG_NOT_PUBLISHED"

    def __init__(self, config_type: str) -> None:
        super().__init__(
            f"No active {config_type} scoring configuration has been published. "
            f"An administrator must publish one before assessments can run.",
            details={"config_type": config_type},
        )


class ProviderUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "EXTERNAL_PROVIDER_UNAVAILABLE"


# ---------------------------------------------------------------------------
# envelope
# ---------------------------------------------------------------------------
def error_payload(
    *, code: str, message: str, details: Any = None, request_id: str | None = None
) -> dict[str, Any]:
    from datetime import datetime

    return {
        "error": {
            "code": code,
            "message": message,
            "details": details if details is not None else {},
            "request_id": request_id,
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
    }


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _record_permission_denial(request: Request, exc: PermissionDeniedError) -> None:
    """Doc 09 §9.8 — a refusal is a security event.

    Ten of these from one caller in five minutes is the documented alert for someone
    probing what their token can reach, so a refusal has to leave a trace. It is written
    here, where the 403 is actually produced, rather than in the dependency: the request
    transaction has already been rolled back by the time we get here, so this opens its own
    session and commits, exactly as a failed login does.

    The imports are local because ``app.core`` must not depend on ``app.services`` at
    module level.
    """
    try:
        from app.core.database import get_session_factory
        from app.services import audit as audit_events
        from app.services.audit import AuditService

        user = getattr(request.state, "current_user", None)
        with get_session_factory()() as session:
            AuditService(session).record(
                action=audit_events.PERMISSION_DENIED,
                actor=user,
                entity_type="endpoint",
                request_id=_request_id(request),
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("User-Agent"),
                status="FAILURE",
                error_message=(
                    f"{request.method} {request.url.path} requires "
                    f"{exc.details.get('required_permission')}"
                ),
            )
            session.commit()
    except Exception:
        # The caller must still get their 403; a failure to log is not grounds for a 500.
        return


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    if isinstance(exc, PermissionDeniedError):
        _record_permission_denial(request, exc)
    return JSONResponse(
        status_code=exc.status_code,
        content=jsonable_encoder(exc.to_payload(_request_id(request))),
        headers={"X-Request-ID": _request_id(request) or ""},
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Map FastAPI's validation errors onto the same envelope, one entry per field."""
    details = [
        {
            "field": ".".join(str(p) for p in err["loc"] if p not in ("body", "query")),
            "code": err["type"].upper(),
            "message": err["msg"],
        }
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=jsonable_encoder(
            error_payload(
                code="VALIDATION_ERROR",
                message="Request validation failed",
                details=details,
                request_id=_request_id(request),
            )
        ),
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Never leak an internal message to the client; the request id links it to the log."""
    import logging

    logging.getLogger("app.error").exception(
        "unhandled exception", extra={"request_id": _request_id(request)}
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=jsonable_encoder(
            error_payload(
                code="INTERNAL_ERROR",
                message="An unexpected error occurred. Quote the request id when reporting it.",
                request_id=_request_id(request),
            )
        ),
    )
