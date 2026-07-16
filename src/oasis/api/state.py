"""Process-wide server state: loaded models, readiness, per-thread SQLite.

The concurrency rules here come straight from CLAUDE.md § HTTP API ›
Concurrency model — SQLite connections are thread-local, model objects and
the LanceDB handle are shared. Getting either one backwards fails silently.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from oasis.api.jobs import EventBroker, IndexJob
from oasis.config import OasisConfig
from oasis.index.db import open_db
from oasis.index.embeddings import EmbeddingModel
from oasis.index.vector import VectorIndex
from oasis.llm.base import LLMProvider
from oasis.query.reranker import CrossEncoderReranker


@dataclass
class AppState:
    """Everything initialized once at startup and shared across requests."""

    # Fresh secrets.token_urlsafe(32) per process; every request but
    # /api/health must present it as a Bearer token.
    token: str
    config: OasisConfig | None = None
    db_path: Path | None = None
    embedder: EmbeddingModel | None = None
    reranker: CrossEncoderReranker | None = None
    # ONE shared instance, never thread-local — see CLAUDE.md § HTTP API ›
    # "LanceDB — verified, not assumed". A per-thread handle pins its table
    # version at open and returns results frozen at process launch forever,
    # with no error raised. Search-during-index stays fresh only because every
    # reader shares the handle the index job writes through.
    vector_index: VectorIndex | None = None
    llm: LLMProvider | None = None  # ensure_ollama() result, cached — including None
    status: Literal["loading", "ready", "error"] = "loading"
    error: str | None = None
    ready: threading.Event = field(default_factory=threading.Event)

    # --- Index job (POST /api/index + SSE + cancel) ---
    # The last job started, running or finished. NOT cleared on completion:
    # re-attach is first-class (a subscriber connecting after a job ends must
    # get a terminal snapshot, not an empty stream), so the finished job stays
    # here until the next POST /api/index overwrites it. The single-job 409
    # guard keys on status == "running", not on "a job exists".
    index_job: IndexJob | None = None
    # Held ONLY across the check-and-set in POST /api/index (is a job running?
    # → install the new one), never across the pipeline run. Without it two
    # concurrent POSTs both read "not running" and both start writing the DB.
    job_lock: threading.Lock = field(default_factory=threading.Lock)
    # Fan-out to SSE subscribers; its event loop is bound in lifespan startup.
    broker: EventBroker = field(default_factory=EventBroker)


# --------------------------------------------------------------------------
# Thread-local SQLite connections
#
# sqlite3 connections raise check_same_thread when touched from a thread other
# than the one that opened them, and FastAPI's def-endpoint threadpool
# guarantees that as soon as two requests overlap. One connection per thread,
# opened lazily via open_db() (WAL mode already permits concurrent readers).
# --------------------------------------------------------------------------

_local = threading.local()
# Bumped by invalidate(); a thread whose cached connection predates the current
# generation reopens on next use. reset will need this — after the DB file is
# deleted, every thread's handle points at a file that no longer exists.
_generation = 0
_generation_lock = threading.Lock()
# Serializes open_db(): two threads opening a fresh DB concurrently race on
# the WAL pragma and schema DDL ("database is locked"). Only paid on first
# use per thread (and after invalidate), never on the cached path.
_open_lock = threading.Lock()


def get_conn(db_path: Path) -> sqlite3.Connection:
    """Return this thread's SQLite connection, opening it on first use."""
    conn: sqlite3.Connection | None = getattr(_local, "conn", None)
    if (
        conn is not None
        and getattr(_local, "generation", -1) == _generation
        and getattr(_local, "db_path", None) == db_path
    ):
        return conn
    if conn is not None:
        conn.close()
    with _open_lock:
        generation = _generation
        conn = open_db(db_path)
    _local.conn = conn
    _local.generation = generation
    _local.db_path = db_path
    return conn


def invalidate() -> None:
    """Mark every thread's cached connection stale so it reopens on next use."""
    global _generation
    with _generation_lock:
        _generation += 1
