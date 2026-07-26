import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from typer.testing import CliRunner

import oasis.cli.app as app_module
import oasis.index.embeddings as emb_mod
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
    # Track upserted doc ids so doc_ids_with_vectors (patched below) reports
    # what was actually written — the pipeline's unchanged-and-vectored skip
    # then behaves as it does against real LanceDB.
    upserted: set[int] = set()
    merge.execute.side_effect = lambda rows: upserted.update(r["doc_id"] for r in rows)
    mock_table.merge_insert.return_value = merge
    mock_table.count_rows.return_value = 0
    mock_table.search.return_value = mock_search
    mock_db = MagicMock()
    mock_db.create_table.return_value = mock_table

    fake_ce = MagicMock()
    fake_ce.predict.side_effect = lambda pairs, **kw: np.zeros(len(pairs), dtype=np.float32)

    # BOTH caches, because this fixture fakes BOTH models. Clearing only the
    # reranker's leaves a MagicMock parked in the embeddings cache under
    # ("all-MiniLM-L6-v2", "cpu"), where it survives the fixture and is handed
    # to whatever asks next — which a real-model test in the same session then
    # asserts against and fails on.
    reranker_mod._MODEL_CACHE.clear()
    emb_mod._MODEL_CACHE.clear()
    with patch("oasis.index.embeddings.SentenceTransformer", return_value=fake_model), \
         patch("oasis.index.vector.lancedb.connect", return_value=mock_db), \
         patch("oasis.index.vector.VectorIndex.doc_ids_with_vectors",
               lambda self: set(upserted)), \
         patch("oasis.query.reranker.CrossEncoder", return_value=fake_ce), \
         patch("oasis.cli.app.ensure_ollama", return_value=None):
        yield
    reranker_mod._MODEL_CACHE.clear()
    emb_mod._MODEL_CACHE.clear()


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
    runner.invoke(app, ["reset", "--db", str(db)], input="n\n")
    assert db.exists()


# ---------------------------------------------------------------------------
# open
# ---------------------------------------------------------------------------


