"""Pydantic response schemas for the HTTP API.

Wire conventions live here (see CLAUDE.md § HTTP API › Wire conventions):
every datetime is serialized as ISO 8601 with an explicit UTC offset, because
Swift's ``.iso8601`` decoding strategy requires a timezone designator and a
naive datetime simply fails to decode. Internals stay naive (SQLite ``mtime``
is a UTC Unix timestamp); UTC is attached at the boundary, in ``ApiModel``.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_serializer
from pydantic_core.core_schema import SerializerFunctionWrapHandler


def _to_utc_iso(value: datetime) -> str:
    """Naive datetimes are UTC by internal convention — attach the offset."""
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.isoformat()


class ApiModel(BaseModel):
    """Base for every API schema. Inherit from this, not from BaseModel.

    Serializes any datetime field as UTC ISO 8601 with an explicit offset, so
    the Swift client can use one ``JSONDecoder`` date strategy everywhere.
    """

    model_config = ConfigDict(extra="forbid")

    @field_serializer("*", mode="wrap", when_used="json")
    def _serialize_datetime(self, value: Any, nxt: SerializerFunctionWrapHandler) -> Any:
        if isinstance(value, datetime):
            return _to_utc_iso(value)
        return nxt(value)


class HealthResponse(ApiModel):
    status: Literal["loading", "ready", "error"]
    version: str
    documents: int | None  # null while loading, or when no index exists
    error: str | None  # message when status == "error"


class ErrorDetail(ApiModel):
    code: str
    message: str


class ErrorResponse(ApiModel):
    """The one error shape every endpoint uses: {"error": {"code", "message"}}."""

    error: ErrorDetail
