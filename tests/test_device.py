"""Device resolution, and the guarantee that inference actually runs on CPU.

The pure `resolve_device` tests stay in the default suite (no model load, so
the suite stays torch-free). The two that construct real models are marked
`slow`, because only inspecting a loaded model's real device proves the
invariant that matters.
"""

from __future__ import annotations

import pytest

from oasis.device import DEFAULT_DEVICE, DEVICE_ENV_VAR, resolve_device

# ---------------------------------------------------------------------------
# resolve_device — pure, no model load
# ---------------------------------------------------------------------------


def test_default_is_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DEVICE_ENV_VAR, raising=False)
    assert resolve_device() == "cpu"


def test_default_device_constant_is_cpu() -> None:
    assert DEFAULT_DEVICE == "cpu"


def test_env_var_respected_when_no_explicit_arg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DEVICE_ENV_VAR, "cuda")
    assert resolve_device() == "cuda"


def test_explicit_arg_beats_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DEVICE_ENV_VAR, "cuda")
    assert resolve_device("cpu") == "cpu"


def test_empty_env_var_falls_back_to_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exported-but-empty OASIS_DEVICE must not resolve to "" and blow up."""
    monkeypatch.setenv(DEVICE_ENV_VAR, "")
    assert resolve_device() == "cpu"


# ---------------------------------------------------------------------------
# Cache keys include the device
#
# Structural only. NOTHING here loads a model on "mps": that is the exact call
# that aborts the process under Metal validation, so testing it would
# reintroduce the crash this commit exists to prevent. The key composition is
# asserted directly instead.
# ---------------------------------------------------------------------------


def test_embedder_cache_key_is_name_and_device(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock, patch

    import oasis.index.embeddings as emb_mod

    monkeypatch.setattr(emb_mod, "_MODEL_CACHE", {})
    fake = MagicMock()
    fake.get_embedding_dimension.return_value = 384
    with patch("oasis.index.embeddings.SentenceTransformer") as MockST:
        MockST.return_value = fake
        emb_mod.SentenceTransformerEmbedder()
        emb_mod.SentenceTransformerEmbedder()

    assert list(emb_mod._MODEL_CACHE) == [(emb_mod.DEFAULT_MODEL, "cpu")]
    # Two embedders, one load: the cache hit is by the full key, not by name.
    assert MockST.call_count == 1
    MockST.assert_called_once_with(emb_mod.DEFAULT_MODEL, device="cpu")


def test_reranker_cache_key_is_name_and_device(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock, patch

    import oasis.query.reranker as reranker_mod

    monkeypatch.setattr(reranker_mod, "_MODEL_CACHE", {})
    with patch("oasis.query.reranker.CrossEncoder") as MockCE:
        MockCE.return_value = MagicMock()
        reranker_mod.CrossEncoderReranker()
        reranker_mod.CrossEncoderReranker()

    assert list(reranker_mod._MODEL_CACHE) == [(reranker_mod.DEFAULT_CE_MODEL, "cpu")]
    assert MockCE.call_count == 1
    MockCE.assert_called_once_with(reranker_mod.DEFAULT_CE_MODEL, device="cpu")


def test_same_name_different_device_are_distinct_cache_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bug the tuple key prevents, asserted without touching a real device.

    Keyed by name alone, the second call would return the CPU instance. The
    fake stands in for a model on another device precisely so no MPS/CUDA
    context is ever created.
    """
    from unittest.mock import MagicMock, patch

    import oasis.index.embeddings as emb_mod

    monkeypatch.setattr(emb_mod, "_MODEL_CACHE", {})
    first, second = MagicMock(), MagicMock()
    with patch("oasis.index.embeddings.SentenceTransformer") as MockST:
        MockST.side_effect = [first, second]
        a = emb_mod._load_model("some-model", "cpu")
        b = emb_mod._load_model("some-model", "some-other-device")

    assert a is not b
    assert set(emb_mod._MODEL_CACHE) == {
        ("some-model", "cpu"),
        ("some-model", "some-other-device"),
    }


# ---------------------------------------------------------------------------
# The real invariant: the loaded model actually runs on CPU.
#
# Asserted against the loaded torch module, never against the string that was
# passed in — a string-only assertion would stay green if sentence-transformers
# ever ignored the argument, which is exactly the regression (device
# auto-selection creeping back) these guard against.
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_cpu_cross_encoder_returns_finite_scores() -> None:
    """The guarantee the whole CPU-default policy rests on.

    Was `xfail(strict=True)` while every stock macOS-arm64 torch wheel linked
    Apple's Accelerate, whose SGEMV path returned all-NaN logits here. The
    marker fired `XPASS(strict)` the moment the project moved to conda-forge's
    OpenBLAS torch (2026-07-25), which is what it was built to do; it is now a
    plain assertion guarding the fix.

    Uses the realistic shape that actually failed — real snippet lengths in a
    multi-pair batch. Short inputs passed even on a broken build, so a toy batch
    here would prove nothing.

    Keep this test on the realistic shape: if a future torch/BLAS swap
    reintroduces the bug, this is the tripwire. Caveat worth knowing — some
    shapes aborted the *process* (SIGBUS in Accelerate's cblas_sgemv) rather
    than returning NaN, and pytest cannot catch that, so the catchable NaN
    shape is the one encoded.
    """
    import numpy as np

    from oasis.query.reranker import CrossEncoderReranker

    query = "in the txt folder, something about a whale"
    snippets = [
        "Call me Ishmael. Some years ago, never mind how long precisely, having little or no "
        "money in my purse, and nothing particular to interest me on shore, I thought I would "
        "sail about a little and see the watery part of the world.",
        "The quarterly revenue report shows growth in enterprise renewals, with net income up "
        "twelve percent year over year across the subscription segment.",
        "Moby Dick is the great white whale hunted by Captain Ahab aboard the Pequod, a voyage "
        "that consumes the crew and ends in the destruction of the ship.",
    ]

    reranker = CrossEncoderReranker(device="cpu")
    scores = np.asarray(
        reranker._model.predict(
            [(query, s) for s in snippets], show_progress_bar=False
        ),
        dtype=float,
    )

    assert np.isfinite(scores).all(), f"cross-encoder produced non-finite logits: {scores}"
    assert np.abs(scores).max() < 1e4, f"cross-encoder logits out of sane range: {scores}"


@pytest.mark.slow
def test_real_embedder_loads_on_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DEVICE_ENV_VAR, raising=False)
    from oasis.index.embeddings import SentenceTransformerEmbedder

    embedder = SentenceTransformerEmbedder()

    assert embedder.device == "cpu"
    param_device = next(embedder._model.parameters()).device
    assert param_device.type == "cpu", f"embedder loaded on {param_device}, expected cpu"


@pytest.mark.slow
def test_real_reranker_loads_on_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cross-encoder specifically: its first real inference is what aborts
    the server under Metal validation when the device is MPS."""
    monkeypatch.delenv(DEVICE_ENV_VAR, raising=False)
    from oasis.query.reranker import CrossEncoderReranker

    reranker = CrossEncoderReranker()

    assert reranker.device == "cpu"
    param_device = next(reranker._model.model.parameters()).device
    assert param_device.type == "cpu", f"cross-encoder loaded on {param_device}, expected cpu"
