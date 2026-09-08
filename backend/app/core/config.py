"""Typed application settings — Doc 02 §2.17.

Every variable is validated at import time, so a misconfiguration is a startup error
rather than a 3 a.m. runtime surprise. Values come from the environment (see
``.env.example``); nothing risk-material is defined here — weights, thresholds and rule
parameters live in the database and are resolved by ``ConfigService``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "ci", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # -- application -------------------------------------------------------
    app_name: str = "EV-RCA"
    app_title: str = "EV Financing Risk Assessment & Portfolio Monitoring Platform"
    app_env: Environment = "local"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    api_version: str = "1.0.0"

    # -- database ----------------------------------------------------------
    database_url: str = Field(
        default="postgresql+psycopg://evrca:evrca@localhost:5432/evrca",
        description="Synchronous SQLAlchemy URL. Alembic uses the same value.",
    )
    db_pool_size: int = Field(default=5, ge=1, le=100)
    db_max_overflow: int = Field(default=10, ge=0, le=100)
    db_pool_pre_ping: bool = True
    db_echo: bool = False
    db_statement_timeout_ms: int = Field(default=30_000, ge=1_000)

    # -- auth --------------------------------------------------------------
    jwt_secret_key: str = Field(
        default="change-me-in-every-environment",
        description="HS256 signing key. RS256 with a mounted key pair in production.",
    )
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=15, ge=1, le=1440)
    refresh_token_expire_days: int = Field(default=7, ge=1, le=90)
    jwt_issuer: str = "evrca"
    jwt_audience: str = "evrca-api"

    # -- lockout and sessions — Doc 09 §9.2.2, §9.2.4 ----------------------
    max_failed_login_attempts: int = Field(default=5, ge=1, le=20)
    lockout_minutes: int = Field(default=15, ge=1, le=1440)
    failed_attempt_window_minutes: int = Field(default=15, ge=1, le=1440)
    session_idle_timeout_minutes: int = Field(default=30, ge=1, le=1440)
    session_absolute_timeout_hours: int = Field(default=12, ge=1, le=720)

    # -- CORS --------------------------------------------------------------
    cors_origins: str = "http://localhost:3000"

    # -- pagination --------------------------------------------------------
    default_page_size: int = Field(default=20, ge=1, le=100)
    max_page_size: int = Field(default=100, ge=1, le=500)

    # -- integrations ------------------------------------------------------
    cib_provider: Literal["mock", "live"] = "mock"
    telematics_provider: Literal["mock", "live"] = "mock"
    cib_report_validity_days: int = Field(default=90, ge=1, le=365)
    demo_seed: str = "20260908"

    # -- observability -----------------------------------------------------
    log_level: str = "INFO"
    metrics_enabled: bool = Field(
        default=True,
        description=(
            "Serve GET /metrics. Unauthenticated by necessity, so the endpoint must be "
            "restricted to the internal network at the ingress (Doc 06 6.13)."
        ),
    )

    @field_validator("jwt_secret_key")
    @classmethod
    def _reject_default_secret_in_production(cls, value: str, info) -> str:
        env = (info.data or {}).get("app_env")
        if env == "production" and value == "change-me-in-every-environment":
            raise ValueError(
                "JWT_SECRET_KEY still holds the default value. Set a real secret before "
                "running in production (Doc 09 §9.9)."
            )
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        """A wildcard is never acceptable outside local development (Doc 09 §9.5)."""
        origins = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        if self.app_env != "local" and "*" in origins:
            raise ValueError("CORS_ORIGINS may not contain '*' outside local development")
        return origins

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def docs_url(self) -> str | None:
        """Interactive docs are exposed everywhere except production, where they are
        served only on the internal network by the reverse proxy."""
        return None if self.is_production else f"{self.api_v1_prefix}/docs"

    @property
    def openapi_url(self) -> str:
        return f"{self.api_v1_prefix}/openapi.json"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached so the environment is read once per process."""
    return Settings()


settings = get_settings()
