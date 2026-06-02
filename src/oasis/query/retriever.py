from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from oasis.index.embeddings import EmbeddingModel
from oasis.index.keyword import KeywordIndex
from oasis.index.vector import VectorIndex, VectorResult
from oasis.query.parser import ParsedQuery

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


def _build_fts_query(parsed: ParsedQuery) -> str:
    """Combine semantic_query with keywords into an FTS5 query string.

    FTS5 treats space-separated terms as AND (all must match).  Multi-word
    keywords are quoted for phrase matching.
    """
    parts = [parsed.semantic_query]
    for kw in parsed.keywords:
        parts.append(f'"{kw}"' if " " in kw else kw)
    return " ".join(parts)


def _build_vec_where(parsed: ParsedQuery) -> str | None:
    """Build a LanceDB SQL WHERE clause from ParsedQuery filters."""
    parts: list[str] = []

    if parsed.file_types:
        quoted = ", ".join(f"'{ext}'" for ext in parsed.file_types)
        parts.append(f"extension IN ({quoted})")

    if parsed.date_range:
        dr = parsed.date_range
        if dr.after is not None:
            parts.append(f"mtime >= {dr.after.timestamp()}")
        if dr.before is not None:
            parts.append(f"mtime < {dr.before.timestamp()}")

    if parsed.folders:
        folder_conds: list[str] = []
        for folder in parsed.folders:
            prefix = str(Path(folder).expanduser()).rstrip("/")
            escaped = prefix.replace("'", "''")
            folder_conds.append(f"path LIKE '{escaped}/%'")
        parts.append(f"({' OR '.join(folder_conds)})")

    return " AND ".join(parts) if parts else None


def _build_kw_filters(parsed: ParsedQuery) -> dict:
    """Extract structured filters suitable for KeywordIndex.search() kwargs."""
    filters: dict = {}
    if parsed.date_range:
        dr = parsed.date_range
        if dr.after is not None:
            filters["after"] = dr.after.timestamp()
        if dr.before is not None:
            filters["before"] = dr.before.timestamp()
    if parsed.folders:
        filters["folders"] = [str(Path(f).expanduser()) for f in parsed.folders]
    if parsed.file_types:
        filters["extensions"] = parsed.file_types
    return filters


def hybrid_search(
    conn: sqlite3.Connection,
    vector_index: VectorIndex,
    embedder: EmbeddingModel,
    parsed: ParsedQuery,
    *,
    top_n: int = DEFAULT_TOP_N,
    candidate_limit: int = CANDIDATE_LIMIT,
) -> list[HybridResult]:
    """Run FTS5 + vector search and fuse with Reciprocal Rank Fusion.

    Uses all structured fields from *parsed*:
    - ``semantic_query`` drives both the embedding and the FTS5 base query.
    - ``keywords`` are appended to the FTS5 query as AND terms.
    - ``file_types``, ``date_range``, and ``folders`` are applied as filters
      to both the FTS5 and vector searches.

    Returns up to *top_n* documents ranked by fused score (descending).
    """
    fts_query = _build_fts_query(parsed)
    kw_filters = _build_kw_filters(parsed)
    vec_where = _build_vec_where(parsed)

    # 1. BM25 via FTS5 — one result per document, ordered best-first.
    kw_results = KeywordIndex(conn).search(fts_query, limit=candidate_limit, **kw_filters)

    # 2. Semantic search — one result per chunk; deduplicate to best chunk per doc.
    query_vec: np.ndarray = embedder.embed([parsed.semantic_query])[0]
    vec_raw = vector_index.search(query_vec, limit=candidate_limit, where=vec_where)

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
