"""GET /api/status — the authenticated detail view of the on-disk index.

The token-gated counterpart to ``/api/health``. Health is the unauth readiness
probe ("is the server up?") and deliberately exposes no paths; status answers
"tell me about the index" and is what the app's manage-index screen reads. It
reuses ``get_capabilities()`` — the capability DB logic is never duplicated —
and derives ``semantic_ready`` / ``reindex_recommended`` exactly as health does,
so the two endpoints never disagree.

**404 vs 200.** A missing index at ``db_path`` is a real ``404`` for this
endpoint (mirroring the CLI's "no index yet"), unlike health, which always
``200``s: health says whether the server is up, status describes the index, and
no-index is the not-found answer to the latter. An index that exists but is
empty (0 documents) is a ``200``, not a ``404``.

Sync ``def``: ``count_stale()`` does one blocking ``stat()`` per document, which
belongs in the threadpool, not on the event loop.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from starlette.exceptions import HTTPException as StarletteHTTPException

from oasis.api.schemas import StatusResponse
from oasis.api.state import AppState, get_conn
from oasis.index.db import db_size_bytes
from oasis.index.keyword import KeywordIndex

# Above this document count, count_stale()'s per-file stat scan is too costly to
# run on a status request (O(documents) filesystem hits), so stale_documents is
# reported as null ("not computed", distinct from 0 = "computed, none stale")
# and the app offers a full reindex instead of a targeted sweep.
STALE_SCAN_CAP = 5000

router = APIRouter()


@router.get("/status", response_model=StatusResponse)
def status(request: Request) -> StatusResponse:
    state: AppState = request.app.state.oasis
    assert state.db_path is not None  # ready implies loaded

    if not state.db_path.exists():
        # No index on disk — a real not-found for THIS endpoint (health always
        # 200s). Do NOT open_db here: it would create the file as a side effect,
        # turning "no index" into an empty one.
        raise StarletteHTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "No index exists at the configured path."},
        )

    idx = KeywordIndex(get_conn(state.db_path))
    caps = idx.get_capabilities()

    # Both derivations live on IndexCapabilities, so /api/health and this
    # endpoint compute them from one implementation rather than two copies —
    # the "these two can't disagree" promise is now structural.
    live_dimension = state.embedder.dimension if state.embedder is not None else None
    semantic_ready = caps.semantic_ready(live_dimension)
    reindex_recommended = caps.reindex_recommended(live_dimension)

    last_at = idx.last_indexed_at()
    # mtime/indexed_at are UTC Unix timestamps; make the datetime UTC-aware so
    # the wire carries an offset (ApiModel serializes it as ISO 8601 +00:00).
    last_indexed_at = datetime.fromtimestamp(last_at, tz=UTC) if last_at is not None else None

    # Gate the stale scan on the cap using the count we already have — over the
    # cap we skip the scan entirely (no per-file stat) and report null.
    if caps.document_count > STALE_SCAN_CAP:
        stale_documents: int | None = None
    else:
        stale_documents = idx.count_stale()

    return StatusResponse(
        documents=caps.document_count,
        db_size_bytes=db_size_bytes(state.db_path),
        last_indexed_at=last_indexed_at,
        db_path=str(state.db_path),
        schema_version=caps.schema_version,
        vectors_built=caps.vectors_built,
        embedding_model=caps.embedding_model,
        embedding_dimension=caps.embedding_dimension,
        semantic_ready=semantic_ready,
        reindex_recommended=reindex_recommended,
        indexed_roots=idx.get_indexed_roots(),
        stale_documents=stale_documents,
    )
