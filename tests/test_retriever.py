"""Tests for oasis.query.retriever."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from oasis.index.keyword import Result as KwResult
from oasis.index.vector import VectorIndex, VectorResult
from oasis.query.parser import DateRange, ParsedQuery
from oasis.query.retriever import (
    CANDIDATE_LIMIT,
    DEFAULT_TOP_N,
    RRF_K,
    HybridResult,
    _build_fts_query,
    _build_kw_filters,
    _build_vec_where,
    _rrf,
    hybrid_search,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DIM = 4


def _pq(
    q: str,
    *,
    keywords: list[str] | None = None,
    file_types: list[str] | None = None,
    date_range: DateRange | None = None,
    folders: list[str] | None = None,
) -> ParsedQuery:
    return ParsedQuery(
        semantic_query=q,
        keywords=keywords or [],
        file_types=file_types or [],
        date_range=date_range,
        folders=folders or [],
    )


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
    scores = _rrf([["shared", "only_kw"], ["shared", "only_vec"]])
    assert scores["shared"] > scores["only_kw"]
    assert scores["shared"] > scores["only_vec"]


def test_rrf_top_rank_in_both_beats_middle_in_both() -> None:
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
# _build_fts_query
# ---------------------------------------------------------------------------


def test_build_fts_query_no_keywords() -> None:
    assert _build_fts_query(_pq("machine learning")) == "machine learning"


def test_build_fts_query_single_keyword_appended() -> None:
    result = _build_fts_query(_pq("tax documents", keywords=["tax"]))
    assert result.startswith("tax documents")
    assert "tax" in result


def test_build_fts_query_multi_word_keyword_quoted() -> None:
    result = _build_fts_query(_pq("documents", keywords=["data protection"]))
    assert '"data protection"' in result


def test_build_fts_query_single_word_keyword_unquoted() -> None:
    result = _build_fts_query(_pq("compliance", keywords=["GDPR"]))
    assert "GDPR" in result
    assert '"GDPR"' not in result


def test_build_fts_query_multiple_keywords() -> None:
    result = _build_fts_query(_pq("report", keywords=["Q3", "budget"]))
    assert "Q3" in result
    assert "budget" in result


# ---------------------------------------------------------------------------
# _build_vec_where
# ---------------------------------------------------------------------------


def test_build_vec_where_none_when_no_filters() -> None:
    assert _build_vec_where(_pq("query")) is None


def test_build_vec_where_file_types() -> None:
    result = _build_vec_where(_pq("q", file_types=[".pdf"]))
    assert result is not None
    assert ".pdf" in result
    assert "extension" in result


def test_build_vec_where_date_after() -> None:
    dr = DateRange(after=datetime(2024, 1, 1))
    result = _build_vec_where(_pq("q", date_range=dr))
    assert result is not None
    assert "mtime >=" in result


def test_build_vec_where_date_before() -> None:
    dr = DateRange(before=datetime(2025, 1, 1))
    result = _build_vec_where(_pq("q", date_range=dr))
    assert result is not None
    assert "mtime <" in result


def test_build_vec_where_date_range_both() -> None:
    dr = DateRange(after=datetime(2024, 1, 1), before=datetime(2025, 1, 1))
    result = _build_vec_where(_pq("q", date_range=dr))
    assert result is not None
    assert "mtime >=" in result
    assert "mtime <" in result


def test_build_vec_where_folder() -> None:
    result = _build_vec_where(_pq("q", folders=["/home/user/docs"]))
    assert result is not None
    assert "path LIKE" in result
    assert "/home/user/docs" in result


def test_build_vec_where_combines_with_and() -> None:
    dr = DateRange(after=datetime(2024, 1, 1))
    result = _build_vec_where(_pq("q", file_types=[".pdf"], date_range=dr))
    assert result is not None
    assert " AND " in result


# ---------------------------------------------------------------------------
# _build_kw_filters
# ---------------------------------------------------------------------------


def test_build_kw_filters_empty_for_plain_query() -> None:
    assert _build_kw_filters(_pq("query")) == {}


def test_build_kw_filters_after_key() -> None:
    dr = DateRange(after=datetime(2024, 1, 1))
    filters = _build_kw_filters(_pq("q", date_range=dr))
    assert "after" in filters
    assert isinstance(filters["after"], float)


def test_build_kw_filters_before_key() -> None:
    dr = DateRange(before=datetime(2025, 1, 1))
    filters = _build_kw_filters(_pq("q", date_range=dr))
    assert "before" in filters


def test_build_kw_filters_extensions() -> None:
    filters = _build_kw_filters(_pq("q", file_types=[".pdf"]))
    assert filters.get("extensions") == [".pdf"]


def test_build_kw_filters_folders_expanded() -> None:
    filters = _build_kw_filters(_pq("q", folders=["/abs/path"]))
    assert "/abs/path" in filters.get("folders", [])


# ---------------------------------------------------------------------------
# hybrid_search — return type and shape
# ---------------------------------------------------------------------------


def test_hybrid_search_returns_list() -> None:
    conn = _fake_conn([_kw("/a.txt", doc_id=1)])
    result = hybrid_search(conn, _fake_vec_index([]), _fake_embedder(), _pq("query"))
    assert isinstance(result, list)


def test_hybrid_search_returns_hybrid_results() -> None:
    conn = _fake_conn([_kw("/a.txt", doc_id=1)])
    results = hybrid_search(conn, _fake_vec_index([]), _fake_embedder(), _pq("query"))
    assert all(isinstance(r, HybridResult) for r in results)


def test_hybrid_search_empty_indexes_returns_empty() -> None:
    conn = _fake_conn([])
    results = hybrid_search(conn, _fake_vec_index([]), _fake_embedder(), _pq("query"))
    assert results == []


def test_hybrid_search_respects_top_n() -> None:
    kw = [_kw(f"/{i}.txt", doc_id=i) for i in range(20)]
    conn = _fake_conn(kw)
    results = hybrid_search(conn, _fake_vec_index([]), _fake_embedder(), _pq("q"), top_n=5)
    assert len(results) <= 5


def test_hybrid_search_default_top_n() -> None:
    kw = [_kw(f"/{i}.txt", doc_id=i, rank=-float(i)) for i in range(30)]
    conn = _fake_conn(kw)
    results = hybrid_search(conn, _fake_vec_index([]), _fake_embedder(), _pq("q"))
    assert len(results) <= DEFAULT_TOP_N


# ---------------------------------------------------------------------------
# hybrid_search — result fields
# ---------------------------------------------------------------------------


def test_result_path_is_path_object() -> None:
    conn = _fake_conn([_kw("/a.txt", doc_id=1)])
    results = hybrid_search(conn, _fake_vec_index([]), _fake_embedder(), _pq("q"))
    assert isinstance(results[0].path, Path)


def test_result_doc_id_from_keyword() -> None:
    conn = _fake_conn([_kw("/a.txt", doc_id=7)])
    results = hybrid_search(conn, _fake_vec_index([]), _fake_embedder(), _pq("q"))
    assert results[0].doc_id == 7


def test_result_doc_id_from_vector_when_kw_absent() -> None:
    conn = _fake_conn([])
    vec = _fake_vec_index([_vec("/b.txt", doc_id=42)])
    results = hybrid_search(conn, vec, _fake_embedder(), _pq("q"))
    assert results[0].doc_id == 42


def test_result_title_from_keyword() -> None:
    conn = _fake_conn([_kw("/a.txt", doc_id=1, title="My Doc")])
    results = hybrid_search(conn, _fake_vec_index([]), _fake_embedder(), _pq("q"))
    assert results[0].title == "My Doc"


def test_result_title_none_when_only_in_vector() -> None:
    conn = _fake_conn([])
    vec = _fake_vec_index([_vec("/b.txt", doc_id=1)])
    results = hybrid_search(conn, vec, _fake_embedder(), _pq("q"))
    assert results[0].title is None


def test_result_snippet_from_keyword() -> None:
    conn = _fake_conn([_kw("/a.txt", doc_id=1, snippet="the \x02fox\x03 jumped")])
    results = hybrid_search(conn, _fake_vec_index([]), _fake_embedder(), _pq("q"))
    assert results[0].snippet == "the \x02fox\x03 jumped"


def test_result_snippet_from_chunk_text_when_only_vector() -> None:
    conn = _fake_conn([])
    vec = _fake_vec_index([_vec("/b.txt", doc_id=1, text="chunk content here")])
    results = hybrid_search(conn, vec, _fake_embedder(), _pq("q"))
    assert results[0].snippet == "chunk content here"


def test_result_score_is_float() -> None:
    conn = _fake_conn([_kw("/a.txt", doc_id=1)])
    results = hybrid_search(conn, _fake_vec_index([]), _fake_embedder(), _pq("q"))
    assert isinstance(results[0].score, float)


# ---------------------------------------------------------------------------
# hybrid_search — ranking and fusion
# ---------------------------------------------------------------------------


def test_results_sorted_by_score_descending() -> None:
    kw = [_kw("/a.txt", doc_id=1), _kw("/b.txt", doc_id=2)]
    conn = _fake_conn(kw)
    results = hybrid_search(conn, _fake_vec_index([]), _fake_embedder(), _pq("q"))
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_doc_in_both_lists_outranks_doc_in_one() -> None:
    kw = [_kw("/shared.txt", doc_id=1), _kw("/kw_only.txt", doc_id=2)]
    vec = [_vec("/shared.txt", doc_id=1, score=0.05)]
    conn = _fake_conn(kw)
    results = hybrid_search(conn, _fake_vec_index(vec), _fake_embedder(), _pq("q"))
    paths = [str(r.path) for r in results]
    assert paths.index("/shared.txt") < paths.index("/kw_only.txt")


def test_kw_only_doc_included_in_results() -> None:
    kw = [_kw("/kw_only.txt", doc_id=1)]
    conn = _fake_conn(kw)
    results = hybrid_search(conn, _fake_vec_index([]), _fake_embedder(), _pq("q"))
    assert any(r.path == Path("/kw_only.txt") for r in results)


def test_vec_only_doc_included_in_results() -> None:
    conn = _fake_conn([])
    vec = _fake_vec_index([_vec("/vec_only.txt", doc_id=1)])
    results = hybrid_search(conn, vec, _fake_embedder(), _pq("q"))
    assert any(r.path == Path("/vec_only.txt") for r in results)


def test_no_duplicate_paths_in_results() -> None:
    kw = [_kw("/a.txt", doc_id=1)]
    vec = [_vec("/a.txt", doc_id=1, chunk_id="a:0", score=0.1),
           _vec("/a.txt", doc_id=1, chunk_id="a:1", score=0.2)]
    conn = _fake_conn(kw)
    results = hybrid_search(conn, _fake_vec_index(vec), _fake_embedder(), _pq("q"))
    paths = [str(r.path) for r in results]
    assert len(paths) == len(set(paths))


# ---------------------------------------------------------------------------
# chunk deduplication
# ---------------------------------------------------------------------------


def test_chunk_dedup_keeps_lowest_distance() -> None:
    conn = _fake_conn([])
    vec = _fake_vec_index([
        _vec("/a.txt", doc_id=1, chunk_id="a:0", score=0.8),
        _vec("/a.txt", doc_id=1, chunk_id="a:1", score=0.1),
    ])
    results = hybrid_search(conn, vec, _fake_embedder(), _pq("q"))
    assert len(results) == 1
    assert results[0].snippet == "chunk text"


def test_chunk_dedup_with_multiple_docs() -> None:
    conn = _fake_conn([])
    vec = _fake_vec_index([
        _vec("/a.txt", doc_id=1, chunk_id="a:0", score=0.5),
        _vec("/a.txt", doc_id=1, chunk_id="a:1", score=0.2),
        _vec("/b.txt", doc_id=2, chunk_id="b:0", score=0.3),
    ])
    results = hybrid_search(conn, vec, _fake_embedder(), _pq("q"))
    assert len(results) == 2


# ---------------------------------------------------------------------------
# embedder interaction
# ---------------------------------------------------------------------------


def test_embed_called_with_semantic_query() -> None:
    conn = _fake_conn([])
    emb = _fake_embedder()
    hybrid_search(conn, _fake_vec_index([]), emb, _pq("my query"))
    emb.embed.assert_called_once_with(["my query"])


def test_embed_uses_semantic_query_not_keywords() -> None:
    conn = _fake_conn([])
    emb = _fake_embedder()
    hybrid_search(conn, _fake_vec_index([]), emb, _pq("machine learning", keywords=["GDPR"]))
    emb.embed.assert_called_once_with(["machine learning"])


def test_vector_search_called_once() -> None:
    conn = _fake_conn([])
    vec = _fake_vec_index([])
    hybrid_search(conn, vec, _fake_embedder(), _pq("q"))
    vec.search.assert_called_once()


def test_vector_search_called_with_candidate_limit() -> None:
    conn = _fake_conn([])
    vec = _fake_vec_index([])
    hybrid_search(conn, vec, _fake_embedder(), _pq("q"), candidate_limit=25)
    _, kwargs = vec.search.call_args
    assert kwargs.get("limit") == 25


# ---------------------------------------------------------------------------
# file_types filter
# ---------------------------------------------------------------------------


def test_file_types_passes_where_to_vector_search() -> None:
    conn = _fake_conn([])
    vec = _fake_vec_index([])
    hybrid_search(conn, vec, _fake_embedder(), _pq("q", file_types=[".pdf", ".docx"]))
    _, kwargs = vec.search.call_args
    where = kwargs.get("where", "")
    assert ".pdf" in where
    assert ".docx" in where


def test_file_types_where_uses_in_clause() -> None:
    conn = _fake_conn([])
    vec = _fake_vec_index([])
    hybrid_search(conn, vec, _fake_embedder(), _pq("q", file_types=[".xlsx"]))
    _, kwargs = vec.search.call_args
    assert "IN" in (kwargs.get("where") or "")


def test_no_file_types_passes_no_where() -> None:
    conn = _fake_conn([])
    vec = _fake_vec_index([])
    hybrid_search(conn, vec, _fake_embedder(), _pq("q"))
    _, kwargs = vec.search.call_args
    assert kwargs.get("where") is None


def test_file_types_allows_vec_match_through() -> None:
    conn = _fake_conn([])
    vec = _fake_vec_index([_vec("/report.pdf", doc_id=1)])
    results = hybrid_search(conn, vec, _fake_embedder(), _pq("q", file_types=[".pdf"]))
    assert len(results) == 1
    assert results[0].path == Path("/report.pdf")


# ---------------------------------------------------------------------------
# date_range filter
# ---------------------------------------------------------------------------


def test_date_range_after_in_vec_where() -> None:
    conn = _fake_conn([])
    vec = _fake_vec_index([])
    dr = DateRange(after=datetime(2024, 1, 1))
    hybrid_search(conn, vec, _fake_embedder(), _pq("q", date_range=dr))
    _, kwargs = vec.search.call_args
    assert "mtime >=" in (kwargs.get("where") or "")


def test_date_range_before_in_vec_where() -> None:
    conn = _fake_conn([])
    vec = _fake_vec_index([])
    dr = DateRange(before=datetime(2025, 1, 1))
    hybrid_search(conn, vec, _fake_embedder(), _pq("q", date_range=dr))
    _, kwargs = vec.search.call_args
    assert "mtime <" in (kwargs.get("where") or "")


# ---------------------------------------------------------------------------
# keywords
# ---------------------------------------------------------------------------


def test_keywords_in_fts_query_param() -> None:
    conn = _fake_conn([])
    hybrid_search(conn, _fake_vec_index([]), _fake_embedder(),
                  _pq("tax documents", keywords=["GDPR"]))
    sql_call = conn.execute.call_args_list[0]
    params = sql_call[0][1]
    fts_query_param = params[0]
    assert "tax documents" in fts_query_param
    assert "GDPR" in fts_query_param


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------


def test_default_top_n_is_ten() -> None:
    assert DEFAULT_TOP_N == 10


def test_candidate_limit_is_fifty() -> None:
    assert CANDIDATE_LIMIT == 50


# ---------------------------------------------------------------------------
# independent arm failure
#
# Regression: FTS and vector once shared a single try block, so an FTS5 syntax
# error (an apostrophe is enough) discarded the semantic arm too and the whole
# call returned nothing.  The arms must fail independently.
# ---------------------------------------------------------------------------


def _failing_conn(exc: Exception) -> MagicMock:
    conn = MagicMock(spec=sqlite3.Connection)
    conn.execute.side_effect = exc
    return conn


def test_fts_failure_degrades_to_semantic_only() -> None:
    conn = _failing_conn(sqlite3.OperationalError("fts5: syntax error near \"'\""))
    vec_idx = _fake_vec_index([_vec("/a.txt", doc_id=1), _vec("/b.txt", doc_id=2)])

    results = hybrid_search(conn, vec_idx, _fake_embedder(), _pq("amazon's revenue"))

    assert [str(r.path) for r in results] == ["/a.txt", "/b.txt"]


def test_fts_failure_preserves_vector_order() -> None:
    # Single-list RRF is well-defined: it just preserves that list's order.
    conn = _failing_conn(sqlite3.OperationalError("fts5: syntax error"))
    vec_idx = _fake_vec_index([
        _vec("/first.txt", doc_id=1, score=0.1),
        _vec("/second.txt", doc_id=2, score=0.2),
        _vec("/third.txt", doc_id=3, score=0.3),
    ])

    results = hybrid_search(conn, vec_idx, _fake_embedder(), _pq("q"))

    assert [str(r.path) for r in results] == ["/first.txt", "/second.txt", "/third.txt"]
    assert results[0].score > results[-1].score


def test_fts_failure_still_populates_result_fields() -> None:
    conn = _failing_conn(sqlite3.OperationalError("fts5: syntax error"))
    vec_idx = _fake_vec_index([_vec("/a.txt", doc_id=7, text="chunk body")])

    (result,) = hybrid_search(conn, vec_idx, _fake_embedder(), _pq("q"))

    assert result.doc_id == 7
    assert result.snippet == "chunk body"
    assert result.title is None


def test_vector_failure_degrades_to_keyword_only() -> None:
    conn = _fake_conn([_kw("/a.txt", doc_id=1), _kw("/b.txt", doc_id=2)])
    vec_idx = MagicMock(spec=VectorIndex)
    vec_idx.search.side_effect = RuntimeError("lance table gone")

    results = hybrid_search(conn, vec_idx, _fake_embedder(), _pq("q"))

    assert [str(r.path) for r in results] == ["/a.txt", "/b.txt"]


def test_embedder_failure_degrades_to_keyword_only() -> None:
    conn = _fake_conn([_kw("/a.txt", doc_id=1)])
    embedder = MagicMock()
    embedder.embed.side_effect = RuntimeError("model unloaded")

    results = hybrid_search(conn, _fake_vec_index([]), embedder, _pq("q"))

    assert [str(r.path) for r in results] == ["/a.txt"]


def test_both_arms_failing_raises_the_fts_error() -> None:
    # Nothing survived, so the caller must hear about it — and the FTS5 error
    # is the one carrying an actionable message.
    conn = _failing_conn(sqlite3.OperationalError("fts5: syntax error"))
    vec_idx = MagicMock(spec=VectorIndex)
    vec_idx.search.side_effect = RuntimeError("lance table gone")

    with pytest.raises(sqlite3.OperationalError, match="fts5"):
        hybrid_search(conn, vec_idx, _fake_embedder(), _pq("q"))


def test_fts_failure_does_not_suppress_empty_vector_results() -> None:
    # Both arms "succeed at returning nothing" is not an error.
    conn = _failing_conn(sqlite3.OperationalError("fts5: syntax error"))

    assert hybrid_search(conn, _fake_vec_index([]), _fake_embedder(), _pq("q")) == []
