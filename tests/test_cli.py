from pathlib import Path

import pytest
from typer.testing import CliRunner

from oasis.cli.app import app
from oasis.index.db import open_db

runner = CliRunner()


def _db(tmp_path: Path) -> Path:
    return tmp_path / ".db" / "index.db"


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------


def test_index_exits_0(tmp_path: Path) -> None:
    (tmp_path / "doc.txt").write_text("hello world")
    result = runner.invoke(app, ["index", str(tmp_path), "--db", str(_db(tmp_path))])
    assert result.exit_code == 0


def test_index_reports_indexed_count(tmp_path: Path) -> None:
    (tmp_path / "doc.txt").write_text("hello")
    result = runner.invoke(app, ["index", str(tmp_path), "--db", str(_db(tmp_path))])
    assert "1 indexed" in result.output


def test_index_multiple_files(tmp_path: Path) -> None:
    for i in range(3):
        (tmp_path / f"doc{i}.txt").write_text(f"content {i}")
    result = runner.invoke(app, ["index", str(tmp_path), "--db", str(_db(tmp_path))])
    assert "3 indexed" in result.output


def test_index_nonexistent_path_exits_1(tmp_path: Path) -> None:
    result = runner.invoke(app, ["index", str(tmp_path / "ghost"), "--db", str(_db(tmp_path))])
    assert result.exit_code == 1


def test_index_file_not_dir_exits_1(tmp_path: Path) -> None:
    f = tmp_path / "file.txt"
    f.write_text("x")
    result = runner.invoke(app, ["index", str(f), "--db", str(_db(tmp_path))])
    assert result.exit_code == 1


def test_index_prints_db_path(tmp_path: Path) -> None:
    db = _db(tmp_path)
    result = runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    # Rich may wrap long paths across lines — check for the db label and filename
    assert "db:" in result.output
    assert db.name in result.output


def test_index_verbose_shows_file_name(tmp_path: Path) -> None:
    (tmp_path / "readme.txt").write_text("hi")
    result = runner.invoke(app, ["index", str(tmp_path), "--db", str(_db(tmp_path)), "--verbose"])
    assert "readme.txt" in result.output


def test_index_verbose_shows_status_label(tmp_path: Path) -> None:
    (tmp_path / "doc.txt").write_text("hi")
    result = runner.invoke(app, ["index", str(tmp_path), "--db", str(_db(tmp_path)), "-v"])
    assert "indexed" in result.output


def test_index_force_reindexes(tmp_path: Path) -> None:
    (tmp_path / "doc.txt").write_text("hi")
    db = _db(tmp_path)
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    result = runner.invoke(app, ["index", str(tmp_path), "--db", str(db), "--force"])
    assert "1 indexed" in result.output


def test_index_second_run_skips_unchanged(tmp_path: Path) -> None:
    (tmp_path / "doc.txt").write_text("hi")
    db = _db(tmp_path)
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    result = runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    assert "skipped" in result.output


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def test_search_no_db_exits_1(tmp_path: Path) -> None:
    result = runner.invoke(app, ["search", "hello", "--db", str(tmp_path / "missing.db")])
    assert result.exit_code == 1


def test_search_no_results(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("hello world")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    result = runner.invoke(app, ["search", "zzznomatch", "--db", str(db)])
    assert result.exit_code == 0
    assert "No results" in result.output


def test_search_finds_result(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("the quick brown fox")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    result = runner.invoke(app, ["search", "fox", "--db", str(db)])
    assert result.exit_code == 0
    # Path may be truncated with … by Rich in narrow terminals — check result footer
    assert "1 result" in result.output


def test_search_shows_result_count(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("unique term here")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    result = runner.invoke(app, ["search", "unique", "--db", str(db)])
    assert "result" in result.output


def test_search_limit_flag(tmp_path: Path) -> None:
    db = _db(tmp_path)
    for i in range(5):
        (tmp_path / f"doc{i}.txt").write_text("common keyword here")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    result = runner.invoke(app, ["search", "keyword", "--db", str(db), "--limit", "2"])
    assert result.exit_code == 0
    assert "2 result" in result.output


def test_search_table_header_present(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("searchable content here")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    result = runner.invoke(app, ["search", "searchable", "--db", str(db)])
    # "File" column header is always rendered regardless of terminal width
    assert "File" in result.output


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_no_db_exits_0(tmp_path: Path) -> None:
    result = runner.invoke(app, ["status", "--db", str(tmp_path / "missing.db")])
    assert result.exit_code == 0


def test_status_no_db_shows_helpful_message(tmp_path: Path) -> None:
    result = runner.invoke(app, ["status", "--db", str(tmp_path / "missing.db")])
    assert "No index" in result.output


def test_status_shows_document_count(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("hello")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    result = runner.invoke(app, ["status", "--db", str(db)])
    assert result.exit_code == 0
    assert "Documents" in result.output


def test_status_shows_db_path(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("hello")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    result = runner.invoke(app, ["status", "--db", str(db)])
    # Rich truncates long paths in narrow terminals — checking for the label is reliable
    assert "DB path" in result.output


def test_status_shows_last_indexed(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("hello")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    result = runner.invoke(app, ["status", "--db", str(db)])
    assert "Last indexed" in result.output


def test_status_shows_db_size(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("hello")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    result = runner.invoke(app, ["status", "--db", str(db)])
    assert "DB size" in result.output


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------


def test_reset_no_db_exits_0(tmp_path: Path) -> None:
    result = runner.invoke(app, ["reset", "--db", str(tmp_path / "missing.db"), "--yes"])
    assert result.exit_code == 0


def test_reset_no_db_shows_message(tmp_path: Path) -> None:
    result = runner.invoke(app, ["reset", "--db", str(tmp_path / "missing.db"), "--yes"])
    assert "Nothing to reset" in result.output


def test_reset_deletes_db_file(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("hello")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    assert db.exists()
    runner.invoke(app, ["reset", "--db", str(db), "--yes"])
    assert not db.exists()


def test_reset_prints_deleted_confirmation(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("hello")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    result = runner.invoke(app, ["reset", "--db", str(db), "--yes"])
    assert result.exit_code == 0
    assert "Deleted" in result.output


def test_reset_without_yes_prompts(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("hello")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    # Decline the confirmation — db should remain
    result = runner.invoke(app, ["reset", "--db", str(db)], input="n\n")
    assert db.exists()
