"""Process-wide server state: loaded models, readiness, per-thread SQLite.

The concurrency rules here come straight from CLAUDE.md § HTTP API ›
Concurrency model — SQLite connections are thread-local, model objects and
the LanceDB handle are shared. Getting either one backwards fails silently.
"""

from __future__ import annotations

import shutil
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from oasis.api.jobs import EventBroker, IndexJob
from oasis.config import OasisConfig
from oasis.index.db import open_db
from oasis.index.embeddings import EmbeddingModel
from oasis.index.keyword import KeywordIndex
from oasis.index.vector import VectorIndex
from oasis.llm.base import LLMProvider
from oasis.llm.manager import ensure_ollama
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
    # ensure_ollama() result, cached — including None. Resolved on the first
    # request that actually wants NL parsing, NOT at startup; see get_llm().
    llm: LLMProvider | None = None
    llm_probed: bool = False
    # Serializes the one-time probe: two concurrent raw=false requests land in
    # different threadpool threads and would otherwise both shell out.
    llm_lock: threading.Lock = field(default_factory=threading.Lock)
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

    def get_llm(self) -> LLMProvider | None:
        """The NL-parsing provider, probed at most once per process. May be None.

        **Deliberately lazy, and the laziness is the point.** ``ensure_ollama()``
        does not merely look: on a machine with the binary on PATH it *starts
        ``ollama serve``* and waits up to five seconds for it. Calling that at
        startup — which is what happened until 2026-08-02 — meant every launch
        of the macOS app spun up a background LLM server the user never asked
        for, left it running after Oasis quit, and paid the probe on the
        readiness path. All for a feature that client cannot reach: the app
        hardcodes ``raw=true``, so ``state.llm`` was never once read.

        Deferring it to the first ``raw=false`` request keeps the property the
        startup probe actually existed for — *never* per request, which would
        spawn an ``ollama list`` subprocess per query — while making the cost
        land only on someone who explicitly asked for parsing. ``llm_probed``
        rather than ``llm is not None`` is what caches a negative result, so an
        absent Ollama is not re-probed on every subsequent request.
        """
        if self.llm_probed:
            return self.llm
        with self.llm_lock:
            if not self.llm_probed:  # Re-check: another thread may have won.
                self.llm = ensure_ollama()
                self.llm_probed = True
        return self.llm

    def set_llm(self, provider: LLMProvider | None) -> None:
        """Install *provider* and mark the probe done, so it never runs.

        Assigning ``state.llm`` alone is not enough and fails quietly in the
        worst way: ``get_llm`` keys on ``llm_probed``, so an injected provider
        would be overwritten by a real ``ensure_ollama()`` — which shells out
        and can start a server. Tests inject through here for that reason.
        """
        self.llm = provider
        self.llm_probed = True

    def reset_index(self) -> None:
        """Delete the index in place and stand up a fresh empty one, swapping in
        a new shared ``VectorIndex`` handle.

        **The caller MUST hold ``job_lock`` and have verified no job is
        running.** This drops the very LanceDB handle the index job writes
        through, so reset and indexing are mutually exclusive by that lock — the
        same lock, for the same reason a second ``POST /api/index`` is refused.

        The inversion this method has to survive: four commits made
        ``vector_index`` one shared handle every search reads and the job writes
        through, and *that* is why search-during-index sees fresh rows. Reset
        destroys and replaces that handle while searches may be mid-flight
        against it. Two properties make that safe:

        1. **In-flight readers degrade, they don't crash.** A search on a
           threadpool thread holds the OLD handle for the life of its call (it
           was passed the reference before this ran). When the ``rmtree`` below
           removes that handle's files, its next ``.search()`` raises, and the
           hybrid vector arm catches it and degrades to keyword-only
           (``query/retriever.py``). Only *subsequent* searches read the new
           handle from ``self.vector_index`` — which is why ``api/search.py``
           reads ``state.vector_index`` fresh per request, never captured once.
        2. **Deletion order keeps the markers honest at every crash point.**
           markers → vectors → documents, so no ``vectors_built`` marker ever
           outlives the vectors it describes. A crash between any two steps
           lands in the conservative "reindex needed" state (documents present,
           markers gone → ``schema_version`` 0), never the one dishonest state,
           "semantic ready with no vectors".
        """
        assert self.db_path is not None and self.embedder is not None  # ready ⇒ loaded
        idx = KeywordIndex(get_conn(self.db_path))

        # 1. Markers first — after this the index reads reindex-needed, so the
        #    vector drop below can't leave a marker claiming vectors that vanish.
        idx.clear_meta()

        # 2. Vector store: rmtree the directory, then reconstruct. checkout_latest
        #    can't help — the versions it would check out are gone with the dir;
        #    only a fresh VectorIndex binds the new empty table. Install it as THE
        #    shared instance so every subsequent search/index gets the new handle.
        lance_path = self.db_path.with_name(self.db_path.stem + ".lance")
        if lance_path.exists():
            shutil.rmtree(lance_path)
        self.vector_index = VectorIndex(lance_path, dimension=self.embedder.dimension)

        # 3. Documents last (the _ad trigger clears FTS). Now 0 rows everywhere.
        idx.clear_documents()

        # 4. The SQL clears are already visible cross-thread via WAL, but bump the
        #    generation so every thread reopens on next use — a clean reopen and
        #    consistent with treating reset as "the index is gone, start over".
        invalidate()


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
# generation reopens on next use. reset_index() uses it: once the index has
# been cleared, every thread's cached handle should reopen rather than linger.
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
