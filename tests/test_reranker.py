"""Tests for oasis.query.reranker."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

import oasis.query.reranker as reranker_mod
from oasis.index.keyword import MATCH_END, MATCH_START
from oasis.query.reranker import (
    DEFAULT_CE_MODEL,
    CrossEncoderReranker,
    _clean,
    _load_model,
)
from oasis.query.retriever import HybridResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_cache() -> None:
    """Clear the model cache before each test and restore after."""
    saved = dict(reranker_mod._MODEL_CACHE)
    reranker_mod._MODEL_CACHE.clear()
    yield
    reranker_mod._MODEL_CACHE.clear()
    reranker_mod._MODEL_CACHE.update(saved)


def _fake_model(scores: list[float]) -> MagicMock:
    """A CrossEncoder stand-in that returns **one score per pair**, like the real one.

    It used to return a fixed-length array regardless of input, which meant a
    3-score fake handed to a 1-result rerank was silently truncated by
    `zip(strict=False)`. Since `rerank` now zips strictly (a short score array
    would otherwise drop results with no error — the failure mode NaN logits
    already produced once), the fake has to be honest about its length or it
    tests a contract the real model doesn't have.
    """
    m = MagicMock()

    def predict(pairs, **_kwargs):
        return np.array(
            [scores[i % len(scores)] for i in range(len(pairs))] if scores else [],
            dtype=np.float32,
        )

    m.predict.side_effect = predict
    return m


def _result(
    path: str = "/doc.txt",
    snippet: str = "some text",
    score: float = 0.5,
    doc_id: int = 1,
    title: str | None = None,
) -> HybridResult:
    return HybridResult(path=Path(path), doc_id=doc_id, title=title, snippet=snippet, score=score)


@pytest.fixture
def fake_ce() -> MagicMock:
    return _fake_model([2.0, 1.0, 0.3])


@pytest.fixture
def reranker(fake_ce: MagicMock) -> CrossEncoderReranker:
    with patch("oasis.query.reranker.CrossEncoder", return_value=fake_ce):
        return CrossEncoderReranker()


# ---------------------------------------------------------------------------
# _clean — marker stripping
# ---------------------------------------------------------------------------


def test_clean_strips_match_start() -> None:
    assert _clean(f"hello {MATCH_START}world") == "hello world"


def test_clean_strips_match_end() -> None:
    assert _clean(f"hello{MATCH_END} world") == "hello world"


def test_clean_strips_both_markers() -> None:
    text = f"the {MATCH_START}quick{MATCH_END} brown fox"
    assert _clean(text) == "the quick brown fox"


def test_clean_preserves_plain_text() -> None:
    text = "nothing special here"
    assert _clean(text) == text


def test_clean_handles_multiple_occurrences() -> None:
    text = f"{MATCH_START}a{MATCH_END} and {MATCH_START}b{MATCH_END}"
    assert _clean(text) == "a and b"


def test_clean_empty_string() -> None:
    assert _clean("") == ""


# ---------------------------------------------------------------------------
# _load_model — caching
# ---------------------------------------------------------------------------


def test_load_model_returns_cross_encoder_instance() -> None:
    with patch("oasis.query.reranker.CrossEncoder") as MockCE:
        MockCE.return_value = _fake_model([])
        result = _load_model("test-model", "cpu")
    assert result is MockCE.return_value


def test_load_model_same_name_returns_cached() -> None:
    with patch("oasis.query.reranker.CrossEncoder") as MockCE:
        MockCE.return_value = _fake_model([])
        first = _load_model("model-a", "cpu")
        second = _load_model("model-a", "cpu")
    assert first is second
    assert MockCE.call_count == 1


def test_load_model_different_names_load_separately() -> None:
    with patch("oasis.query.reranker.CrossEncoder") as MockCE:
        MockCE.side_effect = [_fake_model([]), _fake_model([])]
        _load_model("model-a", "cpu")
        _load_model("model-b", "cpu")
    assert MockCE.call_count == 2


def test_load_model_populates_cache() -> None:
    with patch("oasis.query.reranker.CrossEncoder") as MockCE:
        MockCE.return_value = _fake_model([])
        _load_model("my-model", "cpu")
    assert ("my-model", "cpu") in reranker_mod._MODEL_CACHE


def test_second_reranker_shares_model() -> None:
    with patch("oasis.query.reranker.CrossEncoder") as MockCE:
        MockCE.return_value = _fake_model([])
        r1 = CrossEncoderReranker()
        r2 = CrossEncoderReranker()
    assert r1._model is r2._model
    assert MockCE.call_count == 1


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_default_model_constant() -> None:
    assert DEFAULT_CE_MODEL == "cross-encoder/ms-marco-MiniLM-L-6-v2"


def test_custom_model_name_forwarded() -> None:
    with patch("oasis.query.reranker.CrossEncoder") as MockCE:
        MockCE.return_value = _fake_model([])
        CrossEncoderReranker(model_name="my-custom-ce")
    MockCE.assert_called_once_with("my-custom-ce", device="cpu")


def test_default_model_name_used_when_not_specified() -> None:
    with patch("oasis.query.reranker.CrossEncoder") as MockCE:
        MockCE.return_value = _fake_model([])
        CrossEncoderReranker()
    MockCE.assert_called_once_with(DEFAULT_CE_MODEL, device="cpu")


# ---------------------------------------------------------------------------
# rerank — return type and shape
# ---------------------------------------------------------------------------


def test_rerank_returns_list(reranker: CrossEncoderReranker) -> None:
    result = reranker.rerank("q", [_result()])
    assert isinstance(result, list)


def test_rerank_returns_hybrid_results(reranker: CrossEncoderReranker) -> None:
    result = reranker.rerank("q", [_result()])
    assert all(isinstance(r, HybridResult) for r in result)


def test_rerank_empty_input_returns_empty(reranker: CrossEncoderReranker) -> None:
    assert reranker.rerank("q", []) == []


def test_rerank_returns_same_count_as_input_without_top_n(
    reranker: CrossEncoderReranker,
) -> None:
    results = [_result("/a.txt"), _result("/b.txt"), _result("/c.txt")]
    reranked = reranker.rerank("q", results)
    assert len(reranked) == 3


def test_rerank_single_result() -> None:
    fake_ce = _fake_model([1.5])
    with patch("oasis.query.reranker.CrossEncoder", return_value=fake_ce):
        r = CrossEncoderReranker()
    results = [_result("/a.txt")]
    reranked = r.rerank("q", results)
    assert len(reranked) == 1
    assert reranked[0].score == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# rerank — ordering and scores
# ---------------------------------------------------------------------------


def test_rerank_sorted_by_score_descending() -> None:
    fake = _fake_model([0.1, 0.9, 0.5])
    with patch("oasis.query.reranker.CrossEncoder", return_value=fake):
        r = CrossEncoderReranker()
    results = [_result("/a.txt"), _result("/b.txt"), _result("/c.txt")]
    reranked = r.rerank("q", results)
    assert reranked[0].score > reranked[1].score > reranked[2].score


def test_rerank_correct_result_moved_to_top() -> None:
    # "/b.txt" gets highest score → should be first
    fake = _fake_model([0.1, 0.9, 0.5])
    with patch("oasis.query.reranker.CrossEncoder", return_value=fake):
        r = CrossEncoderReranker()
    results = [_result("/a.txt"), _result("/b.txt"), _result("/c.txt")]
    reranked = r.rerank("q", results)
    assert reranked[0].path == Path("/b.txt")


def test_rerank_lowest_scored_moves_to_last() -> None:
    fake = _fake_model([0.1, 0.9, 0.5])
    with patch("oasis.query.reranker.CrossEncoder", return_value=fake):
        r = CrossEncoderReranker()
    results = [_result("/a.txt"), _result("/b.txt"), _result("/c.txt")]
    reranked = r.rerank("q", results)
    assert reranked[-1].path == Path("/a.txt")


def test_rerank_scores_replaced_by_cross_encoder_values() -> None:
    # Input RRF scores are 0.5; cross-encoder gives 2.0, 1.0, 0.3
    fake = _fake_model([2.0, 1.0, 0.3])
    with patch("oasis.query.reranker.CrossEncoder", return_value=fake):
        r = CrossEncoderReranker()
    results = [_result("/a.txt", score=0.5), _result("/b.txt", score=0.5), _result("/c.txt", score=0.5)]
    reranked = r.rerank("q", results)
    ce_scores = sorted([r.score for r in reranked], reverse=True)
    assert ce_scores[0] == pytest.approx(2.0)
    assert ce_scores[1] == pytest.approx(1.0)
    assert ce_scores[2] == pytest.approx(0.3)


def test_rerank_preserves_other_fields() -> None:
    fake = _fake_model([1.0])
    with patch("oasis.query.reranker.CrossEncoder", return_value=fake):
        r = CrossEncoderReranker()
    original = _result("/doc.txt", doc_id=42, title="My Title", snippet="hello")
    reranked = r.rerank("q", [original])
    assert reranked[0].path == Path("/doc.txt")
    assert reranked[0].doc_id == 42
    assert reranked[0].title == "My Title"
    assert reranked[0].snippet == "hello"


# ---------------------------------------------------------------------------
# rerank — predict call arguments
# ---------------------------------------------------------------------------


def test_predict_called_once(reranker: CrossEncoderReranker, fake_ce: MagicMock) -> None:
    reranker.rerank("q", [_result("/a.txt"), _result("/b.txt"), _result("/c.txt")])
    fake_ce.predict.assert_called_once()


def test_predict_not_called_on_empty(reranker: CrossEncoderReranker, fake_ce: MagicMock) -> None:
    reranker.rerank("q", [])
    fake_ce.predict.assert_not_called()


def test_predict_receives_show_progress_bar_false(
    reranker: CrossEncoderReranker, fake_ce: MagicMock
) -> None:
    reranker.rerank("q", [_result()])
    _, kwargs = fake_ce.predict.call_args
    assert kwargs.get("show_progress_bar") is False


def test_predict_pairs_length_matches_input(
    reranker: CrossEncoderReranker, fake_ce: MagicMock
) -> None:
    reranker.rerank("q", [_result("/a.txt"), _result("/b.txt"), _result("/c.txt")])
    pairs = fake_ce.predict.call_args[0][0]
    assert len(pairs) == 3


def test_predict_query_is_first_in_each_pair(
    reranker: CrossEncoderReranker, fake_ce: MagicMock
) -> None:
    reranker.rerank("my query", [_result("/a.txt"), _result("/b.txt"), _result("/c.txt")])
    pairs = fake_ce.predict.call_args[0][0]
    assert all(p[0] == "my query" for p in pairs)


def test_predict_snippet_is_second_in_each_pair(
    reranker: CrossEncoderReranker, fake_ce: MagicMock
) -> None:
    results = [_result(snippet="text A"), _result(snippet="text B"), _result(snippet="text C")]
    reranker.rerank("q", results)
    pairs = fake_ce.predict.call_args[0][0]
    assert pairs[0][1] == "text A"
    assert pairs[1][1] == "text B"
    assert pairs[2][1] == "text C"


def test_predict_markers_stripped_from_snippet() -> None:
    snippet_with_markers = f"the {MATCH_START}quick{MATCH_END} fox"
    fake = _fake_model([1.0])
    with patch("oasis.query.reranker.CrossEncoder", return_value=fake):
        r = CrossEncoderReranker()
    r.rerank("q", [_result(snippet=snippet_with_markers)])
    pairs = fake.predict.call_args[0][0]
    assert MATCH_START not in pairs[0][1]
    assert MATCH_END not in pairs[0][1]
    assert "quick" in pairs[0][1]


# ---------------------------------------------------------------------------
# rerank — top_n
# ---------------------------------------------------------------------------


def test_top_n_truncates_to_n_results() -> None:
    fake = _fake_model([3.0, 2.0, 1.0, 0.5, 0.1])
    with patch("oasis.query.reranker.CrossEncoder", return_value=fake):
        r = CrossEncoderReranker()
    results = [_result(f"/{i}.txt") for i in range(5)]
    reranked = r.rerank("q", results, top_n=3)
    assert len(reranked) == 3


def test_top_n_keeps_highest_scored() -> None:
    fake = _fake_model([0.1, 3.0, 2.0, 0.5, 1.0])
    with patch("oasis.query.reranker.CrossEncoder", return_value=fake):
        r = CrossEncoderReranker()
    results = [_result(f"/{i}.txt") for i in range(5)]
    reranked = r.rerank("q", results, top_n=2)
    # "/1.txt" got score 3.0, "/2.txt" got 2.0 → should be first two
    paths = {str(x.path) for x in reranked}
    assert "/1.txt" in paths
    assert "/2.txt" in paths


def test_top_n_none_returns_all() -> None:
    fake = _fake_model([1.0, 2.0, 3.0])
    with patch("oasis.query.reranker.CrossEncoder", return_value=fake):
        r = CrossEncoderReranker()
    results = [_result(f"/{i}.txt") for i in range(3)]
    reranked = r.rerank("q", results, top_n=None)
    assert len(reranked) == 3


def test_top_n_larger_than_input_returns_all() -> None:
    fake = _fake_model([1.0, 2.0])
    with patch("oasis.query.reranker.CrossEncoder", return_value=fake):
        r = CrossEncoderReranker()
    results = [_result("/a.txt"), _result("/b.txt")]
    reranked = r.rerank("q", results, top_n=100)
    assert len(reranked) == 2


def test_top_n_zero_returns_empty() -> None:
    fake = _fake_model([1.0, 2.0])
    with patch("oasis.query.reranker.CrossEncoder", return_value=fake):
        r = CrossEncoderReranker()
    results = [_result("/a.txt"), _result("/b.txt")]
    reranked = r.rerank("q", results, top_n=0)
    assert reranked == []
