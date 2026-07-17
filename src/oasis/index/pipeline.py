from __future__ import annotations

import logging
import os
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from oasis.extractors.registry import get_extractor
from oasis.index.chunker import Chunk, chunk_document
from oasis.index.db import SCHEMA_VERSION
from oasis.index.embeddings import EmbeddingModel
from oasis.index.keyword import KeywordIndex
from oasis.index.vector import ChunkRow, VectorIndex
from oasis.index.walker import walk

logger = logging.getLogger(__name__)

OnFile = Callable[[Path, str], None]
# Called as (done, total) after each embedding batch; first call has done=0 to announce total.
OnChunksProgress = Callable[[int, int], None]
# Called once when the stale sweep starts (the API maps it to the "reconciling"
# SSE phase). Deletes are fast, so the phase may be a blink — the durable
# signal is the "removed" count in the returned stats.
OnReconcile = Callable[[], None]

EMBED_BATCH = 64


@dataclass
class _PendingDoc:
    doc_id: int
    path: str
    extension: str
    mtime: float
    chunks: list[Chunk]


def _is_unreadable(path: Path) -> bool:
    """True when *path* cannot be opened for reading at all.

    The Extractor protocol requires extractors to swallow their own I/O errors
    and return None, so a None result on its own can't distinguish "this file
    is corrupt" from "I'm not allowed to read it" — and those need different
    answers from the UI.  Probing costs an open() only on the failure path.
    """
    try:
        with path.open("rb"):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


