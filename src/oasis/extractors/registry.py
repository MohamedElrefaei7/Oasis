from pathlib import Path

from oasis.extractors.base import Extractor
from oasis.extractors.docx import DocxExtractor
from oasis.extractors.pdf import PdfExtractor
from oasis.extractors.pptx import PptxExtractor
from oasis.extractors.text import TextExtractor

_EXTRACTOR_MAP: dict[str, Extractor] = {
    ext: instance
    for instance in (TextExtractor(), PdfExtractor(), DocxExtractor(), PptxExtractor())
    for ext in instance.extensions
}


def get_extractor(path: Path) -> Extractor | None:
    return _EXTRACTOR_MAP.get(path.suffix.lower())
