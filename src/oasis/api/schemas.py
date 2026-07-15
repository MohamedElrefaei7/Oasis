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

from oasis.query.parser import ParsedQuery


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


class Segment(ApiModel):
    """One run of snippet text; segments, never offsets (CLAUDE.md § Snippet format)."""

    text: str
    match: bool


class DateRangeSchema(ApiModel):
    # ApiModel's serializer attaches the UTC offset the domain DateRange
    # (deliberately naive internally) doesn't carry.
    after: datetime | None = None
    before: datetime | None = None


class ParsedQuerySchema(ApiModel):
    """Wire mirror of query.parser.ParsedQuery.

    The domain model is not an ApiModel, so its naive datetimes would hit the
    wire without an offset and break Swift's .iso8601 decoder — mirror it at
    the boundary instead of changing the parser.
    """

    semantic_query: str
    file_types: list[str]
    date_range: DateRangeSchema | None
    folders: list[str]
    keywords: list[str]
    confidence: float

    @classmethod
    def from_domain(cls, parsed: ParsedQuery) -> ParsedQuerySchema:
        return cls(
            semantic_query=parsed.semantic_query,
            file_types=parsed.file_types,
            date_range=(
                DateRangeSchema(after=parsed.date_range.after, before=parsed.date_range.before)
                if parsed.date_range is not None
                else None
            ),
            folders=parsed.folders,
            keywords=parsed.keywords,
            confidence=parsed.confidence,
        )


class SearchResult(ApiModel):
    path: str
    title: str | None
    doc_id: int
    score: float
    snippet: list[Segment]


class SearchResponse(ApiModel):
    results: list[SearchResult]
    mode: str
    # Always present — the fallback ParsedQuery(semantic_query=q) when raw or
    # the LLM was unavailable. Lets the app render filter chips.
    parsed: ParsedQuerySchema
    llm_parsed: bool
    latency_ms: float  # server-side, retrieval + rerank only, warm
    db_path: str
