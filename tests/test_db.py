import sqlite3
from pathlib import Path

import pytest

from oasis.index.db import open_db


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
    expected = {"id", "path", "extension", "size", "mtime", "indexed_at",
                "content_hash", "language", "title", "content", "metadata_json"}
    assert expected.issubset(cols)
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
