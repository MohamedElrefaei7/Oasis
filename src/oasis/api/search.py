"""GET /api/search — the one retrieval endpoint.

Sync ``def``: torch inference, SQLite, and LanceDB are blocking work and
belong in the threadpool, not on the event loop (CLAUDE.md § Concurrency).
Auth + readiness come from protected_router, which includes this router.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from oasis.api.schemas import (
    ParsedQuerySchema,
    SearchResponse,
    SearchResult,
    Segment,
)
from oasis.api.state import AppState, get_conn
from oasis.query.parser import ParsedQuery, parse_query
from oasis.query.retriever import DEFAULT_TOP_N
from oasis.query.search import SearchMode, run_search
from oasis.query.snippets import to_segments

_log = logging.getLogger(__name__)

router = APIRouter()


def _fallback_query(q: str) -> ParsedQuery:
    try:
        return ParsedQuery(semantic_query=q)
    except ValidationError:
        # Whitespace-only q survives FastAPI's min_length check.
        raise StarletteHTTPException(
            status_code=400,
            detail={"code": "bad_request", "message": "Query must not be empty."},
        ) from None


def _parse(q: str, raw: bool, state: AppState) -> tuple[ParsedQuery, bool]:
    """Resolve the ParsedQuery; search never fails because the LLM is absent.

    raw defaults to True at the endpoint: the eval measured NL parsing as a
    −0.108 ndcg@10 / −0.135 recall@10 regression on hybrid+CE, so parsing-off
    is the best-measured path and the LLM never runs unless asked for.
    """
    if raw:
        return _fallback_query(q), False
    # Cached provider from startup — never ensure_ollama() per request, which
    # would spawn an `ollama list` subprocess per query.
    llm = state.llm
    if llm is None:
        return _fallback_query(q), False
    try:
        return parse_query(q, llm), True
    except Exception:
        _log.warning("NL parse failed; falling back to raw query", exc_info=True)
        return _fallback_query(q), False


@router.get("/search", response_model=SearchResponse)
def search(
    request: Request,
    q: Annotated[str, Query(min_length=1, description="Raw query text")],
    mode: SearchMode = SearchMode.hybrid,
    limit: Annotated[int, Query(ge=1)] = DEFAULT_TOP_N,
    raw: Annotated[bool, Query(description="Skip NL parsing (the best-measured path)")] = True,
) -> SearchResponse:
    state: AppState = request.app.state.oasis
    assert state.db_path is not None and state.embedder is not None  # ready implies loaded
    assert state.vector_index is not None

    parsed, llm_parsed = _parse(q, raw, state)
    conn = get_conn(state.db_path)

    # Timed around retrieval + rerank only — the LLM parse is excluded so the
    # number matches what the eval times and stays comparable when raw=false.
    start = time.perf_counter()
    try:
        results = run_search(
            conn,
            state.vector_index,
            state.embedder,
            state.reranker,
            q,
            parsed,
            mode=mode,
            limit=limit,
        )
    except sqlite3.OperationalError as exc:
        if mode is SearchMode.keyword:
            # Keyword mode has no fallback arm, so bad FTS5 syntax is a client
            # error. Hybrid already degraded internally; if it raised, both
            # arms failed — let that reach the catch-all as a 500.
            raise StarletteHTTPException(
                status_code=400,
                detail={
                    "code": "bad_request",
                    "message": f'{exc} — tip: wrap phrases in double quotes, e.g. "machine learning".',
                },
            ) from exc
        raise
    latency_ms = (time.perf_counter() - start) * 1000.0

    return SearchResponse(
        results=[
            SearchResult(
                path=str(r.path),
                title=r.title,
                doc_id=r.doc_id,
                score=float(r.score),
                snippet=[Segment(text=t, match=m) for t, m in to_segments(r.snippet)],
            )
            for r in results
        ],
        mode=mode.value,
        parsed=ParsedQuerySchema.from_domain(parsed),
        llm_parsed=llm_parsed,
        latency_ms=latency_ms,
        db_path=str(state.db_path),
    )
