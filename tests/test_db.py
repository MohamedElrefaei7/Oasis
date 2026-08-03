import sqlite3
from pathlib import Path

import pytest

from oasis.index.db import (
    FTS_COL_CONTENT,
    FTS_COL_FILENAME,
    FTS_COL_PATH,
    FTS_COL_TITLE,
    SCHEMA_VERSION,
    open_db,
)
from oasis.index.keyword import BM25_WEIGHTS

# ---------------------------------------------------------------------------
# Connection basics
# ---------------------------------------------------------------------------


def test_creates_nested_parent_directories(tmp_path: Path) -> None:
    db_path = tmp_path / "a" / "b" / "c" / "index.db"
    conn = open_db(db_path)
    conn.close()
    assert db_path.exists()


def test_returns_sqlite_connection(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "test.db")
    assert isinstance(conn, sqlite3.Connection)
    conn.close()


def test_row_factory_is_sqlite_row(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "test.db")
    assert conn.row_factory is sqlite3.Row
    conn.close()


def test_wal_journal_mode(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "test.db")
    row = conn.execute("PRAGMA journal_mode").fetchone()
    assert row[0] == "wal"
    conn.close()


def test_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    conn = open_db(db_path)
    conn.close()
    conn = open_db(db_path)
    conn.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_documents_table_created(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "test.db")
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "documents" in tables
    conn.close()


def test_fts_table_created(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "test.db")
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "documents_fts" in tables
    conn.close()


def test_documents_has_expected_columns(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "test.db")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(documents)").fetchall()}
    expected = {"id", "path", "filename", "extension", "size", "mtime", "indexed_at",
                "content_hash", "language", "title", "content", "metadata_json"}
    assert expected.issubset(cols)
    conn.close()


def test_fts_column_order_matches_the_named_ordinals(tmp_path: Path) -> None:
    """The FTS_COL_* constants are what snippet() and bm25() address columns by.

    Nothing in SQLite checks them against the CREATE statement, so a reordered
    schema would silently snippet the wrong column and weight the wrong one.
    """
    conn = open_db(tmp_path / "test.db")
    names = [r[1] for r in conn.execute("PRAGMA table_info(documents_fts)").fetchall()]
    assert names[FTS_COL_FILENAME] == "filename"
    assert names[FTS_COL_PATH] == "path"
    assert names[FTS_COL_TITLE] == "title"
    assert names[FTS_COL_CONTENT] == "content"
    assert len(BM25_WEIGHTS) == len(names)
    conn.close()


