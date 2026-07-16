"""Index capability markers: meta table, get_capabilities, pipeline writes,
and the /api/health surface that lets the app tell a legacy index apart from
an empty one.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from oasis.api.app import create_app
from oasis.config import OasisConfig
from oasis.index.db import SCHEMA_VERSION, open_db
from oasis.index.keyword import KeywordIndex
from oasis.index.pipeline import index_directory
from oasis.models import DocumentMetadata, ExtractedDocument

TOKEN = "test-token"
LIVE_DIM = 8


class FakeEmbedder:
    """Stand-in for SentenceTransformerEmbedder with a known dim + model name."""

    def __init__(self, dimension: int = LIVE_DIM, model_name: str = "fake-minilm") -> None:
        self.dimension = dimension
        self.model_name = model_name

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.zeros((len(texts), self.dimension), dtype=np.float32)


class NamelessEmbedder:
    """An EmbeddingModel with no model_name — the Protocol doesn't require one."""

    dimension = LIVE_DIM

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.zeros((len(texts), self.dimension), dtype=np.float32)


class FakeVectorIndex:
    def __init__(self, db_path: Path | None = None, dimension: int = LIVE_DIM) -> None:
        self.rows: list[object] = []

    def upsert_chunks(self, records) -> None:
        self.rows.extend(records)

    def delete_by_doc_id(self, doc_id: int) -> None:
        pass

    def count(self) -> int:
        return len(self.rows)


class FakeReranker:
    def rerank(self, query, results, *, top_n=None):
        return results[:top_n] if top_n is not None else results


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "a.txt").write_text("alpha content about revenue")
    (root / "b.txt").write_text("beta content about machine learning")
    return root


def _legacy_index(db_path: Path, n: int = 3) -> None:
    """An index as built before the meta table existed: documents, no markers."""
    conn = open_db(db_path)
    idx = KeywordIndex(conn)
    for i in range(n):
        text = f"legacy document {i}"
        idx.upsert(
            ExtractedDocument(
                path=Path(f"/legacy/doc{i}.txt"),
                text=text,
                metadata=DocumentMetadata(size_bytes=len(text), mtime=1000.0),
            )
        )
    conn.execute("DELETE FROM meta")  # nothing ever wrote here; be explicit
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# meta round-trips
# ---------------------------------------------------------------------------


def test_meta_set_get_roundtrip(tmp_path):
    conn = open_db(tmp_path / "index.db")
    idx = KeywordIndex(conn)
    assert idx.get_meta("nothing") is None
    idx.set_meta("k", "v")
    assert idx.get_meta("k") == "v"


def test_meta_set_updates_in_place(tmp_path):
    conn = open_db(tmp_path / "index.db")
    idx = KeywordIndex(conn)
    idx.set_meta("k", "first")
    idx.set_meta("k", "second")
    assert idx.get_meta("k") == "second"
    count = conn.execute("SELECT COUNT(*) FROM meta WHERE key = 'k'").fetchone()[0]
    assert count == 1  # ON CONFLICT updates, never duplicates


def test_open_db_is_idempotent_and_writes_no_markers(tmp_path):
    db_path = tmp_path / "index.db"
    open_db(db_path).close()
    conn = open_db(db_path)  # reopening must not fail or invent markers
    assert conn.execute("SELECT COUNT(*) FROM meta").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# get_capabilities — pure DB read
# ---------------------------------------------------------------------------


def test_capabilities_of_fresh_db(tmp_path):
    conn = open_db(tmp_path / "index.db")
    caps = KeywordIndex(conn).get_capabilities()
    assert caps.schema_version == 0
    assert caps.vectors_built is False
    assert caps.embedding_model is None
    assert caps.embedding_dimension is None
    assert caps.document_count == 0


def test_capabilities_of_legacy_index_reads_as_needs_reindex(tmp_path):
    """The case that must NOT read as healthy: documents, but no vectors."""
    db_path = tmp_path / "index.db"
    _legacy_index(db_path, n=3)
    caps = KeywordIndex(open_db(db_path)).get_capabilities()
    assert caps.document_count == 3  # not empty…
    assert caps.vectors_built is False  # …but not semantically searchable
    assert caps.schema_version == 0


