"""Alembic environment.

The database URL comes from ``DATABASE_URL`` so that alembic.ini carries no credentials
(Doc 09 §9.9 — no secrets in git). ``target_metadata`` points at the ORM so that
``alembic check`` can report drift between the models and the live schema.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env, or export it:\n"
            "  export DATABASE_URL=postgresql+psycopg://evrca:evrca@localhost:5432/evrca"
        )
    # Alembic runs synchronously; strip an async driver if one is configured.
    return url.replace("+asyncpg", "+psycopg")


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Keep autogenerate away from objects the raw DDL owns.

    Partitions are created by ``create_monthly_partitions()``, and materialised views are
    not modelled in the ORM at all. Autogenerate would otherwise propose dropping them.
    """
    if type_ == "table" and name is not None:
        if any(name.startswith(prefix) for prefix in
               ("vehicle_telemetry_", "battery_metrics_", "charging_sessions_")):
            return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
