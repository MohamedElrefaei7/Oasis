from pathlib import Path
from typing import Protocol

from oasis.models import DocumentMetadata, ExtractedDocument


class Extractor(Protocol):
    extensions: frozenset[str]

    def extract(self, path: Path) -> ExtractedDocument | None: ...


def stat_metadata(path: Path, **extra: object) -> DocumentMetadata:
    """``DocumentMetadata`` with the three filesystem fields filled in.

    Every extractor needs size/mtime/ctime and nothing else about them varies,
    so the ``path.stat()`` + three-field copy was written out six times. The
    per-format fields (``title``, ``author``, ``page_count``, ``language``)
    stay at the call site as keyword arguments, because those are the part that
    actually differs.

    ``mtime`` matters beyond display: it is the change-detection input
    (``KeywordIndex.is_unchanged``) and the vector store's date filter, so all
    six extractors reading it the same way is a correctness property, not tidiness.
    """
    st = path.stat()
    return DocumentMetadata(
        size_bytes=st.st_size,
        mtime=st.st_mtime,
        ctime=st.st_ctime,
        **extra,  # type: ignore[arg-type]
    )
