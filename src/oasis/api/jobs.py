"""Non-HTTP machinery for the async index job: state + the thread↔loop bridge.

Three pieces, all transport-agnostic so ``index.py`` stays thin:

- ``IndexJob`` — the live record of one indexing run, held in app state. Its
  ``stats`` dict is updated on *every* pipeline callback (cheap, keeps the
  snapshot honest); SSE *events* are throttled separately in ``EventBroker``.
- ``EventBroker`` — fan-out to N SSE subscribers, each with its own bounded
  ``asyncio.Queue``. The index job runs on a worker thread and must never touch
  an ``asyncio.Queue`` directly; every publish crosses into the loop via
  ``loop.call_soon_threadsafe`` (CLAUDE.md § Concurrency: the one thread/loop
  boundary in the server, kept in one place).
- the event-builder helpers that map an ``IndexJob`` to the wire schemas.

Progress is lossy by design (throttled ≥100ms, and droppable under queue
overflow); terminal events (``done``/``cancelled``/``error``) are never
dropped. That asymmetry is load-bearing: a stale progress number self-corrects
on the next absolute-count tick, a dropped terminal hangs the client's spinner
forever.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from oasis.api.schemas import (
    CancelledEvent,
    DoneEvent,
    ErrorEvent,
    ProgressEvent,
    SnapshotEvent,
)

JobStatus = Literal["running", "done", "cancelled", "error"]

# Publish at most one progress event per this interval. on_file fires once per
# file and a first index can be 100k files; ~100ms is ~6 frames, well under a
# 60fps redraw budget and invisible to the user.
PROGRESS_THROTTLE_S = 0.1
# Idle timeout on the SSE queue read; on expiry the handler emits a `: ping`
# comment so proxies / URLSession don't reap a quiet-but-live connection.
HEARTBEAT_S = 15.0
# Bounded so a slow client can't grow an unbounded queue behind it. On overflow
# we drop intermediate progress (never terminal — see _deliver).
QUEUE_MAXSIZE = 64

# File-count stat keys (everything except "chunks"): their sum is scan-phase
# "done". "chunks" is the embed-phase counter and is reported via total/done.
_FILE_STAT_KEYS = ("indexed", "skipped", "failed", "unsupported", "permission_denied")

TERMINAL_TYPES = frozenset({"done", "cancelled", "error"})


@dataclass
class IndexJob:
    """One indexing run. Lives in app state; retained after completion so a late
    SSE subscriber gets a terminal snapshot instead of an empty stream or 404."""

    id: str
    root: str
    force: bool
    status: JobStatus = "running"
    cancel: threading.Event = field(default_factory=threading.Event)
    # Updated live on every callback — always the current partial. Copied (not
    # aliased) into each event so a reader on the loop thread never observes a
    # dict mid-mutation on the worker thread.
    stats: dict[str, int] = field(default_factory=dict)
    phase: str | None = None
    # Progress cursor. During scan, done = files seen and total is None (the
    # walk is a lazy generator, so the count isn't known until it finishes).
    # During embed, done/total are chunk counts.
    done: int = 0
    total: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None

    def files_seen(self) -> int:
        return sum(self.stats.get(k, 0) for k in _FILE_STAT_KEYS)


# ---------------------------------------------------------------------------
# Event builders — IndexJob → wire schema. Kept here (not in index.py) because
# they read job internals; ApiModel serialization attaches the UTC offset.
# ---------------------------------------------------------------------------


def snapshot_event(job: IndexJob | None) -> SnapshotEvent:
    """Current state for a freshly-connected subscriber. ``idle`` when no job
    has ever run."""
    if job is None:
        return SnapshotEvent(
            job_id=None,
            status="idle",
            root=None,
            phase=None,
            stats={},
            done=0,
            total=None,
        )
    return SnapshotEvent(
        job_id=job.id,
        status=job.status,
        root=job.root,
        phase=job.phase,
        stats=dict(job.stats),
        done=job.done,
        total=job.total,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error=job.error,
    )


def progress_event(job: IndexJob) -> ProgressEvent:
    return ProgressEvent(
        job_id=job.id,
        phase=job.phase or "scan",
        stats=dict(job.stats),
        done=job.done,
        total=job.total,
    )


def terminal_event(job: IndexJob) -> DoneEvent | CancelledEvent | ErrorEvent:
    if job.status == "cancelled":
        return CancelledEvent(job_id=job.id, stats=dict(job.stats))
    if job.status == "error":
        return ErrorEvent(job_id=job.id, message=job.error or "Indexing failed.")
    return DoneEvent(job_id=job.id, stats=dict(job.stats))


class EventBroker:
    """Fan-out of index events to SSE subscribers, fed from the worker thread.

    The event loop is captured in lifespan startup (where ``get_running_loop``
    is valid), not in the loader/worker thread. All queue mutation happens on
    the loop thread via ``call_soon_threadsafe``; the ``_lock`` only guards the
    subscriber set and the throttle timestamp, both touched from the worker
    thread too.
    """

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.subscribers: set[asyncio.Queue] = set()
        self._last_progress_emit: float = 0.0
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    # -- subscriber lifecycle (called on the loop thread from the SSE handler) --

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        with self._lock:
            self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self.subscribers.discard(q)

    # -- publishing (called on the worker thread) --

    def publish_progress(self, event: ProgressEvent) -> None:
        """Throttled *before* fan-out, so we never schedule 100k×N loop
        callbacks. Dropped silently when inside the throttle window — the next
        tick carries absolute counts and the client converges."""
        now = time.monotonic()
        with self._lock:
            if now - self._last_progress_emit < PROGRESS_THROTTLE_S:
                return
            self._last_progress_emit = now
        self._fanout(event, terminal=False)

    def publish_terminal(self, event: DoneEvent | CancelledEvent | ErrorEvent) -> None:
        """Always delivered, never coalesced — completion is not lossy."""
        self._fanout(event, terminal=True)

    def _fanout(self, event: object, *, terminal: bool) -> None:
        loop = self.loop
        if loop is None:  # events fired before lifespan bound the loop — nowhere to go
            return
        with self._lock:
            queues = list(self.subscribers)
        for q in queues:
            loop.call_soon_threadsafe(self._deliver, q, event, terminal)

    @staticmethod
    def _deliver(q: asyncio.Queue, event: object, terminal: bool) -> None:
        # Runs on the loop thread, so queue manipulation is race-free here.
        if terminal:
            # Guarantee room by evicting stale progress — a dropped terminal
            # hangs the client forever, a dropped progress self-heals.
            while q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    break
            q.put_nowait(event)
            return
        # Drop intermediate progress under back-pressure — it self-heals on the
        # next absolute-count tick.
        with contextlib.suppress(asyncio.QueueFull):
            q.put_nowait(event)
