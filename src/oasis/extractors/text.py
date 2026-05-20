import logging
from pathlib import Path

from langdetect import detect
from langdetect.lang_detect_exception import LangDetectException

from oasis.models import DocumentMetadata, ExtractedDocument

logger = logging.getLogger(__name__)


class TextExtractor:
    extensions: frozenset[str] = frozenset({".txt", ".md"})

    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() in self.extensions

    def extract(self, path: Path) -> ExtractedDocument | None:
        try:
            stat = path.stat()
            text = path.read_text(encoding="utf-8")
        except Exception:
            logger.warning("Failed to read %s", path, exc_info=True)
            return None

        language: str | None = None
        try:
            language = detect(text[:2000])
        except LangDetectException:
            logger.debug("Language detection failed for %s", path)

        return ExtractedDocument(
            path=path,
            text=text,
            metadata=DocumentMetadata(
                size_bytes=stat.st_size,
                mtime=stat.st_mtime,
                ctime=stat.st_ctime,
                language=language,
            ),
        )
