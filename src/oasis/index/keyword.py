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

    def search(self, query: str, limit: int = 20) -> list[Result]:
        # char(2) and char(3) produce MATCH_START/MATCH_END without any
        # string interpolation — SQLite evaluates them as scalar expressions.
        rows = self._conn.execute(
            """
            SELECT
                d.path,
                d.title,
                snippet(documents_fts, 2, char(2), char(3), '…', 20) AS snippet,
                documents_fts.rank AS rank
            FROM documents_fts
            JOIN documents d ON d.id = documents_fts.rowid
            WHERE documents_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()

        return [
            Result(
                path=Path(row["path"]),
                title=row["title"],
                snippet=row["snippet"],
                rank=row["rank"],
            )
            for row in rows
        ]

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()
        return row[0]

    # ------------------------------------------------------------------
    # Internal helpers (used by the pipeline)
    # ------------------------------------------------------------------

    def is_unchanged(self, path: Path, *, size: int, mtime: float) -> bool:
        row = self._conn.execute(
            "SELECT content_hash FROM documents WHERE path = ?",
            (str(path),),
        ).fetchone()
        return row is not None and row["content_hash"] == _file_hash(size, mtime)
