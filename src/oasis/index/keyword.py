import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from oasis.models import ExtractedDocument


def _file_hash(size: int | None, mtime: float | None) -> str:
    return hashlib.sha256(f"{size}:{mtime}".encode()).hexdigest()[:16]

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

    def delete(self, path: Path) -> None:
        # The documents_ad trigger removes the FTS row automatically.
        self._conn.execute(
            "DELETE FROM documents WHERE path = ?",
            (str(path),),
        )
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
            folders:    Absolute path prefixes — rows whose path starts with any
                        entry are kept (LIKE ``prefix/%`` matching).
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
            conds = " OR ".join("d.path LIKE ?" for _ in folders)
            extra.append(f"({conds})")
            params.extend(f"{f.rstrip('/')}%" for f in folders)
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

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()
        return row[0]

    def last_indexed_at(self) -> float | None:
        row = self._conn.execute("SELECT MAX(indexed_at) FROM documents").fetchone()
        return row[0] if row and row[0] is not None else None

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
