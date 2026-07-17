"""POST /api/reset — delete the index and swap in a fresh empty one.

The deletion is trivial; the commit is the *swap*. VectorIndex is one shared
handle every search reads and the index job writes through, and reset destroys
and replaces it while searches may be mid-flight. The test that the whole
commit turns on is ``test_search_racing_reset_never_500`` — barrier-driven so a
search is genuinely in-flight across the vector drop; it goes red the moment an
in-flight reader hits the dropped table without a clean fallback.

Real uvicorn + real SQLite/LanceDB, models faked (crc32-deterministic) so
PyTorch never loads — same hermetic pattern as test_api_index.
"""

from __future__ import annotations

import json
import shutil
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


@pytest.fixture(autouse=True, scope="module")
def _hermetic_no_torch():
    """This file stands up real servers with faked models — it must never pull
    in PyTorch. Goes red if a module-level sentence_transformers import regresses."""
    yield
    import sys

    assert "torch" not in sys.modules, "test_api_reset must never import PyTorch"


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

    c = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30.0)
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
    d = tmp_path / "corpus"
    d.mkdir()
    (d / "a.txt").write_text("revenue grew in Q3 driven by enterprise renewals")
    (d / "b.txt").write_text("machine learning embeddings zqxwomble distinctive")
    (d / "c.txt").write_text("coral reef bleaching from rising ocean temperatures")
    return d


