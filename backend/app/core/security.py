"""Password hashing and JWT handling — Doc 09 §9.2.

Argon2id for passwords; short-lived HS256/RS256 access tokens carrying the caller's
permission set so authorisation needs no database round trip per request.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from jose import JWTError, jwt

from app.core.config import settings
from app.core.errors import UnauthorizedError

# Doc 09 §9.2.1 parameters.
_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32,
                         salt_len=16)

MIN_PASSWORD_LENGTH = 12


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time where argon2 allows; never raises on a bad hash."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def generate_refresh_token() -> str:
    """256 bits of entropy. Stored only as a SHA-256 hash (Doc 09 §9.2.3)."""
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_access_token(
    *,
    subject: str,
    email: str,
    role: str,
    permissions: list[str],
    expires_minutes: int | None = None,
    now: datetime | None = None,
) -> str:
    """``now`` is injectable so token expiry is testable without sleeping."""
    issued = now or datetime.now(UTC)
    expires = issued + timedelta(
        minutes=expires_minutes or settings.access_token_expire_minutes
    )
    claims: dict[str, Any] = {
        "sub": subject,
        "email": email,
        "role": role,
        "permissions": permissions,
        "jti": secrets.token_urlsafe(16),
        "iat": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    return jwt.encode(claims, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Verifies signature, expiry, issuer and audience. Raises UnauthorizedError."""
    try:
        return jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        )
    except JWTError as exc:
        raise UnauthorizedError(
            "Invalid or expired token", code="TOKEN_EXPIRED"
        ) from exc
