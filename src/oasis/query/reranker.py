from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import numpy as np

from oasis.device import resolve_device
from oasis.index.keyword import MATCH_END, MATCH_START
from oasis.query.retriever import HybridResult

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder
else:
    # NOT imported at module level — sentence_transformers pulls in PyTorch on
    # import, and this module sits on every API/CLI import chain. Bound to the
    # real class on first _load_model call; a None sentinel until then, which
    # tests patch with a fake (also suppressing the import).
    CrossEncoder = None

DEFAULT_CE_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# One CrossEncoder per (model name, device), shared across all reranker
# instances.  The device belongs in the key: keyed by name alone, requesting the
# same model on another device would return the instance loaded on the previous
# one — silently wrong as soon as OASIS_DEVICE is used.
_MODEL_CACHE: dict[tuple[str, str], CrossEncoder] = {}


def _load_model(name: str, device: str) -> CrossEncoder:
    global CrossEncoder
    key = (name, device)
    if key not in _MODEL_CACHE:
        if CrossEncoder is None:
            from sentence_transformers import CrossEncoder as _CrossEncoder

            CrossEncoder = _CrossEncoder
        _MODEL_CACHE[key] = CrossEncoder(name, device=device)
    return _MODEL_CACHE[key]


def _clean(text: str) -> str:
    """Strip FTS5 highlight sentinels so the cross-encoder sees plain text."""
    return text.replace(MATCH_START, "").replace(MATCH_END, "")


class CrossEncoderReranker:
    def __init__(self, model_name: str = DEFAULT_CE_MODEL, device: str | None = None) -> None:
        # Defaults to CPU. This is the model whose first real inference aborts
        # the process under Metal validation in a GUI-spawned server — see
        # oasis.device.
        self.device = resolve_device(device)
        self._model = _load_model(model_name, self.device)

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
            (replace(r, score=float(s)) for r, s in zip(results, scores, strict=False)),
            key=lambda r: r.score,
            reverse=True,
        )

        return reranked[:top_n] if top_n is not None else reranked
