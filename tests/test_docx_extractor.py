from pathlib import Path

import pytest

from oasis.extractors.docx import DocxExtractor
from oasis.models import ExtractedDocument

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def extractor() -> DocxExtractor:
    return DocxExtractor()


@pytest.fixture
def docx_file() -> Path:
    return FIXTURES / "sample.docx"


@pytest.fixture
def corrupted_docx(tmp_path: Path) -> Path:
    path = tmp_path / "corrupted.docx"
    path.write_bytes(b"not a docx at all \x00\x01\x02")
    return path


class TestDocxExtractorInterface:
    def test_extensions_is_frozenset(self, extractor: DocxExtractor) -> None:
        assert isinstance(extractor.extensions, frozenset)

    def test_extensions_contains_docx(self, extractor: DocxExtractor) -> None:
        assert ".docx" in extractor.extensions

    def test_extensions_excludes_pdf(self, extractor: DocxExtractor) -> None:
        assert ".pdf" not in extractor.extensions

    def test_extensions_excludes_txt(self, extractor: DocxExtractor) -> None:
        assert ".txt" not in extractor.extensions


class TestDocxExtractorSuccess:
    def test_returns_extracted_document(self, extractor: DocxExtractor, docx_file: Path) -> None:
        doc = extractor.extract(docx_file)
        assert isinstance(doc, ExtractedDocument)

    def test_extracts_text(self, extractor: DocxExtractor, docx_file: Path) -> None:
        doc = extractor.extract(docx_file)
        assert doc is not None
        assert "DocxExtractor" in doc.text or "sample DOCX" in doc.text.lower()
        assert len(doc.text) > 0

    def test_paragraphs_concatenated(self, extractor: DocxExtractor, docx_file: Path) -> None:
        doc = extractor.extract(docx_file)
        assert doc is not None
        assert "\n" in doc.text

    def test_captures_author(self, extractor: DocxExtractor, docx_file: Path) -> None:
        doc = extractor.extract(docx_file)
        assert doc is not None
        assert doc.metadata.author == "Test Author"

    def test_captures_title(self, extractor: DocxExtractor, docx_file: Path) -> None:
        doc = extractor.extract(docx_file)
        assert doc is not None
        assert doc.metadata.title == "Sample Document"

    def test_captures_size_bytes(self, extractor: DocxExtractor, docx_file: Path) -> None:
        doc = extractor.extract(docx_file)
        assert doc is not None
        assert doc.metadata.size_bytes == docx_file.stat().st_size
        assert doc.metadata.size_bytes > 0

    def test_captures_mtime(self, extractor: DocxExtractor, docx_file: Path) -> None:
        doc = extractor.extract(docx_file)
        assert doc is not None
        assert doc.metadata.mtime == pytest.approx(docx_file.stat().st_mtime)

    def test_no_extraction_errors(self, extractor: DocxExtractor, docx_file: Path) -> None:
        doc = extractor.extract(docx_file)
        assert doc is not None
        assert doc.extraction_errors == []

    def test_page_count_is_none(self, extractor: DocxExtractor, docx_file: Path) -> None:
        doc = extractor.extract(docx_file)
        assert doc is not None
        assert doc.metadata.page_count is None


class TestDocxExtractorFailure:
    def test_returns_none_for_corrupted_file(
        self, extractor: DocxExtractor, corrupted_docx: Path
    ) -> None:
        assert extractor.extract(corrupted_docx) is None

    def test_returns_none_for_missing_file(self, extractor: DocxExtractor, tmp_path: Path) -> None:
        assert extractor.extract(tmp_path / "ghost.docx") is None
