from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import lancedb
import numpy as np
import pyarrow as pa

from oasis.index.chunker import NAME_CHUNK_INDEX

_TABLE_NAME = "chunks"


def make_chunk_id(path: str, chunk_index: int) -> str:
    """The vector store's primary key: one document's path plus a chunk index.

    Centralized here, with ``is_name_chunk``, so the format is defined in one
    place — writers build it and readers pick it apart, and those two agreeing
    is not something the schema enforces.
    """
    return f"{path}:{chunk_index}"


def is_name_chunk(chunk_id: str) -> bool:
    """Whether *chunk_id* identifies a filename chunk rather than real content.

    Retrieval needs to tell them apart: a name chunk is a handful of words and
    the best thing in the index for a filename query, but the *worst* thing to
    hand a cross-encoder, which needs prose to judge relevance from.
    """
    return chunk_id.endswith(f":{NAME_CHUNK_INDEX}")


@dataclass
class ChunkRow:
    chunk_id: str
    doc_id: int
    text: str
    vector: np.ndarray
    extension: str
    mtime: float
    path: str


@dataclass
class VectorResult:
    chunk_id: str
    doc_id: int
    text: str
    path: str
    score: float


def _build_schema(dimension: int) -> pa.Schema:
    return pa.schema([
        pa.field("chunk_id", pa.string()),
        pa.field("doc_id", pa.int64()),
        pa.field("text", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), dimension)),
        pa.field("extension", pa.string()),
        pa.field("mtime", pa.float64()),
        pa.field("path", pa.string()),
    ])


class VectorIndex:
    def __init__(self, db_path: Path, dimension: int) -> None:
        self._dimension = dimension
        db = lancedb.connect(str(db_path))
        schema = _build_schema(dimension)
        self._table = db.create_table(_TABLE_NAME, schema=schema, exist_ok=True)

    def upsert_chunks(self, records: list[ChunkRow]) -> None:
        if not records:
            return
        rows = [
            {
                "chunk_id": r.chunk_id,
                "doc_id": r.doc_id,
                "text": r.text,
                "vector": r.vector.astype(np.float32).tolist(),
                "extension": r.extension,
                "mtime": r.mtime,
                "path": r.path,
            }
            for r in records
        ]
        (
            self._table.merge_insert("chunk_id")
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute(rows)
        )

    def search(
        self,
        query_vector: np.ndarray,
        *,
        limit: int = 10,
        where: str | None = None,
    ) -> list[VectorResult]:
        query = (
            self._table.search(query_vector.astype(np.float32).tolist())
            .metric("cosine")
            .select(["chunk_id", "doc_id", "text", "path", "_distance"])
            .limit(limit)
        )
        if where:
            query = query.where(where)
        rows = query.to_list()
        return [
            VectorResult(
                chunk_id=row["chunk_id"],
                doc_id=row["doc_id"],
                text=row["text"],
                path=row["path"],
                score=row["_distance"],
            )
            for row in rows
        ]

    def delete_by_doc_id(self, doc_id: int) -> None:
        self._table.delete(f"doc_id = {doc_id}")

    def doc_ids_with_vectors(self) -> set[int]:
        """Every distinct doc_id that has at least one vector chunk.

        One bulk projection (doc_id column only — never the vectors), computed
        once per pipeline run so the no-vector backfill check is a set lookup
        per doc, not a query per doc. Note "has any vectors" deliberately says
        nothing about *completeness*: a doc with a partial chunk set (crash
        mid-embed) still counts as vectored.
        """
        total = self._table.count_rows()
        if total == 0:
            return set()
        arr = self._table.search().select(["doc_id"]).limit(total).to_arrow()
        return set(arr["doc_id"].to_pylist())

    def count(self) -> int:
        return self._table.count_rows()
