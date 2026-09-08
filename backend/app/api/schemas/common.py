"""Shared response shapes — Doc 06 §6.1.2 and §6.1.3."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any, Generic, TypeVar
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

ItemT = TypeVar("ItemT")


class ApiModel(BaseModel):
    """Base for every API schema. ``from_attributes`` lets responses be built directly
    from ORM rows without exposing the models themselves."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


#: The public identifier of any resource.
#:
#: Doc 05 §5.1 principle 4 — the API exposes the row's ``uuid``, never its BIGSERIAL, so
#: identifiers cannot be enumerated. Building a response straight from an ORM row would
#: otherwise pick up ``row.id``, so the alias reads ``uuid`` first and falls back to ``id``
#: for responses assembled from a mapper.
PublicId = Annotated[
    UUID,
    Field(
        validation_alias=AliasChoices("uuid", "id"),
        serialization_alias="id",
        description="Public resource identifier",
    ),
]


class Page(ApiModel, Generic[ItemT]):
    """The pagination envelope every collection endpoint returns."""

    items: Sequence[ItemT]
    total: int = Field(description="Total rows matching the filters, across all pages")
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    pages: int = Field(ge=0, description="Total number of pages")

    @classmethod
    def build(
        cls, items: Sequence[ItemT], *, total: int, page: int, page_size: int
    ) -> Page[ItemT]:
        pages = (total + page_size - 1) // page_size if page_size else 0
        return cls(items=items, total=total, page=page, page_size=page_size, pages=pages)


class ErrorDetail(ApiModel):
    field: str | None = None
    code: str | None = None
    message: str | None = None


class ErrorBody(ApiModel):
    code: str = Field(examples=["ROUTE_NOT_FOUND"])
    message: str = Field(examples=["Route was not found"])
    details: Any = Field(default_factory=dict)
    request_id: str | None = None
    timestamp: datetime | None = None


class ErrorResponse(ApiModel):
    """The single error envelope. Documented on every endpoint."""

    error: ErrorBody


class HealthResponse(ApiModel):
    status: str = Field(examples=["ok"])


class ReadinessResponse(ApiModel):
    status: str = Field(examples=["ok"])
    checks: dict[str, Any] = Field(
        description="database, migration revision and the active configuration versions"
    )


#: Reusable OpenAPI response documentation (Doc 06 §6.1.4).
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Business-rule violation"},
    401: {"model": ErrorResponse, "description": "Missing, invalid or expired token"},
    403: {"model": ErrorResponse, "description": "Authenticated but lacking the permission"},
    404: {"model": ErrorResponse, "description": "Not found, or outside the caller's scope"},
    409: {"model": ErrorResponse, "description": "Conflict, duplicate or invalid state"},
    422: {"model": ErrorResponse, "description": "Request validation failed"},
    500: {"model": ErrorResponse, "description": "Unexpected error; quote the request id"},
}

AUTH_RESPONSES = {k: v for k, v in ERROR_RESPONSES.items() if k in (401, 403, 500)}