def test_capabilities_after_indexing_with_embedder(tmp_path, corpus):
    db_path = tmp_path / "index.db"
    conn = open_db(db_path)
    embedder = FakeEmbedder()
    stats = index_directory(
        conn, corpus, vector_index=FakeVectorIndex(), embedder=embedder
    )
    assert stats["indexed"] == 2

    caps = KeywordIndex(conn).get_capabilities()
    assert caps.schema_version == SCHEMA_VERSION == 1
    assert caps.vectors_built is True
    assert caps.embedding_model == "fake-minilm"
    assert caps.embedding_dimension == LIVE_DIM
    assert caps.document_count == 2


def test_capabilities_after_indexing_without_embedder(tmp_path, corpus):
    db_path = tmp_path / "index.db"
    conn = open_db(db_path)
    index_directory(conn, corpus)  # keyword-only run

    caps = KeywordIndex(conn).get_capabilities()
    assert caps.schema_version == SCHEMA_VERSION
    assert caps.vectors_built is False  # no embedder → no claim
    assert caps.embedding_model is None
    assert caps.document_count == 2


def test_embedder_without_model_name_still_records_dimension(tmp_path, corpus):
    conn = open_db(tmp_path / "index.db")
    index_directory(conn, corpus, vector_index=FakeVectorIndex(), embedder=NamelessEmbedder())
    caps = KeywordIndex(conn).get_capabilities()
    assert caps.vectors_built is True
    assert caps.embedding_dimension == LIVE_DIM
    assert caps.embedding_model is None  # optional, read opportunistically


def test_incremental_rerun_does_not_downgrade_vectors_built(tmp_path, corpus):
    """A re-run with nothing new to embed skips the embed phase — it must not
    clear the markers of an index whose vectors are perfectly good."""
    conn = open_db(tmp_path / "index.db")
    embedder = FakeEmbedder()
    index_directory(conn, corpus, vector_index=FakeVectorIndex(), embedder=embedder)
    assert KeywordIndex(conn).get_capabilities().vectors_built is True

    stats = index_directory(conn, corpus, vector_index=FakeVectorIndex(), embedder=embedder)
    assert stats["skipped"] == 2 and stats["chunks"] == 0  # nothing re-embedded

    caps = KeywordIndex(conn).get_capabilities()
    assert caps.vectors_built is True
    assert caps.embedding_dimension == LIVE_DIM


def test_corrupt_marker_reads_as_absent(tmp_path):
    conn = open_db(tmp_path / "index.db")
    idx = KeywordIndex(conn)
    idx.set_meta("schema_version", "not-a-number")
    idx.set_meta("embedding_dimension", "garbage")
    caps = idx.get_capabilities()
    assert caps.schema_version == 0
    assert caps.embedding_dimension is None


# ---------------------------------------------------------------------------
# /api/health surface
# ---------------------------------------------------------------------------


def _client(monkeypatch, db_path: Path, embedder_dim: int = LIVE_DIM):
    monkeypatch.setattr("oasis.api.app.load_config", lambda: OasisConfig(db_path=db_path))
    monkeypatch.setattr(
        "oasis.api.app.SentenceTransformerEmbedder", lambda: FakeEmbedder(dimension=embedder_dim)
    )
    monkeypatch.setattr("oasis.api.app.CrossEncoderReranker", FakeReranker)
    monkeypatch.setattr(
        "oasis.api.app.VectorIndex", lambda db_path, dimension: FakeVectorIndex()
    )
    monkeypatch.setattr("oasis.api.app.ensure_ollama", lambda: None)
    app = create_app(token=TOKEN)
    client = TestClient(app, raise_server_exceptions=False)
    return app, client


