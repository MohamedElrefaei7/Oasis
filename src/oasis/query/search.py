"""Mode dispatch — the one search engine, shared by the HTTP API and the CLI.

Both front-ends call ``run_search``; neither carries its own copy of the
retrieval logic. That was not true until 2026-07-28, and the duplication had
drifted in the way duplication does: the CLI reranked against the *distilled*
``semantic_query`` while the API reranked against the user's raw words, so the
two front-ends disagreed on the one input the eval explicitly measured
(distillation corrupts meaning — CONTEXT.md § Evaluation). One engine is what
stops that recurring, and it is also one engine's worth of code in the frozen
bundle instead of two.
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
    build_fts_query,
    build_kw_filters,
    build_vec_where,
    hybrid_search,
)
from oasis.query.snippets import text_snippet


class SearchMode(StrEnum):
    keyword = "keyword"
    semantic = "semantic"
    hybrid = "hybrid"


def run_search(
    conn: sqlite3.Connection,
    vector_index: VectorIndex | None,
    embedder: EmbeddingModel | None,
    reranker: CrossEncoderReranker | None,
    query: str,
    parsed: ParsedQuery,
    *,
    mode: SearchMode = SearchMode.hybrid,
    limit: int = 10,
) -> list[HybridResult]:
    """Run one search in the given mode; every mode returns HybridResults.

    ``vector_index``/``embedder`` may be ``None`` **only in keyword mode**,
    which touches neither — that is what lets the CLI answer ``--mode keyword``
    without paying a model load, and the assert below is what stops the
    permission being taken any further. The server always passes real ones.
    ``reranker`` is optional in hybrid mode too (no reranking, candidates
    truncated to *limit*).

    Raises sqlite3.OperationalError on bad FTS5 syntax in keyword mode, and
    from hybrid mode only when *both* arms failed (hybrid otherwise degrades
    to the surviving arm). Semantic mode never parses the query as FTS5.
    """
    if mode is SearchMode.keyword:
        kw_results = KeywordIndex(conn).search(
            build_fts_query(parsed), limit=limit, **build_kw_filters(parsed)
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

    assert vector_index is not None and embedder is not None, (
        "semantic and hybrid modes require an embedder and a vector index"
    )

    if mode is SearchMode.semantic:
        query_vec = embedder.embed([parsed.semantic_query])[0]
        # Over-fetch to leave room for per-doc chunk dedup.
        raw_results = vector_index.search(
            query_vec, limit=limit * 3, where=build_vec_where(parsed)
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
