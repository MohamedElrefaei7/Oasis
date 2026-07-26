from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import numpy as np

from oasis.device import resolve_device

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer
else:
    # Deliberately NOT imported at module level: sentence_transformers pulls in
    # PyTorch (seconds + hundreds of MB) the moment it's imported, and this
    # module is reachable from every API/CLI import chain. The real class is
    # bound on first _load_model call; until then the name is a None sentinel
    # that tests patch with a fake (which also suppresses the import).
    SentenceTransformer = None

DEFAULT_MODEL = "all-MiniLM-L6-v2"
BATCH_SIZE = 32

# One SentenceTransformer instance per (model name, device), shared across all
# embedder instances in the process.  Loading a transformer model is expensive
# (~seconds + hundreds of MB); callers should never pay that cost more than once.
#
# The device is part of the key on purpose: keyed by name alone, asking for the
# same model on a different device would hand back the instance loaded on the
# *previous* device — silently wrong the moment OASIS_DEVICE is used.
_MODEL_CACHE: dict[tuple[str, str], SentenceTransformer] = {}


def _load_model(name: str, device: str) -> SentenceTransformer:
    global SentenceTransformer
    key = (name, device)
    if key not in _MODEL_CACHE:
        if SentenceTransformer is None:
            from sentence_transformers import SentenceTransformer as _SentenceTransformer

            SentenceTransformer = _SentenceTransformer
        _MODEL_CACHE[key] = SentenceTransformer(name, device=device)
    return _MODEL_CACHE[key]


class EmbeddingModel(Protocol):
    dimension: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


class SentenceTransformerEmbedder:
    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        batch_size: int = BATCH_SIZE,
        device: str | None = None,
    ) -> None:
        # Public: the pipeline records it as a capability marker so a later run
        # can tell which model an index's vectors were built with.
        self.model_name = model_name
        # Defaults to CPU; see oasis.device for why that is not negotiable.
        self.device = resolve_device(device)
        self._batch_size = batch_size
        self._model = _load_model(model_name, self.device)
        dim = self._model.get_embedding_dimension()
        assert dim is not None, f"Could not determine embedding dimension for {model_name!r}"
        self.dimension: int = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self.dimension), dtype=np.float32)
        return self._model.encode(
            texts,
            batch_size=self._batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
