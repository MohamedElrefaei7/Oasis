"""FastAPI application for `oasis serve`.

Skeleton per CLAUDE.md § HTTP API: model lifecycle (background load + warm),
/api/health, bearer-token auth, readiness gating, and the error envelope.
Search/index/reset/open endpoints land in later commits on `protected_router`.
"""
from __future__ import annotations

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

from oasis.api.schemas import ErrorDetail, ErrorResponse, HealthResponse
from oasis.api.state import AppState, get_conn
from oasis.config import load_config
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
            [HybridResult(path=Path("/dev/null"), doc_id=0, title=None, snippet="warmup", score=0.0)],
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

# Every future endpoint (search, index, events, cancel, reset, open) attaches
# here so auth + readiness apply by construction. /api/health stays off it.
protected_router = APIRouter(prefix="/api", dependencies=PROTECTED)


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
    app = FastAPI(title="oasis", lifespan=_lifespan)
    app.state.oasis = AppState(token=token)
    app.state.db_override = db_path

    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)

    @app.get("/api/health", response_model=HealthResponse)
    def health(request: Request) -> HealthResponse:
        # No auth: this is what the Swift app polls right after the handshake,
        # and it exposes nothing sensitive (db_path lives on /api/status).
        state: AppState = request.app.state.oasis
        documents: int | None = None
        if state.status == "ready" and state.db_path is not None and state.db_path.exists():
            documents = KeywordIndex(get_conn(state.db_path)).count()
        return HealthResponse(
            status=state.status,
            version=importlib.metadata.version("oasis"),
            documents=documents,
            error=state.error,
        )

    app.include_router(protected_router)
    return app