def _wait(pred, timeout=10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


def _index_and_wait(client, root) -> None:
    r = client.post("/api/index", json={"root": str(root)}, headers=AUTH)
    assert r.status_code == 202
    assert _wait(lambda: client.app_state.index_job.status == "done")


def _gate(monkeypatch):
    """Block the pipeline on `go` so a job stays genuinely `running`."""
    go = threading.Event()

    def gated(*args, **kwargs):
        go.wait(10)
        return real_index_directory(*args, **kwargs)

    monkeypatch.setattr("oasis.api.index.index_directory", gated)
    return go


# ==========================================================================
# The four that matter
# ==========================================================================


def test_reset_while_indexing_is_409_and_index_untouched(client, corpus, monkeypatch):
    """Reset takes the same job_lock as /api/index and refuses while a job runs
    — a reset mid-index would drop the handle the job writes through. The index
    must be untouched (nothing deleted). Red if reset doesn't check the lock."""
    _index_and_wait(client, corpus)
    assert client.get("/api/search", params={"q": "zqxwomble", "mode": "keyword"}, headers=AUTH).json()["results"]

    # A gated re-index → a job genuinely stuck in `running`.
    go = _gate(monkeypatch)
    client.post("/api/index", json={"root": str(corpus), "force": True}, headers=AUTH)
    assert _wait(lambda: client.app_state.index_job.status == "running")

    rr = client.post("/api/reset", json={"confirm": True}, headers=AUTH)
    assert rr.status_code == 409
    assert rr.json()["error"]["code"] == "conflict"  # envelope shape

    # Nothing deleted: original content still searchable, status still 3 docs.
    assert client.get("/api/search", params={"q": "zqxwomble", "mode": "keyword"}, headers=AUTH).json()["results"]
    assert client.get("/api/status", headers=AUTH).json()["documents"] == 3

    go.set()  # let the gated re-index finish so teardown is clean
    assert _wait(lambda: client.app_state.index_job.status == "done")


def test_search_racing_reset_never_500(client, corpus, monkeypatch):
    """THE test. A search is held in-flight (holding the OLD VectorIndex handle)
    across the reset's vector drop, then released into the dropped table. It must
    return a well-formed 2xx (pre-reset hits or empty), never a 500/traceback.

    Barrier via a run_search wrapper: the endpoint has already read
    state.vector_index (the old handle) and passed it in before the wrapper
    blocks, so when reset rmtrees that handle's files and installs a new one, the
    released call genuinely searches the dropped table. The hybrid vector arm
    catches the LanceDB error and degrades to keyword-only."""
    _index_and_wait(client, corpus)

    import oasis.api.search as search_mod

    real_run_search = search_mod.run_search
    in_flight = threading.Event()
    release = threading.Event()

    def racing_run_search(*args, **kwargs):
        in_flight.set()  # I hold the OLD handle now (passed as an arg)
        assert release.wait(10)  # ...block until reset has dropped it
        return real_run_search(*args, **kwargs)  # search the dropped table

    monkeypatch.setattr("oasis.api.search.run_search", racing_run_search)

    box: dict[str, object] = {}

    def do_search():
        r = client.get("/api/search", params={"q": "zqxwomble", "mode": "hybrid"}, headers=AUTH)
        box["status"] = r.status_code
        box["text"] = r.text

    searcher = threading.Thread(target=do_search)
    searcher.start()
    assert in_flight.wait(10), "search never entered run_search"

    # Reset on the test thread — drops the old handle's files, installs a new one.
    rr = client.post("/api/reset", json={"confirm": True}, headers=AUTH)
    assert rr.status_code == 204

    release.set()  # let the in-flight search proceed into the now-dropped table
    searcher.join(10)

    assert box["status"] == 200, f"racing search was not 2xx: {box}"
    body = json.loads(box["text"])  # type: ignore[arg-type]
    assert isinstance(body.get("results"), list)  # well-formed
    assert "Traceback" not in box["text"] and "Internal server error" not in box["text"]


def test_reset_then_empty_then_reindex_finds_new_content(client, corpus, tmp_path):
    """The swap actually installed the new handle: reset → empty status/search →
    reindex NEW content → findable, including via SEMANTIC mode (which reads only
    the vector index), proving nothing leaked the dropped handle."""
    _index_and_wait(client, corpus)
    assert client.get("/api/search", params={"q": "zqxwomble", "mode": "hybrid"}, headers=AUTH).json()["results"]

    assert client.post("/api/reset", json={"confirm": True}, headers=AUTH).status_code == 204

    # Status reflects a true empty state (0 docs, markers cleared).
    st = client.get("/api/status", headers=AUTH).json()
    assert st["documents"] == 0
    assert st["vectors_built"] is False
    assert st["reindex_recommended"] is False  # 0 docs is "index me", not "reindex me"
    assert st["indexed_roots"] == []

    # Search returns a well-formed empty body, not an error.
    sr = client.get("/api/search", params={"q": "zqxwomble", "mode": "hybrid"}, headers=AUTH)
    assert sr.status_code == 200 and sr.json()["results"] == []

    # Reindex a NEW dir with a NEW token, then find it through the rebuilt handle.
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    (fresh / "new.txt").write_text("brandnew qznewtoken content lives here")
    _index_and_wait(client, fresh)

    hybrid = client.get("/api/search", params={"q": "qznewtoken", "mode": "hybrid"}, headers=AUTH)
    assert hybrid.status_code == 200
    assert any(r["path"].endswith("new.txt") for r in hybrid.json()["results"])

    # Semantic mode uses ONLY the vector index — finding new content here proves
    # the reindex wrote through, and search reads, the REBUILT handle.
    semantic = client.get("/api/search", params={"q": "qznewtoken", "mode": "semantic"}, headers=AUTH)
    assert semantic.status_code == 200
    assert semantic.json()["results"], "new content not visible via the rebuilt vector handle"


def test_crash_between_stores_is_failsafe_reindex_needed(client, corpus):
    """A crash mid-reset must leave a fail-safe marker state, never corruption.
    Reset's order is markers → vectors → documents; the dangerous intermediate is
    "vectors gone, documents present" — it must read as reindex-needed, NOT
    "semantic ready with no vectors". Simulate that exact window (clear_meta +
    rmtree the .lance, leave documents) and assert status reads honestly."""
    from oasis.api.state import get_conn
    from oasis.index.keyword import KeywordIndex

    _index_and_wait(client, corpus)
    st = client.get("/api/status", headers=AUTH).json()
    assert st["vectors_built"] is True and st["semantic_ready"] is True  # precondition

    # Reproduce the reset state AFTER step 2 (markers cleared + vectors dropped)
    # but BEFORE step 3 (documents cleared) — i.e. a crash between the stores.
    state = client.app_state
    conn = get_conn(state.db_path)  # test-thread conn; the commit is WAL-visible
    KeywordIndex(conn).clear_meta()
    shutil.rmtree(state.db_path.with_name(state.db_path.stem + ".lance"))

    st2 = client.get("/api/status", headers=AUTH).json()
    assert st2["documents"] == 3  # documents survived the "crash"
    assert st2["vectors_built"] is False  # the marker died WITH the vectors...
    assert st2["semantic_ready"] is False  # ...so nothing claims usable vectors
    assert st2["reindex_recommended"] is True  # the conservative, honest state


# ==========================================================================
# Contract extras
# ==========================================================================


def test_reset_requires_confirm_400(client, corpus):
    _index_and_wait(client, corpus)
    # Explicit confirm: false, and an empty body, both → 400 (not 422/500).
    for body in ({"confirm": False}, {}):
        r = client.post("/api/reset", json=body, headers=AUTH)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "bad_request"
    # ...and the index was NOT touched.
    assert client.get("/api/status", headers=AUTH).json()["documents"] == 3


def test_reset_without_token_401(client, corpus):
    _index_and_wait(client, corpus)
    r = client.post("/api/reset", json={"confirm": True})  # no auth header
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"
    assert client.get("/api/status", headers=AUTH).json()["documents"] == 3  # untouched


def test_reset_no_index_is_404(client):
    # Fresh server, never indexed → no .db file → nothing to reset.
    r = client.post("/api/reset", json={"confirm": True}, headers=AUTH)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_reset_happy_path_204_and_idempotent(client, corpus):
    _index_and_wait(client, corpus)
    assert client.post("/api/reset", json={"confirm": True}, headers=AUTH).status_code == 204
    # The db file remains (cleared in place), so a second reset is 204, not 404.
    assert client.post("/api/reset", json={"confirm": True}, headers=AUTH).status_code == 204
    assert client.get("/api/status", headers=AUTH).json()["documents"] == 0
