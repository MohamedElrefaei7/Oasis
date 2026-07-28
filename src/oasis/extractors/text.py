import logging
from pathlib import Path

from langdetect import detect
from langdetect.lang_detect_exception import LangDetectException

from oasis.extractors.base import stat_metadata
from oasis.models import ExtractedDocument

logger = logging.getLogger(__name__)


class TextExtractor:
    extensions: frozenset[str] = frozenset({".txt", ".md"})

    def extract(self, path: Path) -> ExtractedDocument | None:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            logger.warning("Failed to read %s", path, exc_info=True)
            return None

        language: str | None = None
        try:
            language = detect(text[:2000])
        except LangDetectException:
            logger.debug("Language detection failed for %s", path)

        try:
            metadata = stat_metadata(path, language=language)
        except OSError:
            logger.warning("Failed to stat %s", path, exc_info=True)
            return None

        return ExtractedDocument(path=path, text=text, metadata=metadata)
