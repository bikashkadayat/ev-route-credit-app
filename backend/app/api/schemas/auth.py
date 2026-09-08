"""Authentication schemas — Doc 06 §6.2.

The login request validates length only. Telling an unauthenticated caller that their
password is "missing a symbol" would publish the policy to anyone guessing (Doc 09 §9.2.1);
the full policy is enforced, and explained, only where a password is *set*.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import EmailStr, Field

from app.api.schemas.common import ApiModel, PublicId


class LoginRequest(ApiModel):
    email: Annotated[EmailStr, Field(max_length=150)]
    password: Annotated[str, Field(min_length=8, max_length=128)]
    remember_me: bool = False


class RefreshRequest(ApiModel):
    refresh_token: Annotated[str, Field(min_length=20, max_length=512)]


class ChangePasswordRequest(ApiModel):
    current_password: Annotated[str, Field(min_length=1, max_length=128)]
    new_password: Annotated[str, Field(min_length=1, max_length=128)] = Field(
        description=(
            "At least 12 characters with upper, lower, digit and symbol; not a common "
            "password and not the current one."
        )
    )


class ForgotPasswordRequest(ApiModel):
    email: Annotated[EmailStr, Field(max_length=150)]


class ResetPasswordRequest(ApiModel):
    """Doc 06 §6.2 — redeem a reset token."""

    token: Annotated[str, Field(min_length=20, max_length=512)]
    new_password: Annotated[str, Field(min_length=1, max_length=128)] = Field(
        description=(
            "At least 12 characters with upper, lower, digit and symbol; not a common "
            "password, and not one of your last five."
        )
    )


class RoleOut(ApiModel):
    code: str
    name: str


class UserOut(ApiModel):
    """The caller's own identity. Returned by login and by ``GET /auth/me``."""

    id: PublicId
    email: str
    full_name: str
    role: RoleOut
    permissions: list[str] = Field(
        description="Read from the database, not echoed from the token"
    )
    must_change_password: bool
    branch_code: str | None = None
    last_login_at: datetime | None = None


class TokenResponse(ApiModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - the OAuth2 scheme name
    expires_in: int = Field(description="Access-token lifetime in seconds")
    refresh_token: str = Field(
        description="Opaque, single-use. Rotated on every refresh; reuse revokes the family."
    )
    user: UserOut