def test_health_semantic_ready_true_for_vector_index(monkeypatch, tmp_path, corpus):
    db_path = tmp_path / "index.db"
    conn = open_db(db_path)
    index_directory(conn, corpus, vector_index=FakeVectorIndex(), embedder=FakeEmbedder())
    conn.close()

    app, client = _client(monkeypatch, db_path)
    with client:
        assert app.state.oasis.ready.wait(timeout=10)
        body = client.get("/api/health").json()

    assert body["status"] == "ready"
    assert body["documents"] == 2
    assert body["vectors_built"] is True
    assert body["embedding_model"] == "fake-minilm"
    assert body["embedding_dimension"] == LIVE_DIM
    assert body["semantic_ready"] is True


def test_health_over_legacy_index_reports_not_semantic_ready(monkeypatch, tmp_path):
    """The exact 'looks broken but isn't' case: documents present, no vectors."""
    db_path = tmp_path / "index.db"
    _legacy_index(db_path, n=5)

    app, client = _client(monkeypatch, db_path)
    with client:
        assert app.state.oasis.ready.wait(timeout=10)
        body = client.get("/api/health").json()

    assert body["status"] == "ready"
    assert body["documents"] == 5  # not empty — so "no results" isn't the story
    assert body["vectors_built"] is False
    assert body["semantic_ready"] is False  # → app should prompt a reindex
    assert body["embedding_model"] is None
    assert body["embedding_dimension"] is None


def test_health_dimension_mismatch_is_not_semantic_ready(monkeypatch, tmp_path, corpus):
    """Vectors exist, but were built at a dimension the live model can't query."""
    db_path = tmp_path / "index.db"
    conn = open_db(db_path)
    index_directory(
        conn,
        corpus,
        vector_index=FakeVectorIndex(),
        embedder=FakeEmbedder(dimension=384, model_name="old-model"),
    )
    conn.close()

    app, client = _client(monkeypatch, db_path, embedder_dim=512)  # live model differs
    with client:
        assert app.state.oasis.ready.wait(timeout=10)
        body = client.get("/api/health").json()

    assert body["vectors_built"] is True  # they're there…
    assert body["embedding_dimension"] == 384
    assert body["semantic_ready"] is False  # …but unusable at 512


def test_health_while_loading_reports_capability_fields_as_defaults(monkeypatch, tmp_path, corpus):
    import threading

    db_path = tmp_path / "index.db"
    conn = open_db(db_path)
    index_directory(conn, corpus, vector_index=FakeVectorIndex(), embedder=FakeEmbedder())
    conn.close()

    gate = threading.Event()
    monkeypatch.setattr("oasis.api.app.load_config", lambda: OasisConfig(db_path=db_path))
    monkeypatch.setattr("oasis.api.app.CrossEncoderReranker", FakeReranker)
    monkeypatch.setattr("oasis.api.app.VectorIndex", lambda db_path, dimension: FakeVectorIndex())
    monkeypatch.setattr("oasis.api.app.ensure_ollama", lambda: None)

    def gated_embedder():
        assert gate.wait(timeout=30)
        return FakeEmbedder()

    monkeypatch.setattr("oasis.api.app.SentenceTransformerEmbedder", gated_embedder)
    app = create_app(token=TOKEN)
    with TestClient(app) as client:
        body = client.get("/api/health").json()
        assert body["status"] == "loading"
        # No live embedder yet, so nothing to compare a dimension against.
        assert body["documents"] is None
        assert body["vectors_built"] is False
        assert body["embedding_model"] is None
        assert body["embedding_dimension"] is None
        assert body["semantic_ready"] is False

        gate.set()
        assert app.state.oasis.ready.wait(timeout=10)
        body = client.get("/api/health").json()
        assert body["semantic_ready"] is True  # flips once loaded


def test_health_needs_no_auth_and_stays_nonsensitive(monkeypatch, tmp_path, corpus):
    db_path = tmp_path / "index.db"
    conn = open_db(db_path)
    index_directory(conn, corpus, vector_index=FakeVectorIndex(), embedder=FakeEmbedder())
    conn.close()

    app, client = _client(monkeypatch, db_path)
    with client:
        assert app.state.oasis.ready.wait(timeout=10)
        resp = client.get("/api/health")  # no Authorization header
    assert resp.status_code == 200
    # Capability fields must not leak paths or content.
    assert str(db_path) not in resp.text
