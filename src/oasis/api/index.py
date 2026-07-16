"""Index endpoints: async, observable, cancellable indexing over HTTP.

Three routes wrapping the existing ``index_directory`` pipeline:

- ``POST /api/index``        — start a background job (202) or 409 if one runs.
- ``GET  /api/index/events`` — SSE stream: snapshot on connect, then live events.
- ``POST /api/index/cancel`` — cooperatively cancel the running job (202/409).

This commit does *exactly* what ``index_directory`` already does (add + update)
— it does **not** delete stale documents or backfill missing vectors. It only
makes that work async, observable, and cancellable. The done-vs-cancelled
distinction decided in ``_run_job`` is load-bearing for the next commit's stale
sweep (see the note there).

The events endpoint is the server's one ``async def`` handler (CLAUDE.md
§ Concurrency): SSE is a long-lived wait, not CPU-bound work, so it belongs on
the event loop and consumes zero threadpool capacity. Everything else stays
``def``. Auth (Bearer *header*, not a query-param token — the consumer is Swift
``URLSession``, which can set headers) and readiness gating come from
``protected_router``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from oasis.api.jobs import (
    HEARTBEAT_S,
    TERMINAL_TYPES,
    IndexJob,
    progress_event,
    snapshot_event,
    terminal_event,
)
from oasis.api.schemas import IndexRequest, JobResponse
from oasis.api.state import AppState, get_conn
from oasis.index.pipeline import index_directory

_log = logging.getLogger(__name__)

router = APIRouter()

# The "done" states a running job can settle into (job status, not event type).
_TERMINAL_STATUSES = frozenset({"done", "cancelled", "error"})

# Pre-populated so job.stats never grows a key while the SSE thread copies it
# (dict() over a dict being resized on another thread raises). The pipeline
# returns exactly these keys; here they just start at zero.
_ZERO_STATS = ("indexed", "skipped", "failed", "unsupported", "permission_denied", "chunks")


# ---------------------------------------------------------------------------
# Part A — the background job
# ---------------------------------------------------------------------------


def _run_job(state: AppState, job: IndexJob) -> None:
    """Run the pipeline on a worker thread, publishing progress + a terminal event.

    Catches everything: an uncaught exception here would leave status ==
    "running" forever — 409 forever, no terminal event, every SSE client hung.
    A wedged-running job is the exact failure mode to prevent, so the run is
    wrapped and a terminal ``error`` event always goes out on failure.
    """
    broker = state.broker
    assert state.db_path is not None  # ready implies loaded

    def on_file(path, status: str) -> None:
        # Update stats on EVERY file (cheap — keeps the snapshot honest); the
        # SSE event itself is throttled inside publish_progress.
        job.phase = "scan"
        if status in job.stats:
            job.stats[status] += 1
        job.done = job.files_seen()
        job.total = None  # walk is lazy — total is unknown until the scan ends
        broker.publish_progress(progress_event(job))

    def on_chunks_progress(done: int, total: int) -> None:
        job.phase = "embed"
        job.done = done
        job.total = total
        job.stats["chunks"] = done
        broker.publish_progress(progress_event(job))

    try:
        conn = get_conn(state.db_path)  # thread-local: fresh handle for this worker
        final_stats = index_directory(
            conn,
            os.fspath(job.root),  # str root; the pipeline abspaths internally
            force=job.force,
            on_file=on_file,
            vector_index=state.vector_index,
            embedder=state.embedder,
            on_chunks_progress=on_chunks_progress,
            cancel=job.cancel,
        )
        # index_directory returns partial stats whether it finished or was
        # cancelled — it doesn't say which. Decide it HERE, from the cancel flag.
        #
        # NEXT COMMIT: the stale-reconciliation / no-vector-backfill sweep must
        # gate on `status == "done"` and never run on `cancelled` (or a partial
        # walk): "not seen in the walk" is meaningless for an incomplete walk,
        # and sweeping on it would delete everything past the cancel point.
        job.stats.update(final_stats)  # authoritative final counts
        job.status = "cancelled" if job.cancel.is_set() else "done"
    except Exception as exc:
        _log.exception("Index job %s failed", job.id)
        job.status = "error"
        job.error = str(exc)
    finally:
        job.finished_at = datetime.now(UTC)

    broker.publish_terminal(terminal_event(job))


@router.post("/index", status_code=202, response_model=JobResponse)
def start_index(request: Request, body: IndexRequest) -> JobResponse:
    state: AppState = request.app.state.oasis

    # Validate before touching any state: 400 for a non-directory root.
    root = os.path.abspath(body.root)
    if not os.path.isdir(root):
        raise StarletteHTTPException(
            status_code=400,
            detail={"code": "bad_request", "message": f"Not a directory: {body.root}"},
        )

    # Race-free single-job check-and-set: the "is one running?" read and the
    # install of the new job must be atomic, or two concurrent POSTs both see
    # "not running" and both start writing the same DB. Hold the lock ONLY for
    # this — never across the pipeline run.
    with state.job_lock:
        existing = state.index_job
        if existing is not None and existing.status == "running":
            raise StarletteHTTPException(
                status_code=409,
                detail={
                    "code": "conflict",
                    "message": f"An index job is already running (job_id={existing.id}).",
                },
            )
        job = IndexJob(
            id=uuid.uuid4().hex,
            root=root,
            force=body.force,
            stats={k: 0 for k in _ZERO_STATS},
            started_at=datetime.now(UTC),
        )
        # A finished job is NOT cleared — overwriting it here is how a new run
        # starts; a subscriber connecting after the old one finished still got
        # its terminal snapshot. The 409 above keys on status, not existence.
        state.index_job = job

    threading.Thread(
        target=_run_job, args=(state, job), name=f"oasis-index-{job.id}", daemon=True
    ).start()
    return JobResponse(job_id=job.id, status=job.status)


# ---------------------------------------------------------------------------
# Part B — SSE stream (the one async endpoint)
# ---------------------------------------------------------------------------


def _sse(event: object) -> str:
    """One SSE message: an ``event:`` line (matches the CLAUDE.md wire example)
    plus a JSON ``data:`` line carrying ``type`` (the prompt's contract).
    Serialized via ApiModel so datetimes get their UTC offset."""
    return f"event: {event.type}\ndata: {event.model_dump_json()}\n\n"  # type: ignore[attr-defined]


@router.get("/index/events")
async def index_events(request: Request) -> StreamingResponse:
    state: AppState = request.app.state.oasis
    broker = state.broker

    async def stream():
        # Register BEFORE reading the snapshot: an event fired in the gap —
        # including a terminal one — must land in this queue, not vanish. Worst
        # case the snapshot is a hair stale and immediately superseded, which
        # the absolute-count design absorbs.
        queue = broker.subscribe()
        try:
            snapshot = snapshot_event(state.index_job)
            yield _sse(snapshot)

            if snapshot.status == "idle":
                return  # no job to follow — don't hang the stream open
            if snapshot.status in _TERMINAL_STATUSES:
                # Job already finished. Flush anything queued in the
                # register→snapshot window (a terminal event that fired there),
                # then close — no point waiting on a job that's done.
                while True:
                    try:
                        event = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    yield _sse(event)
                return

            # Running: drain live events with a heartbeat until a terminal one.
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
                except TimeoutError:
                    yield ": ping\n\n"  # keep-alive through proxy/URLSession idle
                    continue
                yield _sse(event)
                if getattr(event, "type", None) in TERMINAL_TYPES:
                    break
        finally:
            # Always unregister — otherwise the publisher keeps scheduling into
            # a dead queue on every reconnect and the subscriber set leaks.
            broker.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Part C — cancel
# ---------------------------------------------------------------------------


@router.post("/index/cancel", status_code=202, response_model=JobResponse)
def cancel_index(request: Request) -> JobResponse:
    """Cooperatively cancel the running job. 202 (requested, not synchronously
    effected — the job ends a beat later on its own thread) or 409 if none runs.

    The pipeline checks ``job.cancel`` per file and between embed batches and
    returns partial stats; ``_run_job`` then settles the status to ``cancelled``
    and the broker publishes the terminal ``cancelled`` event. Committed work
    persists — indexing is incremental, so the next run resumes the rest.
    """
    state: AppState = request.app.state.oasis
    job = state.index_job
    if job is None or job.status != "running":
        raise StarletteHTTPException(
            status_code=409,
            detail={"code": "conflict", "message": "No index job is running."},
        )
    job.cancel.set()
    return JobResponse(job_id=job.id, status=job.status)
