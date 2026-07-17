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
        "indexed", "skipped", "failed", "unsupported", "permission_denied", "chunks", "removed",
    }


def test_empty_directory_all_zeros(conn: sqlite3.Connection, tmp_path: Path) -> None:
    stats = index_directory(conn, tmp_path)
    assert stats == {
        "indexed": 0, "skipped": 0, "failed": 0,
        "unsupported": 0, "permission_denied": 0, "chunks": 0, "removed": 0,
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
    # Stateful: doc_ids_with_vectors reports exactly what was upserted, so the
    # pipeline's unchanged-and-vectored skip (no-vector backfill) behaves as it
    # does against real LanceDB while calls stay spy-able.
    m = MagicMock(spec=VectorIndex)
    ids: set[int] = set()
    m.upsert_chunks.side_effect = lambda rows: ids.update(r.doc_id for r in rows)
    m.doc_ids_with_vectors.side_effect = lambda: set(ids)
    return m


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
# Relative roots — stored paths must be absolute
#
# The walker yields root-joined paths, so a relative root used to store
# relative keys.  Those are CWD-ambiguous: `oasis index .` from /a and from /b
# stored the same string for DIFFERENT files, and the UNIQUE path column's
# ON CONFLICT DO UPDATE silently overwrote the first with the second —
# document loss, not an API quirk.  index_directory now absolutizes root once
# at entry (os.path.abspath: lexical, no symlink rewriting).
# ---------------------------------------------------------------------------


def test_relative_roots_from_different_cwds_do_not_collide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = open_db(tmp_path / ".db" / "test.db")
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    (dir_a / "notes.txt").write_text("alpha notes about revenue")
    (dir_b / "notes.txt").write_text("beta notes about biology")

    monkeypatch.chdir(dir_a)
    index_directory(conn, Path("."))
    monkeypatch.chdir(dir_b)
    index_directory(conn, Path("."))

    idx = KeywordIndex(conn)
    # Without the abspath both runs store the key "notes.txt" and this is 1:
    # the second run's upsert overwrote the first document's row.
    assert idx.count() == 2
    assert idx.get_doc_id(dir_a / "notes.txt") is not None
    assert idx.get_doc_id(dir_b / "notes.txt") is not None


def test_relative_root_stores_only_absolute_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A relative stored path is meaningless as a key — nothing records what it
    # was relative to — so it must never reach the documents table.
    conn = open_db(tmp_path / ".db" / "test.db")
    docs = tmp_path / "docs"
    sub = docs / "sub"
    sub.mkdir(parents=True)
    (docs / "one.txt").write_text("one")
    (sub / "two.txt").write_text("two")

    monkeypatch.chdir(tmp_path)
    stats = index_directory(conn, Path("docs"))

    assert stats["indexed"] == 2
    rows = conn.execute("SELECT path FROM documents").fetchall()
    assert len(rows) == 2
    assert all(Path(row["path"]).is_absolute() for row in rows)


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


# ---------------------------------------------------------------------------
# Resume idempotency — instrumentation for the stale-sweep precondition
# ---------------------------------------------------------------------------


def test_cancel_resume_is_idempotent_doc_count_matches_walk(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """Cancel a reindex partway, resume to completion, and assert the invariant
    the stale-sweep (next commit) depends on:

        COUNT(*) == COUNT(DISTINCT path) == indexable files walked under root

    ``path`` is UNIQUE, so extra distinct-path rows are structurally impossible;
    a violation here would mean different *path forms* for one file (the
    relative-path collision class) reached storage. The sweep's set-difference
    assumes stored docs are 1:1 with indexable files — this is the test that
    goes red if a non-idempotent resume ever breaks that assumption.

    Instrumented (prints all three counts + each run's stats) so a live number
    like "resumed to 403 docs over a 400-file root" can be reconciled against
    ground truth instead of guessed at.
    """
    import threading

    from oasis.extractors.registry import get_extractor
    from oasis.index.walker import walk

    for i in range(40):
        (tmp_path / f"f{i:02d}.txt").write_text(f"content number {i} with words")
    (tmp_path / "sub").mkdir()
    for i in range(8):
        (tmp_path / "sub" / f"s{i}.txt").write_text(f"nested content {i}")
    for i in range(3):
        (tmp_path / f"blob{i}.xyz").write_text("unsupported, walked but never a doc row")

    # Ground truth: what a complete walk actually yields as indexable.
    walked_indexable = sum(1 for p in walk(tmp_path) if get_extractor(p) is not None)

    stats_full = index_directory(conn, tmp_path)

    cancel = threading.Event()
    seen = {"indexed": 0}

    def cancel_partway(path: Path, status: str) -> None:
        if status == "indexed":
            seen["indexed"] += 1
            if seen["indexed"] >= 10:
                cancel.set()

    # force=True mirrors the live run: a reindex re-visits every stored file.
    stats_cancelled = index_directory(conn, tmp_path, force=True, cancel=cancel, on_file=cancel_partway)
    stats_resumed = index_directory(conn, tmp_path, force=True)

    count_all = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    count_distinct = conn.execute("SELECT COUNT(DISTINCT path) FROM documents").fetchone()[0]

    print(f"\nwalked_indexable = {walked_indexable}")
    print(f"COUNT(*)         = {count_all}")
    print(f"COUNT(DISTINCT path) = {count_distinct}")
    print(f"run 1 (full):      {stats_full}")
    print(f"run 2 (cancelled): {stats_cancelled}")
    print(f"run 3 (resumed):   {stats_resumed}")

    assert stats_cancelled["indexed"] < walked_indexable  # the cancel really bit
    assert count_all == count_distinct == walked_indexable


# ---------------------------------------------------------------------------
# Stale-document reconciliation (the sweep) — every test here is adversarial:
# the sweep DELETES stored data, so each one is written to go red on a real
# bug (missing separator boundary, missing census gate, cross-root leakage).
# Real SQLite + real LanceDB; only the embedder is fake (deterministic, no
# torch).
# ---------------------------------------------------------------------------


class _CountingEmbedder:
    """Minimal real EmbeddingModel: spy-able calls, real name for markers."""

    dimension = 4
    model_name = "fake-model"

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str]) -> np.ndarray:
        self.calls += 1
        return np.zeros((len(texts), self.dimension), dtype=np.float32)


