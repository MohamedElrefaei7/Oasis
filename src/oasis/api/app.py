"""FastAPI application for `oasis serve`.

Skeleton per CLAUDE.md § HTTP API: model lifecycle (background load + warm),
/api/health, bearer-token auth, readiness gating, and the error envelope.
Search/index/reset/open endpoints land in later commits on `protected_router`.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import logging
import secrets
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from oasis.api.index import router as index_router
from oasis.api.open import router as open_router
from oasis.api.reset import router as reset_router
from oasis.api.schemas import ErrorDetail, ErrorResponse, HealthResponse
from oasis.api.search import router as search_router
from oasis.api.state import AppState, get_conn
from oasis.api.status import router as status_router
from oasis.config import load_config
from oasis.index.db import SCHEMA_VERSION
from oasis.index.embeddings import SentenceTransformerEmbedder
from oasis.index.keyword import KeywordIndex
from oasis.index.vector import VectorIndex
from oasis.llm.manager import ensure_ollama
from oasis.query.reranker import CrossEncoderReranker
from oasis.query.retriever import HybridResult

_log = logging.getLogger(__name__)

_DEFAULT_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    404: "not_found",
    409: "conflict",
    410: "gone",
    422: "validation_error",
    500: "internal_error",
    503: "loading",
}


# ---------------------------------------------------------------------------
# Model lifecycle
# ---------------------------------------------------------------------------


def _load_state(state: AppState, db_override: Path | None) -> None:
    """Load config + models once, warm them, then flip status to ready.

    Runs on a daemon thread spawned by the lifespan — never inline in the
    lifespan body: Uvicorn doesn't accept connections until lifespan startup
    returns, so a synchronous load would make /api/health connection-refused
    for its whole duration, defeating the point of a "loading" state.
    """
    try:
        config = load_config()
        state.config = config
        state.db_path = db_override or config.db_path

        embedder = SentenceTransformerEmbedder()
        reranker = CrossEncoderReranker()
        lance_path = state.db_path.with_name(state.db_path.stem + ".lance")
        vector_index = VectorIndex(lance_path, dimension=embedder.dimension)
        # Once, at startup — the CLI's call-per-search spawns an `ollama list`
        # subprocess per query. Cache the result, None included.
        llm = ensure_ollama()

        # Warm both models: load time and first-inference time are separate
        # costs (lazy kernel init, weight paging); fold both into startup.
        embedder.embed(["warmup"])
        reranker.rerank(
            "warmup",
            [
                HybridResult(
                    path=Path("/dev/null"), doc_id=0, title=None, snippet="warmup", score=0.0
                )
            ],
            top_n=1,
        )

        state.embedder = embedder
        state.reranker = reranker
        state.vector_index = vector_index
        state.llm = llm
        state.status = "ready"
        state.ready.set()
    except Exception as exc:
        _log.exception("Model loading failed")
        state.error = str(exc)
        state.status = "error"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    state: AppState = app.state.oasis
    # Capture the serving loop HERE — this is the one place get_running_loop()
    # is valid. The index worker thread bridges to it via call_soon_threadsafe;
    # capturing in the loader thread (no running loop) or lazily in the SSE
    # handler (racy against events fired before the first subscriber) is wrong.
    state.broker.bind_loop(asyncio.get_running_loop())
    loader = threading.Thread(
        target=_load_state,
        args=(state, app.state.db_override),
        name="oasis-model-loader",
        daemon=True,
    )
    loader.start()
    yield
    loader.join(timeout=2.0)


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def require_auth(request: Request) -> None:
    """401 unless the request carries `Authorization: Bearer <process token>`."""
    state: AppState = request.app.state.oasis
    supplied = request.headers.get("authorization", "")
    expected = f"Bearer {state.token}"
    if not secrets.compare_digest(supplied.encode(), expected.encode()):
        raise StarletteHTTPException(
            status_code=401,
            detail={"code": "unauthorized", "message": "Missing or invalid bearer token."},
        )


def require_ready(request: Request) -> None:
    """503 until models are loaded — an honest 'not yet' beats a hanging request."""
    state: AppState = request.app.state.oasis
    if state.status == "ready":
        return
    if state.status == "error":
        raise StarletteHTTPException(
            status_code=503,
            detail={"code": "startup_error", "message": f"Server failed to start: {state.error}"},
        )
    raise StarletteHTTPException(
        status_code=503,
        detail={"code": "loading", "message": "Models are still loading — poll /api/health."},
    )


PROTECTED = [Depends(require_auth), Depends(require_ready)]

# Every endpoint except /api/health attaches here so auth + readiness apply by
# construction — and it must be fully populated before create_app() runs,
# because the catch-all registered there shadows anything added later.
protected_router = APIRouter(prefix="/api", dependencies=PROTECTED)
protected_router.include_router(status_router)
protected_router.include_router(search_router)
protected_router.include_router(open_router)
protected_router.include_router(index_router)
protected_router.include_router(reset_router)


# ---------------------------------------------------------------------------
# Error envelope — {"error": {"code", "message"}} for every failure.
# FastAPI won't do this on its own; three handlers make the shape hold.
# ---------------------------------------------------------------------------


def _envelope(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status_code, content=body.model_dump())


async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail and "message" in detail:
        return _envelope(exc.status_code, detail["code"], detail["message"])
    code = _DEFAULT_CODES.get(exc.status_code, "error")
    return _envelope(exc.status_code, code, str(detail))


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # FastAPI's default 422 body is a LIST under "detail" — the one error it
    # generates unasked, and the one shape least like the envelope. Flatten it.
    message = "; ".join(
        f"{'.'.join(str(loc) for loc in err.get('loc', []))}: {err.get('msg', 'invalid')}"
        for err in exc.errors()
    )
    return _envelope(422, "validation_error", message or "Invalid request.")


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    _log.exception("Unhandled error on %s %s", request.method, request.url.path)
    # Never leak internals — the traceback goes to the log, not the wire.
    return _envelope(500, "internal_error", "Internal server error.")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(*, token: str, db_path: Path | None = None) -> FastAPI:
    # openapi_url=None disables /openapi.json and both doc UIs: they enumerate
    # every route to unauthenticated callers, and the consumer is a Swift app,
    # not a browser. Restore locally by dropping the kwarg if ever needed.
    app = FastAPI(title="oasis", lifespan=_lifespan, openapi_url=None)
    app.state.oasis = AppState(token=token)
    app.state.db_override = db_path

    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)

    @app.get("/api/health", response_model=HealthResponse)
    def health(request: Request) -> HealthResponse:
        # No auth: this is what the Swift app polls right after the handshake,
        # and it exposes nothing sensitive — counts and a model name, no paths,
        # no query text, no content. (db_path lives on /api/status.)
        state: AppState = request.app.state.oasis
        version = importlib.metadata.version("oasis")
        # While loading (or on error) the fields stay null/false, same as
        # documents already behaved — there's no embedder to compare against yet.
        if state.status != "ready" or state.db_path is None or not state.db_path.exists():
            return HealthResponse(
                status=state.status,
                version=version,
                documents=None,
                error=state.error,
            )

        caps = KeywordIndex(get_conn(state.db_path)).get_capabilities()
        # get_capabilities() is DB-only by design; the live embedder comparison
        # belongs here, where the loaded model is known. Vectors built at a
        # different dimension are unusable, so they don't count as ready.
        live_dimension = state.embedder.dimension if state.embedder is not None else None
        semantic_ready = (
            caps.vectors_built
            and caps.embedding_dimension is not None
            and caps.embedding_dimension == live_dimension
        )
        # Derived here, not in the client — the app shouldn't do version math.
        # The documents > 0 guard keeps a never-indexed DB reading as "index
        # me" (reindex_recommended false), a different state from "reindex me".
        reindex_recommended = caps.document_count > 0 and (
            caps.schema_version < SCHEMA_VERSION or not semantic_ready
        )
        return HealthResponse(
            status=state.status,
            version=version,
            documents=caps.document_count,
            error=state.error,
            vectors_built=caps.vectors_built,
            embedding_model=caps.embedding_model,
            embedding_dimension=caps.embedding_dimension,
            semantic_ready=semantic_ready,
            schema_version=caps.schema_version,
            reindex_recommended=reindex_recommended,
        )

    app.include_router(protected_router)

    def unknown_api_path() -> None:
        raise StarletteHTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Not Found"},
        )

    # Auth-gated catch-all so a tokenless caller can't probe which /api routes
    # exist: unknown paths 401 without a token and 404 only with one, same as
    # real protected routes. No readiness gate — a 404 needs no models.
    # MUST be registered last: Starlette matches in registration order, so any
    # route added after this (on the app or via a later include_router) would
    # be shadowed by it.
    app.add_api_route(
        "/api/{_rest:path}",
        unknown_api_path,
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
        dependencies=[Depends(require_auth)],
        include_in_schema=False,
    )
    return app
