from pathlib import Path

import pytest
from pypdf import PdfWriter

from oasis.extractors.pdf import PdfExtractor
from oasis.models import ExtractedDocument

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def extractor() -> PdfExtractor:
    return PdfExtractor()


@pytest.fixture
def pdf_file() -> Path:
    return FIXTURES / "sample.pdf"


@pytest.fixture
def scanned_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "scanned.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with open(path, "wb") as f:
        writer.write(f)
    return path


@pytest.fixture
def corrupted_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "corrupted.pdf"
    path.write_bytes(b"not a pdf at all \x00\x01\x02")
    return path


class TestPdfExtractorInterface:
    def test_extensions_is_frozenset(self, extractor: PdfExtractor) -> None:
        assert isinstance(extractor.extensions, frozenset)

    def test_can_handle_pdf(self, extractor: PdfExtractor) -> None:
        assert extractor.can_handle(Path("document.pdf")) is True

    def test_can_handle_uppercase(self, extractor: PdfExtractor) -> None:
        assert extractor.can_handle(Path("DOCUMENT.PDF")) is True

    def test_cannot_handle_txt(self, extractor: PdfExtractor) -> None:
        assert extractor.can_handle(Path("notes.txt")) is False

    def test_cannot_handle_docx(self, extractor: PdfExtractor) -> None:
        assert extractor.can_handle(Path("report.docx")) is False


class TestPdfExtractorSuccess:
    def test_returns_extracted_document(self, extractor: PdfExtractor, pdf_file: Path) -> None:
        doc = extractor.extract(pdf_file)
        assert isinstance(doc, ExtractedDocument)

    def test_extracts_text(self, extractor: PdfExtractor, pdf_file: Path) -> None:
        doc = extractor.extract(pdf_file)
        assert doc is not None
        assert "PdfExtractor" in doc.text or "Sample PDF" in doc.text
        assert len(doc.text) > 0

    def test_captures_page_count(self, extractor: PdfExtractor, pdf_file: Path) -> None:
        doc = extractor.extract(pdf_file)
        assert doc is not None
        assert doc.metadata.page_count == 1

    def test_captures_title(self, extractor: PdfExtractor, pdf_file: Path) -> None:
        doc = extractor.extract(pdf_file)
        assert doc is not None
        assert doc.metadata.title == "Sample PDF"

    def test_captures_size_bytes(self, extractor: PdfExtractor, pdf_file: Path) -> None:
        doc = extractor.extract(pdf_file)
        assert doc is not None
        assert doc.metadata.size_bytes == pdf_file.stat().st_size
        assert doc.metadata.size_bytes > 0

    def test_captures_mtime(self, extractor: PdfExtractor, pdf_file: Path) -> None:
        doc = extractor.extract(pdf_file)
        assert doc is not None
        assert doc.metadata.mtime == pytest.approx(pdf_file.stat().st_mtime)

    def test_no_extraction_errors(self, extractor: PdfExtractor, pdf_file: Path) -> None:
        doc = extractor.extract(pdf_file)
        assert doc is not None
        assert doc.extraction_errors == []

    def test_author_is_none(self, extractor: PdfExtractor, pdf_file: Path) -> None:
        doc = extractor.extract(pdf_file)
        assert doc is not None
        assert doc.metadata.author is None


class TestPdfExtractorFailure:
    def test_returns_none_for_corrupted_file(
        self, extractor: PdfExtractor, corrupted_pdf: Path
    ) -> None:
        assert extractor.extract(corrupted_pdf) is None

    def test_returns_none_for_scanned_pdf(
        self, extractor: PdfExtractor, scanned_pdf: Path
    ) -> None:
        assert extractor.extract(scanned_pdf) is None

    def test_returns_none_for_missing_file(self, extractor: PdfExtractor, tmp_path: Path) -> None:
        assert extractor.extract(tmp_path / "ghost.pdf") is None
