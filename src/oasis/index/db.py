import contextlib
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

# What the current schema guarantees: documents + FTS + vector chunks, with
# every stored path absolute (the pipeline absolutizes its root). An index
# built before the meta table exists has no schema_version row and reads as 0.
# Bump when a change requires a reindex to be usable.
#   1 → 2: stored paths guaranteed absolute; relative-root indexes (which
#          could silently collide across CWDs) read as < 2 and get flagged
#          reindex-needed via /api/health.
#   2 → 3: `filename` column — the humanized file name, its own FTS column at
#          its own BM25 weight. `_migrate` backfills it in place, so the
#          keyword arm needs no reindex; the *vector* arm does (the filename
#          chunk is only written during indexing), which is what the bump is
#          actually for — it makes reindex_recommended true.
SCHEMA_VERSION = 3

# filename, title and content must exist in `documents` because the FTS virtual
# table uses content=documents — SQLite fetches those columns by rowid on query.
_SCHEMA = """\
CREATE TABLE IF NOT EXISTS documents (
    id           INTEGER PRIMARY KEY,
    path         TEXT UNIQUE NOT NULL,
    filename     TEXT,
    extension    TEXT,
    size         INTEGER,
    mtime        REAL,
    indexed_at   REAL,
    content_hash TEXT,
    language     TEXT,
    title        TEXT,
    content      TEXT,
    metadata_json TEXT
);

-- Capability markers, written by the pipeline on a successful index run.
-- open_db only ensures the table exists: it must never infer markers from
-- heuristics, because "absent" has to keep meaning "not known to be
-- searchable" for every index built before this table existed.
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    filename,
    path,
    title,
    content,
    content=documents,
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS documents_ai
AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, filename, path, title, content)
    VALUES (new.id, new.filename, new.path, new.title, new.content);
END;

CREATE TRIGGER IF NOT EXISTS documents_ad
AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, filename, path, title, content)
    VALUES ('delete', old.id, old.filename, old.path, old.title, old.content);
END;

CREATE TRIGGER IF NOT EXISTS documents_au
AFTER UPDATE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, filename, path, title, content)
    VALUES ('delete', old.id, old.filename, old.path, old.title, old.content);
    INSERT INTO documents_fts(rowid, filename, path, title, content)
    VALUES (new.id, new.filename, new.path, new.title, new.content);
END;
"""

# Column ordinals in documents_fts, for the two APIs that address columns by
# number rather than name. Derived from the CREATE above; if that order ever
# changes these must move with it, which is why they are named here rather
# than spelled as literals at the call sites.
FTS_COL_FILENAME = 0
FTS_COL_PATH = 1
FTS_COL_TITLE = 2
FTS_COL_CONTENT = 3


def db_size_bytes(db_path: Path) -> int:
    """On-disk size of the index: the SQLite file **plus its -wal/-shm companions**.

    The companions are the whole point. In WAL mode a freshly-finished index
    can hold most of its new content in a `-wal` that hasn't been checkpointed
    yet, so `stat()` on the `.db` alone under-reports — which is how `oasis
    status` and the app's statistics panel came to print different sizes for
    the same index. A missing companion contributes nothing rather than raising.
    """
    total = 0
    for p in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        with contextlib.suppress(OSError):
            total += p.stat().st_size
    return total


def _migrate(conn: sqlite3.Connection) -> bool:
    """Bring a pre-v3 index up to the current shape, in place; did it change anything?

    **This is not optional housekeeping.** ``_SCHEMA`` is all
    ``CREATE ... IF NOT EXISTS``, so against an existing index every statement
    in it is a no-op — including the triggers, which would keep their old
    definitions while the FTS table kept its old columns. The first
    ``documents`` insert after an upgrade would then raise ``no such column:
    new.filename`` and take indexing down entirely. Migrating before the
    schema script runs is what makes the upgrade a non-event.

    Only 2 → 3 exists so far, detected by probing for the column rather than
    trusting ``meta.schema_version``: the marker is absent on legacy indexes
    and stale on any index whose write crashed mid-run, whereas
    ``PRAGMA table_info`` is the ground truth. That also makes this idempotent
    and safe to call on a database that is already current, which it is on
    every single ``open_db``.

    The FTS table is dropped here and recreated by ``_SCHEMA`` rather than
    altered — FTS5 has no ``ADD COLUMN``, and an external-content table stores
    no copy of the documents, so dropping it loses nothing that
    ``documents`` cannot regenerate. ``filename`` is backfilled from the stored
    path, so **the keyword arm gains file-name search without a reindex**;
    only the vector arm's filename chunk needs one.
    """
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'documents'").fetchone():
        return False  # Fresh database — _SCHEMA is about to create it correctly.

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
    if "filename" in columns:
        return False

    # Imported here, not at module scope: oasis.index.filename is a leaf, but
    # db.py is imported by nearly everything and this keeps the dependency
    # pointing one way only.
    from oasis.index.filename import humanize_filename

    try:
        with conn:  # One transaction: a crash mid-migration rolls back whole.
            conn.execute("ALTER TABLE documents ADD COLUMN filename TEXT")
            rows = conn.execute("SELECT id, path FROM documents").fetchall()
            conn.executemany(
                "UPDATE documents SET filename = ? WHERE id = ?",
                [(humanize_filename(row["path"]), row["id"]) for row in rows],
            )
            for trigger in ("documents_ai", "documents_ad", "documents_au"):
                conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
            conn.execute("DROP TABLE IF EXISTS documents_fts")
    except sqlite3.OperationalError as exc:
        # Another *process* migrated between the probe above and the ALTER.
        # The API's own _open_lock serializes threads within one process, but
        # nothing stops `oasis index` in a terminal from racing the server the
        # app spawned. Losing that race is success, not failure — the winner
        # did the work — so the only thing to do is not claim the rebuild.
        if "duplicate column name" not in str(exc):
            raise
        logger.info("Index already migrated to schema v%d by another process", SCHEMA_VERSION)
        return False

    logger.info("Migrating index to schema v%d (filename column, %d docs)", SCHEMA_VERSION, len(rows))
    return True


def open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    migrated = _migrate(conn)
    conn.executescript(_SCHEMA)
    if migrated:
        # _SCHEMA recreated documents_fts *empty*. An external-content table is
        # only an index, never a source of truth, so it has to be told to read
        # `documents` back in — without this the migrated index answers every
        # keyword query with zero results and no error at all.
        conn.execute("INSERT INTO documents_fts(documents_fts) VALUES('rebuild')")
        conn.commit()
    return conn
