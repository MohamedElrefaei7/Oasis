from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from oasis.extractors.registry import get_extractor
from oasis.index.chunker import Chunk, chunk_document
from oasis.index.embeddings import EmbeddingModel
from oasis.index.keyword import KeywordIndex
from oasis.index.vector import ChunkRow, VectorIndex
from oasis.index.walker import walk

logger = logging.getLogger(__name__)

OnFile = Callable[[Path, str], None]
# Called as (done, total) after each embedding batch; first call has done=0 to announce total.
OnChunksProgress = Callable[[int, int], None]

EMBED_BATCH = 64


@dataclass
class _PendingDoc:
    doc_id: int
    path: str
    extension: str
    mtime: float
    chunks: list[Chunk]


def index_directory(
    conn: sqlite3.Connection,
    root: Path,
    *,
    force: bool = False,
    extra_excludes: list[str] | None = None,
    on_file: OnFile | None = None,
    vector_index: VectorIndex | None = None,
    embedder: EmbeddingModel | None = None,
    on_chunks_progress: OnChunksProgress | None = None,
) -> dict[str, int]:
    stats: dict[str, int] = {
        "indexed": 0,
        "skipped": 0,
        "failed": 0,
        "unsupported": 0,
        "chunks": 0,
    }
    idx = KeywordIndex(conn)
    do_embed = vector_index is not None and embedder is not None
    pending: list[_PendingDoc] = []

    # -----------------------------------------------------------------------
    # Phase 1: walk → extract → keyword index
    # -----------------------------------------------------------------------
    for path in walk(root, extra_excludes=extra_excludes):
        extractor = get_extractor(path)
        if extractor is None:
            stats["unsupported"] += 1
            if on_file:
                on_file(path, "unsupported")
            continue

        try:
            st = path.stat()
        except OSError:
            logger.warning("Cannot stat %s", path)
            stats["failed"] += 1
            if on_file:
                on_file(path, "failed")
            continue

        if not force and idx.is_unchanged(path, size=st.st_size, mtime=st.st_mtime):
            stats["skipped"] += 1
            if on_file:
                on_file(path, "skipped")
            continue

        try:
            doc = extractor.extract(path)
        except Exception:
            logger.warning("Unexpected error extracting %s", path, exc_info=True)
            stats["failed"] += 1
            if on_file:
                on_file(path, "failed")
            continue

        if doc is None:
            stats["failed"] += 1
            if on_file:
                on_file(path, "failed")
            continue

        try:
            idx.upsert(doc)
        except Exception:
            logger.warning("Failed to store %s", path, exc_info=True)
            stats["failed"] += 1
            if on_file:
                on_file(path, "failed")
            continue

        stats["indexed"] += 1
        if on_file:
            on_file(path, "indexed")

        if do_embed:
            doc_id = idx.get_doc_id(doc.path)
            if doc_id is not None:
                chunks = chunk_document(doc.text)
                if chunks:
                    pending.append(
                        _PendingDoc(
                            doc_id=doc_id,
                            path=str(doc.path),
                            extension=doc.path.suffix.lower(),
                            mtime=doc.metadata.mtime or 0.0,
                            chunks=chunks,
                        )
                    )

    # -----------------------------------------------------------------------
    # Phase 2: embed + vector upsert
    # -----------------------------------------------------------------------
    if not (do_embed and pending):
        return stats

    assert vector_index is not None
    assert embedder is not None

    # Remove stale vector chunks for every doc being (re-)indexed.
    for p in pending:
        vector_index.delete_by_doc_id(p.doc_id)

    all_items: list[tuple[_PendingDoc, Chunk]] = [
        (p, c) for p in pending for c in p.chunks
    ]
    total = len(all_items)
    done = 0

    if on_chunks_progress:
        on_chunks_progress(0, total)

    for i in range(0, total, EMBED_BATCH):
        batch = all_items[i : i + EMBED_BATCH]
        texts = [item[1].text for item in batch]
        vectors = embedder.embed(texts)

        rows = [
            ChunkRow(
                chunk_id=f"{item[0].path}:{item[1].chunk_index}",
                doc_id=item[0].doc_id,
                text=item[1].text,
                vector=vectors[j],
                extension=item[0].extension,
                mtime=item[0].mtime,
                path=item[0].path,
            )
            for j, item in enumerate(batch)
        ]
        vector_index.upsert_chunks(rows)
        done += len(batch)
        stats["chunks"] += len(batch)

        if on_chunks_progress:
            on_chunks_progress(done, total)

    return stats
