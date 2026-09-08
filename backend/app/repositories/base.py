"""Repository base — Doc 02 §2.4.1.

Repositories are the **only** place SQLAlchemy queries appear. They persist and load; they
never score, never decide, and never open a transaction (the request owns it).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, session: Session) -> None:
        self.session = session

    # -- reads -------------------------------------------------------------
    def get(self, entity_id: int) -> ModelT | None:
        return self.session.get(self.model, entity_id)

    def get_by(self, **filters: Any) -> ModelT | None:
        stmt = select(self.model).filter_by(**filters).limit(1)
        return self.session.execute(stmt).scalar_one_or_none()

    def exists(self, **filters: Any) -> bool:
        stmt = select(func.count()).select_from(self.model).filter_by(**filters)
        return bool(self.session.execute(stmt).scalar_one())

    def count(self, stmt: Select[Any]) -> int:
        """Count the rows a filtered statement would return, without its ORDER BY."""
        counted = stmt.order_by(None).with_only_columns(func.count()).select_from(self.model)
        return int(self.session.execute(counted).scalar_one())

    def paginate(
        self, stmt: Select[Any], *, offset: int, limit: int
    ) -> tuple[Sequence[ModelT], int]:
        """Returns (page of rows, total matching rows). One count, one page."""
        total = self.count(stmt)
        rows = self.session.execute(stmt.offset(offset).limit(limit)).scalars().all()
        return rows, total

    # -- writes ------------------------------------------------------------
    def add(self, entity: ModelT) -> ModelT:
        """Adds and flushes so the caller sees server-generated ids and defaults.

        Flush, not commit: the request-scoped session owns the transaction boundary.
        """
        self.session.add(entity)
        self.session.flush()
        self.session.refresh(entity)
        return entity

    def flush(self) -> None:
        self.session.flush()


def apply_soft_delete_filter(stmt: Select[Any], model: type[Any]) -> Select[Any]:
    """Doc 05 §5.1 principle 6 — soft-deleted rows are invisible to every query."""
    if hasattr(model, "deleted_at"):
        return stmt.where(model.deleted_at.is_(None))
    return stmt


SORT_DIRECTIONS = {"asc", "desc"}


def apply_sort(
    stmt: Select[Any], model: type[Any], sort: str | None, allowed: frozenset[str]
) -> Select[Any]:
    """Doc 06 §6.1.2 — ``?sort=-created_at``, restricted to an allow-list.

    The allow-list is what stops a caller ordering by an unindexed or sensitive column.
    """
    if not sort:
        return stmt
    descending = sort.startswith("-")
    field = sort[1:] if descending else sort
    if field not in allowed:
        from app.core.errors import ValidationError

        raise ValidationError(
            f"Cannot sort by {field!r}",
            details=[{"field": "sort", "code": "NOT_SORTABLE",
                      "message": f"Allowed: {', '.join(sorted(allowed))}"}],
        )
    column = getattr(model, field)
    return stmt.order_by(column.desc() if descending else column.asc())
