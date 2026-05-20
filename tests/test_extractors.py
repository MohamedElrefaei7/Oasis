from pathlib import Path

import pytest

from oasis.extractors.text import TextExtractor
from oasis.models import DocumentMetadata, ExtractedDocument

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def txt_file() -> Path:
    return FIXTURES / "sample.txt"


@pytest.fixture
def md_file() -> Path:
    return FIXTURES / "sample.md"


@pytest.fixture
def extractor() -> TextExtractor:
    return TextExtractor()


class TestTextExtractorInterface:
    def test_extensions_is_frozenset(self, extractor: TextExtractor) -> None:
        assert isinstance(extractor.extensions, frozenset)

    def test_extensions_contains_txt(self, extractor: TextExtractor) -> None:
        assert ".txt" in extractor.extensions

    def test_extensions_contains_md(self, extractor: TextExtractor) -> None:
        assert ".md" in extractor.extensions

    def test_can_handle_txt(self, extractor: TextExtractor, txt_file: Path) -> None:
        assert extractor.can_handle(txt_file) is True

    def test_can_handle_md(self, extractor: TextExtractor, md_file: Path) -> None:
        assert extractor.can_handle(md_file) is True

    def test_cannot_handle_pdf(self, extractor: TextExtractor) -> None:
        assert extractor.can_handle(Path("document.pdf")) is False

    def test_cannot_handle_docx(self, extractor: TextExtractor) -> None:
        assert extractor.can_handle(Path("report.docx")) is False

    def test_returns_none_for_missing_file(self, extractor: TextExtractor, tmp_path: Path) -> None:
        assert extractor.extract(tmp_path / "nonexistent.txt") is None


class TestTextExtractorTxt:
    def test_returns_extracted_document(self, extractor: TextExtractor, txt_file: Path) -> None:
        doc = extractor.extract(txt_file)
        assert isinstance(doc, ExtractedDocument)

    def test_text_content(self, extractor: TextExtractor, txt_file: Path) -> None:
        doc = extractor.extract(txt_file)
        assert doc is not None
        assert "Oasis" in doc.text
        assert len(doc.text) > 0

    def test_path(self, extractor: TextExtractor, txt_file: Path) -> None:
        doc = extractor.extract(txt_file)
        assert doc is not None
        assert doc.path == txt_file

    def test_no_extraction_errors(self, extractor: TextExtractor, txt_file: Path) -> None:
        doc = extractor.extract(txt_file)
        assert doc is not None
        assert doc.extraction_errors == []

    def test_metadata_type(self, extractor: TextExtractor, txt_file: Path) -> None:
        doc = extractor.extract(txt_file)
        assert doc is not None
        assert isinstance(doc.metadata, DocumentMetadata)

    def test_metadata_language(self, extractor: TextExtractor, txt_file: Path) -> None:
        doc = extractor.extract(txt_file)
        assert doc is not None
        assert doc.metadata.language == "en"

    def test_metadata_size(self, extractor: TextExtractor, txt_file: Path) -> None:
        doc = extractor.extract(txt_file)
        assert doc is not None
        assert doc.metadata.size_bytes == txt_file.stat().st_size
        assert doc.metadata.size_bytes > 0

    def test_metadata_mtime(self, extractor: TextExtractor, txt_file: Path) -> None:
        doc = extractor.extract(txt_file)
        assert doc is not None
        assert doc.metadata.mtime == pytest.approx(txt_file.stat().st_mtime)

    def test_metadata_ctime(self, extractor: TextExtractor, txt_file: Path) -> None:
        doc = extractor.extract(txt_file)
        assert doc is not None
        assert doc.metadata.ctime == pytest.approx(txt_file.stat().st_ctime)


class TestTextExtractorMd:
    def test_returns_extracted_document(self, extractor: TextExtractor, md_file: Path) -> None:
        doc = extractor.extract(md_file)
        assert isinstance(doc, ExtractedDocument)

    def test_text_content(self, extractor: TextExtractor, md_file: Path) -> None:
        doc = extractor.extract(md_file)
        assert doc is not None
        assert "markdown" in doc.text.lower()

    def test_metadata_language(self, extractor: TextExtractor, md_file: Path) -> None:
        doc = extractor.extract(md_file)
        assert doc is not None
        assert doc.metadata.language == "en"

    def test_metadata_size(self, extractor: TextExtractor, md_file: Path) -> None:
        doc = extractor.extract(md_file)
        assert doc is not None
        assert doc.metadata.size_bytes == md_file.stat().st_size

    def test_metadata_pdf_fields_absent(self, extractor: TextExtractor, md_file: Path) -> None:
        doc = extractor.extract(md_file)
        assert doc is not None
        assert doc.metadata.page_count is None
        assert doc.metadata.author is None
        assert doc.metadata.title is None
