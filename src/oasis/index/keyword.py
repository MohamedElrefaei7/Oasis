import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from oasis.index.db import SCHEMA_VERSION
from oasis.models import ExtractedDocument


def _file_hash(size: int | None, mtime: float | None) -> str:
    return hashlib.sha256(f"{size}:{mtime}".encode()).hexdigest()[:16]


# Escape character for LIKE patterns built from user-supplied paths.
LIKE_ESCAPE = "\\"


def folder_like_pattern(folder: str) -> str:
    """A LIKE pattern matching exactly the files *under* directory *folder*.

    Two ways a naive ``LIKE folder + '%'`` is wrong, and both were live:

    1. **No separator boundary.** ``/tmp/a`` matched ``/tmp/ab/sibling.txt``,
       because a bare prefix says nothing about where the directory ends. The
       trailing separator is what makes "under this folder" mean it.
    2. **``_`` and ``%`` are LIKE wildcards.** They are perfectly ordinary
       characters in a filename, so a folder literally named ``a_b`` matched
       ``axb`` — the same class of bug ``docs_under`` avoids by filtering in
       Python. Here the filter has to stay in SQL (it composes with the FTS5
       MATCH in one statement), so the wildcards are escaped instead and the
       caller pairs this with ``ESCAPE '\\'``.

    Callers must use the abspath form storage uses; matching is textual.
    """
    prefix = folder.rstrip("/")
    escaped = (
        prefix.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )
    return f"{escaped}{os.sep}%"

# Non-printable sentinels passed to snippet() via SQLite's char() function.
# char(2)/char(3) in the SQL avoids any string interpolation in the query.
# They cannot appear in document text and don't conflict with Rich's [...] markup.
MATCH_START = "\x02"
MATCH_END = "\x03"


@dataclass
class Result:
    path: Path
    doc_id: int
    title: str | None
    snippet: str
    rank: float


@dataclass(frozen=True)
class IndexCapabilities:
    """What an index on disk supports. Internal — the API mirrors it at its boundary.

    Every field is read from the DB alone: this deliberately knows nothing
    about any live embedder, so "were the vectors built at the dimension we
    now use?" is the caller's comparison to make, not this dataclass's.
    """

    # 0 for any index built before the meta table existed.
    schema_version: int
    vectors_built: bool
    embedding_model: str | None
    embedding_dimension: int | None
    document_count: int

    def semantic_ready(self, live_dimension: int | None) -> bool:
        """Vectors exist **and** were built at the dimension now in use.

        The live-embedder comparison can't live in ``get_capabilities`` (which
        is a pure DB read by design), but it must not live in each endpoint
        either: ``/api/health`` and ``/api/status`` both report this field and
        both docstrings promise they can't disagree — a promise that was kept
        by copy-paste until 2026-07-28. Stored vectors at a different dimension
        are unusable, so they don't count as ready.
        """
        return (
            self.vectors_built
            and self.embedding_dimension is not None
            and self.embedding_dimension == live_dimension
        )

    def reindex_recommended(self, live_dimension: int | None) -> bool:
        """Whether the app should nudge the user to reindex.

        Derived **server-side**; the client does no version math. The
        ``document_count > 0`` guard is what keeps a never-indexed DB reading as
        "index me" (false) rather than "reindex me" — two different states, and
        the app words them differently.
        """
        return self.document_count > 0 and (
            self.schema_version < SCHEMA_VERSION or not self.semantic_ready(live_dimension)
        )