def _write_capability_markers(idx: KeywordIndex, embedder: EmbeddingModel | None) -> None:
    """Record what this index supports. Only called on successful completion.

    Markers are only ever *set*, never cleared: an incremental re-run with
    nothing new to embed doesn't reach the embed phase, and must not downgrade
    an index whose vectors are perfectly good. Absence keeps meaning "unknown",
    so a cancelled or crashed run conservatively reads as needs-reindex.
    """
    idx.set_meta("schema_version", str(SCHEMA_VERSION))
    if embedder is None:
        return
    idx.set_meta("vectors_built", "true")
    idx.set_meta("embedding_dimension", str(embedder.dimension))
    # dimension is on the EmbeddingModel Protocol; the model's name is not, so
    # read it opportunistically — a custom embedder needn't provide one.
    model_name = getattr(embedder, "model_name", None)
    if model_name:
        idx.set_meta("embedding_model", str(model_name))


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
    cancel: threading.Event | None = None,
    on_reconcile: OnReconcile | None = None,
) -> dict[str, int]:
    """Walk *root*, extract and index every supported file, embed, reconcile.

    Returns a stats dict whose keys are always present.  ``permission_denied``
    is counted separately from ``failed`` so callers can tell "this file is
    broken" apart from "I am not allowed to read this" — on macOS the latter
    means Full Disk Access has not been granted, which is a user action, not
    an error.

    When *cancel* is set the run stops at the next file (or embedding batch)
    and returns partial stats.  Work already committed stays committed;
    indexing is incremental, so the next run resumes where this one stopped.

    **Stale reconciliation** (``removed`` in the stats): after a complete,
    clean walk, stored documents under *root* that the walk did not see —
    removed from disk, moved, or newly excluded — are deleted from SQLite
    (the ``_ad`` trigger cleans FTS) and from the vector store. The sweep
    lives here, not in any caller, because this is the only place holding
    both the authoritative seen-set and the completeness signal. It runs on
    *any* complete walk, not only ``force`` — ``force`` governs embedding,
    never walking, and an incremental reindex still walks the whole root.
    It is skipped entirely (``removed: 0``) unless the walk was a trustworthy
    census: not cancelled, zero walk errors, zero permission denials. The
    permission gate is the landmine case: a ``chmod 000`` subdir yields a walk
    that *completes* but never saw that subtree, and sweeping on it would
    silently mass-delete every doc under a folder we merely couldn't read
    this run. When the census is in doubt, delete nothing.

    **No-vector backfill**: an unchanged file is skipped *only if* its doc
    already has vector rows (when embedding is on). A pre-vector index is
    therefore repaired by a plain reindex — no ``--force`` needed. Known
    limitation, deliberate: "has any vectors" treats a partial chunk set
    (crash mid-embed) as vectored; ``--force`` remains the full-rebuild
    escape hatch.
    """
    # Stored paths are root-joined walker output, so a relative root means
    # relative stored keys — CWD-ambiguous, and two runs from different CWDs
    # silently overwrite each other's rows via upsert's ON CONFLICT (the same
    # relative string names different files). Absolutize once, here, so every
    # caller inherits it and no relative path can ever reach the documents
    # table. os.path.abspath, not resolve(): lexical only, no symlink
    # rewriting, and a no-op on already-absolute roots.
    root = Path(os.path.abspath(root))

    stats: dict[str, int] = {
        "indexed": 0,
        "skipped": 0,
        "failed": 0,
        "unsupported": 0,
        "permission_denied": 0,
        "chunks": 0,
        "removed": 0,
    }
    idx = KeywordIndex(conn)
    # Record which root this index now covers, before the walk — so even a
    # cancelled or permission-denied run (which returns early below) leaves the
    # root registered. abspath'd above, deduped by add_indexed_root. The app
    # reads this for its reindex button, and the planned full-reindex stale
    # sweep is only valid against roots recorded here.
    idx.add_indexed_root(str(root))
    do_embed = vector_index is not None and embedder is not None
    pending: list[_PendingDoc] = []

    # Every path the walk yields, downstream outcome irrespective: a file that
    # failed extraction this run still EXISTS, so it must never look "unseen"
    # to the sweep. This set plus a clean census is the sweep's whole authority.
    seen: set[str] = set()
    walk_errors = 0

    # Backfill support: which docs already have vectors, one bulk read before
    # the walk (never a per-doc query in the loop). Only needed when embedding
    # is on and the unchanged-skip path exists at all (force re-embeds anyway).
    vectored: set[int] = (
        vector_index.doc_ids_with_vectors() if do_embed and not force else set()
    )

    def on_walk_error(exc: OSError) -> None:
        # os.walk swallows directory-level errors unless onerror is given, so
        # without this an unreadable tree yields nothing and is indistinguishable
        # from an empty one.  Full Disk Access denials land here, not in the
        # per-file handlers below — the files are never yielded at all.
        nonlocal walk_errors
        walk_errors += 1
        if isinstance(exc, PermissionError):
            logger.info("Permission denied: %s", exc.filename)
            stats["permission_denied"] += 1
        else:
            logger.warning("Cannot read %s: %s", exc.filename, exc.strerror)
            stats["failed"] += 1

    def reconcile() -> None:
        """Delete stored docs under root the walk didn't see — gated hard.

        Runs only on a trustworthy complete census: not cancelled (a partial
        walk's "not seen" is meaningless — it would delete everything past the
        cancel point), zero walk errors, zero permission denials (a subtree we
        couldn't read this run is invisible, not deleted). Coarse skip is the
        deliberate first cut; per-subtree exclusion is a future refinement.
        """
        if cancel is not None and cancel.is_set():
            return
        if walk_errors or stats["permission_denied"]:
            logger.info(
                "Skipping stale sweep: census incomplete (%d walk error(s), %d permission denied)",
                walk_errors,
                stats["permission_denied"],
            )
            return
        if on_reconcile:
            on_reconcile()
        for doc_id, stored_path in idx.docs_under(str(root)):
            if stored_path in seen:
                continue
            # Converge both stores per doc — a doc gone from one arm but live
            # in the other would return stale hits. Vectors first, then the
            # documents row (whose _ad trigger cleans FTS).
            if vector_index is not None:
                vector_index.delete_by_doc_id(doc_id)
            idx.delete(Path(stored_path))
            stats["removed"] += 1
        if stats["removed"]:
            logger.info("Removed %d stale document(s) under %s", stats["removed"], root)

    # -----------------------------------------------------------------------
    # Phase 1: walk → extract → keyword index
    # -----------------------------------------------------------------------
    for path in walk(root, extra_excludes=extra_excludes, on_error=on_walk_error):
        if cancel is not None and cancel.is_set():
            logger.info("Indexing cancelled after %d file(s)", stats["indexed"])
            return stats
        seen.add(str(path))

        extractor = get_extractor(path)
        if extractor is None:
            stats["unsupported"] += 1
            if on_file:
                on_file(path, "unsupported")
            continue

        # PermissionError is an OSError, so it must be caught first or the
        # broad handler below silently counts it as a generic failure.
        try:
            st = path.stat()
        except PermissionError:
            logger.info("Permission denied: %s", path)
            stats["permission_denied"] += 1
            if on_file:
                on_file(path, "permission_denied")
            continue
        except OSError:
            logger.warning("Cannot stat %s", path)
            stats["failed"] += 1
            if on_file:
                on_file(path, "failed")
            continue

        if not force and idx.is_unchanged(path, size=st.st_size, mtime=st.st_mtime):
            # Backfill check: unchanged is only skippable when the doc's
            # vectors exist (or embedding is off). An unchanged-but-unvectored
            # doc falls through to full re-extract + embed, so a plain reindex
            # repairs a pre-vector index without --force.
            doc_id = idx.get_doc_id(path) if do_embed else None
            if not do_embed or doc_id in vectored:
                stats["skipped"] += 1
                if on_file:
                    on_file(path, "skipped")
                continue

        try:
            doc = extractor.extract(path)
        except PermissionError:
            logger.info("Permission denied: %s", path)
            stats["permission_denied"] += 1
            if on_file:
                on_file(path, "permission_denied")
            continue
        except Exception:
            logger.warning("Unexpected error extracting %s", path, exc_info=True)
            stats["failed"] += 1
            if on_file:
                on_file(path, "failed")
            continue

        if doc is None:
            # Extractors return None for every failure mode, so probe to find
            # out whether this was "unreadable" or genuinely "broken".
            if _is_unreadable(path):
                logger.info("Permission denied: %s", path)
                stats["permission_denied"] += 1
                if on_file:
                    on_file(path, "permission_denied")
            else:
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
        # The embed phase didn't run — either no embedder, or nothing new to
        # embed. This is the main sweep path: a reindex where only deletions
        # happened has nothing to embed but still must reconcile.
        reconcile()
        # Record the schema version but don't claim vectors: if a prior
        # run built them, its marker is still there and stays true.
        _write_capability_markers(idx, None)
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
        if cancel is not None and cancel.is_set():
            logger.info("Indexing cancelled after %d/%d chunk(s)", done, total)
            return stats

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

    # Sweep after embed so a run cancelled mid-embed never deletes (matching
    # the job-level "sweep only on done" rule); reconcile() re-checks cancel.
    reconcile()
    _write_capability_markers(idx, embedder)
    return stats