def _write_last_results(paths: list[Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """Point _LAST_RESULTS_PATH at a temp file and populate it."""
    fake = paths[0].parent / ".last_results.json"
    fake.write_text(json.dumps([str(p) for p in paths]))
    monkeypatch.setattr(app_module, "_LAST_RESULTS_PATH", fake)


def test_open_no_last_search_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "_LAST_RESULTS_PATH", tmp_path / "missing.json")
    result = runner.invoke(app, ["open", "1"])
    assert result.exit_code == 1
    assert "No recent search" in result.output


def test_open_n_out_of_range_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "doc.txt"
    f.write_text("hi")
    _write_last_results([f], monkeypatch)
    result = runner.invoke(app, ["open", "5"])
    assert result.exit_code == 1
    assert "No result #5" in result.output


def test_open_missing_file_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ghost = tmp_path / "ghost.txt"  # never created
    _write_last_results([ghost], monkeypatch)
    result = runner.invoke(app, ["open", "1"])
    assert result.exit_code == 1
    assert "no longer exists" in result.output


def test_open_calls_system_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "doc.txt"
    f.write_text("content")
    _write_last_results([f], monkeypatch)
    with patch("oasis.cli.app.subprocess.run") as mock_run:
        result = runner.invoke(app, ["open", "1"])
    assert result.exit_code == 0
    mock_run.assert_called_once_with(["open", str(f)], check=False)


def test_open_correct_file_selected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = [tmp_path / f"doc{i}.txt" for i in range(1, 4)]
    for f in files:
        f.write_text("hi")
    _write_last_results(files, monkeypatch)
    with patch("oasis.cli.app.subprocess.run") as mock_run:
        runner.invoke(app, ["open", "2"])
    mock_run.assert_called_once_with(["open", str(files[1])], check=False)


def test_open_prints_filename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "report.txt"
    f.write_text("content")
    _write_last_results([f], monkeypatch)
    with patch("oasis.cli.app.subprocess.run"):
        result = runner.invoke(app, ["open", "1"])
    assert "report.txt" in result.output


# ---------------------------------------------------------------------------
# search — --mode flag
# ---------------------------------------------------------------------------


def test_search_mode_keyword_exits_0(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("the quick brown fox")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    result = runner.invoke(app, ["search", "fox", "--db", str(db), "--mode", "keyword"])
    assert result.exit_code == 0


def test_search_mode_keyword_finds_result(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("the quick brown fox")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    result = runner.invoke(app, ["search", "fox", "--db", str(db), "--mode", "keyword"])
    assert "1 result" in result.output


def test_search_mode_keyword_footer_says_keyword(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("the quick brown fox")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    result = runner.invoke(app, ["search", "fox", "--db", str(db), "--mode", "keyword"])
    assert "mode: keyword" in result.output


def test_search_mode_semantic_exits_0(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("content")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    result = runner.invoke(app, ["search", "query", "--db", str(db), "--mode", "semantic"])
    assert result.exit_code == 0


def test_search_mode_semantic_no_results_with_empty_vector_index(tmp_path: Path) -> None:
    # Mocked vector search returns [] → no semantic results.
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("content here")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    result = runner.invoke(app, ["search", "content", "--db", str(db), "--mode", "semantic"])
    assert "No results" in result.output


def test_search_mode_hybrid_exits_0(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("the quick brown fox")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    result = runner.invoke(app, ["search", "fox", "--db", str(db), "--mode", "hybrid"])
    assert result.exit_code == 0


def test_search_mode_hybrid_footer_says_hybrid(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("the quick brown fox")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    result = runner.invoke(app, ["search", "fox", "--db", str(db), "--mode", "hybrid"])
    assert "mode: hybrid" in result.output


def test_search_mode_short_flag(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("the quick brown fox")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    result = runner.invoke(app, ["search", "fox", "--db", str(db), "-m", "keyword"])
    assert result.exit_code == 0


def test_search_mode_invalid_exits_nonzero(tmp_path: Path) -> None:
    db = _db(tmp_path)
    result = runner.invoke(app, ["search", "fox", "--db", str(db), "--mode", "badmode"])
    assert result.exit_code != 0


def test_search_default_mode_is_hybrid(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("the quick brown fox")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    result = runner.invoke(app, ["search", "fox", "--db", str(db)])
    assert "mode: hybrid" in result.output


def test_search_saves_last_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = _db(tmp_path)
    fake_results = tmp_path / ".last_results.json"
    monkeypatch.setattr(app_module, "_LAST_RESULTS_PATH", fake_results)
    (tmp_path / "doc.txt").write_text("unique searchable content")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    runner.invoke(app, ["search", "unique", "--db", str(db)])
    assert fake_results.exists()
    paths = json.loads(fake_results.read_text())
    assert len(paths) == 1
    assert paths[0].endswith("doc.txt")


# ---------------------------------------------------------------------------
# search — NL parsing integration
# ---------------------------------------------------------------------------


def test_search_calls_ensure_ollama(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("hello")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    with patch("oasis.cli.app.ensure_ollama", return_value=None) as mock_eo:
        runner.invoke(app, ["search", "hello", "--db", str(db)])
    mock_eo.assert_called_once()


def test_search_uses_semantic_query_when_parsed(tmp_path: Path) -> None:
    from oasis.query.parser import ParsedQuery
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("machine learning notes")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])

    fake_llm = MagicMock()
    fake_llm.complete.return_value = ParsedQuery(
        semantic_query="machine learning", file_types=[".pptx"]
    )
    with patch("oasis.cli.app.ensure_ollama", return_value=fake_llm), \
         patch("oasis.cli.app.parse_query", return_value=ParsedQuery(
             semantic_query="machine learning", file_types=[".pptx"]
         )) as mock_pq:
        runner.invoke(app, ["search", "powerpoints about ML", "--db", str(db)])
    mock_pq.assert_called_once()
    prompt_arg = mock_pq.call_args[0][0]
    assert "powerpoints about ML" in prompt_arg


def test_search_fallback_to_raw_query_when_ollama_unavailable(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("fox content")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    # ensure_ollama already returns None from the fixture — search should still work
    result = runner.invoke(app, ["search", "fox", "--db", str(db), "--mode", "keyword"])
    assert result.exit_code == 0


def test_search_footer_shows_parsed_when_llm_available(tmp_path: Path) -> None:
    from oasis.query.parser import ParsedQuery
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("quick brown fox")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])

    parsed = ParsedQuery(semantic_query="fox")
    with patch("oasis.cli.app.ensure_ollama", return_value=MagicMock()), \
         patch("oasis.cli.app.parse_query", return_value=parsed):
        result = runner.invoke(
            app, ["search", "fox", "--db", str(db), "--mode", "keyword"]
        )
    assert "parsed" in result.output


def test_search_footer_no_parsed_label_when_llm_unavailable(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("quick brown fox")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    # fixture already patches ensure_ollama → None
    result = runner.invoke(
        app, ["search", "fox", "--db", str(db), "--mode", "keyword"]
    )
    # "·  parsed" is the footer marker; db path may contain word "parsed" so check the marker
    assert "·  parsed" not in result.output


def test_search_parse_exception_falls_back_gracefully(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("fox content")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    with patch("oasis.cli.app.ensure_ollama", return_value=MagicMock()), \
         patch("oasis.cli.app.parse_query", side_effect=RuntimeError("LLM down")):
        result = runner.invoke(
            app, ["search", "fox", "--db", str(db), "--mode", "keyword"]
        )
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# search — --raw flag (4.7)
# ---------------------------------------------------------------------------


def test_search_raw_flag_skips_parsing(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("fox content")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    with patch("oasis.cli.app.ensure_ollama") as mock_eo:
        runner.invoke(app, ["search", "fox", "--db", str(db), "--raw"])
    mock_eo.assert_not_called()


def test_search_raw_flag_exits_0(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("quick brown fox")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    result = runner.invoke(app, ["search", "fox", "--db", str(db), "--raw", "--mode", "keyword"])
    assert result.exit_code == 0


def test_search_raw_flag_no_parsed_in_footer(tmp_path: Path) -> None:
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("quick brown fox")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    result = runner.invoke(app, ["search", "fox", "--db", str(db), "--raw", "--mode", "keyword"])
    assert "·  parsed" not in result.output


def test_search_default_mode_not_raw(tmp_path: Path) -> None:
    """Without --raw, ensure_ollama is called (mocked to None in fixture)."""
    db = _db(tmp_path)
    (tmp_path / "doc.txt").write_text("quick brown fox")
    runner.invoke(app, ["index", str(tmp_path), "--db", str(db)])
    with patch("oasis.cli.app.ensure_ollama", return_value=None) as mock_eo:
        runner.invoke(app, ["search", "fox", "--db", str(db)])
    mock_eo.assert_called_once()
