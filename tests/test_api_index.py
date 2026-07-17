"""POST /api/index + SSE events + cancel — the async index job over HTTP.

Structured as the three parts of the commit: A (job runner), B (SSE stream),
C (cancel). Real SQLite + LanceDB over a temp corpus; the embedder/reranker are
faked so PyTorch never loads and vectors are deterministic. The tests that
matter most are the two concurrency ones — concurrent-start exclusivity and
no-lost-terminal — written to go red when the locking/ordering is wrong.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import zlib
from pathlib import Path

import httpx
import numpy as np
import pytest
import uvicorn

from oasis.api.app import create_app
from oasis.config import OasisConfig
from oasis.index.pipeline import index_directory as real_index_directory

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
DIM = 8
TERMINAL = {"done", "cancelled", "error"}
ZERO_STATS = {
    "indexed": 0,
    "skipped": 0,
    "failed": 0,
    "unsupported": 0,
    "permission_denied": 0,
    "chunks": 0,
}


@pytest.fixture(autouse=True, scope="module")
def _hermetic_no_torch():
    """These tests stand up real uvicorn servers but with faked models — the
    whole point is that the default suite never pays for PyTorch. Goes red if
    anything on this file's import-or-run path drags the real weights back in
    (e.g. a module-level ``from sentence_transformers import …`` regression)."""
    yield
    import sys

    assert "torch" not in sys.modules, "test_api_index must never import PyTorch"


class FakeEmbedder:
    dimension = DIM
    model_name = "fake-model"

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.stack(
            [
                np.random.default_rng(zlib.crc32(t.encode())).random(DIM, dtype=np.float32)
                for t in texts
            ]
        )


class FakeReranker:
    def rerank(self, query, results, *, top_n=None):
        return results[:top_n] if top_n is not None else results


@pytest.fixture
def client(monkeypatch, tmp_path):
    """A real uvicorn server in a thread + an httpx client.

    Not TestClient: Starlette's TestClient buffers streaming responses, so a
    lone ``snapshot`` on an otherwise-idle SSE stream never flushes to the
    reader until another event follows — which deadlocks the "read snapshot,
    then act" pattern these tests rely on. A live server streams incrementally,
    exactly as the Swift client will see it.
    """
    db_path = tmp_path / "index.db"
    monkeypatch.setattr("oasis.api.app.load_config", lambda: OasisConfig(db_path=db_path))
    monkeypatch.setattr("oasis.api.app.SentenceTransformerEmbedder", FakeEmbedder)
    monkeypatch.setattr("oasis.api.app.CrossEncoderReranker", FakeReranker)
    monkeypatch.setattr("oasis.api.app.ensure_ollama", lambda: None)
    app = create_app(token=TOKEN)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    # No default auth header — the auth tests send requests without a token.
    c = httpx.Client(base_url=base_url, timeout=30.0)
    # Poll health until the (faked, fast) models finish loading. Sub-second in
    # practice — the fakes keep PyTorch out entirely (guard below), so the only
    # cold-start cost is LanceDB. Same 10s budget as test_api_skeleton.
    last = "no response"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            r = c.get("/api/health")
            body = r.json()
            last = f"status={body.get('status')} error={body.get('error')}"
            if r.status_code == 200 and body["status"] == "ready":
                break
        except httpx.HTTPError as exc:
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(0.05)
    else:  # pragma: no cover
        raise RuntimeError(f"server did not become ready ({last})")

    c.app_state = app.state.oasis
    try:
        yield c
    finally:
        c.close()
        server.should_exit = True
        thread.join(timeout=5)


@pytest.fixture
def corpus(tmp_path) -> Path:
    """A small directory to index, with one distinctive token for search."""
    d = tmp_path / "corpus"
    d.mkdir()
    (d / "a.txt").write_text("revenue grew in Q3 driven by enterprise renewals")
    (d / "b.txt").write_text("machine learning embeddings zqxwomble distinctive")
    (d / "c.txt").write_text("coral reef bleaching from rising ocean temperatures")
    return d


@pytest.fixture
def manyfiles(tmp_path) -> Path:
    d = tmp_path / "many"
    d.mkdir()
    for i in range(60):
        (d / f"f{i}.txt").write_text(f"file number {i} with some indexable words here")
    return d


# --------------------------------------------------------------------------
# SSE parsing helpers
# --------------------------------------------------------------------------


def _parse(line: str) -> dict | None:
    if line.startswith("data:"):
        return json.loads(line[len("data:") :].strip())
    if line.startswith(":"):
        return {"type": "ping"}
    return None


def _next_event(lines_iter) -> dict | None:
    for line in lines_iter:
        ev = _parse(line)
        if ev is not None:
            return ev
    return None


def _read_until(lines_iter, stop_types, max_reads=5000) -> list[dict]:
    out: list[dict] = []
    for _, line in zip(range(max_reads), lines_iter, strict=False):
        ev = _parse(line)
        if ev is None:
            continue
        out.append(ev)
        if ev["type"] in stop_types:
            break
    return out


def _wait(pred, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


def _gate(monkeypatch):
    """Patch the pipeline so the worker blocks on `go` before the real run.

    Lets a test connect / assert while the job is genuinely `running`, then
    release it to observe real progress + a terminal event.
    """
    go = threading.Event()
    calls = {"n": 0}

    def gated(*args, **kwargs):
        calls["n"] += 1
        go.wait(10)
        return real_index_directory(*args, **kwargs)

    monkeypatch.setattr("oasis.api.index.index_directory", gated)
    return go, calls


# ==========================================================================
# Part A — job runner
# ==========================================================================


def test_concurrent_start_is_exclusive(client, corpus, monkeypatch):
    """Two concurrent POSTs → exactly one 202, one 409, pipeline invoked once.
    Goes red if the check-and-set isn't under job_lock (TOCTOU: both start)."""
    release = threading.Event()
    calls: list[int] = []
    calls_lock = threading.Lock()

    def spy(*args, **kwargs):
        with calls_lock:
            calls.append(1)
        release.wait(5)
        return dict(ZERO_STATS)

    monkeypatch.setattr("oasis.api.index.index_directory", spy)

    barrier = threading.Barrier(2)
    results: list[int] = []
    res_lock = threading.Lock()

    def post():
        barrier.wait()
        r = client.post("/api/index", json={"root": str(corpus)}, headers=AUTH)
        with res_lock:
            results.append(r.status_code)

    threads = [threading.Thread(target=post) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [202, 409]
    # Wait for the single winner's worker to enter the (blocked) pipeline, then
    # release. A second, unlocked start would have entered a second time.
    assert _wait(lambda: len(calls) >= 1)
    time.sleep(0.2)  # give any erroneously-started second job time to appear
    release.set()
    assert len(calls) == 1, "pipeline must run exactly once under concurrent starts"


def test_pipeline_failure_does_not_wedge(client, corpus, monkeypatch):
    """Pipeline raising → status error + terminal `error` event, and the next
    POST is accepted (not stuck at 409 forever)."""
    go = threading.Event()

    def boom(*args, **kwargs):
        go.wait(5)
        raise RuntimeError("disk exploded")

    monkeypatch.setattr("oasis.api.index.index_directory", boom)

    r = client.post("/api/index", json={"root": str(corpus)}, headers=AUTH)
    assert r.status_code == 202

    with client.stream("GET", "/api/index/events", headers=AUTH) as s:
        lines = s.iter_lines()
        snap = _next_event(lines)
        assert snap["type"] == "snapshot" and snap["status"] == "running"
        go.set()
        events = _read_until(lines, TERMINAL)

    assert any(e["type"] == "error" and "disk exploded" in e["message"] for e in events)
    assert client.app_state.index_job.status == "error"

    # Not wedged: a new job is accepted even though the last one errored.
    r2 = client.post("/api/index", json={"root": str(corpus)}, headers=AUTH)
    assert r2.status_code == 202


def test_reattach_after_completion_gets_terminal_snapshot(client, corpus):
    """Connecting after a job finished → a `snapshot` carrying the terminal
    status, not an empty stream or a 404."""
    r = client.post("/api/index", json={"root": str(corpus)}, headers=AUTH)
    assert r.status_code == 202
    assert _wait(lambda: client.app_state.index_job.status != "running")

    with client.stream("GET", "/api/index/events", headers=AUTH) as s:
        snap = _next_event(s.iter_lines())

    assert snap["type"] == "snapshot"
    assert snap["status"] == "done"
    assert snap["stats"]["indexed"] == 3


def test_index_while_searching_uses_shared_vector_index(client, corpus, monkeypatch):
    """The VectorIndex-not-thread-local regression test. Content indexed by the
    job thread becomes findable through the shared handle a search thread reads.
    Goes red if VectorIndex is ever made thread-local (search threads pin an
    empty handle at startup and never see the writes — silently)."""
    go, _ = _gate(monkeypatch)
    r = client.post("/api/index", json={"root": str(corpus)}, headers=AUTH)
    assert r.status_code == 202

    # A search fired while the job is still gated must not error.
    mid = client.get("/api/search", params={"q": "zqxwomble", "mode": "keyword"}, headers=AUTH)
    assert mid.status_code == 200

    go.set()
    assert _wait(lambda: client.app_state.index_job.status == "done")

    # The distinctive token, written by the job thread, is now findable.
    found = client.get("/api/search", params={"q": "zqxwomble", "mode": "hybrid"}, headers=AUTH)
    assert found.status_code == 200
    paths = [res["path"] for res in found.json()["results"]]
    assert any(p.endswith("b.txt") for p in paths), "new content not visible via shared VectorIndex"


# ==========================================================================
# Part B — SSE stream
# ==========================================================================


# Fan-out / coalescing / overflow are broker mechanics; TestClient's single
# portal can't hold two concurrent SSE streams open, so they're exercised
# against the EventBroker directly with a real loop rather than over HTTP.


def _make_broker():
    import asyncio

    from oasis.api.jobs import EventBroker

    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    broker = EventBroker()
    broker.bind_loop(loop)

    def call(coro):
        return asyncio.run_coroutine_threadsafe(coro, loop).result(5)

    def stop():
        loop.call_soon_threadsafe(loop.stop)

    return broker, call, stop


def test_fanout_each_subscriber_gets_its_own_terminal():
    """Two simultaneous subscribers both receive the terminal event — one
    subscriber's queue never steals another's."""
    from oasis.api.schemas import DoneEvent

    broker, call, stop = _make_broker()
    try:

        async def subscribe():
            return broker.subscribe()

        q1 = call(subscribe())
        q2 = call(subscribe())
        broker.publish_terminal(DoneEvent(job_id="j", stats={}))
        e1 = call(q1.get())
        e2 = call(q2.get())
        assert e1.type == "done" and e2.type == "done"
    finally:
        stop()


def test_terminal_never_dropped_when_queue_overflows():
    """Under queue overflow, intermediate progress is dropped but a terminal
    event still gets in — it evicts a stale progress to make room."""
    from oasis.api.jobs import QUEUE_MAXSIZE
    from oasis.api.schemas import DoneEvent, ProgressEvent

    broker, call, stop = _make_broker()
    try:

        async def subscribe():
            return broker.subscribe()

        q = call(subscribe())

        async def flood_and_finish():
            # Overflow the queue with progress (dropped once full)...
            for i in range(QUEUE_MAXSIZE + 10):
                broker._deliver(
                    q, ProgressEvent(job_id="j", phase="scan", stats={}, done=i, total=None), False
                )
            assert q.qsize() == QUEUE_MAXSIZE
            # ...then a terminal, which must land despite the full queue.
            broker._deliver(q, DoneEvent(job_id="j", stats={}), True)
            drained = []
            while not q.empty():
                drained.append(q.get_nowait())
            return drained

        drained = call(flood_and_finish())
        assert any(getattr(e, "type", None) == "done" for e in drained)
    finally:
        stop()


def test_no_lost_terminal_across_register_and_snapshot(client, corpus, monkeypatch):
    """A terminal event fired in the window between queue-register and
    snapshot-read must still reach the client. Register-before-snapshot puts it
    in the queue; the running-drain loop delivers it. Goes red if snapshot is
    read before the queue is registered (the event would vanish)."""
    go, _ = _gate(monkeypatch)
    import oasis.api.index as index_mod
    from oasis.api.schemas import DoneEvent

    orig_snapshot = index_mod.snapshot_event
    injected = {"done": False}

    def snapshot_with_gap(job):
        # Simulate a terminal firing AFTER subscribe() (real order) but at
        # snapshot-build time: it fans out to the already-registered queue.
        if job is not None and not injected["done"]:
            injected["done"] = True
            client.app_state.broker.publish_terminal(
                DoneEvent(job_id=job.id, stats=dict(job.stats))
            )
        return orig_snapshot(job)  # job still running → a running snapshot

    monkeypatch.setattr("oasis.api.index.snapshot_event", snapshot_with_gap)

    client.post("/api/index", json={"root": str(corpus)}, headers=AUTH)
    with client.stream("GET", "/api/index/events", headers=AUTH) as s:
        lines = s.iter_lines()
        snap = _next_event(lines)
        assert snap["type"] == "snapshot"
        events = _read_until(lines, {"done"})

    go.set()
    assert any(e["type"] == "done" for e in events), "terminal lost across register/snapshot gap"


def test_progress_is_coalesced_but_terminal_always_delivered(client, manyfiles, monkeypatch):
    """Flood progress faster than the 100ms throttle → far fewer `progress`
    events than files, but the terminal `done` is always seen."""
    go, _ = _gate(monkeypatch)

    client.post("/api/index", json={"root": str(manyfiles)}, headers=AUTH)
    with client.stream("GET", "/api/index/events", headers=AUTH) as s:
        lines = s.iter_lines()
        assert _next_event(lines)["type"] == "snapshot"
        go.set()  # 60 files flood on_file well inside one throttle window
        events = _read_until(lines, TERMINAL)

    types = [e["type"] for e in events]
    progress = [e for e in events if e["type"] == "progress"]
    assert "done" in types, "terminal must never be dropped under flooding"
    assert len(progress) < 60, "progress events should be coalesced well below the file count"


def test_lossy_progress_self_heals_to_true_final_stats(client, manyfiles, monkeypatch):
    """Whatever progress was dropped, the terminal `done` carries the true final
    stats (the absolute-count self-healing property)."""
    go, _ = _gate(monkeypatch)

    client.post("/api/index", json={"root": str(manyfiles)}, headers=AUTH)
    with client.stream("GET", "/api/index/events", headers=AUTH) as s:
        lines = s.iter_lines()
        assert _next_event(lines)["type"] == "snapshot"
        go.set()
        events = _read_until(lines, TERMINAL)

    done = next(e for e in events if e["type"] == "done")
    assert done["stats"] == client.app_state.index_job.stats
    assert done["stats"]["indexed"] == 60


def test_heartbeat_keeps_quiet_stream_open(client, corpus, monkeypatch):
    """A running job that emits nothing still yields `: ping` and stays open."""
    monkeypatch.setattr("oasis.api.index.HEARTBEAT_S", 0.2)
    go, _ = _gate(monkeypatch)  # worker blocks → no progress events at all

    client.post("/api/index", json={"root": str(corpus)}, headers=AUTH)
    with client.stream("GET", "/api/index/events", headers=AUTH) as s:
        lines = s.iter_lines()
        snap = _next_event(lines)
        assert snap["type"] == "snapshot" and snap["status"] == "running"
        nxt = _next_event(lines)  # nothing else emitted → must be a heartbeat
        assert nxt["type"] == "ping"
    go.set()


def test_disconnect_removes_subscriber(client, corpus, monkeypatch):
    # Small heartbeat so the loop wakes and polls is_disconnected() promptly
    # after the client closes the stream.
    monkeypatch.setattr("oasis.api.index.HEARTBEAT_S", 0.1)
    go, _ = _gate(monkeypatch)
    broker = client.app_state.broker

    client.post("/api/index", json={"root": str(corpus)}, headers=AUTH)
    with client.stream("GET", "/api/index/events", headers=AUTH) as s:
        lines = s.iter_lines()
        assert _next_event(lines)["type"] == "snapshot"
        assert _wait(lambda: len(broker.subscribers) == 1)
    # After the client disconnects, the generator's finally unregisters the queue.
    assert _wait(lambda: len(broker.subscribers) == 0), "subscriber leaked after disconnect"
    go.set()


def test_sse_is_async_and_bearer_header_auth(client):
    """The one async endpoint; auth is the standard Bearer header, not a
    query-param token."""
    import inspect

    import oasis.api.index as index_mod

    assert inspect.iscoroutinefunction(index_mod.index_events)

    # No token → 401.
    assert client.get("/api/index/events").status_code == 401
    # Query-param token is NOT accepted (Swift URLSession sends a header).
    assert client.get("/api/index/events", params={"token": TOKEN}).status_code == 401
    # Bearer header, no job → 200 with an idle snapshot, stream closes.
    ok = client.get("/api/index/events", headers=AUTH)
    assert ok.status_code == 200


def test_event_datetimes_carry_utc_offset(client, corpus):
    r = client.post("/api/index", json={"root": str(corpus)}, headers=AUTH)
    assert r.status_code == 202
    assert _wait(lambda: client.app_state.index_job.status != "running")

    with client.stream("GET", "/api/index/events", headers=AUTH) as s:
        snap = _next_event(s.iter_lines())

    for field in ("started_at", "finished_at"):
        value = snap[field]
        assert value is not None
        assert value.endswith("+00:00") or value.endswith("Z"), f"naive datetime: {value}"


# ==========================================================================
# Part C — cancel
# ==========================================================================


def test_cancel_mid_index_yields_cancelled_not_done(client, tmp_path, monkeypatch):
    """Cancel a running job → terminal `cancelled` (not `done`), partial stats,
    and work committed before the cancel stays searchable."""
    from oasis.index.keyword import KeywordIndex
    from oasis.models import DocumentMetadata, ExtractedDocument

    d = tmp_path / "cancelme"
    d.mkdir()
    committed = threading.Event()

    def fake(conn, root, *, cancel, on_file, on_chunks_progress=None, **kwargs):
        # Commit one real doc mid-index, then wait for the cancel signal and
        # return partial — exactly what index_directory does when cancelled.
        p = Path(root) / "committed.txt"
        KeywordIndex(conn).upsert(
            ExtractedDocument(
                path=p,
                text="committed searchme token",
                metadata=DocumentMetadata(size_bytes=24, mtime=1000.0, title=None),
            )
        )
        conn.commit()
        on_file(p, "indexed")
        committed.set()
        while not cancel.is_set():
            time.sleep(0.01)
        return {
            "indexed": 1,
            "skipped": 0,
            "failed": 0,
            "unsupported": 0,
            "permission_denied": 0,
            "chunks": 0,
        }

    monkeypatch.setattr("oasis.api.index.index_directory", fake)

    job_id = client.post("/api/index", json={"root": str(d)}, headers=AUTH).json()["job_id"]
    assert committed.wait(5)

    # Mid-index: the doc committed before cancelling is already searchable.
    found = client.get("/api/search", params={"q": "searchme", "mode": "keyword"}, headers=AUTH)
    assert found.status_code == 200 and found.json()["results"]

    with client.stream("GET", "/api/index/events", headers=AUTH) as s:
        lines = s.iter_lines()
        assert _next_event(lines)["type"] == "snapshot"
        r = client.post("/api/index/cancel", json={"job_id": job_id}, headers=AUTH)
        assert r.status_code == 202
        events = _read_until(lines, TERMINAL)

    types = [e["type"] for e in events]
    assert "cancelled" in types
    assert "done" not in types
    job = client.app_state.index_job
    assert job.status == "cancelled"
    assert job.stats["indexed"] == 1  # partial


def test_cancel_with_no_running_job_is_409(client):
    r = client.post("/api/index/cancel", json={"job_id": "anything"}, headers=AUTH)
    assert r.status_code == 409


def test_cancel_wrong_job_id_is_409_and_does_not_touch_running_job(client, corpus, monkeypatch):
    """The auto-reindex-race guard: a cancel naming a job that is NOT the one
    running must 409 and must NOT set the running job's cancel event. This is
    the test that goes red if cancel ever reverts to bodyless
    'cancel whatever is running'."""
    go, _ = _gate(monkeypatch)
    job_id = client.post("/api/index", json={"root": str(corpus)}, headers=AUTH).json()["job_id"]

    r = client.post("/api/index/cancel", json={"job_id": "stale-or-wrong"}, headers=AUTH)
    assert r.status_code == 409
    job = client.app_state.index_job
    assert job.status == "running"
    assert not job.cancel.is_set(), "a mismatched cancel must not cancel the running job"

    # The correct id still works: 202 and the event is set.
    r = client.post("/api/index/cancel", json={"job_id": job_id}, headers=AUTH)
    assert r.status_code == 202
    assert job.cancel.is_set()
    go.set()
    assert _wait(lambda: client.app_state.index_job.status == "cancelled")


def test_cancel_finished_job_id_is_409(client, corpus):
    """An id naming a job that already finished is 'not the one running' → 409,
    even though it's a real id the client legitimately held."""
    job_id = client.post("/api/index", json={"root": str(corpus)}, headers=AUTH).json()["job_id"]
    assert _wait(lambda: client.app_state.index_job.status == "done")

    r = client.post("/api/index/cancel", json={"job_id": job_id}, headers=AUTH)
    assert r.status_code == 409


def test_not_wedged_after_cancel(client, corpus, monkeypatch):
    go, _ = _gate(monkeypatch)
    job_id = client.post("/api/index", json={"root": str(corpus)}, headers=AUTH).json()["job_id"]
    assert _wait(lambda: client.app_state.index_job is not None)

    r = client.post("/api/index/cancel", json={"job_id": job_id}, headers=AUTH)
    assert r.status_code == 202
    go.set()
    assert _wait(lambda: client.app_state.index_job.status == "cancelled")

    # A fresh job is accepted once the cancelled one finishes.
    r = client.post("/api/index", json={"root": str(corpus)}, headers=AUTH)
    assert r.status_code == 202
