from pathlib import Path

from oasis.extractors.base import Extractor
from oasis.extractors.docx import DocxExtractor
from oasis.extractors.pdf import PdfExtractor
from oasis.extractors.text import TextExtractor

_EXTRACTORS: list[Extractor] = [
    TextExtractor(),
    PdfExtractor(),
    DocxExtractor(),
]


def get_extractor(path: Path) -> Extractor | None:
    for extractor in _EXTRACTORS:
        if extractor.can_handle(path):
            return extractor
    return None
