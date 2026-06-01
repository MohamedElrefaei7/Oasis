"""Tests for oasis.query.snippets."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from oasis.index.db import open_db
from oasis.index.keyword import MATCH_END, MATCH_START
from oasis.query.snippets import (
    SNIPPET_TOKENS,
    _extract_terms,
    _highlight_terms,
    fts_snippet,
    get_snippet,
    text_snippet,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "test.db")


def _insert_doc(
    conn: sqlite3.Connection,
    *,
    path: str = "/doc.txt",
    title: str = "Doc",
    content: str,
) -> int:
    conn.execute(
        "INSERT INTO documents (path, extension, size, mtime, indexed_at, content_hash, title, content)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (path, ".txt", 100, 1.0, 1.0, "abc123", title, content),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM documents WHERE path = ?", (path,)).fetchone()
    return row["id"]


# ---------------------------------------------------------------------------
# _extract_terms
# ---------------------------------------------------------------------------


def test_extract_terms_simple() -> None:
    assert _extract_terms("fox") == ["fox"]


def test_extract_terms_multiple_words() -> None:
    assert _extract_terms("fox cat") == ["fox", "cat"]


def test_extract_terms_strips_and() -> None:
    result = _extract_terms("fox AND cat")
    assert "AND" not in result
    assert "fox" in result
    assert "cat" in result


def test_extract_terms_strips_or() -> None:
    result = _extract_terms("fox OR cat")
    assert "OR" not in result
    assert "fox" in result


def test_extract_terms_strips_not() -> None:
    result = _extract_terms("fox NOT dog")
    assert "NOT" not in result
    assert "fox" in result


def test_extract_terms_strips_near() -> None:
    result = _extract_terms("NEAR(fox cat)")
    assert "NEAR" not in result


def test_extract_terms_quoted_phrase_expanded() -> None:
    result = _extract_terms('"quick fox"')
    assert "quick" in result
    assert "fox" in result


def test_extract_terms_empty_string() -> None:
    assert _extract_terms("") == []


def test_extract_terms_operators_only() -> None:
    assert _extract_terms("AND OR NOT") == []


def test_extract_terms_preserves_case() -> None:
    result = _extract_terms("Hello World")
    assert "Hello" in result
    assert "World" in result


# ---------------------------------------------------------------------------
# _highlight_terms
# ---------------------------------------------------------------------------


def test_highlight_wraps_match() -> None:
    result = _highlight_terms("hello world", ["world"])
    assert result == f"hello {MATCH_START}world{MATCH_END}"


def test_highlight_case_insensitive() -> None:
    result = _highlight_terms("Hello World", ["hello"])
    assert MATCH_START in result
    assert MATCH_END in result


def test_highlight_multiple_terms() -> None:
    result = _highlight_terms("cat and dog", ["cat", "dog"])
    assert result.count(MATCH_START) == 2


def test_highlight_empty_terms_returns_unchanged() -> None:
    text = "nothing to highlight"
    assert _highlight_terms(text, []) == text


def test_highlight_no_match_returns_unchanged() -> None:
    assert _highlight_terms("hello world", ["xyz"]) == "hello world"


def test_highlight_preserves_surrounding_text() -> None:
    result = _highlight_terms("the quick brown fox", ["quick"])
    assert result.startswith("the ")
    assert result.endswith(" brown fox")


def test_highlight_multiple_occurrences() -> None:
    result = _highlight_terms("fox and fox", ["fox"])
    assert result.count(MATCH_START) == 2


# ---------------------------------------------------------------------------
# fts_snippet
# ---------------------------------------------------------------------------


def test_fts_snippet_constant() -> None:
    assert SNIPPET_TOKENS == 40


def test_fts_snippet_returns_string(conn: sqlite3.Connection) -> None:
    doc_id = _insert_doc(conn, content="The quick brown fox jumps over the lazy dog.")
    result = fts_snippet(conn, "fox", doc_id)
    assert isinstance(result, str)


def test_fts_snippet_contains_match_markers(conn: sqlite3.Connection) -> None:
    doc_id = _insert_doc(conn, content="The quick brown fox jumps over the lazy dog.")
    result = fts_snippet(conn, "fox", doc_id)
    assert result is not None
    assert MATCH_START in result
    assert MATCH_END in result


def test_fts_snippet_contains_matched_term(conn: sqlite3.Connection) -> None:
    doc_id = _insert_doc(conn, content="The quick brown fox jumps over the lazy dog.")
    result = fts_snippet(conn, "fox", doc_id)
    assert result is not None
    assert "fox" in result


def test_fts_snippet_none_when_doc_not_found(conn: sqlite3.Connection) -> None:
    result = fts_snippet(conn, "fox", 9999)
    assert result is None


def test_fts_snippet_none_on_operational_error() -> None:
    conn = MagicMock(spec=sqlite3.Connection)
    conn.execute.side_effect = sqlite3.OperationalError("syntax error")
    result = fts_snippet(conn, "bad query", 1)
    assert result is None


def test_fts_snippet_custom_num_tokens(conn: sqlite3.Connection) -> None:
    long_content = " ".join(["word"] * 100)
    doc_id = _insert_doc(conn, content=long_content)
    result = fts_snippet(conn, "word", doc_id, num_tokens=5)
    assert isinstance(result, str)


def test_fts_snippet_returns_none_for_empty_table(conn: sqlite3.Connection) -> None:
    result = fts_snippet(conn, "anything", 1)
    assert result is None


# ---------------------------------------------------------------------------
# text_snippet
# ---------------------------------------------------------------------------


def test_text_snippet_returns_string() -> None:
    assert isinstance(text_snippet("hello world", "hello"), str)


def test_text_snippet_short_text_no_ellipsis() -> None:
    result = text_snippet("short text", "short")
    assert not result.startswith("…")
    assert not result.endswith("…")


def test_text_snippet_highlights_matched_term() -> None:
    result = text_snippet("the quick brown fox", "fox")
    assert MATCH_START in result
    assert MATCH_END in result


def test_text_snippet_no_match_starts_from_beginning() -> None:
    result = text_snippet("hello world foo bar", "zzz", length=200)
    assert "hello" in result


def test_text_snippet_empty_query_returns_start_of_text() -> None:
    result = text_snippet("hello world", "")
    assert "hello" in result


def test_text_snippet_truncates_long_text() -> None:
    text = "a" * 500
    result = text_snippet(text, "a", length=200)
    clean = result.replace(MATCH_START, "").replace(MATCH_END, "").replace("…", "")
    assert len(clean) <= 200


def test_text_snippet_trailing_ellipsis_when_text_continues() -> None:
    # Match is at the very start; text is much longer than window.
    text = "needle " + "filler " * 100
    result = text_snippet(text, "needle", length=20)
    assert result.endswith("…")


def test_text_snippet_no_leading_ellipsis_when_match_at_start() -> None:
    text = "needle " + "filler " * 100
    result = text_snippet(text, "needle", length=20)
    assert not result.startswith("…")


def test_text_snippet_leading_ellipsis_when_match_is_deep() -> None:
    # Push the match far into the text so the window doesn't start at 0.
    text = "filler " * 50 + "needle " + "filler " * 50
    result = text_snippet(text, "needle", length=20)
    assert result.startswith("…")


def test_text_snippet_no_trailing_ellipsis_when_text_fits() -> None:
    text = "the fox"
    result = text_snippet(text, "fox", length=200)
    assert not result.endswith("…")


# ---------------------------------------------------------------------------
# get_snippet
# ---------------------------------------------------------------------------


def test_get_snippet_returns_string(conn: sqlite3.Connection) -> None:
    doc_id = _insert_doc(conn, content="test content here")
    result = get_snippet(conn, "test", doc_id, "fallback")
    assert isinstance(result, str)


def test_get_snippet_uses_fts_when_available(conn: sqlite3.Connection) -> None:
    doc_id = _insert_doc(conn, content="The quick brown fox jumps over the lazy dog.")
    result = get_snippet(conn, "fox", doc_id, "fallback text")
    assert MATCH_START in result
    assert "fox" in result


def test_get_snippet_falls_back_when_doc_not_in_index(conn: sqlite3.Connection) -> None:
    result = get_snippet(conn, "word", 9999, "fallback text with word here")
    assert "fallback" in result or "word" in result


def test_get_snippet_fallback_highlights_terms(conn: sqlite3.Connection) -> None:
    result = get_snippet(conn, "word", 9999, "fallback text with word here")
    assert MATCH_START in result


def test_get_snippet_falls_back_on_operational_error() -> None:
    conn = MagicMock(spec=sqlite3.Connection)
    conn.execute.side_effect = sqlite3.OperationalError("error")
    result = get_snippet(conn, "query", 1, "the fallback text with query")
    assert isinstance(result, str)
    assert "fallback" in result
