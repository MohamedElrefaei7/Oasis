"""GET /api/status — the authenticated index detail view.

Real SQLite over a small corpus; the embedder/reranker/vector index are faked
so PyTorch never loads. Capability fields, the 404/200 split, the stale-scan
cap, indexed_roots persistence, and reindex_recommended parity with /api/health.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from oasis.api.app import create_app
from oasis.config import OasisConfig
from oasis.index.db import SCHEMA_VERSION, open_db
from oasis.index.keyword import KeywordIndex
from oasis.models import DocumentMetadata, ExtractedDocument

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
DIM = 8

DOCS = [
    ("a.txt", "Q3 Revenue Report", "revenue grew in Q3 driven by enterprise renewals"),
    ("b.md", None, "machine learning embeddings and vector search"),
    ("c.txt", "Coral Reefs", "coral reef bleaching from rising ocean temperatures"),
]


class FakeEmbedder:
    dimension = DIM

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.zeros((len(texts), DIM), dtype=np.float32)


class FakeReranker:
    def rerank(self, query, results, *, top_n=None):
        return results[:top_n] if top_n is not None else results


class FakeVectorIndex:
    def __init__(self, db_path: Path, dimension: int) -> None:
        self.db_path = db_path
        self.dimension = dimension


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


def _build_index(
    tmp_path: Path,
    db_path: Path,
    *,
    docs: list[tuple[str, str | None, str]] = DOCS,
    markers: bool = True,
    roots: list[str] | None = None,
) -> Path:
    """Write *docs* to a corpus dir and index them into *db_path*.

    ``markers`` writes the capability meta a real vectored index would carry;
    omit it for a legacy (keyword-only) index. Returns the corpus dir.
    """
    conn = open_db(db_path)
    kw = KeywordIndex(conn)
    corpus = tmp_path / "corpus"
    corpus.mkdir(exist_ok=True)
    for name, title, text in docs:
        p = corpus / name
        p.write_text(text)
        kw.upsert(
            ExtractedDocument(
                path=p,
                text=text,
                metadata=DocumentMetadata(size_bytes=len(text), mtime=1000.0, title=title),
            )
        )
    if markers:
        kw.set_meta("schema_version", str(SCHEMA_VERSION))
        kw.set_meta("vectors_built", "true")
        kw.set_meta("embedding_dimension", str(DIM))
        kw.set_meta("embedding_model", "all-MiniLM-L6-v2")
    for r in roots or []:
        kw.add_indexed_root(r)
    conn.close()
    return corpus


@pytest.fixture
def status_client(monkeypatch):
    """Factory: given a db_path, returns a ready TestClient pointed at it.

    The db need not exist — the loader (fakes) becomes ready regardless, which
    is exactly the "no index" case /api/status must 404 on.
    """
    entered: list[TestClient] = []

    def _factory(db_path: Path) -> TestClient:
        monkeypatch.setattr("oasis.api.app.load_config", lambda: OasisConfig(db_path=db_path))
        monkeypatch.setattr("oasis.api.app.SentenceTransformerEmbedder", FakeEmbedder)
        monkeypatch.setattr("oasis.api.app.CrossEncoderReranker", FakeReranker)
        monkeypatch.setattr("oasis.api.app.VectorIndex", FakeVectorIndex)
        monkeypatch.setattr("oasis.api.app.ensure_ollama", lambda: None)
        app = create_app(token=TOKEN)
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        assert app.state.oasis.ready.wait(timeout=10), "loader never became ready"
        entered.append(client)
        return client

    yield _factory
    for client in entered:
        client.__exit__(None, None, None)


def _assert_envelope(body: dict) -> None:
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}


# ---------------------------------------------------------------------------
# 404 vs 200
# ---------------------------------------------------------------------------


def test_no_index_is_404_envelope(status_client, tmp_path):
    db_path = tmp_path / "index.db"  # never created
    client = status_client(db_path)
    resp = client.get("/api/status", headers=AUTH)
    assert resp.status_code == 404
    _assert_envelope(resp.json())
    assert resp.json()["error"]["code"] == "not_found"


def test_empty_index_is_200_with_zero_counts(status_client, tmp_path):
    db_path = tmp_path / "index.db"
    open_db(db_path).close()  # exists, but no documents
    client = status_client(db_path)
    resp = client.get("/api/status", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["documents"] == 0
    assert body["stale_documents"] == 0  # computed, none stale (not null)
    assert body["last_indexed_at"] is None
    assert body["reindex_recommended"] is False  # 0 docs is "index me", not "reindex me"


# ---------------------------------------------------------------------------
# Populated index
# ---------------------------------------------------------------------------


def test_populated_index_matches_capabilities(status_client, tmp_path):
    db_path = tmp_path / "index.db"
    _build_index(tmp_path, db_path)
    client = status_client(db_path)
    resp = client.get("/api/status", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()

    # Cross-check the capability fields against get_capabilities directly.
    caps = KeywordIndex(open_db(db_path)).get_capabilities()
    assert body["documents"] == caps.document_count == len(DOCS)
    assert body["schema_version"] == caps.schema_version == SCHEMA_VERSION
    assert body["vectors_built"] == caps.vectors_built is True
    assert body["embedding_model"] == caps.embedding_model == "all-MiniLM-L6-v2"
    assert body["embedding_dimension"] == caps.embedding_dimension == DIM

    assert body["db_size_bytes"] > 0
    assert body["db_path"] == str(db_path)
    # last_indexed_at is a UTC-offset ISO string, not naive.
    parsed = datetime.fromisoformat(body["last_indexed_at"])
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0

    # Vectors built at the live embedder's dimension → ready, no reindex.
    assert body["semantic_ready"] is True
    assert body["reindex_recommended"] is False


# ---------------------------------------------------------------------------
# stale_documents
# ---------------------------------------------------------------------------


def test_stale_documents_counts_missing_files(status_client, tmp_path):
    db_path = tmp_path / "index.db"
    corpus = _build_index(tmp_path, db_path)
    (corpus / "a.txt").unlink()  # one indexed file removed from disk
    client = status_client(db_path)
    body = client.get("/api/status", headers=AUTH).json()
    assert body["stale_documents"] == 1


def test_stale_scan_cap_reports_null_and_skips_scan(status_client, tmp_path, monkeypatch):
    db_path = tmp_path / "index.db"
    _build_index(tmp_path, db_path)  # 3 docs

    # Spy on the scan so we can prove it never runs over the cap.
    calls: list[int] = []
    real = KeywordIndex.count_stale

    def spy(self):
        calls.append(1)
        return real(self)

    monkeypatch.setattr(KeywordIndex, "count_stale", spy)
    monkeypatch.setattr("oasis.api.status.STALE_SCAN_CAP", 0)  # 3 docs > 0 → skip

    client = status_client(db_path)
    body = client.get("/api/status", headers=AUTH).json()
    assert body["stale_documents"] is None  # "not computed", distinct from 0
    assert calls == []  # no per-file stat scan ran


# ---------------------------------------------------------------------------
# indexed_roots (persistence added in the pipeline)
# ---------------------------------------------------------------------------


def test_indexed_roots_present_and_abspath(status_client, tmp_path):
    db_path = tmp_path / "index.db"
    # Persist a deliberately relative root; add_indexed_root stores what it's
    # given, and the pipeline abspaths before calling — assert the app sees an
    # absolute path when the pipeline runs (below) and the field is populated.
    root = str(Path(tmp_path / "corpus"))
    _build_index(tmp_path, db_path, roots=[root])
    client = status_client(db_path)
    body = client.get("/api/status", headers=AUTH).json()
    assert body["indexed_roots"] == [root]
    assert Path(body["indexed_roots"][0]).is_absolute()


def test_pipeline_persists_abspath_root(tmp_path, monkeypatch):
    """End-to-end: index_directory records the abspath'd root it walked."""
    from oasis.index.pipeline import index_directory

    corpus = tmp_path / "docs"
    corpus.mkdir()
    (corpus / "x.txt").write_text("hello world")

    db_path = tmp_path / "index.db"
    conn = open_db(db_path)
    # Drive with a RELATIVE root from the corpus's parent, to prove abspath.
    monkeypatch.chdir(tmp_path)
    index_directory(conn, Path("docs"))
    roots = KeywordIndex(conn).get_indexed_roots()
    conn.close()
    assert roots == [str(corpus)]
    assert Path(roots[0]).is_absolute()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_no_token_is_401_envelope(status_client, tmp_path):
    db_path = tmp_path / "index.db"
    _build_index(tmp_path, db_path)
    client = status_client(db_path)
    resp = client.get("/api/status")  # no Authorization header
    assert resp.status_code == 401
    _assert_envelope(resp.json())
    assert resp.json()["error"]["code"] == "unauthorized"


# ---------------------------------------------------------------------------
# reindex_recommended parity with /api/health
# ---------------------------------------------------------------------------


def test_reindex_recommended_parity_with_health_on_legacy_index(status_client, tmp_path):
    db_path = tmp_path / "index.db"
    _build_index(tmp_path, db_path, markers=False)  # docs, no capability markers
    client = status_client(db_path)

    status_body = client.get("/api/status", headers=AUTH).json()
    health_body = client.get("/api/health").json()

    # A legacy index (schema_version 0, no vectors) → reindex_recommended true,
    # and status must report exactly what health does.
    assert status_body["reindex_recommended"] is True
    assert status_body["reindex_recommended"] == health_body["reindex_recommended"]
    assert status_body["semantic_ready"] == health_body["semantic_ready"] is False
    assert status_body["schema_version"] == health_body["schema_version"] == 0
