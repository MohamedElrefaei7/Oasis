from pathlib import Path

import pytest

from oasis.extractors.pptx import PptxExtractor
from oasis.models import ExtractedDocument

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def extractor() -> PptxExtractor:
    return PptxExtractor()


@pytest.fixture
def pptx_file() -> Path:
    return FIXTURES / "sample.pptx"


@pytest.fixture
def corrupted_pptx(tmp_path: Path) -> Path:
    path = tmp_path / "corrupted.pptx"
    path.write_bytes(b"not a pptx at all \x00\x01\x02")
    return path


class TestPptxExtractorInterface:
    def test_extensions_is_frozenset(self, extractor: PptxExtractor) -> None:
        assert isinstance(extractor.extensions, frozenset)

    def test_can_handle_pptx(self, extractor: PptxExtractor) -> None:
        assert extractor.can_handle(Path("deck.pptx")) is True

    def test_can_handle_uppercase(self, extractor: PptxExtractor) -> None:
        assert extractor.can_handle(Path("DECK.PPTX")) is True

    def test_cannot_handle_pdf(self, extractor: PptxExtractor) -> None:
        assert extractor.can_handle(Path("deck.pdf")) is False

    def test_cannot_handle_docx(self, extractor: PptxExtractor) -> None:
        assert extractor.can_handle(Path("deck.docx")) is False

    def test_cannot_handle_txt(self, extractor: PptxExtractor) -> None:
        assert extractor.can_handle(Path("notes.txt")) is False


class TestPptxExtractorSuccess:
    def test_returns_extracted_document(self, extractor: PptxExtractor, pptx_file: Path) -> None:
        doc = extractor.extract(pptx_file)
        assert isinstance(doc, ExtractedDocument)

    def test_extracts_text_from_all_slides(self, extractor: PptxExtractor, pptx_file: Path) -> None:
        doc = extractor.extract(pptx_file)
        assert doc is not None
        assert "Sample Presentation" in doc.text
        assert "Second Slide" in doc.text

    def test_extracts_body_text(self, extractor: PptxExtractor, pptx_file: Path) -> None:
        doc = extractor.extract(pptx_file)
        assert doc is not None
        assert "PptxExtractor" in doc.text or "Oasis" in doc.text

    def test_slides_joined_with_newlines(self, extractor: PptxExtractor, pptx_file: Path) -> None:
        doc = extractor.extract(pptx_file)
        assert doc is not None
        assert "\n" in doc.text

    def test_captures_slide_count(self, extractor: PptxExtractor, pptx_file: Path) -> None:
        doc = extractor.extract(pptx_file)
        assert doc is not None
        assert doc.metadata.page_count == 2

    def test_captures_title(self, extractor: PptxExtractor, pptx_file: Path) -> None:
        doc = extractor.extract(pptx_file)
        assert doc is not None
        assert doc.metadata.title == "Sample Presentation"

    def test_captures_author(self, extractor: PptxExtractor, pptx_file: Path) -> None:
        doc = extractor.extract(pptx_file)
        assert doc is not None
        assert doc.metadata.author == "Test Author"

    def test_captures_size_bytes(self, extractor: PptxExtractor, pptx_file: Path) -> None:
        doc = extractor.extract(pptx_file)
        assert doc is not None
        assert doc.metadata.size_bytes == pptx_file.stat().st_size
        assert doc.metadata.size_bytes > 0

    def test_captures_mtime(self, extractor: PptxExtractor, pptx_file: Path) -> None:
        doc = extractor.extract(pptx_file)
        assert doc is not None
        assert doc.metadata.mtime == pytest.approx(pptx_file.stat().st_mtime)

    def test_no_extraction_errors(self, extractor: PptxExtractor, pptx_file: Path) -> None:
        doc = extractor.extract(pptx_file)
        assert doc is not None
        assert doc.extraction_errors == []

    def test_language_is_none(self, extractor: PptxExtractor, pptx_file: Path) -> None:
        doc = extractor.extract(pptx_file)
        assert doc is not None
        assert doc.metadata.language is None


class TestPptxExtractorFailure:
    def test_returns_none_for_corrupted_file(
        self, extractor: PptxExtractor, corrupted_pptx: Path
    ) -> None:
        assert extractor.extract(corrupted_pptx) is None

    def test_returns_none_for_missing_file(
        self, extractor: PptxExtractor, tmp_path: Path
    ) -> None:
        assert extractor.extract(tmp_path / "ghost.pptx") is None
