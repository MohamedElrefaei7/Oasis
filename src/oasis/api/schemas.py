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

    # Index capabilities — lets the app tell "this index predates vectors, tell
    # the user to reindex" apart from "semantic search returned nothing".
    # Null/false while loading, same as documents.
    vectors_built: bool = False
    embedding_model: str | None = None
    embedding_dimension: int | None = None
    # vectors_built AND they were built at the dimension the live embedder uses.
    # A dimension mismatch means the stored vectors are unusable even though
    # they exist, so this is what the app should gate its search box on.
    semantic_ready: bool = False
    # The index's recorded schema version; 0 when absent (pre-meta-table).
    schema_version: int = 0
    # Derived server-side (the client does no version math): there are
    # documents, and either the schema predates the current one or semantic
    # search isn't usable. A never-indexed DB (0 docs) is "index me", a
    # different state — this stays false there. The granular fields above are
    # kept so the app can word the prompt.
    reindex_recommended: bool = False


class StatusResponse(ApiModel):
    """GET /api/status — the token-gated detail view of the on-disk index.

    The authenticated counterpart to HealthResponse: everything health reports
    about index capability, plus the paths, sizes and disk-derived stats the
    unauth readiness probe deliberately omits. Capability fields are read from
    the same ``get_capabilities()`` health uses; ``semantic_ready`` and
    ``reindex_recommended`` are derived identically, so the two never disagree.
    """

    documents: int
    # SQLite file size, summing the .db and its -wal/-shm companions when present.
    db_size_bytes: int
    # UTC ISO 8601 with an offset via ApiModel's serializer; null if never indexed.
    last_indexed_at: datetime | None
    db_path: str

    # Capability fields — mirror get_capabilities() (see HealthResponse).
    schema_version: int  # 0 when absent (pre-meta-table / legacy index)
    vectors_built: bool
    embedding_model: str | None
    embedding_dimension: int | None
    # vectors_built AND built at the LIVE embedder's dimension — the same
    # comparison /api/health makes, so a dimension mismatch reads unusable here
    # too.
    semantic_ready: bool
    # Derived server-side, identical to /api/health: documents exist and either
    # the schema predates the current one or semantic search isn't usable.
    reindex_recommended: bool

    # Absolute directory roots this index covers — for the app's reindex button.
    # Empty when the index predates root tracking (treated as "unknown").
    indexed_roots: list[str]
    # Indexed documents whose file is gone from disk — what the app shows before
    # offering a targeted cleanup. NULL means "not computed": the index has more
    # than STALE_SCAN_CAP documents, so the per-file stat scan was skipped as too
    # costly and the app should offer a full reindex instead of a targeted sweep.
    # 0 means "computed, none stale" — a distinct state from null.
    stale_documents: int | None


class OpenRequest(ApiModel):
    path: str


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


# ---------------------------------------------------------------------------
# POST /api/index — request + job handle
# ---------------------------------------------------------------------------


class IndexRequest(ApiModel):
    """Body of POST /api/index.

    ``force`` is part of the contract *now* even though this commit only passes
    it through to ``index_directory(force=...)`` (re-embed everything, skip the
    unchanged check). The next commit gives it stale-sweep semantics; keeping it
    on the wire from the start means the Swift client never has to change shape.
    """

    root: str
    force: bool = False


class JobResponse(ApiModel):
    """202 body for POST /api/index — the handle the client polls SSE for."""

    job_id: str
    status: str  # JobStatus value: running | done | cancelled | error


class CancelRequest(ApiModel):
    """Body of POST /api/index/cancel — cancel is bound to a specific job.

    A bodyless "cancel whatever is running" loses a race once FSEvents-driven
    auto-reindex exists: a cancel tap aimed at job N can arrive after N finished
    and N+1 auto-started, and would silently kill N+1. The client holds the
    job_id from the 202, so requiring it costs nothing and closes that race.
    """

    job_id: str


# ---------------------------------------------------------------------------
# SSE events (GET /api/index/events)
#
# Every event is an ApiModel so ``.model_dump_json()`` runs the UTC-offset
# datetime serializer — a hand-rolled ``json.dumps`` would emit naive
# started_at/finished_at that Swift's ``.iso8601`` decoder silently rejects.
# Progress carries ABSOLUTE counts (``stats``/``done``/``total``), never
# deltas: delivery is lossy (throttled + droppable under overflow), so a delta
# stream would desync permanently on one dropped event, while absolute counts
# self-heal on the next tick. ``phase`` ("scan" | "embed") is how the consumer
# tells "still walking, total unknown" from "embedding, total known" — it keys
# off phase, never off "total is null".
# ---------------------------------------------------------------------------


class SnapshotEvent(ApiModel):
    """Sent once on connect, reflecting current job state (re-attach is first-class).

    ``status`` is ``idle`` when no job has ever run; otherwise the live or
    terminal status of the last job. The stream stays open only while
    ``running`` — a terminal or idle snapshot is followed by close.
    """

    type: Literal["snapshot"] = "snapshot"
    job_id: str | None
    status: str  # running | done | cancelled | error | idle
    root: str | None
    phase: str | None
    stats: dict[str, int]
    done: int
    total: int | None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


class ProgressEvent(ApiModel):
    type: Literal["progress"] = "progress"
    job_id: str
    phase: str  # scan | embed
    stats: dict[str, int]
    done: int
    total: int | None  # null during scan (lazy walk — count unknown until done)


class DoneEvent(ApiModel):
    type: Literal["done"] = "done"
    job_id: str
    stats: dict[str, int]  # final, authoritative — never dropped, so clients converge


class CancelledEvent(ApiModel):
    type: Literal["cancelled"] = "cancelled"
    job_id: str
    stats: dict[str, int]  # partial (committed work stays committed)


class ErrorEvent(ApiModel):
    type: Literal["error"] = "error"
    job_id: str
    message: str
