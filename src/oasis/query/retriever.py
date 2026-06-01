from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from oasis.index.embeddings import EmbeddingModel
from oasis.index.keyword import KeywordIndex
from oasis.index.vector import VectorIndex, VectorResult

RRF_K = 60
CANDIDATE_LIMIT = 50
DEFAULT_TOP_N = 10


@dataclass
class HybridResult:
    path: Path
    doc_id: int
    title: str | None
    # FTS5 snippet (with MATCH_START/MATCH_END markers) when available, else raw chunk text.
    snippet: str
    score: float  # RRF score — higher is better


def _rrf(ranked_lists: list[list[str]]) -> dict[str, float]:
    """Reciprocal Rank Fusion across arbitrarily many ranked lists.

    score(d) = Σ 1 / (RRF_K + rank_i)  for each list i where d appears.
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, key in enumerate(ranked, 1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank)
    return scores


def hybrid_search(
    conn: sqlite3.Connection,
    vector_index: VectorIndex,
    embedder: EmbeddingModel,
    query: str,
    *,
    top_n: int = DEFAULT_TOP_N,
    candidate_limit: int = CANDIDATE_LIMIT,
) -> list[HybridResult]:
    """Run FTS5 + vector search and fuse with Reciprocal Rank Fusion.

    Returns up to *top_n* documents ranked by fused score (descending).
    """
    # 1. BM25 via FTS5 — one result per document, ordered best-first.
    kw_results = KeywordIndex(conn).search(query, limit=candidate_limit)

    # 2. Semantic search — one result per chunk; deduplicate to best chunk per doc.
    query_vec: np.ndarray = embedder.embed([query])[0]
    vec_raw = vector_index.search(query_vec, limit=candidate_limit)

    best_vec: dict[str, VectorResult] = {}
    for r in vec_raw:
        if r.path not in best_vec or r.score < best_vec[r.path].score:
            best_vec[r.path] = r
    # Re-sort ascending by score (lowest distance = most similar = rank 1).
    vec_deduped = sorted(best_vec.values(), key=lambda r: r.score)

    # 3. RRF over path-keyed ranked lists.
    kw_ranked = [str(r.path) for r in kw_results]
    vec_ranked = [r.path for r in vec_deduped]  # VectorResult.path is already a str
    fused = _rrf([kw_ranked, vec_ranked])

    # 4. Build fast lookups.
    kw_by_path = {str(r.path): r for r in kw_results}
    vec_by_path = {r.path: r for r in vec_deduped}

    # 5. Assemble results, sorted by fused score descending.
    results: list[HybridResult] = []
    for path_str, score in sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:top_n]:
        kw = kw_by_path.get(path_str)
        vec = vec_by_path.get(path_str)

        # Every key in fused came from one of the two ranked lists, so at least
        # one of kw/vec is non-None.
        doc_id: int = kw.doc_id if kw is not None else vec.doc_id  # type: ignore[union-attr]
        title: str | None = kw.title if kw is not None else None
        snippet: str = kw.snippet if kw is not None else (vec.text if vec is not None else "")

        results.append(
            HybridResult(
                path=Path(path_str),
                doc_id=doc_id,
                title=title,
                snippet=snippet,
                score=score,
            )
        )

    return results