@pytest.fixture
def stores(tmp_path: Path):
    """(conn, vector_index, embedder) over real backends, under a dotdir the
    walker never descends into."""
    conn = open_db(tmp_path / ".db" / "test.db")
    vi = VectorIndex(tmp_path / ".db" / "test.lance", dimension=4)
    return conn, vi, _CountingEmbedder()


def test_sweep_removes_deleted_file_from_all_stores(stores, tmp_path: Path) -> None:
    """Test 1 + 7: a file deleted from disk is swept from SQLite, FTS, and the
    vector store on a plain (non-force) reindex; survivors are untouched, and
    the swept doc returns from neither search arm."""
    conn, vi, emb = stores
    for name in ("a", "b", "c"):
        (tmp_path / f"{name}.txt").write_text(f"{name} distinctive zq{name}token content")
    index_directory(conn, tmp_path, vector_index=vi, embedder=emb)
    idx = KeywordIndex(conn)
    doomed_id = idx.get_doc_id(tmp_path / "c.txt")
    assert doomed_id is not None and doomed_id in vi.doc_ids_with_vectors()

    (tmp_path / "c.txt").unlink()
    stats = index_directory(conn, tmp_path, vector_index=vi, embedder=emb)

    assert stats["removed"] == 1
    assert idx.get_doc_id(tmp_path / "c.txt") is None
    assert idx.get_doc_id(tmp_path / "a.txt") is not None
    assert idx.get_doc_id(tmp_path / "b.txt") is not None
    # Keyword arm (FTS row gone via the _ad trigger):
    assert idx.search("zqctoken") == []
    # Semantic arm (no orphaned vectors to surface stale hits):
    assert doomed_id not in vi.doc_ids_with_vectors()


def test_sweep_sibling_prefix_isolation(stores, tmp_path: Path) -> None:
    """Test 2: reindexing /x/a must never sweep /x/ab — goes red on a missing
    separator boundary (bare prefix match) or a LIKE-wildcard filter."""
    conn, vi, emb = stores
    root_a = tmp_path / "a"
    root_ab = tmp_path / "ab"
    root_a.mkdir()
    root_ab.mkdir()
    (root_a / "inside.txt").write_text("inside the a root")
    (root_ab / "sibling.txt").write_text("inside the ab sibling root")
    index_directory(conn, root_a, vector_index=vi, embedder=emb)
    index_directory(conn, root_ab, vector_index=vi, embedder=emb)

    stats = index_directory(conn, root_a, vector_index=vi, embedder=emb)

    assert stats["removed"] == 0
    assert KeywordIndex(conn).get_doc_id(root_ab / "sibling.txt") is not None


