import logging
from pathlib import Path

from langdetect import detect
from langdetect.lang_detect_exception import LangDetectException

from oasis.extractors.base import ExtractedDocument, FileMetadata

logger = logging.getLogger(__name__)


class TextExtractor:
    @property
    def supported_extensions(self) -> list[str]:
        return [".txt", ".md"]

    def extract(self, path: Path) -> ExtractedDocument:
        stat = path.stat()
        content = path.read_text(encoding="utf-8")

        language: str | None
        try:
            language = detect(content)
        except LangDetectException:
            logger.debug("Language detection failed for %s", path)
            language = None

        return ExtractedDocument(
            path=path,
            content=content,
            language=language,
            metadata=FileMetadata(
                size_bytes=stat.st_size,
                mtime=stat.st_mtime,
                extension=path.suffix,
            ),
        )
