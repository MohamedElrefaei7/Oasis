"""Tests for oasis.index.chunker."""

import pytest

from oasis.index.chunker import CHUNK_SIZE, OVERLAP, Chunk, chunk_document, encoding

# ---------------------------------------------------------------------------
# Return type / shape
# ---------------------------------------------------------------------------


def test_returns_list() -> None:
    assert isinstance(chunk_document("hello world"), list)


def test_each_element_is_chunk() -> None:
    chunks = chunk_document("hello world")
    assert all(isinstance(c, Chunk) for c in chunks)


def test_chunk_has_correct_fields() -> None:
    chunk = chunk_document("hello world")[0]
    assert hasattr(chunk, "chunk_index")
    assert hasattr(chunk, "text")
    assert hasattr(chunk, "token_count")


# ---------------------------------------------------------------------------
# Empty / whitespace input
# ---------------------------------------------------------------------------


def test_empty_string_returns_empty_list() -> None:
    assert chunk_document("") == []


def test_whitespace_only_returns_empty_list() -> None:
    assert chunk_document("   \n\t  ") == []


def test_newline_only_returns_empty_list() -> None:
    assert chunk_document("\n\n\n") == []


# ---------------------------------------------------------------------------
# Short text (fits in a single chunk)
# ---------------------------------------------------------------------------


def test_short_text_produces_one_chunk() -> None:
    assert len(chunk_document("hello world")) == 1


def test_single_chunk_index_is_zero() -> None:
    chunk = chunk_document("hello world")[0]
    assert chunk.chunk_index == 0


def test_single_chunk_text_roundtrips() -> None:
    text = "the quick brown fox jumps over the lazy dog"
    chunks = chunk_document(text)
    assert len(chunks) == 1
    assert chunks[0].text == text


def test_single_chunk_token_count_is_accurate() -> None:
    text = "the quick brown fox"
    chunks = chunk_document(text)
    expected_tokens = len(encoding().encode(text))
    assert chunks[0].token_count == expected_tokens


def test_token_count_matches_text_length() -> None:
    text = "some arbitrary sentence for testing purposes"
    chunks = chunk_document(text)
    for chunk in chunks:
        assert chunk.token_count == len(encoding().encode(chunk.text))


# ---------------------------------------------------------------------------
# Long text (multiple chunks)
# ---------------------------------------------------------------------------


def _text_of_n_tokens(n: int) -> str:
    """Return text that encodes to exactly n tokens.

    " hello" is token 23748 on cl100k_base — a stable single-token unit whose
    decode→re-encode roundtrip is identity, making it safe for token-exact tests.
    """
    return " hello" * n


def test_long_text_produces_multiple_chunks() -> None:
    text = _text_of_n_tokens(1100)
    chunks = chunk_document(text, chunk_size=500, overlap=50)
    assert len(chunks) >= 2


def test_chunk_indices_are_sequential() -> None:
    text = _text_of_n_tokens(1100)
    chunks = chunk_document(text, chunk_size=500, overlap=50)
    for i, chunk in enumerate(chunks):
        assert chunk.chunk_index == i


def test_first_chunk_size_is_chunk_size() -> None:
    text = _text_of_n_tokens(1100)
    chunks = chunk_document(text, chunk_size=500, overlap=50)
    assert chunks[0].token_count == 500


def test_last_chunk_is_no_larger_than_chunk_size() -> None:
    text = _text_of_n_tokens(1100)
    chunks = chunk_document(text, chunk_size=500, overlap=50)
    assert chunks[-1].token_count <= 500


def test_all_chunks_within_size_limit() -> None:
    text = _text_of_n_tokens(2000)
    chunks = chunk_document(text, chunk_size=500, overlap=50)
    for chunk in chunks:
        assert chunk.token_count <= 500


# ---------------------------------------------------------------------------
# Overlap
# ---------------------------------------------------------------------------


def test_consecutive_chunks_share_tokens() -> None:
    """The tail of chunk N and the head of chunk N+1 should share content."""
    text = _text_of_n_tokens(1100)
    chunks = chunk_document(text, chunk_size=500, overlap=50)
    assert len(chunks) >= 2

    tail_tokens = encoding().encode(chunks[0].text)[-50:]
    head_tokens = encoding().encode(chunks[1].text)[:50]
    assert tail_tokens == head_tokens


def test_overlap_zero_partitions_token_sequence() -> None:
    text = _text_of_n_tokens(1000)
    chunks = chunk_document(text, chunk_size=500, overlap=0)
    assert len(chunks) == 2
    # With no overlap the chunks must partition the full token sequence exactly.
    reconstructed = encoding().encode(chunks[0].text) + encoding().encode(chunks[1].text)
    assert reconstructed == encoding().encode(text)