def test_sweep_skipped_on_permission_denied_subtree(stores, tmp_path: Path) -> None:
    """Test 3 — the mass-deletion landmine: a chmod-000 subdir yields a walk
    that COMPLETES but never saw that subtree. Sweeping would silently delete
    every doc under a folder we merely couldn't read this run."""
    conn, vi, emb = stores
    (tmp_path / "top.txt").write_text("top level doc")
    sub = tmp_path / "locked"
    sub.mkdir()
    (sub / "precious.txt").write_text("indexed then locked away")
    index_directory(conn, tmp_path, vector_index=vi, embedder=emb)
    precious_id = KeywordIndex(conn).get_doc_id(sub / "precious.txt")
    assert precious_id is not None

    sub.chmod(0o000)
    try:
        stats = index_directory(conn, tmp_path, vector_index=vi, embedder=emb)
    finally:
        sub.chmod(0o755)

    assert stats["permission_denied"] > 0  # the census really was dirty
    assert stats["removed"] == 0
    assert KeywordIndex(conn).get_doc_id(sub / "precious.txt") is not None
    assert precious_id in vi.doc_ids_with_vectors()


def test_sweep_skipped_on_cancel(stores, tmp_path: Path) -> None:
    """Test 4: a cancelled reindex has an incomplete seen-set — everything past
    the cancel point would look 'unseen'. It must delete nothing."""
    import threading

    conn, vi, emb = stores
    for i in range(10):
        (tmp_path / f"f{i:02d}.txt").write_text(f"cancellable content {i}")
    index_directory(conn, tmp_path, vector_index=vi, embedder=emb)
    (tmp_path / "f09.txt").unlink()  # genuinely stale — but the run is cancelled

    cancel = threading.Event()

    def cancel_early(path: Path, status: str) -> None:
        cancel.set()  # cancel after the very first file

    stats = index_directory(
        conn, tmp_path, vector_index=vi, embedder=emb, cancel=cancel, on_file=cancel_early
    )

    assert stats["removed"] == 0
    # The stale doc SURVIVES — reconciling it is the next complete run's job.
    assert KeywordIndex(conn).get_doc_id(tmp_path / "f09.txt") is not None


def test_sweep_reconciles_rename(stores, tmp_path: Path) -> None:
    """Test 5: a rename is a delete at the old path plus an add at the new one
    — which disk-existence checks (count_stale) can never see, because the old
    row's file is 'gone' only in the sense that its path moved."""
    conn, vi, emb = stores
    (tmp_path / "old-name.txt").write_text("stable content that moves")
    index_directory(conn, tmp_path, vector_index=vi, embedder=emb)

    (tmp_path / "old-name.txt").rename(tmp_path / "new-name.txt")
    stats = index_directory(conn, tmp_path, vector_index=vi, embedder=emb)

    idx = KeywordIndex(conn)
    assert stats["removed"] == 1
    assert idx.get_doc_id(tmp_path / "old-name.txt") is None
    assert idx.get_doc_id(tmp_path / "new-name.txt") is not None
    assert idx.count() == 1


def test_sweep_reconciles_new_exclude(stores, tmp_path: Path) -> None:
    """Test 5b: a file newly covered by excludes was 'seen by disk' but not by
    the walk — the sweep removes its stale row."""
    conn, vi, emb = stores
    (tmp_path / "keep.txt").write_text("keep this one")
    (tmp_path / "drop.txt").write_text("exclude this one later")
    index_directory(conn, tmp_path, vector_index=vi, embedder=emb)

    stats = index_directory(
        conn, tmp_path, vector_index=vi, embedder=emb, extra_excludes=["drop.txt"]
    )

    assert stats["removed"] == 1
    assert KeywordIndex(conn).get_doc_id(tmp_path / "drop.txt") is None
    assert KeywordIndex(conn).get_doc_id(tmp_path / "keep.txt") is not None


def test_sweep_cross_root_isolation(stores, tmp_path: Path) -> None:
    """Test 6: reindexing one root never sweeps another root's docs, even when
    the other root has genuinely stale rows."""
    conn, vi, emb = stores
    r1, r2 = tmp_path / "roots" / "one", tmp_path / "roots" / "two"
    r1.mkdir(parents=True)
    r2.mkdir(parents=True)
    (r1 / "one.txt").write_text("first root doc")
    (r2 / "two.txt").write_text("second root doc")
    index_directory(conn, r1, vector_index=vi, embedder=emb)
    index_directory(conn, r2, vector_index=vi, embedder=emb)

    (r2 / "two.txt").unlink()  # stale under r2 — but we reindex r1
    stats = index_directory(conn, r1, vector_index=vi, embedder=emb)

    assert stats["removed"] == 0
    assert KeywordIndex(conn).get_doc_id(r2 / "two.txt") is not None


# ---------------------------------------------------------------------------
# No-vector backfill
# ---------------------------------------------------------------------------


