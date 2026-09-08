"""Engine, session factory and the request-scoped session dependency — Doc 02 §2.4.1.

Transaction policy: **one transaction per request**, committed by the dependency when the
handler returns cleanly and rolled back on any exception. Services therefore never call
``commit()`` themselves — they orchestrate, and the unit of work is the request. That is
what makes "the audit row commits with the business write or not at all" (Doc 09 §9.8)
true by construction rather than by discipline.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def create_db_engine(url: str | None = None) -> Engine:
    engine = create_engine(
        url or settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=settings.db_pool_pre_ping,
        echo=settings.db_echo,
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _set_statement_timeout(dbapi_connection, _record) -> None:
        """A runaway query must not hold a connection open indefinitely (Doc 09 §9.11)."""
        with dbapi_connection.cursor() as cursor:
            cursor.execute(f"SET statement_timeout = {settings.db_statement_timeout_ms}")

    return engine


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_db_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(
            bind=get_engine(), autoflush=False, expire_on_commit=False, future=True
        )
    return _SessionFactory


def configure_engine(engine: Engine) -> None:
    """Point the process at a specific engine. Used by the test fixtures to bind the
    application to a throwaway PostgreSQL instance."""
    global _engine, _SessionFactory
    _engine = engine
    _SessionFactory = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False, future=True
    )


def reset_engine() -> None:
    global _engine, _SessionFactory
    _engine = None
    _SessionFactory = None


def get_db() -> Iterator[Session]:
    """FastAPI dependency. One session and one transaction per request."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Same policy outside a request — used by CLI commands and background jobs."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database(session: Session) -> dict[str, object]:
    """Readiness probe — Doc 06 §6.13.

    Reports more than "can I connect": an API that cannot resolve an active scoring
    configuration cannot assess anything, so that is part of readiness.
    """
    result: dict[str, object] = {"database": "error", "migrations": "unknown",
                                 "active_configs": {}}
    session.execute(text("SELECT 1"))
    result["database"] = "ok"

    # A database built by applying db/schema.sql directly has no alembic_version table.
    # That is a migration-tracking gap, not a connectivity failure, so it is reported as
    # such rather than being allowed to fail the whole probe.
    tracked = session.execute(
        text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
    ).scalar_one()
    revision = (
        session.execute(
            text("SELECT version_num FROM alembic_version LIMIT 1")
        ).scalar_one_or_none()
        if tracked
        else None
    )
    result["migrations"] = revision or "not-applied"

    rows = session.execute(
        text(
            "SELECT config_type::text, version_no FROM scoring_configurations "
            "WHERE status = 'ACTIVE' ORDER BY config_type"
        )
    ).all()
    result["active_configs"] = {row[0]: row[1] for row in rows}
    return result
