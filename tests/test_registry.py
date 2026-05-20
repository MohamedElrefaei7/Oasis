from pathlib import Path

from oasis.extractors.docx import DocxExtractor
from oasis.extractors.pdf import PdfExtractor
from oasis.extractors.registry import get_extractor
from oasis.extractors.text import TextExtractor

FIXTURES = Path(__file__).parent / "fixtures"


class TestGetExtractor:
    def test_txt_returns_text_extractor(self) -> None:
        assert isinstance(get_extractor(Path("notes.txt")), TextExtractor)

    def test_md_returns_text_extractor(self) -> None:
        assert isinstance(get_extractor(Path("readme.md")), TextExtractor)

    def test_pdf_returns_pdf_extractor(self) -> None:
        assert isinstance(get_extractor(Path("report.pdf")), PdfExtractor)

    def test_docx_returns_docx_extractor(self) -> None:
        assert isinstance(get_extractor(Path("report.docx")), DocxExtractor)

    def test_xlsx_returns_none(self) -> None:
        assert get_extractor(Path("data.xlsx")) is None

    def test_pptx_returns_none(self) -> None:
        assert get_extractor(Path("slides.pptx")) is None

    def test_unknown_extension_returns_none(self) -> None:
        assert get_extractor(Path("file.xyz")) is None

    def test_uppercase_extension(self) -> None:
        assert get_extractor(Path("NOTES.TXT")) is not None

    def test_returned_extractor_can_handle_path(self) -> None:
        path = Path("notes.txt")
        extractor = get_extractor(path)
        assert extractor is not None
        assert extractor.can_handle(path) is True

    def test_round_trip_txt(self) -> None:
        path = FIXTURES / "sample.txt"
        extractor = get_extractor(path)
        assert extractor is not None
        doc = extractor.extract(path)
        assert doc is not None
        assert len(doc.text) > 0

    def test_round_trip_md(self) -> None:
        path = FIXTURES / "sample.md"
        extractor = get_extractor(path)
        assert extractor is not None
        doc = extractor.extract(path)
        assert doc is not None
        assert len(doc.text) > 0

    def test_round_trip_pdf(self) -> None:
        path = FIXTURES / "sample.pdf"
        extractor = get_extractor(path)
        assert extractor is not None
        doc = extractor.extract(path)
        assert doc is not None
        assert len(doc.text) > 0

    def test_round_trip_docx(self) -> None:
        path = FIXTURES / "sample.docx"
        extractor = get_extractor(path)
        assert extractor is not None
        doc = extractor.extract(path)
        assert doc is not None
        assert len(doc.text) > 0
