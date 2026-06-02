import sqlite3
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
# _file_hash
# ---------------------------------------------------------------------------


def test_file_hash_deterministic() -> None:
    assert _file_hash(100, 1000.0) == _file_hash(100, 1000.0)


def test_file_hash_differs_on_size() -> None:
    assert _file_hash(100, 1000.0) != _file_hash(200, 1000.0)


def test_file_hash_differs_on_mtime() -> None:
    assert _file_hash(100, 1000.0) != _file_hash(100, 1001.0)


def test_file_hash_length() -> None:
    assert len(_file_hash(100, 1000.0)) == 16


# ---------------------------------------------------------------------------
# is_unchanged
# ---------------------------------------------------------------------------


def test_is_unchanged_new_file_returns_false(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    assert not idx.is_unchanged(Path("/tmp/new.txt"), size=100, mtime=1000.0)


def test_is_unchanged_after_upsert_returns_true(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    doc = _doc(size=100, mtime=1000.0)
    idx.upsert(doc)
    assert idx.is_unchanged(doc.path, size=100, mtime=1000.0)


def test_is_unchanged_after_mtime_change_returns_false(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    doc = _doc(size=100, mtime=1000.0)
    idx.upsert(doc)
    assert not idx.is_unchanged(doc.path, size=100, mtime=9999.0)


def test_is_unchanged_after_size_change_returns_false(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    doc = _doc(size=100, mtime=1000.0)
    idx.upsert(doc)
    assert not idx.is_unchanged(doc.path, size=999, mtime=1000.0)


def test_is_unchanged_both_changed_returns_false(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    doc = _doc(size=100, mtime=1000.0)
    idx.upsert(doc)
    assert not idx.is_unchanged(doc.path, size=200, mtime=2000.0)


# ---------------------------------------------------------------------------
# count
# ---------------------------------------------------------------------------


def test_count_empty(conn: sqlite3.Connection) -> None:
    assert KeywordIndex(conn).count() == 0


def test_count_after_upserts(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    idx.upsert(_doc("/tmp/a.txt"))
    idx.upsert(_doc("/tmp/b.txt"))
    assert idx.count() == 2


def test_count_upsert_same_path_no_duplicate(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    idx.upsert(_doc("/tmp/a.txt", mtime=1.0))
    idx.upsert(_doc("/tmp/a.txt", mtime=2.0))
    assert idx.count() == 1


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_removes_document(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    doc = _doc()
    idx.upsert(doc)
    idx.delete(doc.path)
    assert idx.count() == 0


def test_delete_removes_from_fts(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    doc = _doc(text="unique_term_xyz")
    idx.upsert(doc)
    idx.delete(doc.path)
    results = idx.search("unique_term_xyz")
    assert results == []


def test_delete_nonexistent_path_is_noop(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    idx.delete(Path("/tmp/nonexistent.txt"))
    assert idx.count() == 0


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def test_search_returns_match(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    idx.upsert(_doc(text="the quick brown fox"))
    results = idx.search("fox")
    assert len(results) == 1
    assert results[0].path == Path("/tmp/a.txt")


def test_search_no_match_returns_empty(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    idx.upsert(_doc(text="hello world"))
    assert idx.search("zzznomatch") == []


def test_search_porter_stemming(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    idx.upsert(_doc(text="the indexer is indexing files"))
    results = idx.search("index")
    assert len(results) == 1


def test_search_snippet_contains_sentinels(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    idx.upsert(_doc(text="the quick brown fox jumps"))
    results = idx.search("fox")
    assert MATCH_START in results[0].snippet
    assert MATCH_END in results[0].snippet


def test_search_title_field_populated(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    idx.upsert(_doc(text="content here", title="My Document"))
    results = idx.search("content")
    assert results[0].title == "My Document"


def test_search_limit_respected(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    for i in range(10):
        idx.upsert(_doc(f"/tmp/file{i}.txt", text="common keyword"))
    results = idx.search("keyword", limit=3)
    assert len(results) <= 3


def test_search_rank_is_float(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    idx.upsert(_doc(text="ranking test content"))
    results = idx.search("ranking")
    assert isinstance(results[0].rank, float)


# ---------------------------------------------------------------------------
# search — structured filters (4.5)
# ---------------------------------------------------------------------------


def test_search_after_excludes_older_docs(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    idx.upsert(_doc("/tmp/old.txt", text="budget report", mtime=1000.0))
    idx.upsert(_doc("/tmp/new.txt", text="budget report", mtime=9000.0))
    results = idx.search("budget", after=5000.0)
    paths = [str(r.path) for r in results]
    assert "/tmp/new.txt" in paths
    assert "/tmp/old.txt" not in paths


def test_search_before_excludes_newer_docs(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    idx.upsert(_doc("/tmp/old.txt", text="budget report", mtime=1000.0))
    idx.upsert(_doc("/tmp/new.txt", text="budget report", mtime=9000.0))
    results = idx.search("budget", before=5000.0)
    paths = [str(r.path) for r in results]
    assert "/tmp/old.txt" in paths
    assert "/tmp/new.txt" not in paths


def test_search_after_and_before_window(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    idx.upsert(_doc("/tmp/a.txt", text="invoice", mtime=1000.0))
    idx.upsert(_doc("/tmp/b.txt", text="invoice", mtime=5000.0))
    idx.upsert(_doc("/tmp/c.txt", text="invoice", mtime=9000.0))
    results = idx.search("invoice", after=2000.0, before=7000.0)
    paths = [str(r.path) for r in results]
    assert "/tmp/b.txt" in paths
    assert "/tmp/a.txt" not in paths
    assert "/tmp/c.txt" not in paths


def test_search_folders_prefix_filter(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    idx.upsert(_doc("/home/alice/docs/report.txt", text="quarterly results"))
    idx.upsert(_doc("/home/bob/report.txt", text="quarterly results"))
    results = idx.search("quarterly", folders=["/home/alice"])
    paths = [str(r.path) for r in results]
    assert "/home/alice/docs/report.txt" in paths
    assert "/home/bob/report.txt" not in paths


def test_search_folders_multiple_prefixes(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    idx.upsert(_doc("/home/alice/a.txt", text="notes here"))
    idx.upsert(_doc("/home/bob/b.txt", text="notes here"))
    idx.upsert(_doc("/tmp/c.txt", text="notes here"))
    results = idx.search("notes", folders=["/home/alice", "/home/bob"])
    paths = [str(r.path) for r in results]
    assert "/home/alice/a.txt" in paths
    assert "/home/bob/b.txt" in paths
    assert "/tmp/c.txt" not in paths


def test_search_extensions_filter(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    idx.upsert(_doc("/tmp/doc.pdf", text="contract details"))
    idx.upsert(_doc("/tmp/doc.docx", text="contract details"))
    results = idx.search("contract", extensions=[".pdf"])
    paths = [str(r.path) for r in results]
    assert "/tmp/doc.pdf" in paths
    assert "/tmp/doc.docx" not in paths


def test_search_no_filters_returns_all_matches(conn: sqlite3.Connection) -> None:
    idx = KeywordIndex(conn)
    idx.upsert(_doc("/tmp/a.txt", text="searchterm", mtime=1000.0))
    idx.upsert(_doc("/tmp/b.txt", text="searchterm", mtime=9000.0))
    results = idx.search("searchterm")
    assert len(results) == 2
