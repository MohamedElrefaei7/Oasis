"""POST /api/open — index-membership validation and path normalization.

subprocess.run is mocked throughout: no real `open` ever fires.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from oasis.api.app import create_app
from oasis.config import OasisConfig
from oasis.index.db import open_db
from oasis.index.keyword import KeywordIndex
from oasis.models import DocumentMetadata, ExtractedDocument

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
DIM = 8


class FakeEmbedder:
    dimension = DIM
    model_name = "fake-model"

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.zeros((len(texts), DIM), dtype=np.float32)


class FakeReranker:
    def rerank(self, query, results, *, top_n=None):
        return results[:top_n] if top_n is not None else results


class FakeVectorIndex:
    def __init__(self, db_path: Path, dimension: int) -> None:
        pass


def _index_file(db_path: Path, path: Path, text: str = "indexed content") -> None:
    """Index *path* exactly as the pipeline does — str(path), not resolved."""
    conn = open_db(db_path)
    KeywordIndex(conn).upsert(
        ExtractedDocument(
            path=path,
            text=text,
            metadata=DocumentMetadata(size_bytes=len(text), mtime=1000.0),
        )
    )
    conn.commit()
    conn.close()


@pytest.fixture
def run_mock(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return None

    monkeypatch.setattr("oasis.api.open.subprocess.run", fake_run)
    return calls


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    """A ready client plus the tmp dirs the tests index into."""
    db_path = tmp_path / "index.db"
    monkeypatch.setattr("oasis.api.app.load_config", lambda: OasisConfig(db_path=db_path))
    monkeypatch.setattr("oasis.api.app.SentenceTransformerEmbedder", FakeEmbedder)
    monkeypatch.setattr("oasis.api.app.CrossEncoderReranker", FakeReranker)
    monkeypatch.setattr("oasis.api.app.VectorIndex", FakeVectorIndex)
    monkeypatch.setattr("oasis.api.app.ensure_ollama", lambda: None)
    app = create_app(token=TOKEN)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert app.state.oasis.ready.wait(timeout=10)
        yield client, tmp_path, db_path


def _post(client, path) -> object:
    return client.post("/api/open", headers=AUTH, json={"path": str(path)})


def _assert_envelope(body: dict) -> None:
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_indexed_file_opens_204(ctx, run_mock):
    client, tmp_path, db_path = ctx
    target = tmp_path / "doc.txt"
    target.write_text("indexed content")
    _index_file(db_path, target)

    resp = _post(client, target)
    assert resp.status_code == 204
    assert resp.content == b""
    # Opened exactly as stored — no resolve(), no rewriting.
    assert run_mock == [["open", str(target)]]


# ---------------------------------------------------------------------------
# Path normalization — the request must equal the stored (abspath) form.
# Storage normalizes with os.path.abspath (lexical, no symlink following), so
# the lookup does the identical thing and never chases symlink aliases.
# ---------------------------------------------------------------------------


def test_stored_form_containing_symlink_opens_204(ctx, run_mock):
    """Indexed *through* a symlinked root (so the stored path contains the
    symlink, unresolved) and requested in that exact form — what the app
    round-trips back from /api/search, since search returns paths as stored.

    A resolve() anywhere in the lookup would 404 this legitimate request.
    """
    client, tmp_path, db_path = ctx
    real_dir = tmp_path / "real2"
    real_dir.mkdir()
    (real_dir / "doc.txt").write_text("indexed content")

    link_dir = tmp_path / "link2"
    link_dir.symlink_to(real_dir, target_is_directory=True)
    via_link = link_dir / "doc.txt"
    _index_file(db_path, via_link)  # stored: /…/link2/doc.txt — NOT resolved
    assert via_link.resolve() != via_link

    resp = _post(client, via_link)
    assert resp.status_code == 204
    # Opened by the stored form, not rewritten through the symlink.
    assert run_mock == [["open", str(via_link)]]


def test_symlink_alias_of_stored_form_is_404(ctx, run_mock):
    """Indexed by its real path, requested through a symlink alias.

    Open matches the exact stored form and does not chase aliases — safe
    (fail-closed), and a non-issue for the real client, which only ever sends
    paths it received from /api/search.
    """
    client, tmp_path, db_path = ctx
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    target = real_dir / "doc.txt"
    target.write_text("indexed content")
    _index_file(db_path, target)  # stored: /…/real/doc.txt

    link_dir = tmp_path / "link"
    link_dir.symlink_to(real_dir, target_is_directory=True)
    via_link = link_dir / "doc.txt"
    assert via_link.resolve() == target.resolve()  # same file, different alias

    resp = _post(client, via_link)
    assert resp.status_code == 404
    _assert_envelope(resp.json())
    assert run_mock == []


def test_traversal_path_is_404(ctx, run_mock):
    client, tmp_path, db_path = ctx
    target = tmp_path / "doc.txt"
    target.write_text("indexed content")
    _index_file(db_path, target)

    # abspath-normalizes to /etc/passwd, which is not indexed.
    resp = _post(client, tmp_path / ".." / ".." / ".." / ".." / "etc" / "passwd")
    assert resp.status_code == 404
    _assert_envelope(resp.json())
    assert run_mock == []


def test_relative_path_is_404(ctx, run_mock):
    # Storage never contains relative paths (the pipeline absolutizes its
    # root), and the client echoes stored paths — a relative request is
    # defensively absolutized against the server's CWD and misses the index.
    client, _tmp_path, _db_path = ctx
    resp = _post(client, "relative/doc.txt")
    assert resp.status_code == 404
    _assert_envelope(resp.json())
    assert run_mock == []


# ---------------------------------------------------------------------------
# Not found / gone
# ---------------------------------------------------------------------------


def test_unindexed_path_is_404(ctx, run_mock):
    client, tmp_path, _db_path = ctx
    stranger = tmp_path / "never-indexed.txt"
    stranger.write_text("hi")  # exists on disk, but Oasis never saw it

    resp = _post(client, stranger)
    assert resp.status_code == 404
    _assert_envelope(resp.json())
    assert resp.json()["error"]["code"] == "not_found"
    assert run_mock == []


def test_indexed_but_deleted_file_is_410(ctx, run_mock):
    client, tmp_path, db_path = ctx
    target = tmp_path / "vanished.txt"
    target.write_text("indexed content")
    _index_file(db_path, target)
    target.unlink()  # indexed, then removed from disk

    resp = _post(client, target)
    assert resp.status_code == 410
    _assert_envelope(resp.json())
    assert resp.json()["error"]["code"] == "gone"
    assert run_mock == []


def test_410_is_distinct_from_404(ctx, run_mock):
    """The two cases must not collapse: 410 means 'reindex me', 404 means 'never saw it'."""
    client, tmp_path, db_path = ctx
    gone = tmp_path / "gone.txt"
    gone.write_text("indexed content")
    _index_file(db_path, gone)
    gone.unlink()

    assert _post(client, gone).status_code == 410
    assert _post(client, tmp_path / "unknown.txt").status_code == 404


# ---------------------------------------------------------------------------
# Auth / validation
# ---------------------------------------------------------------------------


def test_no_token_is_401(ctx, run_mock):
    client, tmp_path, _db_path = ctx
    resp = client.post("/api/open", json={"path": str(tmp_path / "doc.txt")})
    assert resp.status_code == 401
    _assert_envelope(resp.json())
    assert run_mock == []


def test_missing_path_field_is_422_envelope(ctx, run_mock):
    client, _tmp_path, _db_path = ctx
    resp = client.post("/api/open", headers=AUTH, json={})
    assert resp.status_code == 422
    _assert_envelope(resp.json())
    assert run_mock == []
