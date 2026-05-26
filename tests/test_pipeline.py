import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from oasis.extractors.text import TextExtractor
from oasis.index.db import open_db
from oasis.index.keyword import KeywordIndex
from oasis.index.pipeline import index_directory


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    # Store the DB under a dotdir so the walker (exclude_dotfiles=True by default) ignores it.
    return open_db(tmp_path / ".db" / "test.db")


# ---------------------------------------------------------------------------
# Stats dict contract
# ---------------------------------------------------------------------------


def test_returns_all_stat_keys(conn: sqlite3.Connection, tmp_path: Path) -> None:
    stats = index_directory(conn, tmp_path)
    assert set(stats.keys()) == {"indexed", "skipped", "failed", "unsupported"}


def test_empty_directory_all_zeros(conn: sqlite3.Connection, tmp_path: Path) -> None:
    stats = index_directory(conn, tmp_path)
    assert stats == {"indexed": 0, "skipped": 0, "failed": 0, "unsupported": 0}


# ---------------------------------------------------------------------------
# Indexed
# ---------------------------------------------------------------------------


def test_indexes_supported_file(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / "doc.txt").write_text("hello world")
    stats = index_directory(conn, tmp_path)
    assert stats["indexed"] == 1
    assert stats["failed"] == 0


def test_multiple_files_all_indexed(conn: sqlite3.Connection, tmp_path: Path) -> None:
    for i in range(3):
        (tmp_path / f"doc{i}.txt").write_text(f"content {i}")
    stats = index_directory(conn, tmp_path)
    assert stats["indexed"] == 3


def test_indexed_file_appears_in_db(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / "doc.txt").write_text("searchable content")
    index_directory(conn, tmp_path)
    assert KeywordIndex(conn).count() == 1


# ---------------------------------------------------------------------------
# Skipped (change detection)
# ---------------------------------------------------------------------------


def test_unchanged_file_skipped_on_second_run(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / "doc.txt").write_text("hello")
    index_directory(conn, tmp_path)
    stats = index_directory(conn, tmp_path)
    assert stats["skipped"] == 1
    assert stats["indexed"] == 0


def test_force_reindexes_unchanged_file(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / "doc.txt").write_text("hello")
    index_directory(conn, tmp_path)
    stats = index_directory(conn, tmp_path, force=True)
    assert stats["indexed"] == 1
    assert stats["skipped"] == 0


# ---------------------------------------------------------------------------
# Unsupported
# ---------------------------------------------------------------------------


def test_unknown_extension_counted_as_unsupported(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / "data.xyz123").write_text("irrelevant")
    stats = index_directory(conn, tmp_path)
    assert stats["unsupported"] == 1
    assert stats["indexed"] == 0


def test_mixed_supported_and_unsupported(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / "doc.txt").write_text("hi")
    (tmp_path / "data.xyz").write_text("nope")
    stats = index_directory(conn, tmp_path)
    assert stats["indexed"] == 1
    assert stats["unsupported"] == 1


# ---------------------------------------------------------------------------
# Failed — stat error
# ---------------------------------------------------------------------------


def test_broken_symlink_counts_as_failed(conn: sqlite3.Connection, tmp_path: Path) -> None:
    # A symlink whose target doesn't exist causes path.stat() to raise OSError.
    link = tmp_path / "ghost.txt"
    link.symlink_to(tmp_path / "nonexistent.txt")
    stats = index_directory(conn, tmp_path)
    assert stats["failed"] == 1
    assert stats["indexed"] == 0


# ---------------------------------------------------------------------------
# Failed — extractor errors (never crash the run)
# ---------------------------------------------------------------------------


def test_extractor_returning_none_counts_as_failed(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / "doc.txt").write_text("hi")
    with patch.object(TextExtractor, "extract", return_value=None):
        stats = index_directory(conn, tmp_path)
    assert stats["failed"] == 1
    assert stats["indexed"] == 0


def test_extractor_raising_counts_as_failed(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / "doc.txt").write_text("hi")
    with patch.object(TextExtractor, "extract", side_effect=RuntimeError("boom")):
        stats = index_directory(conn, tmp_path)
    assert stats["failed"] == 1
    assert stats["indexed"] == 0


def test_upsert_raising_counts_as_failed(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / "doc.txt").write_text("hi")
    with patch.object(KeywordIndex, "upsert", side_effect=sqlite3.OperationalError("disk full")):
        stats = index_directory(conn, tmp_path)
    assert stats["failed"] == 1
    assert stats["indexed"] == 0


def test_one_failure_does_not_stop_other_files(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / "good.txt").write_text("fine")
    (tmp_path / "bad.txt").write_text("also fine")

    original_extract = TextExtractor.extract

    def flaky_extract(self: TextExtractor, path: Path):  # noqa: ANN001
        if path.name == "bad.txt":
            raise RuntimeError("corrupt")
        return original_extract(self, path)

    with patch.object(TextExtractor, "extract", flaky_extract):
        stats = index_directory(conn, tmp_path)

    assert stats["indexed"] == 1
    assert stats["failed"] == 1


# ---------------------------------------------------------------------------
# on_file callback
# ---------------------------------------------------------------------------


def test_on_file_called_for_each_file(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    calls: list[tuple[Path, str]] = []
    index_directory(conn, tmp_path, on_file=lambda p, s: calls.append((p, s)))
    assert len(calls) == 2


def test_on_file_status_values(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / "doc.txt").write_text("hi")
    (tmp_path / "data.xyz").write_text("nope")
    statuses: list[str] = []
    index_directory(conn, tmp_path, on_file=lambda p, s: statuses.append(s))
    assert "indexed" in statuses
    assert "unsupported" in statuses


def test_on_file_skipped_status_on_second_run(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / "doc.txt").write_text("hi")
    index_directory(conn, tmp_path)
    statuses: list[str] = []
    index_directory(conn, tmp_path, on_file=lambda p, s: statuses.append(s))
    assert statuses == ["skipped"]


def test_on_file_none_is_safe(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / "doc.txt").write_text("hi")
    stats = index_directory(conn, tmp_path, on_file=None)
    assert stats["indexed"] == 1
