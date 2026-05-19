from pathlib import Path

import pytest

from oasis.extractors.base import ExtractedDocument, FileMetadata
from oasis.extractors.text import TextExtractor

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


class TestTextExtractorSupport:
    def test_supported_extensions(self, extractor: TextExtractor) -> None:
        assert ".txt" in extractor.supported_extensions
        assert ".md" in extractor.supported_extensions

    def test_supported_extensions_type(self, extractor: TextExtractor) -> None:
        assert isinstance(extractor.supported_extensions, list)
        assert all(isinstance(ext, str) for ext in extractor.supported_extensions)


class TestTextExtractorTxt:
    def test_returns_extracted_document(self, extractor: TextExtractor, txt_file: Path) -> None:
        doc = extractor.extract(txt_file)
        assert isinstance(doc, ExtractedDocument)

    def test_content(self, extractor: TextExtractor, txt_file: Path) -> None:
        doc = extractor.extract(txt_file)
        assert "Oasis" in doc.content
        assert len(doc.content) > 0

    def test_path(self, extractor: TextExtractor, txt_file: Path) -> None:
        doc = extractor.extract(txt_file)
        assert doc.path == txt_file

    def test_language_detected(self, extractor: TextExtractor, txt_file: Path) -> None:
        doc = extractor.extract(txt_file)
        assert doc.language == "en"

    def test_metadata_type(self, extractor: TextExtractor, txt_file: Path) -> None:
        doc = extractor.extract(txt_file)
        assert isinstance(doc.metadata, FileMetadata)

    def test_metadata_size(self, extractor: TextExtractor, txt_file: Path) -> None:
        doc = extractor.extract(txt_file)
        assert doc.metadata.size_bytes == txt_file.stat().st_size
        assert doc.metadata.size_bytes > 0

    def test_metadata_mtime(self, extractor: TextExtractor, txt_file: Path) -> None:
        doc = extractor.extract(txt_file)
        assert doc.metadata.mtime == pytest.approx(txt_file.stat().st_mtime)

    def test_metadata_extension(self, extractor: TextExtractor, txt_file: Path) -> None:
        doc = extractor.extract(txt_file)
        assert doc.metadata.extension == ".txt"


class TestTextExtractorMd:
    def test_returns_extracted_document(self, extractor: TextExtractor, md_file: Path) -> None:
        doc = extractor.extract(md_file)
        assert isinstance(doc, ExtractedDocument)

    def test_content(self, extractor: TextExtractor, md_file: Path) -> None:
        doc = extractor.extract(md_file)
        assert "markdown" in doc.content.lower()

    def test_language_detected(self, extractor: TextExtractor, md_file: Path) -> None:
        doc = extractor.extract(md_file)
        assert doc.language == "en"

    def test_metadata_extension(self, extractor: TextExtractor, md_file: Path) -> None:
        doc = extractor.extract(md_file)
        assert doc.metadata.extension == ".md"

    def test_metadata_size(self, extractor: TextExtractor, md_file: Path) -> None:
        doc = extractor.extract(md_file)
        assert doc.metadata.size_bytes == md_file.stat().st_size