class KeywordIndex:
    """All keyword-index SQL lives here. No SQL anywhere else in the index layer."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def upsert(self, doc: ExtractedDocument) -> None:
        m = doc.metadata
        content_hash = _file_hash(m.size_bytes, m.mtime)
        self._conn.execute(
            """
            INSERT INTO documents
                (path, extension, size, mtime, indexed_at, content_hash,
                 language, title, content, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                extension     = excluded.extension,
                size          = excluded.size,
                mtime         = excluded.mtime,
                indexed_at    = excluded.indexed_at,
                content_hash  = excluded.content_hash,
                language      = excluded.language,
                title         = excluded.title,
                content       = excluded.content,
                metadata_json = excluded.metadata_json
            """,
            (
                str(doc.path),
                doc.path.suffix.lower(),
                m.size_bytes,
                m.mtime,
                time.time(),
                content_hash,
                m.language,
                m.title,
                doc.text,
                json.dumps(m.model_dump(exclude_none=True)),
            ),
        )
        self._conn.commit()

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            """
            INSERT INTO meta (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        self._conn.commit()

    def delete(self, path: Path) -> None:
        # The documents_ad trigger removes the FTS row automatically.
        self._conn.execute(
            "DELETE FROM documents WHERE path = ?",
            (str(path),),
        )
        self._conn.commit()

    def clear_documents(self) -> None:
        """Delete every document row (the ``_ad`` trigger clears FTS per row).

        The document half of ``POST /api/reset``. Ordered *after* the vector
        drop in ``AppState.reset_index`` so a crash mid-reset never leaves a
        live document row whose semantic arm points at a dropped vector table.
        """
        self._conn.execute("DELETE FROM documents")
        self._conn.commit()

    def clear_meta(self) -> None:
        """Delete every capability marker (``vectors_built``, ``schema_version``,
        ``indexed_roots``, …).

        The first step of ``POST /api/reset``, run *before* the vectors are
        dropped so no ``vectors_built`` marker ever outlives the vectors it
        describes — the intermediate state then reads as "reindex needed"
        (markers absent → ``schema_version`` 0), never "semantic ready with no
        vectors", which is the one dishonest state a crash could otherwise
        leave behind.
        """
        self._conn.execute("DELETE FROM meta")
        self._conn.commit()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        limit: int = 20,
        *,
        after: float | None = None,
        before: float | None = None,
        folders: list[str] | None = None,
        extensions: list[str] | None = None,
    ) -> list[Result]:
        """Run an FTS5 query with optional structured filters.

        Args:
            after:      Minimum mtime (Unix timestamp, inclusive).
            before:     Maximum mtime (Unix timestamp, exclusive).
            folders:    Absolute directory paths — rows *under* any of them are
                        kept, on a separator boundary and with LIKE wildcards in
                        the path escaped (see ``folder_like_pattern``).
            extensions: Allowed file extensions, e.g. ``[".pdf", ".pptx"]``.
        """
        params: list[object] = [query]
        extra: list[str] = []

        if after is not None:
            extra.append("d.mtime >= ?")
            params.append(after)
        if before is not None:
            extra.append("d.mtime < ?")
            params.append(before)
        if folders:
            conds = " OR ".join(f"d.path LIKE ? ESCAPE '{LIKE_ESCAPE}'" for _ in folders)
            extra.append(f"({conds})")
            params.extend(folder_like_pattern(f) for f in folders)
        if extensions:
            phs = ",".join("?" * len(extensions))
            extra.append(f"d.extension IN ({phs})")
            params.extend(extensions)

        params.append(limit)
        where_extra = "".join(f"\n  AND {clause}" for clause in extra)

        # char(2)/char(3) produce MATCH_START/MATCH_END without string interpolation.
        rows = self._conn.execute(
            f"""
            SELECT
                d.id,
                d.path,
                d.title,
                snippet(documents_fts, 2, char(2), char(3), '…', 20) AS snippet,
                documents_fts.rank AS rank
            FROM documents_fts
            JOIN documents d ON d.id = documents_fts.rowid
            WHERE documents_fts MATCH ?{where_extra}
            ORDER BY rank
            LIMIT ?
            """,
            params,
        ).fetchall()

        return [
            Result(
                path=Path(row["path"]),
                doc_id=row["id"],
                title=row["title"],
                snippet=row["snippet"],
                rank=row["rank"],
            )
            for row in rows
        ]

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def get_capabilities(self) -> IndexCapabilities:
        """Read this index's capability markers. Pure DB read, no live models.

        Absence is conservative: an index with no markers reads as
        ``vectors_built=False`` / ``schema_version=0``, i.e. "needs a reindex",
        which is exactly what a legacy keyword-only index is.
        """
        def _int(key: str) -> int | None:
            value = self.get_meta(key)
            if value is None:
                return None
            try:
                return int(value)
            except ValueError:
                # A corrupt marker means we can't trust it; treat it as absent
                # rather than propagating a crash into /api/health.
                return None

        return IndexCapabilities(
            schema_version=_int("schema_version") or 0,
            vectors_built=self.get_meta("vectors_built") == "true",
            embedding_model=self.get_meta("embedding_model"),
            embedding_dimension=_int("embedding_dimension"),
            document_count=self.count(),
        )

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()
        return row[0]

    def docs_under(self, root: str) -> list[tuple[int, str]]:
        """(doc_id, path) for every stored document under directory *root*.

        The authoritative filter is the Python separator-boundary check, NOT
        SQL LIKE: paths routinely contain ``_``, which LIKE treats as a
        single-char wildcard, so ``LIKE '/tmp/a/%'`` silently over-matches —
        and a bare prefix check would put ``/tmp/ab/...`` "under" ``/tmp/a``.
        *root* must be the abspath form the pipeline stores (it is the sweep's
        deletion scope, so over-matching here deletes someone else's rows).
        """
        prefix = root if root.endswith(os.sep) else root + os.sep
        rows = self._conn.execute("SELECT id, path FROM documents").fetchall()
        return [
            (row["id"], row["path"]) for row in rows if row["path"].startswith(prefix)
        ]

    def count_stale(self) -> int:
        """Count indexed documents whose file no longer exists on disk.

        One ``Path.exists()`` stat per document — cheap per call, but O(documents)
        filesystem hits, so callers gate it behind a scan cap for large indexes
        (see the status endpoint's ``STALE_SCAN_CAP``). SQL and path logic stay
        here in the index layer; the endpoint only decides whether to call it.
        """
        rows = self._conn.execute("SELECT path FROM documents").fetchall()
        return sum(1 for row in rows if not Path(row["path"]).exists())

    def last_indexed_at(self) -> float | None:
        row = self._conn.execute("SELECT MAX(indexed_at) FROM documents").fetchone()
        return row[0] if row and row[0] is not None else None

    def get_indexed_roots(self) -> list[str]:
        """The absolute directory roots this index was built from (deduped).

        Stored as a JSON list under the ``indexed_roots`` meta key. Empty when
        the index predates root tracking — callers treat that as "unknown"
        coverage, not "covers nothing".
        """
        raw = self.get_meta("indexed_roots")
        if raw is None:
            return []
        try:
            roots = json.loads(raw)
        except (ValueError, TypeError):
            # A corrupt marker can't be trusted; treat it as absent rather than
            # crashing a status request.
            return []
        return [r for r in roots if isinstance(r, str)] if isinstance(roots, list) else []

    def add_indexed_root(self, root: str) -> None:
        """Record *root* as a directory this index covers (idempotent).

        *root* must already be absolutized (the pipeline applies
        ``os.path.abspath`` before calling) so the stored form matches the
        document paths built from it — the stale-sweep reconciliation planned
        for full reindex is only valid against the exact root that produced the
        rows, never a guessed common prefix.
        """
        roots = self.get_indexed_roots()
        if root in roots:
            return
        roots.append(root)
        self.set_meta("indexed_roots", json.dumps(roots))

    def remove_indexed_root(self, root: str) -> bool:
        """Untrack *root*; return whether it was there. Idempotent.

        Until this existed ``indexed_roots`` only ever grew, which is what makes
        a root deleted from disk wedge Reindex permanently — the sequence 400s
        on the missing root and there is no way to drop it short of a full
        reset. *root* must be the abspath form ``add_indexed_root`` stored;
        matching is exact, never prefix-based (see ``docs_under``).

        Deleting the root's documents is the caller's job, not this method's:
        the two are separate steps so the caller can order them
        documents-then-marker, which is the crash-recoverable order — a crash
        with rows gone but the root still listed leaves the operation
        retryable, whereas the reverse orphans rows under a root nobody can
        name anymore.
        """
        roots = self.get_indexed_roots()
        if root not in roots:
            return False
        self.set_meta("indexed_roots", json.dumps([r for r in roots if r != root]))
        return True

    # ------------------------------------------------------------------
    # Internal helpers (used by the pipeline)
    # ------------------------------------------------------------------

    def is_unchanged(self, path: Path, *, size: int, mtime: float) -> bool:
        row = self._conn.execute(
            "SELECT content_hash FROM documents WHERE path = ?",
            (str(path),),
        ).fetchone()
        return row is not None and row["content_hash"] == _file_hash(size, mtime)

    def get_doc_id(self, path: Path) -> int | None:
        row = self._conn.execute(
            "SELECT id FROM documents WHERE path = ?",
            (str(path),),
        ).fetchone()
        return row["id"] if row else None
