import logging
from pathlib import Path

from pypdf import PdfReader

from oasis.models import DocumentMetadata, ExtractedDocument

logger = logging.getLogger(__name__)


class PdfExtractor:
    extensions: frozenset[str] = frozenset({".pdf"})

    def extract(self, path: Path) -> ExtractedDocument | None:
        try:
            reader = PdfReader(path)
        except Exception:
            logger.warning("Failed to open PDF %s", path, exc_info=True)
            return None

        try:
            page_count = len(reader.pages)
            pages: list[str] = []
            for i, page in enumerate(reader.pages):
                try:
                    pages.append(page.extract_text() or "")
                except Exception:
                    logger.debug("Failed to extract text from page %d of %s", i, path, exc_info=True)
                    pages.append("")

            text = "\n".join(pages).strip()
            if not text:
                logger.info("No text extracted from %s — likely a scanned PDF", path)
                return None

            meta = reader.metadata
            title: str | None = (meta.title if meta and meta.title else None)

            stat = path.stat()
            return ExtractedDocument(
                path=path,
                text=text,
                metadata=DocumentMetadata(
                    size_bytes=stat.st_size,
                    mtime=stat.st_mtime,
                    ctime=stat.st_ctime,
                    page_count=page_count,
                    title=title,
                ),
            )
        except Exception:
            logger.warning("Failed to extract content from PDF %s", path, exc_info=True)
            return None
