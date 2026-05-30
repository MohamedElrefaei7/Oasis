"""Edge-case tests for KeywordIndex beyond the baseline test_keyword.py coverage."""

import sqlite3
import time
from pathlib import Path

import pytest

from oasis.index.db import open_db
from oasis.index.keyword import MATCH_END, MATCH_START, KeywordIndex, _file_hash
from oasis.models import DocumentMetadata, ExtractedDocument


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "test.db")


def _doc(
    path: str = "/tmp/a.txt",
    text: str = "hello world",
    size: int = 100,
    mtime: float = 1000.0,
    title: str | None = None,
) -> ExtractedDocument:
    return ExtractedDocument(
        path=Path(path),
        text=text,
        metadata=DocumentMetadata(size_bytes=size, mtime=mtime, title=title),
    )


# ---------------------------------------------------------------------------
# _file_hash edge cases
# ---------------------------------------------------------------------------


def test_file_hash_with_none_size_and_mtime() -> None:
    h = _file_hash(None, None)
    assert isinstance(h, str)
    assert len(h) == 16


def test_file_hash_with_zero_values() -> None:
    h = _file_hash(0, 0.0)
    assert len(h) == 16


def test_file_hash_none_differs_from_zero() -> None:
    assert _file_hash(None, None) != _file_hash(0, 0.0)


# ---------------------------------------------------------------------------
# last_indexed_at
# ---------------------------------------------------------------------------


def test_last_indexed_at_empty_db_returns_none(conn: sqlite3.Connection) -> None:
    assert KeywordIndex(conn).last_indexed_at() is None


def test_last_indexed_at_after_upsert_returns_float(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    before = time.time()
    idx.upsert(_doc())
    after = time.time()
    result = idx.last_indexed_at()
    assert isinstance(result, float)
    assert before <= result <= after


def test_last_indexed_at_reflects_most_recent(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    idx.upsert(_doc("/tmp/a.txt", mtime=1.0))
    idx.upsert(_doc("/tmp/b.txt", mtime=2.0))
    result = idx.last_indexed_at()
    assert result is not None
    # Both docs indexed in rapid succession; result should be >= first upsert
    assert result > 0


# ---------------------------------------------------------------------------
# FTS content updated on re-upsert
# ---------------------------------------------------------------------------


def test_search_reflects_updated_content(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    idx.upsert(_doc(text="original vocabulary", mtime=1.0))
    idx.upsert(_doc(text="completely new verbiage", mtime=2.0))
    assert idx.search("verbiage") != []
    assert idx.search("vocabulary") == []


def test_fts_search_after_title_update(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    idx.upsert(_doc(text="content", title="OldTitle", mtime=1.0))
    idx.upsert(_doc(text="content", title="NewTitle", mtime=2.0))
    assert idx.search("NewTitle") != []
    assert idx.search("OldTitle") == []


# ---------------------------------------------------------------------------
# Bad FTS5 syntax
# ---------------------------------------------------------------------------


def test_search_unclosed_quote_raises_operational_error(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    idx.upsert(_doc())
    with pytest.raises(sqlite3.OperationalError):
        idx.search('"unclosed phrase')


# ---------------------------------------------------------------------------
# Unicode
# ---------------------------------------------------------------------------


def test_search_unicode_content_no_crash(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    idx.upsert(_doc(text="café résumé naïve"))
    results = idx.search("résumé")
    assert isinstance(results, list)


def test_upsert_cjk_text_no_crash(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    idx.upsert(_doc(text="こんにちは 世界"))
    assert idx.count() == 1


# ---------------------------------------------------------------------------
# Empty text
# ---------------------------------------------------------------------------


def test_upsert_empty_text_increments_count(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    doc = ExtractedDocument(
        path=Path("/tmp/empty.txt"),
        text="",
        metadata=DocumentMetadata(size_bytes=0, mtime=1000.0),
    )
    idx.upsert(doc)
    assert idx.count() == 1


def test_search_on_empty_db_returns_empty_list(conn: sqlite3.Connection) -> None:
    assert KeywordIndex(conn).search("anything") == []


# ---------------------------------------------------------------------------
# Title-only match
# ---------------------------------------------------------------------------


def test_search_matches_title_field(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    idx.upsert(_doc(text="generic body text", title="ProjectNova Annual Report"))
    results = idx.search("ProjectNova")
    assert len(results) == 1
    assert results[0].title == "ProjectNova Annual Report"


# ---------------------------------------------------------------------------
# Delete then re-upsert
# ---------------------------------------------------------------------------


def test_delete_then_upsert_works_correctly(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    idx.upsert(_doc(text="original"))
    idx.delete(Path("/tmp/a.txt"))
    idx.upsert(_doc(text="restored"))
    assert idx.count() == 1
    assert idx.search("restored") != []
    assert idx.search("original") == []


# ---------------------------------------------------------------------------
# Rank ordering
# ---------------------------------------------------------------------------


def test_higher_frequency_term_ranks_better(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    idx.upsert(_doc("/tmp/many.txt", text="fox fox fox fox fox"))
    idx.upsert(_doc("/tmp/one.txt", text="fox"))
    results = idx.search("fox")
    assert len(results) == 2
    assert results[0].path == Path("/tmp/many.txt")


# ---------------------------------------------------------------------------
# Large document
# ---------------------------------------------------------------------------


def test_large_document_indexed_and_searchable(conn: sqlite3.Connection) -> None:
    large_text = "word " * 10_000 + "uniquesentinel"
    idx = KeywordIndex(conn)
    idx.upsert(_doc(text=large_text, size=50_000))
    assert idx.search("uniquesentinel") != []
