from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import numpy as np

from oasis.device import resolve_device
from oasis.index.filename import humanize_filename
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


def _passage(result: HybridResult) -> str:
    """The text the cross-encoder judges: the file's name, then its best prose.

    Two decisions here, both measured, and both about giving this model
    something it can actually judge.

    **The name comes first.** Without it this stage actively undoes the
    retrieval it is reranking: a document found *because* its filename matched
    arrives carrying a content snippet that, by construction, contains none of
    the query terms, so the model sees an irrelevant passage and pushes it back
    down — the two arms surface the file and the reranker buries it. Leading
    with a short label is also in-distribution for MS MARCO cross-encoders,
    which are trained on passages that begin with a title.

    **The body is the semantic arm's best content chunk when there is one, not
    the FTS snippet.** This is the difference between reranking being a net
    loss and being worth having. An FTS snippet is 20 tokens centred on a
    keyword hit — a fragment, often mid-sentence, and for a filename-only match
    it is about something else entirely. A chunk is coherent prose. Worth
    **+0.070 ndcg@10** (0.6280 → 0.6981), which takes reranking from *below*
    raw fusion back to comfortably above it. Simply making the snippet longer
    is worth **nothing** — 20/48/96/200 tokens all scored the same, because the
    fragment's problem was never its length. Note *content* chunk: the closest
    chunk to a filename query is frequently the name chunk itself, and handing
    the model back the same three words is measurably no better than the
    snippet (0.6264). Hits with no content chunk — keyword-only, or reached
    only by name — fall back to the snippet, which is all that exists.

    ``snippet`` stays the display text — a user wants the highlighted match,
    not 500 tokens of prose. The two audiences genuinely want different things.
    """
    name = humanize_filename(result.path)
    body = _clean(result.rerank_text or result.snippet)
    if not name:
        return body
    return f"{name}\n{body}" if body else name


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

        pairs = [(query, _passage(r)) for r in results]
        raw = self._model.predict(pairs, show_progress_bar=False)
        scores = np.atleast_1d(np.asarray(raw, dtype=np.float32))

        # strict=True: a scores array shorter than `results` would otherwise
        # silently drop the tail — results vanishing from a search with no
        # error anywhere, which is precisely the failure mode this model
        # already produced once via NaN logits (oasis.device). If the model
        # ever returns a wrong-length array, fail loudly.
        reranked = sorted(
            (replace(r, score=float(s)) for r, s in zip(results, scores, strict=True)),
            key=lambda r: r.score,
            reverse=True,
        )

        return reranked[:top_n] if top_n is not None else reranked
