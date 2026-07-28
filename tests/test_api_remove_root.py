"""POST /api/index/remove-root — forget one indexed folder.

Destructive, so tested adversarially like the stale sweep. The two tests the
endpoint exists for:

- ``test_remove_root_when_directory_deleted_from_disk`` — the wedge case. A root
  gone from disk is precisely what this endpoint must handle, because that is
  the state that jams Reindex. It goes red the moment anyone reintroduces a
  census gate or a walk, which would make the endpoint fail exactly where it is
  needed.
- ``test_sibling_prefix_is_not_removed`` — removing ``/tmp/a`` must not touch
  ``/tmp/ab``. The separator-boundary trap, and the reason ``docs_under``
  filters in Python instead of with SQL ``LIKE`` (where a path's ``_`` is a
  single-char wildcard).

Real uvicorn + real SQLite/LanceDB, models faked (crc32-deterministic) so
PyTorch never loads — the hermetic pattern from test_api_index/test_api_reset.
"""

from __future__ import annotations

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
    """Faked models — this file must never pull in PyTorch. Goes red if a
    module-level sentence_transformers import regresses."""
    yield
    import sys

    assert "torch" not in sys.modules, "test_api_remove_root must never import PyTorch"


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wait(pred, timeout=10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


def _index_and_wait(client, root) -> None:
    r = client.post("/api/index", json={"root": str(root)}, headers=AUTH)
    assert r.status_code == 202, r.text
    assert _wait(lambda: client.app_state.index_job.status == "done")


def _gate(monkeypatch):
    """Block the pipeline on `go` so a job stays genuinely `running`."""
    go = threading.Event()

    def gated(*args, **kwargs):
        go.wait(10)
        return real_index_directory(*args, **kwargs)

    monkeypatch.setattr("oasis.api.index.index_directory", gated)
    return go


def _keyword_paths(client, term: str) -> set[str]:
    """Paths the FTS/keyword arm returns for *term* — the keyword+FTS check."""
    r = client.get("/api/search", params={"q": term, "mode": "keyword"}, headers=AUTH)
    assert r.status_code == 200, r.text
    return {hit["path"] for hit in r.json()["results"]}


def _roots(client) -> list[str]:
    r = client.get("/api/status", headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()["indexed_roots"]


def _vector_doc_ids(client) -> set[int]:
    """doc_ids still holding vectors — the third arm, read straight off LanceDB."""
    return client.app_state.vector_index.doc_ids_with_vectors()


def _doc_id(client, path: Path) -> int | None:
    from oasis.api.state import get_conn
    from oasis.index.keyword import KeywordIndex

    return KeywordIndex(get_conn(client.app_state.db_path)).get_doc_id(path)


@pytest.fixture
def two_roots(tmp_path) -> tuple[Path, Path]:
    """Two independent roots, each with a distinctive term so search can tell
    them apart without relying on ranking."""
    keep = tmp_path / "keep"
    keep.mkdir()
    (keep / "k1.txt").write_text("quarterly revenue grew, keepword zqxkeep")
    (keep / "k2.txt").write_text("enterprise renewals summary, keepword zqxkeep")

    drop = tmp_path / "drop"
    drop.mkdir()
    (drop / "d1.txt").write_text("coral reef bleaching, dropword zqxdrop")
    (drop / "d2.txt").write_text("ocean temperature rise, dropword zqxdrop")
    (drop / "nested").mkdir()
    (drop / "nested" / "d3.txt").write_text("nested deeper file, dropword zqxdrop")
    return keep, drop


# ==========================================================================
# 1 — removes the root's docs from all three arms and untracks it
# ==========================================================================


def test_remove_root_clears_all_three_arms_and_untracks(client, two_roots):
    """Keyword rows, FTS rows and vectors all go for docs under the removed
    root; the root leaves indexed_roots; the other root is untouched.

    Red if the endpoint only deletes one arm — a doc gone from documents but
    live in LanceDB (or the reverse) is exactly the stale-hit state the sweep's
    per-doc convergence exists to prevent.
    """
    keep, drop = two_roots
    _index_and_wait(client, keep)
    _index_and_wait(client, drop)

    assert set(_roots(client)) == {str(keep), str(drop)}
    assert client.get("/api/status", headers=AUTH).json()["documents"] == 5
    drop_ids = {_doc_id(client, p) for p in sorted(drop.rglob("*.txt"))}
    keep_ids = {_doc_id(client, p) for p in sorted(keep.rglob("*.txt"))}
    assert None not in drop_ids and None not in keep_ids
    assert drop_ids <= _vector_doc_ids(client)  # vectors exist before removal

    r = client.post("/api/index/remove-root", json={"root": str(drop)}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json() == {"root": str(drop), "removed": 3}

    # Keyword + FTS: the removed root's term finds nothing, the other's is intact.
    assert _keyword_paths(client, "zqxdrop") == set()
    assert _keyword_paths(client, "zqxkeep") == {str(keep / "k1.txt"), str(keep / "k2.txt")}

    # Vectors: not one of the removed doc_ids still holds a chunk, and every
    # surviving doc_id does.
    remaining_vectors = _vector_doc_ids(client)
    assert drop_ids.isdisjoint(remaining_vectors)
    assert keep_ids <= remaining_vectors

    # The marker, and the count the app renders.
    assert _roots(client) == [str(keep)]
    assert client.get("/api/status", headers=AUTH).json()["documents"] == 2


# ==========================================================================
# 2 — sibling-prefix isolation (the LIKE-wildcard / separator-boundary trap)
# ==========================================================================


def test_sibling_prefix_is_not_removed(client, tmp_path):
    """Removing ``<tmp>/a`` must leave ``<tmp>/ab`` alone.

    Two traps in one: a bare ``startswith(root)`` puts ``/a/ab`` under ``/a``,
    and SQL ``LIKE 'a/%'`` treats the ``_`` in a real filename as a wildcard.
    The ``a_b`` file is there to make the second one bite if anyone rewrites
    docs_under in SQL.
    """
    a = tmp_path / "a"
    a.mkdir()
    (a / "a_b.txt").write_text("inside plain a, alphaword zqxalpha")

    ab = tmp_path / "ab"
    ab.mkdir()
    (ab / "f.txt").write_text("inside sibling ab, betaword zqxbeta")

    _index_and_wait(client, a)
    _index_and_wait(client, ab)
    assert client.get("/api/status", headers=AUTH).json()["documents"] == 2

    r = client.post("/api/index/remove-root", json={"root": str(a)}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["removed"] == 1  # NOT 2 — the sibling must not be swept up

    assert _keyword_paths(client, "zqxalpha") == set()
    assert _keyword_paths(client, "zqxbeta") == {str(ab / "f.txt")}
    assert _roots(client) == [str(ab)]
    assert client.get("/api/status", headers=AUTH).json()["documents"] == 1


# ==========================================================================
# 3 — THE wedge case: files gone from disk → still removes
# ==========================================================================


def test_remove_root_when_directory_deleted_from_disk(client, two_roots):
    """The reason this endpoint exists. A root deleted from disk wedges Reindex
    (the sequence 400s the missing directory and halts) and, before this, had no
    recourse short of a full reset.

    Removal must therefore be UNCONDITIONAL — no walk, no census gate. This test
    goes red the moment anyone makes remove-root reconcile against disk, because
    there is no disk left to reconcile against.
    """
    keep, drop = two_roots
    _index_and_wait(client, keep)
    _index_and_wait(client, drop)
    assert _keyword_paths(client, "zqxdrop")  # findable while the folder exists

    shutil.rmtree(drop)
    assert not drop.exists()
    # Precondition of the wedge: indexing that root now 400s, so the app cannot
    # get out of this state by reindexing.
    wedged = client.post("/api/index", json={"root": str(drop)}, headers=AUTH)
    assert wedged.status_code == 400
    assert str(drop) in _roots(client)  # ...and the root is still tracked

    r = client.post("/api/index/remove-root", json={"root": str(drop)}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["removed"] == 3  # deleted from the index, not from disk

    assert _keyword_paths(client, "zqxdrop") == set()
    assert _roots(client) == [str(keep)]
    assert client.get("/api/status", headers=AUTH).json()["documents"] == 2

    # Unwedged: the surviving root reindexes cleanly, which is the user-visible
    # point of the whole endpoint.
    _index_and_wait(client, keep)
    assert client.app_state.index_job.status == "done"


# ==========================================================================
# 4 — non-tracked root → 404
# ==========================================================================


def test_unknown_root_is_404(client, two_roots, tmp_path):
    """A root that was never indexed is a real not-found, and nothing is
    deleted on the way to saying so."""
    keep, _drop = two_roots
    _index_and_wait(client, keep)

    never = tmp_path / "never-indexed"
    never.mkdir()
    r = client.post("/api/index/remove-root", json={"root": str(never)}, headers=AUTH)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"  # envelope shape

    # A *subdirectory* of a tracked root is not itself a tracked root — matching
    # is exact, never prefix-based, so this is a 404 too rather than a partial
    # delete of its parent's documents.
    sub = keep / "sub"
    sub.mkdir()
    assert client.post(
        "/api/index/remove-root", json={"root": str(sub)}, headers=AUTH
    ).status_code == 404

    assert _keyword_paths(client, "zqxkeep")  # untouched
    assert _roots(client) == [str(keep)]


# ==========================================================================
# 5 — while a job is running → 409, index untouched
# ==========================================================================


def test_remove_root_while_indexing_is_409_and_index_untouched(client, two_roots, monkeypatch):
    """remove-root takes the same job_lock as /api/index and /api/reset: it
    mutates the index through the shared VectorIndex handle a running job writes
    through, so the two must not interleave. Nothing may be deleted."""
    keep, drop = two_roots
    _index_and_wait(client, keep)
    _index_and_wait(client, drop)

    go = _gate(monkeypatch)
    client.post("/api/index", json={"root": str(keep), "force": True}, headers=AUTH)
    assert _wait(lambda: client.app_state.index_job.status == "running")

    r = client.post("/api/index/remove-root", json={"root": str(drop)}, headers=AUTH)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"  # envelope shape

    # Nothing deleted, nothing untracked.
    assert _keyword_paths(client, "zqxdrop")
    assert set(_roots(client)) == {str(keep), str(drop)}
    assert client.get("/api/status", headers=AUTH).json()["documents"] == 5

    go.set()  # let the gated job finish so teardown is clean
    assert _wait(lambda: client.app_state.index_job.status == "done")
