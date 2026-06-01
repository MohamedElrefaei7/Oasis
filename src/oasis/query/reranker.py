from __future__ import annotations

from dataclasses import replace

import numpy as np
from sentence_transformers import CrossEncoder

from oasis.index.keyword import MATCH_END, MATCH_START
from oasis.query.retriever import HybridResult

DEFAULT_CE_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# One CrossEncoder per model name, shared across all reranker instances.
_MODEL_CACHE: dict[str, CrossEncoder] = {}


def _load_model(name: str) -> CrossEncoder:
    if name not in _MODEL_CACHE:
        _MODEL_CACHE[name] = CrossEncoder(name)
    return _MODEL_CACHE[name]


def _clean(text: str) -> str:
    """Strip FTS5 highlight sentinels so the cross-encoder sees plain text."""
    return text.replace(MATCH_START, "").replace(MATCH_END, "")


class CrossEncoderReranker:
    def __init__(self, model_name: str = DEFAULT_CE_MODEL) -> None:
        self._model = _load_model(model_name)

    def rerank(
        self,
        query: str,
        results: list[HybridResult],
        *,
        top_n: int | None = None,
    ) -> list[HybridResult]:
        """Score every (query, snippet) pair and return results sorted by relevance.

        The ``score`` field of each returned ``HybridResult`` is replaced with the
        cross-encoder logit (higher = more relevant).
        """
        if not results:
            return []

        pairs = [(query, _clean(r.snippet)) for r in results]
        raw = self._model.predict(pairs, show_progress_bar=False)
        scores = np.atleast_1d(np.asarray(raw, dtype=np.float32))

        reranked = sorted(
            (replace(r, score=float(s)) for r, s in zip(results, scores)),
            key=lambda r: r.score,
            reverse=True,
        )

        return reranked[:top_n] if top_n is not None else reranked