def test_full_reconstruction_with_overlap_removed() -> None:
    """De-overlapping all chunks should reconstruct the original token sequence."""
    text = _text_of_n_tokens(1100)
    overlap = 50
    chunks = chunk_document(text, chunk_size=500, overlap=overlap)

    reconstructed: list[int] = encoding().encode(chunks[0].text)
    for chunk in chunks[1:]:
        reconstructed += encoding().encode(chunk.text)[overlap:]

    assert reconstructed == encoding().encode(text)


# ---------------------------------------------------------------------------
# Exact boundary: text is exactly chunk_size tokens
# ---------------------------------------------------------------------------


def test_text_exactly_chunk_size_produces_one_chunk() -> None:
    text = _text_of_n_tokens(500)
    chunks = chunk_document(text, chunk_size=500, overlap=50)
    assert len(chunks) == 1
    assert chunks[0].token_count == 500


def test_text_one_token_over_produces_two_chunks() -> None:
    text = _text_of_n_tokens(501)
    chunks = chunk_document(text, chunk_size=500, overlap=50)
    assert len(chunks) == 2


def test_text_exactly_step_size_produces_one_chunk() -> None:
    # step = 500 - 50 = 450; a 450-token text fits in one chunk
    text = _text_of_n_tokens(450)
    chunks = chunk_document(text, chunk_size=500, overlap=50)
    assert len(chunks) == 1


# ---------------------------------------------------------------------------
# Custom chunk_size and overlap parameters
# ---------------------------------------------------------------------------


def test_custom_chunk_size_respected() -> None:
    text = _text_of_n_tokens(300)
    chunks = chunk_document(text, chunk_size=100, overlap=10)
    assert all(c.token_count <= 100 for c in chunks)


def test_custom_overlap_reflected_in_shared_tokens() -> None:
    text = _text_of_n_tokens(500)
    chunks = chunk_document(text, chunk_size=200, overlap=20)
    assert len(chunks) >= 2
    tail = encoding().encode(chunks[0].text)[-20:]
    head = encoding().encode(chunks[1].text)[:20]
    assert tail == head


def test_overlap_zero_allowed() -> None:
    text = _text_of_n_tokens(100)
    chunks = chunk_document(text, chunk_size=50, overlap=0)
    assert len(chunks) == 2


# ---------------------------------------------------------------------------
# Default constants
# ---------------------------------------------------------------------------


def test_default_chunk_size_constant() -> None:
    assert CHUNK_SIZE == 500


def test_default_overlap_constant() -> None:
    assert OVERLAP == 50


def test_default_parameters_used_when_not_specified() -> None:
    text = _text_of_n_tokens(600)
    chunks_default = chunk_document(text)
    chunks_explicit = chunk_document(text, chunk_size=500, overlap=50)
    assert len(chunks_default) == len(chunks_explicit)
    assert all(
        a.token_count == b.token_count
        for a, b in zip(chunks_default, chunks_explicit, strict=False)
    )


# ---------------------------------------------------------------------------
# Unicode and special content
# ---------------------------------------------------------------------------


def test_unicode_text_no_crash() -> None:
    chunks = chunk_document("café résumé naïve — em-dash: —")
    assert len(chunks) >= 1


def test_cjk_text_no_crash() -> None:
    chunks = chunk_document("こんにちは世界。これはテストです。" * 20)
    assert all(c.token_count > 0 for c in chunks)


def test_newlines_preserved_in_chunk_text() -> None:
    text = "line one\nline two\nline three"
    chunks = chunk_document(text)
    assert "\n" in chunks[0].text


# ---------------------------------------------------------------------------
# Invalid arguments
# ---------------------------------------------------------------------------


def test_chunk_size_zero_raises() -> None:
    with pytest.raises(ValueError, match="chunk_size"):
        chunk_document("text", chunk_size=0)


def test_chunk_size_negative_raises() -> None:
    with pytest.raises(ValueError, match="chunk_size"):
        chunk_document("text", chunk_size=-1)


def test_overlap_equals_chunk_size_raises() -> None:
    with pytest.raises(ValueError, match="overlap"):
        chunk_document("text", chunk_size=100, overlap=100)


def test_overlap_greater_than_chunk_size_raises() -> None:
    with pytest.raises(ValueError, match="overlap"):
        chunk_document("text", chunk_size=100, overlap=200)
