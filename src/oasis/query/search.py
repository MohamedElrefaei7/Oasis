"""Mode dispatch shared by the HTTP API (and, in a later commit, the CLI).

Mirrors cli/app.py's search command exactly; the CLI still carries its own
copy and migrates here separately — brief duplication, one-engine direction.
"""
from __future__ import annotations

import sqlite3
from enum import StrEnum
from pathlib import Path

from oasis.index.embeddings import EmbeddingModel
from oasis.index.keyword import KeywordIndex
from oasis.index.vector import VectorIndex, VectorResult
from oasis.query.parser import ParsedQuery
from oasis.query.reranker import CrossEncoderReranker
from oasis.query.retriever import (
    HybridResult,
    _build_fts_query,
    _build_kw_filters,
    _build_vec_where,
    hybrid_search,
)
from oasis.query.snippets import text_snippet


class SearchMode(StrEnum):
    # Duplicates cli/app.py's enum until the CLI migrates to this module.
    keyword = "keyword"
    semantic = "semantic"
    hybrid = "hybrid"


def run_search(
    conn: sqlite3.Connection,
    vector_index: VectorIndex,
    embedder: EmbeddingModel,
    reranker: CrossEncoderReranker | None,
    query: str,
    parsed: ParsedQuery,
    *,
    mode: SearchMode = SearchMode.hybrid,
    limit: int = 10,
) -> list[HybridResult]:
    """Run one search in the given mode; every mode returns HybridResults.

    Raises sqlite3.OperationalError on bad FTS5 syntax in keyword mode, and
    from hybrid mode only when *both* arms failed (hybrid otherwise degrades
    to the surviving arm). Semantic mode never parses the query as FTS5.
    """
    if mode is SearchMode.keyword:
        kw_results = KeywordIndex(conn).search(
            _build_fts_query(parsed), limit=limit, **_build_kw_filters(parsed)
        )
        return [
            HybridResult(
                path=r.path,
                doc_id=r.doc_id,
                title=r.title,
                snippet=r.snippet,
                # FTS5 rank is negative, more negative = better; negate so
                # score follows HybridResult's higher-is-better convention.
                score=-r.rank,
            )
            for r in kw_results
        ]

    if mode is SearchMode.semantic:
        query_vec = embedder.embed([parsed.semantic_query])[0]
        # Over-fetch to leave room for per-doc chunk dedup.
        raw_results = vector_index.search(
            query_vec, limit=limit * 3, where=_build_vec_where(parsed)
        )
        best: dict[str, VectorResult] = {}
        for r in raw_results:
            if r.path not in best or r.score < best[r.path].score:
                best[r.path] = r
        deduped = sorted(best.values(), key=lambda r: r.score)[:limit]
        return [
            HybridResult(
                path=Path(r.path),
                doc_id=r.doc_id,
                title=None,
                snippet=text_snippet(r.text, parsed.semantic_query),
                # _distance is cosine distance (lower = better); 1 − distance
                # is cosine similarity, restoring higher-is-better.
                score=1.0 - r.score,
            )
            for r in deduped
        ]

    # hybrid: over-fetch candidates so the reranker has a pool to work with.
    candidates = hybrid_search(conn, vector_index, embedder, parsed, top_n=max(limit * 2, 20))
    if reranker is None:
        return candidates[:limit]
    # Rerank against the user's actual words, not the distilled semantic_query
    # — the eval showed distillation corrupts meaning (CONTEXT.md § Evaluation).
    return reranker.rerank(query, candidates, top_n=limit)