def test_plain_reindex_backfills_keyword_only_index(stores, tmp_path: Path) -> None:
    """Test 8: the real ~/.oasis repair. An index built before vectors gets its
    vectors populated by a PLAIN reindex — no --force required."""
    from oasis.index.db import SCHEMA_VERSION

    conn, vi, emb = stores
    for i in range(3):
        (tmp_path / f"doc{i}.txt").write_text(f"pre-vector era content {i}")
    index_directory(conn, tmp_path)  # keyword-only: no embedder, no vectors
    assert vi.count() == 0
    assert KeywordIndex(conn).get_capabilities().vectors_built is False

    stats = index_directory(conn, tmp_path, vector_index=vi, embedder=emb)

    assert stats["chunks"] > 0 and vi.count() > 0
    caps = KeywordIndex(conn).get_capabilities()
    assert caps.vectors_built is True
    assert caps.embedding_dimension == emb.dimension  # semantic_ready's comparison
    assert caps.schema_version == SCHEMA_VERSION  # reindex_recommended flips false


def test_fully_vectored_unchanged_corpus_re_embeds_nothing(stores, tmp_path: Path) -> None:
    """Test 9: backfill must not degenerate into always-re-embed — that would
    silently kill the incremental optimization."""
    conn, vi, emb = stores
    for i in range(3):
        (tmp_path / f"doc{i}.txt").write_text(f"stable content {i}")
    index_directory(conn, tmp_path, vector_index=vi, embedder=emb)
    calls_after_first = emb.calls
    assert calls_after_first > 0

    stats = index_directory(conn, tmp_path, vector_index=vi, embedder=emb)

    assert emb.calls == calls_after_first, "plain reindex of a vectored corpus re-embedded"
    assert stats["skipped"] == 3 and stats["chunks"] == 0


def test_markers_correct_post_backfill(stores, tmp_path: Path) -> None:
    """Test 10: backfill makes the embed phase run, so the capability markers
    are set — model and dimension recorded."""
    conn, vi, emb = stores
    (tmp_path / "doc.txt").write_text("needs vectors")
    index_directory(conn, tmp_path)  # keyword-only

    index_directory(conn, tmp_path, vector_index=vi, embedder=emb)  # plain backfill

    caps = KeywordIndex(conn).get_capabilities()
    assert caps.vectors_built is True
    assert caps.embedding_model == "fake-model"
    assert caps.embedding_dimension == 4


def test_extraction_failure_does_not_sweep_the_present_file(stores, tmp_path: Path) -> None:
    """A file still present on disk whose extraction returns None THIS RUN must
    not look "unseen" to the stale sweep — the census gate (cancel/walk-errors/
    permission_denied) doesn't protect this case, because extractors swallow
    their own I/O errors and return None without ever touching those counters.
    The seen-set is keyed on the walk yield (before extraction is attempted),
    specifically so an extraction failure can't masquerade as deletion.

    Distinct from test_sweep_skipped_on_permission_denied_subtree (a directory
    denial) and test_sweep_skipped_on_cancel (a partial walk): this is the
    present-but-extraction-failed case, and the sweep must still run (it's a
    clean, complete census) but must not touch the failing file's row.
    """
    import os

    conn, vi, emb = stores
    (tmp_path / "a.txt").write_text("a content")
    (tmp_path / "b.txt").write_text("b content")
    c_path = tmp_path / "c.txt"
    c_path.write_text("c content searchable via cqctoken")
    index_directory(conn, tmp_path, vector_index=vi, embedder=emb)
    idx = KeywordIndex(conn)
    c_id = idx.get_doc_id(c_path)
    assert c_id is not None and c_id in vi.doc_ids_with_vectors()

    # Bump c.txt's mtime so it's no longer "unchanged" and this run actually
    # attempts extraction for it (an unchanged file never reaches extract()).
    new_mtime = c_path.stat().st_mtime + 100
    os.utime(c_path, (new_mtime, new_mtime))

    original_extract = TextExtractor.extract

    def flaky_extract(self, path: Path):
        if path.name == "c.txt":
            return None  # e.g. a transient I/O error the extractor swallowed
        return original_extract(self, path)

    with patch.object(TextExtractor, "extract", flaky_extract):
        stats = index_directory(conn, tmp_path, vector_index=vi, embedder=emb)

    assert stats["failed"] == 1  # the extraction failure is counted...
    assert stats["removed"] == 0  # ...but NOT as a sweep deletion
    assert idx.get_doc_id(c_path) == c_id  # row survives, same doc_id
    assert c_id in vi.doc_ids_with_vectors()  # prior vectors survive
    assert idx.search("cqctoken") != []  # still searchable via keyword
