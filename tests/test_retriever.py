"""Tests for oasis.query.retriever."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from oasis.index.keyword import Result as KwResult
from oasis.index.vector import VectorIndex, VectorResult
from oasis.query.retriever import (
    CANDIDATE_LIMIT,
    DEFAULT_TOP_N,
    RRF_K,
    HybridResult,
    _rrf,
    hybrid_search,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DIM = 4


def _kw(path: str, doc_id: int = 1, title: str | None = None,
        snippet: str = "snip", rank: float = -1.0) -> KwResult:
    return KwResult(path=Path(path), doc_id=doc_id, title=title, snippet=snippet, rank=rank)


def _vec(path: str, doc_id: int = 1, chunk_id: str = "c0",
         text: str = "chunk text", score: float = 0.1) -> VectorResult:
    return VectorResult(chunk_id=chunk_id, doc_id=doc_id, text=text, path=path, score=score)


def _fake_embedder(dim: int = DIM) -> MagicMock:
    m = MagicMock()
    m.embed.side_effect = lambda texts: np.zeros((len(texts), dim), dtype=np.float32)
    return m


def _fake_vec_index(results: list[VectorResult]) -> MagicMock:
    m = MagicMock(spec=VectorIndex)
    m.search.return_value = results
    return m


def _fake_conn(kw_results: list[KwResult]) -> MagicMock:
    """Return a mock sqlite3.Connection that makes KeywordIndex.search return kw_results."""
    conn = MagicMock(spec=sqlite3.Connection)
    rows = [
        {
            "id": r.doc_id,
            "path": str(r.path),
            "title": r.title,
            "snippet": r.snippet,
            "rank": r.rank,
        }
        for r in kw_results
    ]
    conn.execute.return_value.fetchall.return_value = rows
    return conn


# ---------------------------------------------------------------------------
# _rrf — unit tests
# ---------------------------------------------------------------------------


def test_rrf_single_list_score_is_reciprocal_rank() -> None:
    scores = _rrf([["a", "b", "c"]])
    assert scores["a"] == pytest.approx(1.0 / (RRF_K + 1))
    assert scores["b"] == pytest.approx(1.0 / (RRF_K + 2))
    assert scores["c"] == pytest.approx(1.0 / (RRF_K + 3))


def test_rrf_single_list_rank_order_preserved() -> None:
    scores = _rrf([["a", "b", "c"]])
    assert scores["a"] > scores["b"] > scores["c"]


def test_rrf_doc_in_both_lists_scores_higher() -> None:
    # "shared" appears in both; "only_kw" and "only_vec" appear in one each.
    scores = _rrf([["shared", "only_kw"], ["shared", "only_vec"]])
    assert scores["shared"] > scores["only_kw"]
    assert scores["shared"] > scores["only_vec"]


def test_rrf_top_rank_in_both_beats_middle_in_both() -> None:
    # rank-1 in both lists beats rank-2 in both lists.
    scores = _rrf([["best", "second"], ["best", "second"]])
    assert scores["best"] > scores["second"]


def test_rrf_empty_lists_returns_empty() -> None:
    assert _rrf([[], []]) == {}


def test_rrf_empty_returns_empty() -> None:
    assert _rrf([]) == {}


def test_rrf_each_key_appears_exactly_once_per_list_contribution() -> None:
    scores = _rrf([["a"], ["a"]])
    expected = 2.0 / (RRF_K + 1)
    assert scores["a"] == pytest.approx(expected)


def test_rrf_k_constant_is_60() -> None:
    assert RRF_K == 60


# ---------------------------------------------------------------------------
# hybrid_search — return type and shape
# ---------------------------------------------------------------------------


def test_hybrid_search_returns_list() -> None:
    conn = _fake_conn([_kw("/a.txt", doc_id=1)])
    result = hybrid_search(conn, _fake_vec_index([]), _fake_embedder(), "query")
    assert isinstance(result, list)


def test_hybrid_search_returns_hybrid_results() -> None:
    conn = _fake_conn([_kw("/a.txt", doc_id=1)])
    results = hybrid_search(conn, _fake_vec_index([]), _fake_embedder(), "query")
    assert all(isinstance(r, HybridResult) for r in results)


def test_hybrid_search_empty_indexes_returns_empty() -> None:
    conn = _fake_conn([])
    results = hybrid_search(conn, _fake_vec_index([]), _fake_embedder(), "query")
    assert results == []


def test_hybrid_search_respects_top_n() -> None:
    kw = [_kw(f"/{i}.txt", doc_id=i) for i in range(20)]
    conn = _fake_conn(kw)
    results = hybrid_search(conn, _fake_vec_index([]), _fake_embedder(), "q", top_n=5)
    assert len(results) <= 5


def test_hybrid_search_default_top_n() -> None:
    kw = [_kw(f"/{i}.txt", doc_id=i, rank=-float(i)) for i in range(30)]
    conn = _fake_conn(kw)
    results = hybrid_search(conn, _fake_vec_index([]), _fake_embedder(), "q")
    assert len(results) <= DEFAULT_TOP_N


# ---------------------------------------------------------------------------
# hybrid_search — result fields
# ---------------------------------------------------------------------------


def test_result_path_is_path_object() -> None:
    conn = _fake_conn([_kw("/a.txt", doc_id=1)])
    results = hybrid_search(conn, _fake_vec_index([]), _fake_embedder(), "q")
    assert isinstance(results[0].path, Path)


def test_result_doc_id_from_keyword() -> None:
    conn = _fake_conn([_kw("/a.txt", doc_id=7)])
    results = hybrid_search(conn, _fake_vec_index([]), _fake_embedder(), "q")
    assert results[0].doc_id == 7


def test_result_doc_id_from_vector_when_kw_absent() -> None:
    conn = _fake_conn([])
    vec = _fake_vec_index([_vec("/b.txt", doc_id=42)])
    results = hybrid_search(conn, vec, _fake_embedder(), "q")
    assert results[0].doc_id == 42


def test_result_title_from_keyword() -> None:
    conn = _fake_conn([_kw("/a.txt", doc_id=1, title="My Doc")])
    results = hybrid_search(conn, _fake_vec_index([]), _fake_embedder(), "q")
    assert results[0].title == "My Doc"


def test_result_title_none_when_only_in_vector() -> None:
    conn = _fake_conn([])
    vec = _fake_vec_index([_vec("/b.txt", doc_id=1)])
    results = hybrid_search(conn, vec, _fake_embedder(), "q")
    assert results[0].title is None


def test_result_snippet_from_keyword() -> None:
    conn = _fake_conn([_kw("/a.txt", doc_id=1, snippet="the \x02fox\x03 jumped")])
    results = hybrid_search(conn, _fake_vec_index([]), _fake_embedder(), "q")
    assert results[0].snippet == "the \x02fox\x03 jumped"


def test_result_snippet_from_chunk_text_when_only_vector() -> None:
    conn = _fake_conn([])
    vec = _fake_vec_index([_vec("/b.txt", doc_id=1, text="chunk content here")])
    results = hybrid_search(conn, vec, _fake_embedder(), "q")
    assert results[0].snippet == "chunk content here"


def test_result_score_is_float() -> None:
    conn = _fake_conn([_kw("/a.txt", doc_id=1)])
    results = hybrid_search(conn, _fake_vec_index([]), _fake_embedder(), "q")
    assert isinstance(results[0].score, float)


# ---------------------------------------------------------------------------
# hybrid_search — ranking and fusion
# ---------------------------------------------------------------------------


def test_results_sorted_by_score_descending() -> None:
    kw = [_kw("/a.txt", doc_id=1), _kw("/b.txt", doc_id=2)]
    conn = _fake_conn(kw)
    results = hybrid_search(conn, _fake_vec_index([]), _fake_embedder(), "q")
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_doc_in_both_lists_outranks_doc_in_one() -> None:
    # "/shared.txt" appears in both kw and vec; "/kw_only.txt" only in kw.
    kw = [_kw("/shared.txt", doc_id=1), _kw("/kw_only.txt", doc_id=2)]
    vec = [_vec("/shared.txt", doc_id=1, score=0.05)]
    conn = _fake_conn(kw)
    results = hybrid_search(conn, _fake_vec_index(vec), _fake_embedder(), "q")
    paths = [str(r.path) for r in results]
    assert paths.index("/shared.txt") < paths.index("/kw_only.txt")


def test_kw_only_doc_included_in_results() -> None:
    kw = [_kw("/kw_only.txt", doc_id=1)]
    conn = _fake_conn(kw)
    results = hybrid_search(conn, _fake_vec_index([]), _fake_embedder(), "q")
    assert any(r.path == Path("/kw_only.txt") for r in results)


def test_vec_only_doc_included_in_results() -> None:
    conn = _fake_conn([])
    vec = _fake_vec_index([_vec("/vec_only.txt", doc_id=1)])
    results = hybrid_search(conn, vec, _fake_embedder(), "q")
    assert any(r.path == Path("/vec_only.txt") for r in results)


def test_no_duplicate_paths_in_results() -> None:
    kw = [_kw("/a.txt", doc_id=1)]
    vec = [_vec("/a.txt", doc_id=1, chunk_id="a:0", score=0.1),
           _vec("/a.txt", doc_id=1, chunk_id="a:1", score=0.2)]
    conn = _fake_conn(kw)
    results = hybrid_search(conn, _fake_vec_index(vec), _fake_embedder(), "q")
    paths = [str(r.path) for r in results]
    assert len(paths) == len(set(paths))


# ---------------------------------------------------------------------------
# chunk deduplication
# ---------------------------------------------------------------------------


def test_chunk_dedup_keeps_lowest_distance() -> None:
    """Two chunks from the same doc → only the better one (lower score) contributes."""
    conn = _fake_conn([])
    vec = _fake_vec_index([
        _vec("/a.txt", doc_id=1, chunk_id="a:0", score=0.8),  # worse
        _vec("/a.txt", doc_id=1, chunk_id="a:1", score=0.1),  # better
    ])
    results = hybrid_search(conn, vec, _fake_embedder(), "q")
    assert len(results) == 1
    # The snippet is the text of the best chunk (chunk_id "a:1")
    assert results[0].snippet == "chunk text"


def test_chunk_dedup_with_multiple_docs() -> None:
    conn = _fake_conn([])
    vec = _fake_vec_index([
        _vec("/a.txt", doc_id=1, chunk_id="a:0", score=0.5),
        _vec("/a.txt", doc_id=1, chunk_id="a:1", score=0.2),
        _vec("/b.txt", doc_id=2, chunk_id="b:0", score=0.3),
    ])
    results = hybrid_search(conn, vec, _fake_embedder(), "q")
    assert len(results) == 2


# ---------------------------------------------------------------------------
# embedder interaction
# ---------------------------------------------------------------------------


def test_embed_called_with_query_text() -> None:
    conn = _fake_conn([])
    emb = _fake_embedder()
    hybrid_search(conn, _fake_vec_index([]), emb, "my query")
    emb.embed.assert_called_once_with(["my query"])


def test_vector_search_called_once() -> None:
    conn = _fake_conn([])
    vec = _fake_vec_index([])
    hybrid_search(conn, vec, _fake_embedder(), "q")
    vec.search.assert_called_once()


def test_vector_search_called_with_candidate_limit() -> None:
    conn = _fake_conn([])
    vec = _fake_vec_index([])
    hybrid_search(conn, vec, _fake_embedder(), "q", candidate_limit=25)
    _, kwargs = vec.search.call_args
    assert kwargs.get("limit") == 25


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------


def test_default_top_n_is_ten() -> None:
    assert DEFAULT_TOP_N == 10


def test_candidate_limit_is_fifty() -> None:
    assert CANDIDATE_LIMIT == 50
