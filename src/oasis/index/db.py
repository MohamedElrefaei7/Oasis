import sqlite3
from pathlib import Path

# What the current schema guarantees: documents + FTS + vector chunks, with
# every stored path absolute (the pipeline absolutizes its root). An index
# built before the meta table exists has no schema_version row and reads as 0.
# Bump when a change requires a reindex to be usable.
#   1 → 2: stored paths guaranteed absolute; relative-root indexes (which
#          could silently collide across CWDs) read as < 2 and get flagged
#          reindex-needed via /api/health.
SCHEMA_VERSION = 2

# title and content must exist in `documents` because the FTS virtual table
# uses content=documents — SQLite fetches those columns by rowid on query.
_SCHEMA = """\
CREATE TABLE IF NOT EXISTS documents (
    id           INTEGER PRIMARY KEY,
    path         TEXT UNIQUE NOT NULL,
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
    path,
    title,
    content,
    content=documents,
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS documents_ai
AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, path, title, content)
    VALUES (new.id, new.path, new.title, new.content);
END;

CREATE TRIGGER IF NOT EXISTS documents_ad
AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, path, title, content)
    VALUES ('delete', old.id, old.path, old.title, old.content);
END;

CREATE TRIGGER IF NOT EXISTS documents_au
AFTER UPDATE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, path, title, content)
    VALUES ('delete', old.id, old.path, old.title, old.content);
    INSERT INTO documents_fts(rowid, path, title, content)
    VALUES (new.id, new.path, new.title, new.content);
END;
"""


def open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA)
    return conn
