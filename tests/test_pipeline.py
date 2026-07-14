import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from oasis.extractors.text import TextExtractor
from oasis.index.db import open_db
from oasis.index.keyword import KeywordIndex
from oasis.index.pipeline import EMBED_BATCH, index_directory
from oasis.index.vector import VectorIndex


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    # Store the DB under a dotdir so the walker (exclude_dotfiles=True by default) ignores it.
    return open_db(tmp_path / ".db" / "test.db")


# ---------------------------------------------------------------------------
# Stats dict contract
# ---------------------------------------------------------------------------


def test_returns_all_stat_keys(conn: sqlite3.Connection, tmp_path: Path) -> None:
    stats = index_directory(conn, tmp_path)
    assert set(stats.keys()) == {
        "indexed", "skipped", "failed", "unsupported", "permission_denied", "chunks",
    }


def test_empty_directory_all_zeros(conn: sqlite3.Connection, tmp_path: Path) -> None:
    stats = index_directory(conn, tmp_path)
    assert stats == {
        "indexed": 0, "skipped": 0, "failed": 0,
        "unsupported": 0, "permission_denied": 0, "chunks": 0,
    }


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


# ---------------------------------------------------------------------------
# chunks key — always present
# ---------------------------------------------------------------------------


def test_chunks_zero_without_embedder(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / "doc.txt").write_text("hello world")
    vec_idx = MagicMock(spec=VectorIndex)
    stats = index_directory(conn, tmp_path, vector_index=vec_idx)
    assert stats["chunks"] == 0


def test_chunks_zero_without_vector_index(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / "doc.txt").write_text("hello world")
    emb = _fake_embedder()
    stats = index_directory(conn, tmp_path, embedder=emb)
    assert stats["chunks"] == 0


# ---------------------------------------------------------------------------
# Helpers for vector tests
# ---------------------------------------------------------------------------


def _fake_embedder(dim: int = 4) -> MagicMock:
    m = MagicMock()
    m.dimension = dim
    m.embed.side_effect = lambda texts: np.zeros((len(texts), dim), dtype=np.float32)
    return m


def _fake_vector_index() -> MagicMock:
    return MagicMock(spec=VectorIndex)


# ---------------------------------------------------------------------------
# embed called / vector upsert called
# ---------------------------------------------------------------------------


def test_embed_called_for_indexed_file(conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / "doc.txt").write_text("hello world")
    emb = _fake_embedder()
    index_directory(conn, tmp_path, vector_index=_fake_vector_index(), embedder=emb)
    emb.embed.assert_called()


