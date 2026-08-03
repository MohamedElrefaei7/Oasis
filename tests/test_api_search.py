"""GET /api/search — mode dispatch, raw-by-default parsing, error contract.

Real SQLite + LanceDB over a 3-doc corpus; embedder/reranker/LLM are faked so
PyTorch never loads and every vector is deterministic.
"""
from __future__ import annotations

import zlib
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from oasis.api.app import create_app
from oasis.config import OasisConfig
from oasis.index.db import open_db
from oasis.index.keyword import MATCH_END, MATCH_START, KeywordIndex
from oasis.index.vector import ChunkRow, VectorIndex
from oasis.models import DocumentMetadata, ExtractedDocument
from oasis.query.parser import DateRange, ParsedQuery

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
        # Deterministic per text (crc32-seeded), so index-time and query-time
        # vectors for identical text agree across processes and calls.
        return np.stack([
            np.random.default_rng(zlib.crc32(t.encode())).random(DIM, dtype=np.float32)
            for t in texts
        ])


class FakeReranker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int | None]] = []

    def rerank(self, query, results, *, top_n=None):
        self.calls.append((query, len(results), top_n))
        return results[:top_n] if top_n is not None else results


class FakeLLM:
    """LLMProvider stand-in: returns a canned ParsedQuery or raises."""

    def __init__(self, result: ParsedQuery | None = None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls = 0

    def complete(self, prompt, response_model, *, system=None):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


def _build_corpus(tmp_path: Path, db_path: Path) -> None:
    conn = open_db(db_path)
    kw = KeywordIndex(conn)
    emb = FakeEmbedder()
    vec = VectorIndex(db_path.with_name(db_path.stem + ".lance"), dimension=DIM)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    rows: list[ChunkRow] = []
    for name, title, text in DOCS:
        p = corpus / name
        p.write_text(text)
        kw.upsert(
            ExtractedDocument(
                path=p,
                text=text,
                metadata=DocumentMetadata(size_bytes=len(text), mtime=1000.0, title=title),
            )
        )
        doc_id = kw.get_doc_id(p)
        assert doc_id is not None
        rows.append(
            ChunkRow(
                chunk_id=f"{p}:0",
                doc_id=doc_id,
                text=text,
                vector=emb.embed([text])[0],
                extension=p.suffix,
                mtime=1000.0,
                path=str(p),
            )
        )
    conn.commit()
    conn.close()
    vec.upsert_chunks(rows)


@pytest.fixture
def client(monkeypatch, tmp_path):
    db_path = tmp_path / "index.db"
    _build_corpus(tmp_path, db_path)
    monkeypatch.setattr("oasis.api.app.load_config", lambda: OasisConfig(db_path=db_path))
    monkeypatch.setattr("oasis.api.app.SentenceTransformerEmbedder", FakeEmbedder)
    monkeypatch.setattr("oasis.api.app.CrossEncoderReranker", FakeReranker)
    monkeypatch.setattr("oasis.api.state.ensure_ollama", lambda *a, **k: None)
    app = create_app(token=TOKEN)
    with TestClient(app, raise_server_exceptions=False) as c:
        assert app.state.oasis.ready.wait(timeout=10)
        c.app_state = app.state.oasis
        yield c


def _get(client, **params):
    return client.get("/api/search", params={"q": "revenue", **params}, headers=AUTH)


def _assert_well_formed(body: dict, mode: str) -> None:
    assert set(body) == {"results", "mode", "parsed", "llm_parsed", "latency_ms", "db_path"}
    assert body["mode"] == mode
    assert isinstance(body["llm_parsed"], bool)
    assert isinstance(body["latency_ms"], float) and body["latency_ms"] >= 0
    assert isinstance(body["db_path"], str)
    assert set(body["parsed"]) == {
        "semantic_query", "file_types", "date_range", "folders", "keywords", "confidence",
    }
    for r in body["results"]:
        assert set(r) == {"path", "title", "doc_id", "score", "snippet"}
        assert isinstance(r["doc_id"], int)
        assert isinstance(r["score"], float)
        for seg in r["snippet"]:
            assert set(seg) == {"text", "match"}


# ---------------------------------------------------------------------------
# Modes return well-formed responses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["keyword", "semantic", "hybrid"])
def test_each_mode_returns_well_formed_response(client, mode):
    resp = _get(client, mode=mode)
    assert resp.status_code == 200
    body = resp.json()
    _assert_well_formed(body, mode)
    assert body["results"], f"{mode} found nothing for a term present in the corpus"


def test_zero_matches_is_200_empty_results(client):
    resp = _get(client, q="qqqqzzzz", mode="keyword")
    assert resp.status_code == 200
    assert resp.json()["results"] == []


def test_limit_honored_in_hybrid(client):
    resp = _get(client, q="revenue reef learning", mode="hybrid", limit=2)
    assert resp.status_code == 200
    assert len(resp.json()["results"]) <= 2
    # Over-fetch then rerank-to-limit: the reranker saw the candidate pool and
    # was asked for exactly `limit`.
    query, pool, top_n = client.app_state.reranker.calls[-1]
    assert top_n == 2
    assert pool <= max(2 * 2, 20)


# ---------------------------------------------------------------------------
# raw defaults True — parsing is opt-in
# ---------------------------------------------------------------------------


def test_raw_default_skips_llm(client, monkeypatch):
    llm = FakeLLM(ParsedQuery(semantic_query="WRONG — parser must not run"))
    client.app_state.set_llm(llm)
    parse_calls = []
    monkeypatch.setattr(
        "oasis.api.search.parse_query",
        lambda *a, **k: parse_calls.append(a) or ParsedQuery(semantic_query="x"),
    )

    resp = _get(client, q="machine learning stuff")  # no raw param at all
    assert resp.status_code == 200
    body = resp.json()
    assert body["llm_parsed"] is False
    assert body["parsed"]["semantic_query"] == "machine learning stuff"
    assert parse_calls == []
    assert llm.calls == 0


def test_raw_false_uses_cached_llm(client):
    canned = ParsedQuery(
        semantic_query="tax documents",
        file_types=[".pdf"],
        keywords=["tax"],
        confidence=0.9,
    )
    client.app_state.set_llm(FakeLLM(canned))

    resp = _get(client, q="that tax PDF from 2024", raw="false")
    assert resp.status_code == 200
    body = resp.json()
    assert body["llm_parsed"] is True
    assert body["parsed"]["semantic_query"] == "tax documents"
    assert body["parsed"]["file_types"] == [".pdf"]
    assert body["parsed"]["keywords"] == ["tax"]


def test_raw_false_llm_error_falls_back(client):
    client.app_state.set_llm(FakeLLM(error=RuntimeError("inference 500")))
    resp = _get(client, q="machine learning", raw="false")
    assert resp.status_code == 200
    body = resp.json()
    assert body["llm_parsed"] is False
    assert body["parsed"]["semantic_query"] == "machine learning"
    assert body["parsed"]["file_types"] == []


def test_raw_false_no_llm_falls_back(client, monkeypatch):
    # ensure_ollama is patched, not merely absent: get_llm() probes lazily, so
    # an unpatched run would shell out here and could start a real server.
    monkeypatch.setattr("oasis.api.state.ensure_ollama", lambda *a, **k: None)
    resp = _get(client, q="machine learning", raw="false")
    assert resp.status_code == 200
    assert resp.json()["llm_parsed"] is False


def test_llm_is_not_probed_at_startup(client, monkeypatch):
    """The probe must not run until someone actually asks for parsing.

    ``ensure_ollama()`` *starts* ``ollama serve`` when the binary is on PATH,
    so probing at startup spun up an LLM server on every launch of an app that
    hardcodes ``raw=true`` and can never use it.
    """
    probes = []
    monkeypatch.setattr(
        "oasis.api.state.ensure_ollama", lambda *a, **k: probes.append(1) or None
    )
    assert client.app_state.llm_probed is False

    _get(client, q="machine learning")  # raw defaults True
    assert probes == []

    _get(client, q="machine learning", raw="false")  # now it is asked for
    assert probes == [1]

    _get(client, q="something else", raw="false")  # cached, including the None
    assert probes == [1]


def test_parsed_datetime_serialized_with_utc_offset(client):
    client.app_state.set_llm(
        FakeLLM(
            ParsedQuery(
                semantic_query="tax",
                date_range=DateRange(after=datetime(2024, 1, 1), before=datetime(2025, 1, 1)),
            )
        )
    )
    resp = _get(client, q="tax from 2024", raw="false")
    assert resp.status_code == 200
    dr = resp.json()["parsed"]["date_range"]
    assert dr is not None
    for value in (dr["after"], dr["before"]):
        assert value.endswith("+00:00") or value.endswith("Z"), f"naive datetime on the wire: {value}"


# ---------------------------------------------------------------------------
# Error contract: keyword 400s, hybrid degrades, semantic is immune
# ---------------------------------------------------------------------------

BAD_FTS = "rock 'n roll, can't touch"


def test_keyword_bad_fts5_is_400_with_tip(client):
    resp = _get(client, q=BAD_FTS, mode="keyword")
    assert resp.status_code == 400
    body = resp.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert "double quotes" in body["error"]["message"]


def test_hybrid_bad_fts5_degrades_to_200(client):
    resp = _get(client, q=BAD_FTS, mode="hybrid")
    assert resp.status_code == 200
    assert resp.json()["results"], "semantic arm should still return results"


def test_semantic_bad_fts5_is_200(client):
    resp = _get(client, q=BAD_FTS, mode="semantic")
    assert resp.status_code == 200
    assert resp.json()["results"]


# ---------------------------------------------------------------------------
# Snippets on the wire
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["keyword", "semantic", "hybrid"])
def test_snippet_segments_are_canonical(client, mode):
    resp = _get(client, q="revenue enterprise", mode=mode)
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert results
    for r in results:
        segs = r["snippet"]
        # No sentinel control characters leak onto the wire.
        assert all(MATCH_START not in s["text"] and MATCH_END not in s["text"] for s in segs)
        # No empty segments; no two adjacent segments share a match value.
        assert all(s["text"] for s in segs)
        assert all(a["match"] != b["match"] for a, b in zip(segs, segs[1:], strict=False))
    # At least one result actually highlights the query term.
    assert any(s["match"] for r in results for s in r["snippet"])


# ---------------------------------------------------------------------------
# Validation and auth
# ---------------------------------------------------------------------------


def test_invalid_mode_is_422_envelope(client):
    resp = _get(client, mode="bogus")
    assert resp.status_code == 422
    body = resp.json()
    assert "detail" not in body
    assert set(body) == {"error"}
    assert body["error"]["code"] == "validation_error"


def test_no_token_is_401_envelope(client):
    resp = client.get("/api/search", params={"q": "revenue"})
    assert resp.status_code == 401
    assert set(resp.json()) == {"error"}
