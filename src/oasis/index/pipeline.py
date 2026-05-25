import logging
import sqlite3
from pathlib import Path
from typing import Callable

from oasis.extractors.registry import get_extractor
from oasis.index.keyword import KeywordIndex
from oasis.index.walker import walk

logger = logging.getLogger(__name__)

OnFile = Callable[[Path, str], None]


def index_directory(
    conn: sqlite3.Connection,
    root: Path,
    *,
    force: bool = False,
    extra_excludes: list[str] | None = None,
    on_file: OnFile | None = None,
) -> dict[str, int]:
    stats: dict[str, int] = {"indexed": 0, "skipped": 0, "failed": 0, "unsupported": 0}
    idx = KeywordIndex(conn)

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

        doc = extractor.extract(path)
        if doc is None:
            stats["failed"] += 1
            if on_file:
                on_file(path, "failed")
            continue

        idx.upsert(doc)
        stats["indexed"] += 1
        if on_file:
            on_file(path, "indexed")

    return stats
