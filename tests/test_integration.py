"""
End-to-end integration tests: real files on disk → index_directory → KeywordIndex.search.

Unit tests cover each layer in isolation with synthetic data.  These tests
verify the layers compose correctly: the walker finds the right files, the
extractors pull out text, the pipeline stores it, and search returns it.
"""

import os
import sqlite3
from pathlib import Path

import pytest

from oasis.index.db import open_db
from oasis.index.keyword import MATCH_START, KeywordIndex
from oasis.index.pipeline import index_directory


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / ".db" / "index.db")


# ---------------------------------------------------------------------------
# Store and retrieve
# ---------------------------------------------------------------------------


def test_indexed_content_is_searchable(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / "doc.txt").write_text("the quick brown fox jumps over the lazy dog")
    index_directory(conn, tmp_path)
    assert len(KeywordIndex(conn).search("fox")) == 1


def test_search_returns_path_of_indexed_file(conn: sqlite3.Connection, tmp_path: Path) -> None:
    f = tmp_path / "notes.txt"
    f.write_text("machine learning is powerful")
    index_directory(conn, tmp_path)
    results = KeywordIndex(conn).search("learning")
    assert results[0].path.resolve() == f.resolve()


def test_search_snippet_marks_matched_term(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / "doc.txt").write_text("deep learning transforms many fields")
    index_directory(conn, tmp_path)
    results = KeywordIndex(conn).search("learning")
    assert MATCH_START in results[0].snippet


