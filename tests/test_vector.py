"""Tests for oasis.index.vector."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from oasis.index.chunker import NAME_CHUNK_INDEX
from oasis.index.vector import (
    _TABLE_NAME,
    ChunkRow,
    VectorIndex,
    VectorResult,
    _build_schema,
    is_name_chunk,
    make_chunk_id,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DIM = 4  # tiny dimension — keeps tests fast


def _vec(val: float = 0.5, dim: int = DIM) -> np.ndarray:
    return np.full(dim, val, dtype=np.float32)


def _row(
    chunk_id: str = "c1",
    doc_id: int = 1,
    text: str = "hello",
    val: float = 0.5,
    ext: str = ".txt",
    mtime: float = 1000.0,
    path: str = "/a/b.txt",
) -> ChunkRow:
    return ChunkRow(
        chunk_id=chunk_id,
        doc_id=doc_id,
        text=text,
        vector=_vec(val),
        extension=ext,
        mtime=mtime,
        path=path,
    )


# ---------------------------------------------------------------------------
# _build_schema
# ---------------------------------------------------------------------------


def test_build_schema_has_chunk_id() -> None:
    schema = _build_schema(DIM)
    assert schema.get_field_index("chunk_id") >= 0


def test_build_schema_has_doc_id() -> None:
    schema = _build_schema(DIM)
    assert schema.get_field_index("doc_id") >= 0


def test_build_schema_has_text() -> None:
    schema = _build_schema(DIM)
    assert schema.get_field_index("text") >= 0


def test_build_schema_has_vector() -> None:
    schema = _build_schema(DIM)
    assert schema.get_field_index("vector") >= 0


def test_build_schema_has_extension() -> None:
    schema = _build_schema(DIM)
    assert schema.get_field_index("extension") >= 0


def test_build_schema_has_mtime() -> None:
    schema = _build_schema(DIM)
    assert schema.get_field_index("mtime") >= 0


def test_build_schema_has_path() -> None:
    schema = _build_schema(DIM)
    assert schema.get_field_index("path") >= 0


def test_build_schema_vector_list_size_matches_dimension() -> None:
    import pyarrow as pa
    schema = _build_schema(8)
    vtype = schema.field("vector").type
    assert pa.types.is_fixed_size_list(vtype)
    assert vtype.list_size == 8


# ---------------------------------------------------------------------------
# Fixtures — mocked VectorIndex
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_table() -> MagicMock:
    tbl = MagicMock()
    tbl.count_rows.return_value = 0

    # fluent merge_insert chain
    merge = MagicMock()
    merge.when_matched_update_all.return_value = merge
    merge.when_not_matched_insert_all.return_value = merge
    tbl.merge_insert.return_value = merge

    # fluent search chain
    search_q = MagicMock()
    search_q.metric.return_value = search_q
    search_q.select.return_value = search_q
    search_q.limit.return_value = search_q
    search_q.where.return_value = search_q
    search_q.to_list.return_value = []
    tbl.search.return_value = search_q

    return tbl


@pytest.fixture
def index(mock_table: MagicMock, tmp_path: Path) -> VectorIndex:
    mock_db = MagicMock()
    mock_db.create_table.return_value = mock_table
    with patch("oasis.index.vector.lancedb.connect", return_value=mock_db):
        return VectorIndex(tmp_path / "test.lance", dimension=DIM)


# ---------------------------------------------------------------------------
# VectorIndex construction
# ---------------------------------------------------------------------------


def test_connect_called_with_db_path(tmp_path: Path) -> None:
    mock_db = MagicMock()
    mock_db.create_table.return_value = MagicMock()
    db_path = tmp_path / "mydb.lance"
    with patch("oasis.index.vector.lancedb.connect", return_value=mock_db) as mock_connect:
        VectorIndex(db_path, dimension=DIM)
    mock_connect.assert_called_once_with(str(db_path))


def test_create_table_called_with_correct_name(tmp_path: Path) -> None:
    mock_db = MagicMock()
    mock_table = MagicMock()
    mock_db.create_table.return_value = mock_table
    with patch("oasis.index.vector.lancedb.connect", return_value=mock_db):
        VectorIndex(tmp_path / "db.lance", dimension=DIM)
    args, kwargs = mock_db.create_table.call_args
    assert args[0] == _TABLE_NAME


def test_create_table_uses_exist_ok(tmp_path: Path) -> None:
    mock_db = MagicMock()
    mock_db.create_table.return_value = MagicMock()
    with patch("oasis.index.vector.lancedb.connect", return_value=mock_db):
        VectorIndex(tmp_path / "db.lance", dimension=DIM)
    _, kwargs = mock_db.create_table.call_args
    assert kwargs.get("exist_ok") is True


# ---------------------------------------------------------------------------
# upsert_chunks
# ---------------------------------------------------------------------------


def test_upsert_empty_list_skips_merge(index: VectorIndex, mock_table: MagicMock) -> None:
    index.upsert_chunks([])
    mock_table.merge_insert.assert_not_called()


def test_upsert_calls_merge_insert_on_chunk_id(index: VectorIndex, mock_table: MagicMock) -> None:
    index.upsert_chunks([_row()])
    mock_table.merge_insert.assert_called_once_with("chunk_id")


def test_upsert_chains_when_matched_update_all(index: VectorIndex, mock_table: MagicMock) -> None:
    index.upsert_chunks([_row()])
    mock_table.merge_insert.return_value.when_matched_update_all.assert_called_once()


def test_upsert_chains_when_not_matched_insert_all(
    index: VectorIndex, mock_table: MagicMock
) -> None:
    index.upsert_chunks([_row()])
    mock_table.merge_insert.return_value.when_not_matched_insert_all.assert_called_once()


def test_upsert_calls_execute(index: VectorIndex, mock_table: MagicMock) -> None:
    index.upsert_chunks([_row()])
    mock_table.merge_insert.return_value.execute.assert_called_once()


def test_upsert_passes_all_rows_to_execute(index: VectorIndex, mock_table: MagicMock) -> None:
    rows = [_row("c1"), _row("c2"), _row("c3")]
    index.upsert_chunks(rows)
    passed = mock_table.merge_insert.return_value.execute.call_args[0][0]
    assert len(passed) == 3


def test_upsert_row_has_chunk_id_field(index: VectorIndex, mock_table: MagicMock) -> None:
    index.upsert_chunks([_row(chunk_id="abc")])
    rows = mock_table.merge_insert.return_value.execute.call_args[0][0]
    assert rows[0]["chunk_id"] == "abc"


def test_upsert_row_has_doc_id_field(index: VectorIndex, mock_table: MagicMock) -> None:
    index.upsert_chunks([_row(doc_id=42)])
    rows = mock_table.merge_insert.return_value.execute.call_args[0][0]
    assert rows[0]["doc_id"] == 42


def test_upsert_row_has_text_field(index: VectorIndex, mock_table: MagicMock) -> None:
    index.upsert_chunks([_row(text="world")])
    rows = mock_table.merge_insert.return_value.execute.call_args[0][0]
    assert rows[0]["text"] == "world"


def test_upsert_row_vector_is_list(index: VectorIndex, mock_table: MagicMock) -> None:
    index.upsert_chunks([_row()])
    rows = mock_table.merge_insert.return_value.execute.call_args[0][0]
    assert isinstance(rows[0]["vector"], list)


def test_upsert_row_vector_length_matches_dimension(
    index: VectorIndex, mock_table: MagicMock
) -> None:
    index.upsert_chunks([_row()])
    rows = mock_table.merge_insert.return_value.execute.call_args[0][0]
    assert len(rows[0]["vector"]) == DIM


def test_upsert_row_has_extension(index: VectorIndex, mock_table: MagicMock) -> None:
    index.upsert_chunks([_row(ext=".pdf")])
    rows = mock_table.merge_insert.return_value.execute.call_args[0][0]
    assert rows[0]["extension"] == ".pdf"


def test_upsert_row_has_mtime(index: VectorIndex, mock_table: MagicMock) -> None:
    index.upsert_chunks([_row(mtime=999.0)])
    rows = mock_table.merge_insert.return_value.execute.call_args[0][0]
    assert rows[0]["mtime"] == 999.0


def test_upsert_row_has_path(index: VectorIndex, mock_table: MagicMock) -> None:
    index.upsert_chunks([_row(path="/x/y.md")])
    rows = mock_table.merge_insert.return_value.execute.call_args[0][0]
    assert rows[0]["path"] == "/x/y.md"


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def _make_search_row(
    chunk_id: str = "c1",
    doc_id: int = 1,
    text: str = "hi",
    path: str = "/a.txt",
    distance: float = 0.1,
) -> dict:
    return {
        "chunk_id": chunk_id,
        "doc_id": doc_id,
        "text": text,
        "path": path,
        "_distance": distance,
    }


def test_search_calls_table_search(index: VectorIndex, mock_table: MagicMock) -> None:
    index.search(_vec())
    mock_table.search.assert_called_once()


def test_search_passes_query_as_list(index: VectorIndex, mock_table: MagicMock) -> None:
    index.search(_vec(0.5))
    args, _ = mock_table.search.call_args
    assert isinstance(args[0], list)


def test_search_uses_cosine_metric(index: VectorIndex, mock_table: MagicMock) -> None:
    index.search(_vec())
    mock_table.search.return_value.metric.assert_called_once_with("cosine")


def test_search_selects_expected_columns(index: VectorIndex, mock_table: MagicMock) -> None:
    index.search(_vec())
    q = mock_table.search.return_value
    cols = q.select.call_args[0][0]
    assert "chunk_id" in cols
    assert "doc_id" in cols
    assert "text" in cols
    assert "path" in cols
    assert "_distance" in cols


def test_search_applies_limit(index: VectorIndex, mock_table: MagicMock) -> None:
    index.search(_vec(), limit=5)
    q = mock_table.search.return_value
    q.limit.assert_called_once_with(5)


def test_search_default_limit_is_ten(index: VectorIndex, mock_table: MagicMock) -> None:
    index.search(_vec())
    q = mock_table.search.return_value
    q.limit.assert_called_once_with(10)


def test_search_no_where_skips_where_call(index: VectorIndex, mock_table: MagicMock) -> None:
    index.search(_vec())
    q = mock_table.search.return_value
    q.where.assert_not_called()


def test_search_where_passed_through(index: VectorIndex, mock_table: MagicMock) -> None:
    index.search(_vec(), where="extension = '.pdf'")
    q = mock_table.search.return_value
    q.where.assert_called_once_with("extension = '.pdf'")


def test_search_returns_list(index: VectorIndex, mock_table: MagicMock) -> None:
    result = index.search(_vec())
    assert isinstance(result, list)


def test_search_returns_vector_results(index: VectorIndex, mock_table: MagicMock) -> None:
    mock_table.search.return_value.to_list.return_value = [_make_search_row()]
    results = index.search(_vec())
    assert all(isinstance(r, VectorResult) for r in results)


def test_search_result_chunk_id(index: VectorIndex, mock_table: MagicMock) -> None:
    mock_table.search.return_value.to_list.return_value = [_make_search_row(chunk_id="xyz")]
    results = index.search(_vec())
    assert results[0].chunk_id == "xyz"


def test_search_result_doc_id(index: VectorIndex, mock_table: MagicMock) -> None:
    mock_table.search.return_value.to_list.return_value = [_make_search_row(doc_id=99)]
    results = index.search(_vec())
    assert results[0].doc_id == 99


def test_search_result_text(index: VectorIndex, mock_table: MagicMock) -> None:
    mock_table.search.return_value.to_list.return_value = [_make_search_row(text="some text")]
    results = index.search(_vec())
    assert results[0].text == "some text"


def test_search_result_path(index: VectorIndex, mock_table: MagicMock) -> None:
    mock_table.search.return_value.to_list.return_value = [_make_search_row(path="/foo/bar.txt")]
    results = index.search(_vec())
    assert results[0].path == "/foo/bar.txt"


def test_search_result_score_from_distance(index: VectorIndex, mock_table: MagicMock) -> None:
    mock_table.search.return_value.to_list.return_value = [_make_search_row(distance=0.42)]
    results = index.search(_vec())
    assert results[0].score == pytest.approx(0.42)


def test_search_empty_results(index: VectorIndex, mock_table: MagicMock) -> None:
    mock_table.search.return_value.to_list.return_value = []
    results = index.search(_vec())
    assert results == []


def test_search_multiple_results_ordering_preserved(
    index: VectorIndex, mock_table: MagicMock
) -> None:
    rows = [
        _make_search_row(chunk_id="c1", distance=0.1),
        _make_search_row(chunk_id="c2", distance=0.3),
        _make_search_row(chunk_id="c3", distance=0.5),
    ]
    mock_table.search.return_value.to_list.return_value = rows
    results = index.search(_vec())
    assert [r.chunk_id for r in results] == ["c1", "c2", "c3"]


# ---------------------------------------------------------------------------
# delete_by_doc_id
# ---------------------------------------------------------------------------


def test_delete_calls_table_delete(index: VectorIndex, mock_table: MagicMock) -> None:
    index.delete_by_doc_id(7)
    mock_table.delete.assert_called_once()


def test_delete_predicate_contains_doc_id(index: VectorIndex, mock_table: MagicMock) -> None:
    index.delete_by_doc_id(42)
    predicate = mock_table.delete.call_args[0][0]
    assert "42" in predicate


def test_delete_predicate_references_doc_id_column(
    index: VectorIndex, mock_table: MagicMock
) -> None:
    index.delete_by_doc_id(1)
    predicate = mock_table.delete.call_args[0][0]
    assert "doc_id" in predicate


def test_delete_different_ids_produce_different_predicates(
    index: VectorIndex, mock_table: MagicMock
) -> None:
    index.delete_by_doc_id(1)
    pred1 = mock_table.delete.call_args[0][0]
    index.delete_by_doc_id(2)
    pred2 = mock_table.delete.call_args[0][0]
    assert pred1 != pred2


# ---------------------------------------------------------------------------
# count
# ---------------------------------------------------------------------------


def test_count_returns_zero_initially(index: VectorIndex, mock_table: MagicMock) -> None:
    mock_table.count_rows.return_value = 0
    assert index.count() == 0


def test_count_reflects_row_count(index: VectorIndex, mock_table: MagicMock) -> None:
    mock_table.count_rows.return_value = 5
    assert index.count() == 5


def test_count_calls_count_rows(index: VectorIndex, mock_table: MagicMock) -> None:
    index.count()
    mock_table.count_rows.assert_called_once()


# ---------------------------------------------------------------------------
# Integration: real LanceDB (uses tmp_path filesystem)
# ---------------------------------------------------------------------------


def test_integration_upsert_and_count(tmp_path: Path) -> None:
    idx = VectorIndex(tmp_path / "db.lance", dimension=DIM)
    idx.upsert_chunks([_row("c1"), _row("c2")])
    assert idx.count() == 2


def test_integration_search_returns_results(tmp_path: Path) -> None:
    idx = VectorIndex(tmp_path / "db.lance", dimension=DIM)
    idx.upsert_chunks([_row("c1", val=0.1), _row("c2", val=0.9)])
    results = idx.search(_vec(0.1))
    assert len(results) == 2


def test_integration_search_result_fields(tmp_path: Path) -> None:
    idx = VectorIndex(tmp_path / "db.lance", dimension=DIM)
    idx.upsert_chunks([_row("c1", doc_id=7, text="test text", path="/p.txt")])
    results = idx.search(_vec())
    r = results[0]
    assert r.chunk_id == "c1"
    assert r.doc_id == 7
    assert r.text == "test text"
    assert r.path == "/p.txt"
    assert isinstance(r.score, float)


def test_integration_upsert_overwrites_on_same_chunk_id(tmp_path: Path) -> None:
    idx = VectorIndex(tmp_path / "db.lance", dimension=DIM)
    idx.upsert_chunks([_row("c1", text="original")])
    idx.upsert_chunks([_row("c1", text="updated")])
    assert idx.count() == 1
    results = idx.search(_vec())
    assert results[0].text == "updated"


def test_integration_delete_removes_rows(tmp_path: Path) -> None:
    idx = VectorIndex(tmp_path / "db.lance", dimension=DIM)
    idx.upsert_chunks([_row("c1", doc_id=1), _row("c2", doc_id=2)])
    idx.delete_by_doc_id(1)
    assert idx.count() == 1


def test_integration_search_with_where_filter(tmp_path: Path) -> None:
    idx = VectorIndex(tmp_path / "db.lance", dimension=DIM)
    idx.upsert_chunks([
        _row("c1", ext=".txt"),
        _row("c2", ext=".pdf"),
    ])
    results = idx.search(_vec(), where="extension = '.txt'")
    assert all(r.chunk_id == "c1" for r in results)


def test_integration_search_limit_respected(tmp_path: Path) -> None:
    idx = VectorIndex(tmp_path / "db.lance", dimension=DIM)
    idx.upsert_chunks([_row(f"c{i}") for i in range(10)])
    results = idx.search(_vec(), limit=3)
    assert len(results) <= 3


def test_integration_empty_table_search_returns_empty(tmp_path: Path) -> None:
    idx = VectorIndex(tmp_path / "db.lance", dimension=DIM)
    results = idx.search(_vec())
    assert results == []


def test_integration_persist_across_open(tmp_path: Path) -> None:
    """Re-opening the same path should see previously upserted rows."""
    db_path = tmp_path / "db.lance"
    idx1 = VectorIndex(db_path, dimension=DIM)
    idx1.upsert_chunks([_row("c1")])

    idx2 = VectorIndex(db_path, dimension=DIM)
    assert idx2.count() == 1


# ---------------------------------------------------------------------------
# chunk_id format — writers and readers must agree
# ---------------------------------------------------------------------------


def test_make_chunk_id_composes_path_and_index() -> None:
    assert make_chunk_id("/docs/a.txt", 0) == "/docs/a.txt:0"


def test_name_chunk_is_recognized() -> None:
    assert is_name_chunk(make_chunk_id("/docs/a.txt", NAME_CHUNK_INDEX))


def test_content_chunks_are_not_name_chunks() -> None:
    assert not is_name_chunk(make_chunk_id("/docs/a.txt", 0))
    assert not is_name_chunk(make_chunk_id("/docs/a.txt", 12))


def test_a_path_ending_in_the_sentinel_is_not_a_name_chunk() -> None:
    # The suffix check must key on the separator, not on the digits: a file
    # genuinely named "-1" would otherwise read as every doc's name chunk.
    assert not is_name_chunk(make_chunk_id("/docs/-1", 3))
