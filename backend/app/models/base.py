"""SQLAlchemy declarative base and shared mixins — Doc 05 §5.1, Doc 02 §2.6.

The ORM mirrors `db/schema.sql`; the SQL file remains the authority on DDL and Alembic
migration 001 applies it verbatim. ``tests/integration/test_model_schema_parity.py``
compares this metadata against a database built from that file, so the two cannot drift.
"""

from __future__ import annotations

import uuid as uuid_module
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared type map so money, scores and ratios have one definition each."""

    type_annotation_map = {
        Decimal: Numeric(14, 2),
        datetime: DateTime(timezone=True),
        date: Date,
        dict[str, Any]: JSONB,
        list[str]: ARRAY(Text),
    }


# ---------------------------------------------------------------------------
# Column factories — Doc 05 §5.1 principles 2, 3 and 11
# ---------------------------------------------------------------------------
def money(**kw: Any) -> Mapped[Decimal]:
    """NUMERIC(14,2). Never float — Doc 05 §5.1 principle 2."""
    return mapped_column(Numeric(14, 2), **kw)


def small_money(**kw: Any) -> Mapped[Decimal]:
    return mapped_column(Numeric(12, 2), **kw)


def score(**kw: Any) -> Mapped[Decimal]:
    """NUMERIC(5,2) — 0.00 to 100.00."""
    return mapped_column(Numeric(5, 2), **kw)


def ratio(**kw: Any) -> Mapped[Decimal]:
    """NUMERIC(6,4) — weights and ratios stored as fractions."""
    return mapped_column(Numeric(6, 4), **kw)


def pk() -> Mapped[int]:
    return mapped_column(BigInteger, primary_key=True, autoincrement=True)


def external_uuid() -> Mapped[uuid_module.UUID]:
    """The identifier the API exposes; the BIGSERIAL never leaves the database."""
    return mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True,
        server_default=func.gen_random_uuid(),
    )


def enum_column(name: str, *values: str, **kw: Any) -> Mapped[str]:
    """A PostgreSQL ENUM that already exists in the schema — never re-created here."""
    return mapped_column(
        SAEnum(*values, name=name, create_type=False, native_enum=True), **kw
    )


def fk(target: str, *, ondelete: str = "RESTRICT", **kw: Any) -> Mapped[int]:
    return mapped_column(BigInteger, ForeignKey(target, ondelete=ondelete), **kw)


# ---------------------------------------------------------------------------
# Mixins
# ---------------------------------------------------------------------------
class TimestampMixin:
    """``created_at``/``updated_at``. ``updated_at`` is maintained by a database
    trigger (Doc 05 §5.1 principle 8), so the ORM never sets it."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CreatedAtMixin:
    """Append-only tables carry only a creation timestamp."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SoftDeleteMixin:
    """Doc 05 §5.1 principle 6 — business entities are never hard-deleted."""

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class ActorMixin:
    """Who created and last changed the row."""

    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    updated_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )


class OptimisticLockMixin:
    """Doc 02 §2.12 — a stale write returns 409 rather than silently clobbering."""

    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


__all__ = [
    "ARRAY",
    "INET",
    "JSONB",
    "UUID",
    "ActorMixin",
    "Base",
    "BigInteger",
    "Boolean",
    "CreatedAtMixin",
    "Date",
    "DateTime",
    "ForeignKey",
    "Integer",
    "Mapped",
    "Numeric",
    "OptimisticLockMixin",
    "SmallInteger",
    "SoftDeleteMixin",
    "String",
    "Text",
    "TimestampMixin",
    "enum_column",
    "external_uuid",
    "fk",
    "func",
    "mapped_column",
    "money",
    "pk",
    "ratio",
    "score",
    "small_money",
]