def test_multiple_docs_only_matching_returned(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("python is great for data science")
    (tmp_path / "b.txt").write_text("python runs fast with numpy")
    (tmp_path / "c.txt").write_text("completely unrelated content about cooking")
    index_directory(conn, tmp_path)
    results = KeywordIndex(conn).search("python")
    assert len(results) == 2


def test_no_match_returns_empty_list(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / "doc.txt").write_text("hello world")
    index_directory(conn, tmp_path)
    assert KeywordIndex(conn).search("zzznomatch") == []


def test_porter_stemming_end_to_end(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / "doc.txt").write_text("the program is currently indexing all documents")
    index_directory(conn, tmp_path)
    # "index" should match "indexing" via the porter tokenizer
    assert len(KeywordIndex(conn).search("index")) == 1


def test_markdown_file_indexed_and_searchable(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / "readme.md").write_text("# Setup\n\nInstall all required dependencies first.")
    index_directory(conn, tmp_path)
    assert len(KeywordIndex(conn).search("dependencies")) == 1


def test_nested_file_indexed_and_searchable(conn: sqlite3.Connection, tmp_path: Path) -> None:
    sub = tmp_path / "docs" / "api"
    sub.mkdir(parents=True)
    (sub / "reference.txt").write_text("complete API reference documentation")
    index_directory(conn, tmp_path)
    assert len(KeywordIndex(conn).search("reference")) == 1


def test_upsert_does_not_create_duplicates(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / "doc.txt").write_text("hello world")
    index_directory(conn, tmp_path)
    index_directory(conn, tmp_path, force=True)
    assert KeywordIndex(conn).count() == 1


# ---------------------------------------------------------------------------
# Walker exclusions through the full pipeline
# ---------------------------------------------------------------------------


def test_excluded_dir_files_not_indexed(conn: sqlite3.Connection, tmp_path: Path) -> None:
    excluded = tmp_path / "node_modules"
    excluded.mkdir()
    (excluded / "bundle.txt").write_text("bundled noise content")
    (tmp_path / "app.txt").write_text("real app content")
    stats = index_directory(conn, tmp_path)
    assert stats["indexed"] == 1
    assert KeywordIndex(conn).search("bundled") == []


def test_dotfile_not_indexed_by_default(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET_KEY=abc123")
    (tmp_path / "app.txt").write_text("normal app content")
    stats = index_directory(conn, tmp_path)
    assert stats["indexed"] == 1
    assert KeywordIndex(conn).search("SECRET_KEY") == []


def test_dotdir_contents_not_indexed(conn: sqlite3.Connection, tmp_path: Path) -> None:
    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    # Use a term that cannot appear in any filesystem path component
    (hidden / "secret.txt").write_text("xyzHIDDENTERM xyzHIDDENTERM")
    (tmp_path / "public.txt").write_text("public content here")
    stats = index_directory(conn, tmp_path)
    assert stats["indexed"] == 1
    assert KeywordIndex(conn).search("xyzHIDDENTERM") == []


def test_gitignore_pattern_excluded_from_index(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("*.log\n")
    (tmp_path / "app.log").write_text("verbose log output data")
    (tmp_path / "app.txt").write_text("application source content")
    stats = index_directory(conn, tmp_path)
    assert stats["indexed"] == 1
    assert KeywordIndex(conn).search("verbose") == []


def test_extra_excludes_forwarded_to_walker(conn: sqlite3.Connection, tmp_path: Path) -> None:
    # Use a term that cannot appear in any filesystem path component
    (tmp_path / "credentials.key").write_text("xyzCREDENTIALTERM xyzCREDENTIALTERM")
    (tmp_path / "readme.txt").write_text("public readme content")
    stats = index_directory(conn, tmp_path, extra_excludes=["*.key"])
    assert stats["indexed"] == 1
    assert KeywordIndex(conn).search("xyzCREDENTIALTERM") == []


# ---------------------------------------------------------------------------
# Incremental re-indexing
# ---------------------------------------------------------------------------


def test_unchanged_file_skipped_on_second_run(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / "doc.txt").write_text("initial content")
    index_directory(conn, tmp_path)
    stats = index_directory(conn, tmp_path)
    assert stats["skipped"] == 1
    assert stats["indexed"] == 0


def test_mtime_change_triggers_reindex(conn: sqlite3.Connection, tmp_path: Path) -> None:
    f = tmp_path / "doc.txt"
    f.write_text("some content")
    index_directory(conn, tmp_path)

    # Advance mtime by one second without changing content or size
    t = f.stat().st_mtime + 1
    os.utime(f, (t, t))

    stats = index_directory(conn, tmp_path)
    assert stats["indexed"] == 1
    assert stats["skipped"] == 0


def test_size_change_triggers_reindex(conn: sqlite3.Connection, tmp_path: Path) -> None:
    f = tmp_path / "doc.txt"
    f.write_text("short")
    index_directory(conn, tmp_path)

    # Write longer content (size changes, hash changes)
    f.write_text("much longer content that is clearly different in length")

    stats = index_directory(conn, tmp_path)
    assert stats["indexed"] == 1
    assert stats["skipped"] == 0


def test_updated_content_replaces_old_in_search(conn: sqlite3.Connection, tmp_path: Path) -> None:
    f = tmp_path / "doc.txt"
    f.write_text("original vocabulary words")
    index_directory(conn, tmp_path)

    f.write_text("completely new verbiage after editing the file")
    t = f.stat().st_mtime + 1
    os.utime(f, (t, t))
    index_directory(conn, tmp_path)

    assert len(KeywordIndex(conn).search("verbiage")) == 1
    assert KeywordIndex(conn).search("vocabulary") == []


def test_new_file_picked_up_on_second_run(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / "first.txt").write_text("first document content")
    index_directory(conn, tmp_path)

    (tmp_path / "second.txt").write_text("second document added later")
    stats = index_directory(conn, tmp_path)

    assert stats["indexed"] == 1   # only the new file
    assert stats["skipped"] == 1   # first.txt unchanged
    assert KeywordIndex(conn).count() == 2


def test_force_flag_reindexes_all_files(conn: sqlite3.Connection, tmp_path: Path) -> None:
    for i in range(3):
        (tmp_path / f"doc{i}.txt").write_text(f"document {i}")
    index_directory(conn, tmp_path)
    stats = index_directory(conn, tmp_path, force=True)
    assert stats["indexed"] == 3
    assert stats["skipped"] == 0