def test_schema_idempotent_double_open(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    conn1 = open_db(db_path)
    conn1.execute("INSERT INTO documents (path, content, title) VALUES (?, ?, ?)",
                  ("/keep.txt", "important data", "Title"))
    conn1.commit()
    conn1.close()
    conn2 = open_db(db_path)
    count = conn2.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert count == 1
    conn2.close()


# ---------------------------------------------------------------------------
# FTS INSERT trigger
# ---------------------------------------------------------------------------


def test_insert_trigger_syncs_fts(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "test.db")
    conn.execute(
        "INSERT INTO documents (path, content, title) VALUES (?, ?, ?)",
        ("/a.txt", "triggertest content here", None),
    )
    conn.commit()
    rows = conn.execute(
        "SELECT * FROM documents_fts WHERE documents_fts MATCH 'triggertest'"
    ).fetchall()
    assert len(rows) == 1
    conn.close()


def test_insert_trigger_indexes_title(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "test.db")
    conn.execute(
        "INSERT INTO documents (path, content, title) VALUES (?, ?, ?)",
        ("/a.txt", "body text", "UniqueProjectName"),
    )
    conn.commit()
    rows = conn.execute(
        "SELECT * FROM documents_fts WHERE documents_fts MATCH 'UniqueProjectName'"
    ).fetchall()
    assert len(rows) == 1
    conn.close()


# ---------------------------------------------------------------------------
# FTS DELETE trigger
# ---------------------------------------------------------------------------


def test_delete_trigger_removes_from_fts(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "test.db")
    conn.execute(
        "INSERT INTO documents (path, content, title) VALUES (?, ?, ?)",
        ("/a.txt", "deletetest content", None),
    )
    conn.commit()
    conn.execute("DELETE FROM documents WHERE path = ?", ("/a.txt",))
    conn.commit()
    rows = conn.execute(
        "SELECT * FROM documents_fts WHERE documents_fts MATCH 'deletetest'"
    ).fetchall()
    assert len(rows) == 0
    conn.close()


def test_delete_trigger_does_not_affect_other_rows(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "test.db")
    conn.execute("INSERT INTO documents (path, content, title) VALUES (?, ?, ?)",
                 ("/a.txt", "term_alpha content", None))
    conn.execute("INSERT INTO documents (path, content, title) VALUES (?, ?, ?)",
                 ("/b.txt", "term_beta content", None))
    conn.commit()
    conn.execute("DELETE FROM documents WHERE path = ?", ("/a.txt",))
    conn.commit()
    assert len(conn.execute("SELECT * FROM documents_fts WHERE documents_fts MATCH 'term_alpha'").fetchall()) == 0
    assert len(conn.execute("SELECT * FROM documents_fts WHERE documents_fts MATCH 'term_beta'").fetchall()) == 1
    conn.close()


# ---------------------------------------------------------------------------
# FTS UPDATE trigger
# ---------------------------------------------------------------------------


def test_update_trigger_replaces_fts_content(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "test.db")
    conn.execute(
        "INSERT INTO documents (path, content, title) VALUES (?, ?, ?)",
        ("/a.txt", "oldword vocabulary", None),
    )
    conn.commit()
    conn.execute(
        "UPDATE documents SET content = ? WHERE path = ?",
        ("newword different", "/a.txt"),
    )
    conn.commit()
    assert len(conn.execute("SELECT * FROM documents_fts WHERE documents_fts MATCH 'oldword'").fetchall()) == 0
    assert len(conn.execute("SELECT * FROM documents_fts WHERE documents_fts MATCH 'newword'").fetchall()) == 1
    conn.close()


# ---------------------------------------------------------------------------
# Data persistence
# ---------------------------------------------------------------------------


def test_data_persists_across_close_and_reopen(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    conn = open_db(db_path)
    conn.execute(
        "INSERT INTO documents (path, content, title) VALUES (?, ?, ?)",
        ("/a.txt", "persistent content", None),
    )
    conn.commit()
    conn.close()

    conn2 = open_db(db_path)
    count = conn2.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert count == 1
    conn2.close()


# ---------------------------------------------------------------------------
# Migration from schema v2 (no filename column)
# ---------------------------------------------------------------------------

# The v2 schema, verbatim, so the migration is tested against the shape that
# actually shipped rather than against a mock of it.
_V2_SCHEMA = """\
CREATE TABLE documents (
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
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE VIRTUAL TABLE documents_fts USING fts5(
    path, title, content, content=documents, tokenize='porter unicode61'
);
CREATE TRIGGER documents_ai AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, path, title, content)
    VALUES (new.id, new.path, new.title, new.content);
END;
CREATE TRIGGER documents_ad AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, path, title, content)
    VALUES ('delete', old.id, old.path, old.title, old.content);
END;
CREATE TRIGGER documents_au AFTER UPDATE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, path, title, content)
    VALUES ('delete', old.id, old.path, old.title, old.content);
    INSERT INTO documents_fts(rowid, path, title, content)
    VALUES (new.id, new.path, new.title, new.content);
END;
"""


@pytest.fixture
def v2_db(tmp_path: Path) -> Path:
    """A populated schema-v2 index, as an existing user's install would be."""
    db_path = tmp_path / "v2.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_V2_SCHEMA)
    conn.execute(
        "INSERT INTO documents (path, content, title) VALUES (?, ?, ?)",
        ("/docs/paper-okapi-at-trec3.pdf", "legacy body about ranking", "Okapi"),
    )
    conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', '2')")
    conn.commit()
    conn.close()
    return db_path


def test_migration_adds_and_backfills_filename(v2_db: Path) -> None:
    conn = open_db(v2_db)
    row = conn.execute("SELECT filename FROM documents").fetchone()
    assert row["filename"] == "paper okapi at trec 3"
    conn.close()


def test_migration_preserves_existing_documents(v2_db: Path) -> None:
    conn = open_db(v2_db)
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    conn.close()


def test_migration_rebuilds_fts_so_search_still_works(v2_db: Path) -> None:
    """Dropping documents_fts loses the index; without the rebuild every
    keyword query would return nothing, with no error to notice."""
    conn = open_db(v2_db)
    rows = conn.execute(
        "SELECT * FROM documents_fts WHERE documents_fts MATCH 'legacy'"
    ).fetchall()
    assert len(rows) == 1
    conn.close()


def test_migration_makes_backfilled_filenames_searchable(v2_db: Path) -> None:
    # The whole point of backfilling rather than waiting for a reindex: the
    # keyword arm gains file-name search the moment the app is upgraded.
    conn = open_db(v2_db)
    rows = conn.execute(
        "SELECT * FROM documents_fts WHERE documents_fts MATCH 'trec'"
    ).fetchall()
    assert len(rows) == 1
    conn.close()


def test_migrated_triggers_accept_new_writes(v2_db: Path) -> None:
    """The failure this migration exists to prevent: stale triggers referencing
    `new.filename` against an FTS table that has no such column."""
    conn = open_db(v2_db)
    conn.execute(
        "INSERT INTO documents (path, filename, content) VALUES (?, ?, ?)",
        ("/docs/new-file.txt", "new file", "freshbody"),
    )
    conn.commit()
    assert len(conn.execute(
        "SELECT * FROM documents_fts WHERE documents_fts MATCH 'freshbody'"
    ).fetchall()) == 1
    conn.close()


def test_migration_is_idempotent(v2_db: Path) -> None:
    open_db(v2_db).close()
    conn = open_db(v2_db)
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    assert conn.execute(
        "SELECT filename FROM documents"
    ).fetchone()["filename"] == "paper okapi at trec 3"
    conn.close()


def test_migrated_index_reports_stale_schema_until_reindexed(v2_db: Path) -> None:
    """The version marker is deliberately NOT advanced by the migration.

    The vector arm's filename chunk can only be written by an indexing run, so
    a migrated index is genuinely still behind — reindex_recommended must stay
    true until one happens.
    """
    conn = open_db(v2_db)
    stored = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    assert int(stored["value"]) < SCHEMA_VERSION
    conn.close()


def test_path_unique_constraint(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "test.db")
    conn.execute("INSERT INTO documents (path, content, title) VALUES (?, ?, ?)",
                 ("/a.txt", "first", None))
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO documents (path, content, title) VALUES (?, ?, ?)",
                     ("/a.txt", "second", None))
        conn.commit()
    conn.close()
