"""Edge-case tests for the CLI beyond the baseline test_cli.py coverage."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from typer.testing import CliRunner

import oasis.cli.app as app_module
import oasis.query.reranker as reranker_mod
from oasis.cli.app import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _mock_heavy_deps():
    """Prevent real model loading and LanceDB I/O in CLI tests."""
    fake_model = MagicMock()
    fake_model.get_embedding_dimension.return_value = 4
    fake_model.encode.side_effect = lambda texts, **kw: np.zeros((len(texts), 4), dtype=np.float32)

    mock_search = MagicMock()
    mock_search.metric.return_value = mock_search
    mock_search.select.return_value = mock_search
    mock_search.limit.return_value = mock_search
    mock_search.where.return_value = mock_search
    mock_search.to_list.return_value = []

    mock_table = MagicMock()
    merge = MagicMock()
    merge.when_matched_update_all.return_value = merge
    merge.when_not_matched_insert_all.return_value = merge
    mock_table.merge_insert.return_value = merge
    mock_table.count_rows.return_value = 0
    mock_table.search.return_value = mock_search
    mock_db = MagicMock()
    mock_db.create_table.return_value = mock_table

    fake_ce = MagicMock()
    fake_ce.predict.side_effect = lambda pairs, **kw: np.zeros(len(pairs), dtype=np.float32)

    reranker_mod._MODEL_CACHE.clear()
    with patch("oasis.index.embeddings.SentenceTransformer", return_value=fake_model), \
         patch("oasis.index.vector.lancedb.connect", return_value=mock_db), \
         patch("oasis.query.reranker.CrossEncoder", return_value=fake_ce):
        yield
    reranker_mod._MODEL_CACHE.clear()


def _db(tmp_path: Path) -> Path:
    return tmp_path / ".db" / "index.db"


def _index(tmp_path: Path, db: Path) -> None:
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])


# ---------------------------------------------------------------------------
# search — bad FTS5 syntax
# ---------------------------------------------------------------------------


def test_search_bad_fts5_syntax_exits_1(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("content")
    _index(tmp_path, db)
    result = runner.invoke(app, ["search", '"unclosed', "--db", str(db)])
    assert result.exit_code == 1


def test_search_bad_fts5_syntax_shows_tip(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("content")
    _index(tmp_path, db)
    result = runner.invoke(app, ["search", '"unclosed', "--db", str(db)])
    assert "Tip:" in result.output


def test_search_bad_fts5_syntax_shows_query_error(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("content")
    _index(tmp_path, db)
    result = runner.invoke(app, ["search", '"unclosed', "--db", str(db)])
    assert "Query error" in result.output


# ---------------------------------------------------------------------------
# reset — WAL/SHM companions
# ---------------------------------------------------------------------------


def test_reset_also_deletes_wal_companion(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("hello")
    _index(tmp_path, db)
    wal = Path(str(db) + "-wal")
    wal.write_bytes(b"fake wal data")
    runner.invoke(app, ["reset", "--db", str(db), "--yes"])
    assert not wal.exists()


def test_reset_also_deletes_shm_companion(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("hello")
    _index(tmp_path, db)
    shm = Path(str(db) + "-shm")
    shm.write_bytes(b"fake shm data")
    runner.invoke(app, ["reset", "--db", str(db), "--yes"])
    assert not shm.exists()


def test_reset_when_no_wal_shm_does_not_crash(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("hello")
    _index(tmp_path, db)
    result = runner.invoke(app, ["reset", "--db", str(db), "--yes"])
    assert result.exit_code == 0
    assert not db.exists()


# ---------------------------------------------------------------------------
# open — corrupted / invalid last_results.json
# ---------------------------------------------------------------------------


def test_open_corrupted_json_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = tmp_path / ".last_results.json"
    fake.write_text("{not valid json!!!")
    monkeypatch.setattr(app_module, "_LAST_RESULTS_PATH", fake)
    result = runner.invoke(app, ["open", "1"])
    assert result.exit_code == 1
    assert "Could not read" in result.output


def test_open_n_zero_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "doc.txt"
    f.write_text("hi")
    fake = tmp_path / ".last_results.json"
    fake.write_text(json.dumps([str(f)]))
    monkeypatch.setattr(app_module, "_LAST_RESULTS_PATH", fake)
    result = runner.invoke(app, ["open", "0"])
    assert result.exit_code == 1


def test_open_n_negative_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "doc.txt"
    f.write_text("hi")
    fake = tmp_path / ".last_results.json"
    fake.write_text(json.dumps([str(f)]))
    monkeypatch.setattr(app_module, "_LAST_RESULTS_PATH", fake)
    result = runner.invoke(app, ["open", "-1"])
    # typer may interpret -1 as a flag and exit differently
    assert result.exit_code != 0 or "No result" in result.output


# ---------------------------------------------------------------------------
# status — exact count
# ---------------------------------------------------------------------------


def test_status_shows_exact_document_count(tmp_path: Path) -> None:
    db = _db(tmp_path)
    for i in range(4):
        (tmp_path / f"doc{i}.txt").write_text(f"content {i}")
    _index(tmp_path, db)
    result = runner.invoke(app, ["status", "--db", str(db)])
    assert "4" in result.output


def test_status_zero_docs_after_empty_index(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _index(tmp_path, db)
    result = runner.invoke(app, ["status", "--db", str(db)])
    assert "0" in result.output


# ---------------------------------------------------------------------------
# index — summary omits zero counts
# ---------------------------------------------------------------------------


def test_index_summary_omits_zero_failed(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("hello")
    result = runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    assert "failed" not in result.output


def test_index_summary_omits_zero_skipped_on_first_run(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("hello")
    result = runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    assert "skipped" not in result.output


def test_index_unsupported_files_in_summary(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "data.xyz123").write_text("binary")
    result = runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    assert "unsupported" in result.output


def test_index_no_files_shows_zero_indexed(tmp_path: Path) -> None:
    db = _db(tmp_path)
    result = runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    assert "0 indexed" in result.output


# ---------------------------------------------------------------------------
# search — saves last results only when there are results
# ---------------------------------------------------------------------------


def test_search_does_not_save_last_results_on_no_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _db(tmp_path)
    fake = tmp_path / ".last_results.json"
    monkeypatch.setattr(app_module, "_LAST_RESULTS_PATH", fake)
    (tmp_path / "doc.txt").write_text("hello world")
    _index(tmp_path, db)
    runner.invoke(app, ["search", "zzznomatch", "--db", str(db)])
    assert not fake.exists()


# ---------------------------------------------------------------------------
# search — mode-specific error handling
# ---------------------------------------------------------------------------


def test_search_keyword_mode_bad_fts5_exits_1(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("content")
    _index(tmp_path, db)
    result = runner.invoke(app, ["search", '"unclosed', "--db", str(db), "--mode", "keyword"])
    assert result.exit_code == 1


def test_search_hybrid_mode_bad_fts5_exits_1(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("content")
    _index(tmp_path, db)
    result = runner.invoke(app, ["search", '"unclosed', "--db", str(db), "--mode", "hybrid"])
    assert result.exit_code == 1


def test_search_semantic_mode_ignores_fts5_syntax(tmp_path: Path) -> None:
    # Semantic mode embeds the query string as-is — no FTS5 parsing, no OperationalError.
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("content")
    _index(tmp_path, db)
    result = runner.invoke(app, ["search", '"unclosed', "--db", str(db), "--mode", "semantic"])
    assert result.exit_code == 0


def test_search_keyword_mode_no_results_shows_no_results(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("hello world")
    _index(tmp_path, db)
    result = runner.invoke(app, ["search", "zzznomatch", "--db", str(db), "--mode", "keyword"])
    assert result.exit_code == 0
    assert "No results" in result.output


def test_search_hybrid_mode_no_results_shows_no_results(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("hello world")
    _index(tmp_path, db)
    result = runner.invoke(app, ["search", "zzznomatch", "--db", str(db), "--mode", "hybrid"])
    assert result.exit_code == 0
    assert "No results" in result.output


def test_search_last_results_contains_correct_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _db(tmp_path)
    fake = tmp_path / ".last_results.json"
    monkeypatch.setattr(app_module, "_LAST_RESULTS_PATH", fake)
    for i in range(3):
        (tmp_path / f"doc{i}.txt").write_text("common keyword content")
    _index(tmp_path, db)
    runner.invoke(app, ["search", "keyword", "--db", str(db)])
    assert fake.exists()
    paths = json.loads(fake.read_text())
    assert len(paths) == 3
