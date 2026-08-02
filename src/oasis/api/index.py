"""Index endpoints: async, observable, cancellable indexing over HTTP.

Four routes. Three wrap the existing ``index_directory`` pipeline; the fourth
undoes it for one folder:

- ``POST /api/index``             — start a background job (202) or 409 if one runs.
- ``GET  /api/index/events``      — SSE stream: snapshot on connect, then live events.
- ``POST /api/index/cancel``      — cooperatively cancel the running job (202/409).
- ``POST /api/index/remove-root`` — forget one indexed folder: delete its stored
  documents and untrack the root (200/404/409). The recourse for a root deleted
  from disk, which otherwise wedges Reindex forever. See Part D — it is
  deliberately unconditional where the stale sweep is gated.

These routes add no retrieval or storage behaviour of their own: they make
``index_directory``'s existing work (add, update, stale sweep, vector backfill)
async, observable, and cancellable. The done-vs-cancelled distinction decided
in ``_run_job`` is what the pipeline's sweep gate reads — a cancelled run
reports ``removed: 0`` and deletes nothing.

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
from oasis.api.schemas import (
    CancelRequest,
    IndexRequest,
    JobResponse,
    RemoveRootRequest,
    RemoveRootResponse,
)
from oasis.api.state import AppState, get_conn
from oasis.index.keyword import KeywordIndex
from oasis.index.pipeline import delete_documents, index_directory

_log = logging.getLogger(__name__)

router = APIRouter()

# The "done" states a running job can settle into (job status, not event type).
_TERMINAL_STATUSES = frozenset({"done", "cancelled", "error"})

# Pre-populated so job.stats never grows a key while the SSE thread copies it
# (dict() over a dict being resized on another thread raises). The pipeline
# returns exactly these keys; here they just start at zero.
_ZERO_STATS = (
    "indexed",
    "skipped",
    "failed",
    "unsupported",
    "permission_denied",
    "chunks",
    "removed",
)


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

    def on_reconcile() -> None:
        # The stale sweep is starting. Often a blink (deletes are fast) and the
        # progress event is throttled/droppable — the durable signal is the
        # `removed` count in the terminal stats, which is never dropped.
        job.phase = "reconciling"
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
            on_reconcile=on_reconcile,
        )
        # index_directory returns partial stats whether it finished or was
        # cancelled — it doesn't say which. Decide it HERE, from the cancel flag.
        # (The stale sweep lives inside the pipeline and gates itself on the
        # same cancel event plus census cleanliness — a cancelled or dirty walk
        # reports removed: 0 and deletes nothing.)
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
def cancel_index(request: Request, body: CancelRequest) -> JobResponse:
    """Cooperatively cancel a specific running job. 202 (requested, not
    synchronously effected — the job ends a beat later on its own thread), or
    409 when ``body.job_id`` is not the currently-running job: a stale id, an
    id naming a finished job, or no job running at all.

    Binding cancel to a job_id (not "whatever is running") matters once
    auto-reindex exists: a cancel aimed at job N arriving after N finished and
    N+1 auto-started must NOT kill N+1 — it 409s instead.

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
    if body.job_id != job.id:
        # Never touch the running job's cancel event on a mismatch — that
        # would be exactly the kill-the-wrong-job race the body exists to close.
        raise StarletteHTTPException(
            status_code=409,
            detail={
                "code": "conflict",
                "message": f"job_id {body.job_id!r} is not the running job ({job.id}).",
            },
        )
    job.cancel.set()
    return JobResponse(job_id=job.id, status=job.status)


# ---------------------------------------------------------------------------
# Part D — remove-root
# ---------------------------------------------------------------------------


@router.post("/index/remove-root", response_model=RemoveRootResponse)
def remove_root(request: Request, body: RemoveRootRequest) -> RemoveRootResponse:
    """Forget one indexed folder: delete its documents, untrack the root.

    **Why it exists.** ``indexed_roots`` was append-only, so a root deleted from
    disk **wedges Reindex permanently** — the sequence 400s on the missing
    directory and halts, and the only escape was ``/api/reset``, which is far
    too blunt (wipe everything to drop one folder). This is the targeted
    recourse, and it is the endpoint the app's Settings › Folders tab is built
    on.

    **It is UNCONDITIONAL, and that is the whole design.** The superficially
    similar thing is the pipeline's stale sweep, which deletes stored docs the
    walk didn't see and is therefore gated hard on a clean, complete census
    (not cancelled, zero walk errors, zero permission denials) — because there,
    "not seen" only means "deleted" if the walk could be trusted to have seen
    everything. This endpoint answers a different question. It is *"forget this
    folder"*, not *"reconcile this folder against disk"*: the user has already
    decided. So it does **not walk** and has **no census gate**, and it must
    not grow one — the wedge case it exists for is a root whose files are
    *gone*, where a walk cannot succeed by definition. A census gate here would
    make the endpoint fail in exactly the situation it was written for.
    ``test_remove_root_when_directory_deleted_from_disk`` is the guard.

    Shares the ``job_lock`` with ``/api/index`` and ``/api/reset``, held across
    the whole operation, for the same reason they do: this mutates the index
    (including through the shared ``VectorIndex`` handle the job writes) and
    must not interleave with a running job.
    """
    state: AppState = request.app.state.oasis
    assert state.db_path is not None  # ready implies loaded

    # The same normalization storage uses (the pipeline abspaths its root once
    # before recording it), so a client's spelling can't drift from the stored
    # form. Lexical only — no resolve(), which would follow symlinks storage
    # did not and reintroduce the mismatch.
    root = os.path.abspath(body.root)

    with state.job_lock:
        job = state.index_job
        if job is not None and job.status == "running":
            raise StarletteHTTPException(
                status_code=409,
                detail={
                    "code": "conflict",
                    "message": (
                        f"An index job is running (job_id={job.id}); "
                        "cancel it before removing a folder."
                    ),
                },
            )

        idx = KeywordIndex(get_conn(state.db_path))
        if root not in idx.get_indexed_roots():
            raise StarletteHTTPException(
                status_code=404,
                detail={"code": "not_found", "message": f"Not an indexed folder: {root}"},
            )

        # Scoped delete, reusing the sweep's two helpers. docs_under does the
        # separator-boundary check in Python precisely because SQL LIKE would
        # treat a path's `_` as a wildcard, and a bare prefix match would put
        # /tmp/ab under /tmp/a. Over-matching here deletes someone else's rows.
        # delete_documents owns the vectors-then-row ordering both call sites
        # depend on. What differs here is only the predicate: every doc under
        # the root, with no census gate (see the docstring above).
        removed = delete_documents(idx, state.vector_index, idx.docs_under(root))

        # Marker LAST. A crash with the rows gone but the root still listed
        # leaves the operation retryable; the reverse orphans rows under a root
        # the user can no longer name — the unrecoverable direction.
        idx.remove_indexed_root(root)

    return RemoveRootResponse(root=root, removed=removed)
