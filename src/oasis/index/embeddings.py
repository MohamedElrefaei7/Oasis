from typing import Protocol

import numpy as np
from sentence_transformers import SentenceTransformer

DEFAULT_MODEL = "all-MiniLM-L6-v2"
BATCH_SIZE = 32

# One SentenceTransformer instance per model name, shared across all embedder
# instances in the process.  Loading a transformer model is expensive (~seconds
# + hundreds of MB); callers should never pay that cost more than once.
_MODEL_CACHE: dict[str, SentenceTransformer] = {}


def _load_model(name: str) -> SentenceTransformer:
    if name not in _MODEL_CACHE:
        _MODEL_CACHE[name] = SentenceTransformer(name)
    return _MODEL_CACHE[name]


class EmbeddingModel(Protocol):
    dimension: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


class SentenceTransformerEmbedder:
    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        batch_size: int = BATCH_SIZE,
    ) -> None:
        # Public: the pipeline records it as a capability marker so a later run
        # can tell which model an index's vectors were built with.
        self.model_name = model_name
        self._batch_size = batch_size
        self._model = _load_model(model_name)
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