def test_embed_not_called_when_no_files_indexed(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    emb = _fake_embedder()
    index_directory(conn, tmp_path, vector_index=_fake_vector_index(), embedder=emb)
    emb.embed.assert_not_called()


def test_vector_upsert_called_for_indexed_file(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    (tmp_path / "doc.txt").write_text("hello world")
    vec_idx = _fake_vector_index()
    index_directory(conn, tmp_path, vector_index=vec_idx, embedder=_fake_embedder())
    vec_idx.upsert_chunks.assert_called()


def test_vector_delete_called_before_upsert(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    (tmp_path / "doc.txt").write_text("hello world")
    vec_idx = _fake_vector_index()
    call_order: list[str] = []
    vec_idx.delete_by_doc_id.side_effect = lambda _: call_order.append("delete")
    vec_idx.upsert_chunks.side_effect = lambda _: call_order.append("upsert")
    index_directory(conn, tmp_path, vector_index=vec_idx, embedder=_fake_embedder())
    assert call_order.index("delete") < call_order.index("upsert")


# ---------------------------------------------------------------------------
# chunks stat
# ---------------------------------------------------------------------------


def test_chunks_stat_equals_chunks_produced(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    (tmp_path / "doc.txt").write_text("hello world")
    stats = index_directory(
        conn, tmp_path,
        vector_index=_fake_vector_index(),
        embedder=_fake_embedder(),
    )
    # "hello world" is a short text → exactly 1 chunk
    assert stats["chunks"] == 1


def test_chunks_stat_sums_across_docs(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    (tmp_path / "a.txt").write_text("hello world")
    (tmp_path / "b.txt").write_text("foo bar baz")
    stats = index_directory(
        conn, tmp_path,
        vector_index=_fake_vector_index(),
        embedder=_fake_embedder(),
    )
    assert stats["chunks"] == 2


# ---------------------------------------------------------------------------
# on_chunks_progress callback
# ---------------------------------------------------------------------------


def test_on_chunks_progress_not_called_without_embedding(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    (tmp_path / "doc.txt").write_text("hello world")
    calls: list[tuple[int, int]] = []
    index_directory(conn, tmp_path, on_chunks_progress=lambda d, t: calls.append((d, t)))
    assert calls == []


def test_on_chunks_progress_first_call_done_zero(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    (tmp_path / "doc.txt").write_text("hello world")
    calls: list[tuple[int, int]] = []
    index_directory(
        conn, tmp_path,
        vector_index=_fake_vector_index(),
        embedder=_fake_embedder(),
        on_chunks_progress=lambda d, t: calls.append((d, t)),
    )
    assert calls[0][0] == 0


def test_on_chunks_progress_last_call_done_equals_total(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    (tmp_path / "doc.txt").write_text("hello world")
    calls: list[tuple[int, int]] = []
    index_directory(
        conn, tmp_path,
        vector_index=_fake_vector_index(),
        embedder=_fake_embedder(),
        on_chunks_progress=lambda d, t: calls.append((d, t)),
    )
    done, total = calls[-1]
    assert done == total


def test_on_chunks_progress_total_matches_chunks_stat(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    (tmp_path / "doc.txt").write_text("hello world")
    calls: list[tuple[int, int]] = []
    stats = index_directory(
        conn, tmp_path,
        vector_index=_fake_vector_index(),
        embedder=_fake_embedder(),
        on_chunks_progress=lambda d, t: calls.append((d, t)),
    )
    assert calls[0][1] == stats["chunks"]


# ---------------------------------------------------------------------------
# Skipped files are not re-embedded
# ---------------------------------------------------------------------------


def test_skipped_file_not_re_embedded(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    (tmp_path / "doc.txt").write_text("hello world")
    emb = _fake_embedder()
    vec_idx = _fake_vector_index()

    index_directory(conn, tmp_path, vector_index=vec_idx, embedder=emb)
    first_embed_count = emb.embed.call_count

    index_directory(conn, tmp_path, vector_index=vec_idx, embedder=emb)
    assert emb.embed.call_count == first_embed_count  # no new embed calls


# ---------------------------------------------------------------------------
# permission_denied — distinct from failed
#
# Folded into `failed`, a Full Disk Access denial is indistinguishable from a
# corrupt PDF, and the app can only say "indexed 0 files" instead of offering
# the fix.  PermissionError is an OSError, so it must be caught ahead of the
# broad handlers or it lands in `failed` silently.
# ---------------------------------------------------------------------------


def test_permission_denied_key_always_present(conn: sqlite3.Connection, tmp_path: Path) -> None:
    stats = index_directory(conn, tmp_path)
    assert stats["permission_denied"] == 0


def test_unreadable_file_counted_as_permission_denied_not_failed(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    (tmp_path / "ok.txt").write_text("readable")
    denied = tmp_path / "denied.txt"
    denied.write_text("secret")
    denied.chmod(0o000)
    try:
        stats = index_directory(conn, tmp_path)
    finally:
        denied.chmod(0o644)

    assert stats["permission_denied"] == 1
    assert stats["failed"] == 0
    assert stats["indexed"] == 1


def test_unreadable_directory_counted_not_silently_skipped(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    # The macOS Full Disk Access case: the denial is at the DIRECTORY level.
    # os.walk swallows it unless onerror is wired up, so without the walker's
    # on_error hook this returns indexed=0, permission_denied=0 — an empty
    # index that looks identical to an empty folder.
    (tmp_path / "ok.txt").write_text("readable")
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "secret.txt").write_text("secret")
    locked.chmod(0o000)
    try:
        stats = index_directory(conn, tmp_path)
    finally:
        locked.chmod(0o755)

    assert stats["permission_denied"] == 1
    assert stats["indexed"] == 1


def test_permission_denied_reported_via_on_file(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    denied = tmp_path / "denied.txt"
    denied.write_text("secret")
    denied.chmod(0o000)
    seen: list[tuple[Path, str]] = []
    try:
        index_directory(conn, tmp_path, on_file=lambda p, s: seen.append((p, s)))
    finally:
        denied.chmod(0o644)

    assert ("permission_denied") in [s for _, s in seen]


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------


def test_cancel_before_start_indexes_nothing(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    import threading

    for i in range(5):
        (tmp_path / f"f{i}.txt").write_text(f"content {i}")
    cancel = threading.Event()
    cancel.set()

    stats = index_directory(conn, tmp_path, cancel=cancel)

    assert stats["indexed"] == 0


def test_cancel_midway_returns_partial_stats(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    import threading

    for i in range(10):
        (tmp_path / f"f{i:02d}.txt").write_text(f"content {i}")
    cancel = threading.Event()

    def on_file(path: Path, status: str) -> None:
        if status == "indexed":
            cancel.set()  # stop after the very first indexed file

    stats = index_directory(conn, tmp_path, cancel=cancel, on_file=on_file)

    assert stats["indexed"] == 1
    assert stats["indexed"] < 10


def test_cancel_none_indexes_everything(conn: sqlite3.Connection, tmp_path: Path) -> None:
    for i in range(5):
        (tmp_path / f"f{i}.txt").write_text(f"content {i}")

    stats = index_directory(conn, tmp_path, cancel=None)

    assert stats["indexed"] == 5


def test_cancel_committed_work_persists(conn: sqlite3.Connection, tmp_path: Path) -> None:
    # Indexing is incremental — a cancelled run just means the next one resumes.
    import threading

    for i in range(10):
        (tmp_path / f"f{i:02d}.txt").write_text(f"content {i}")
    cancel = threading.Event()

    def on_file(path: Path, status: str) -> None:
        if status == "indexed":
            cancel.set()

    index_directory(conn, tmp_path, cancel=cancel, on_file=on_file)
    assert KeywordIndex(conn).count() == 1

    stats = index_directory(conn, tmp_path)  # resume, no cancel
    assert stats["indexed"] == 9
    assert stats["skipped"] == 1
    assert KeywordIndex(conn).count() == 10
