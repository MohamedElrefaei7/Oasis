"""Tests for oasis.index.embeddings."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

import oasis.index.embeddings as emb_mod
from oasis.index.embeddings import (
    BATCH_SIZE,
    DEFAULT_MODEL,
    SentenceTransformerEmbedder,
    _load_model,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_cache() -> None:
    """Clear the module-level model cache before each test and restore after."""
    saved = dict(emb_mod._MODEL_CACHE)
    emb_mod._MODEL_CACHE.clear()
    yield
    emb_mod._MODEL_CACHE.clear()
    emb_mod._MODEL_CACHE.update(saved)


def _fake_model(dim: int = 384) -> MagicMock:
    m = MagicMock()
    m.get_embedding_dimension.return_value = dim
    m.encode.side_effect = lambda texts, **kw: np.zeros((len(texts), dim), dtype=np.float32)
    return m


@pytest.fixture
def fake_model() -> MagicMock:
    return _fake_model()


@pytest.fixture
def embedder(fake_model: MagicMock) -> SentenceTransformerEmbedder:
    with patch("oasis.index.embeddings._load_model", return_value=fake_model):
        return SentenceTransformerEmbedder()


# ---------------------------------------------------------------------------
# _load_model caching
# ---------------------------------------------------------------------------


def test_load_model_returns_sentence_transformer() -> None:
    with patch("oasis.index.embeddings.SentenceTransformer") as MockST:
        MockST.return_value = _fake_model()
        result = _load_model("all-MiniLM-L6-v2", "cpu")
    assert result is MockST.return_value


def test_load_model_same_name_returns_cached_instance() -> None:
    with patch("oasis.index.embeddings.SentenceTransformer") as MockST:
        MockST.return_value = _fake_model()
        first = _load_model("all-MiniLM-L6-v2", "cpu")
        second = _load_model("all-MiniLM-L6-v2", "cpu")
    assert first is second
    assert MockST.call_count == 1


def test_load_model_different_names_load_separately() -> None:
    with patch("oasis.index.embeddings.SentenceTransformer") as MockST:
        MockST.side_effect = [_fake_model(384), _fake_model(768)]
        m1 = _load_model("model-a", "cpu")
        m2 = _load_model("model-b", "cpu")
    assert m1 is not m2
    assert MockST.call_count == 2


def test_load_model_populates_cache() -> None:
    with patch("oasis.index.embeddings.SentenceTransformer") as MockST:
        MockST.return_value = _fake_model()
        _load_model("all-MiniLM-L6-v2", "cpu")
    assert ("all-MiniLM-L6-v2", "cpu") in emb_mod._MODEL_CACHE


def test_second_embedder_reuses_cached_model() -> None:
    """Two SentenceTransformerEmbedder instances for the same model name share one
    underlying SentenceTransformer object."""
    with patch("oasis.index.embeddings.SentenceTransformer") as MockST:
        MockST.return_value = _fake_model()
        e1 = SentenceTransformerEmbedder()
        e2 = SentenceTransformerEmbedder()
    assert e1._model is e2._model
    assert MockST.call_count == 1


# ---------------------------------------------------------------------------
# SentenceTransformerEmbedder construction
# ---------------------------------------------------------------------------


def test_dimension_set_from_model(embedder: SentenceTransformerEmbedder) -> None:
    assert embedder.dimension == 384


def test_dimension_reflects_custom_model() -> None:
    with patch("oasis.index.embeddings._load_model", return_value=_fake_model(dim=768)):
        e = SentenceTransformerEmbedder(model_name="large-model")
    assert e.dimension == 768


def test_default_model_name() -> None:
    assert DEFAULT_MODEL == "all-MiniLM-L6-v2"


def test_default_batch_size() -> None:
    assert BATCH_SIZE == 32


def test_custom_batch_size_stored(fake_model: MagicMock) -> None:
    with patch("oasis.index.embeddings._load_model", return_value=fake_model):
        e = SentenceTransformerEmbedder(batch_size=64)
    assert e._batch_size == 64


def test_custom_model_name_forwarded() -> None:
    with patch("oasis.index.embeddings._load_model") as mock_load:
        mock_load.return_value = _fake_model()
        SentenceTransformerEmbedder(model_name="custom-model")
    mock_load.assert_called_once_with("custom-model", "cpu")


# ---------------------------------------------------------------------------
# embed — return type and shape
# ---------------------------------------------------------------------------


def test_embed_returns_ndarray(embedder: SentenceTransformerEmbedder) -> None:
    result = embedder.embed(["hello"])
    assert isinstance(result, np.ndarray)


def test_embed_shape_single_text(embedder: SentenceTransformerEmbedder) -> None:
    result = embedder.embed(["hello"])
    assert result.shape == (1, 384)


def test_embed_shape_multiple_texts(embedder: SentenceTransformerEmbedder) -> None:
    result = embedder.embed(["a", "b", "c"])
    assert result.shape == (3, 384)


def test_embed_dtype_is_float32(embedder: SentenceTransformerEmbedder) -> None:
    result = embedder.embed(["hello world"])
    assert result.dtype == np.float32


def test_embed_empty_list_returns_empty_array(embedder: SentenceTransformerEmbedder) -> None:
    result = embedder.embed([])
    assert isinstance(result, np.ndarray)
    assert result.shape == (0, 384)
    assert result.dtype == np.float32


def test_embed_empty_does_not_call_model(
    embedder: SentenceTransformerEmbedder, fake_model: MagicMock
) -> None:
    embedder.embed([])
    fake_model.encode.assert_not_called()


# ---------------------------------------------------------------------------
# embed — encode call arguments
# ---------------------------------------------------------------------------


def test_embed_passes_texts_to_encode(
    embedder: SentenceTransformerEmbedder, fake_model: MagicMock
) -> None:
    texts = ["first sentence", "second sentence"]
    embedder.embed(texts)
    args, _ = fake_model.encode.call_args
    assert args[0] == texts


def test_embed_passes_batch_size_to_encode(
    embedder: SentenceTransformerEmbedder, fake_model: MagicMock
) -> None:
    embedder.embed(["text"])
    _, kwargs = fake_model.encode.call_args
    assert kwargs["batch_size"] == BATCH_SIZE


def test_embed_disables_progress_bar(
    embedder: SentenceTransformerEmbedder, fake_model: MagicMock
) -> None:
    embedder.embed(["text"])
    _, kwargs = fake_model.encode.call_args
    assert kwargs["show_progress_bar"] is False


def test_embed_requests_numpy_output(
    embedder: SentenceTransformerEmbedder, fake_model: MagicMock
) -> None:
    embedder.embed(["text"])
    _, kwargs = fake_model.encode.call_args
    assert kwargs["convert_to_numpy"] is True


def test_embed_custom_batch_size_forwarded_to_encode() -> None:
    fake = _fake_model()
    with patch("oasis.index.embeddings._load_model", return_value=fake):
        e = SentenceTransformerEmbedder(batch_size=16)
    e.embed(["text"])
    _, kwargs = fake.encode.call_args
    assert kwargs["batch_size"] == 16


# ---------------------------------------------------------------------------
# embed — batch processing (the model handles splitting internally)
# ---------------------------------------------------------------------------


def test_embed_all_texts_passed_in_one_call(
    embedder: SentenceTransformerEmbedder, fake_model: MagicMock
) -> None:
    """sentence-transformers handles internal batching; we must pass all texts
    in a single encode() call, not loop one-by-one."""
    texts = [f"sentence {i}" for i in range(100)]
    embedder.embed(texts)
    assert fake_model.encode.call_count == 1
    passed_texts, _ = fake_model.encode.call_args
    assert len(passed_texts[0]) == 100


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


def test_satisfies_embedding_model_protocol(embedder: SentenceTransformerEmbedder) -> None:
    assert hasattr(embedder, "dimension")
    assert isinstance(embedder.dimension, int)
    assert hasattr(embedder, "embed")
    assert callable(embedder.embed)


def test_dimension_is_int(embedder: SentenceTransformerEmbedder) -> None:
    assert type(embedder.dimension) is int
