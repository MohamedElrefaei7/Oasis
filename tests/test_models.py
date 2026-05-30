from pathlib import Path

from oasis.models import DocumentMetadata, ExtractedDocument


class TestDocumentMetadata:
    def test_all_fields_default_to_none(self) -> None:
        m = DocumentMetadata()
        assert m.size_bytes is None
        assert m.mtime is None
        assert m.ctime is None
        assert m.language is None
        assert m.author is None
        assert m.title is None
        assert m.page_count is None

    def test_partial_construction(self) -> None:
        m = DocumentMetadata(size_bytes=1024, title="Report")
        assert m.size_bytes == 1024
        assert m.title == "Report"
        assert m.author is None

    def test_model_dump_excludes_none(self) -> None:
        m = DocumentMetadata(size_bytes=100, mtime=999.0)
        dumped = m.model_dump(exclude_none=True)
        assert "size_bytes" in dumped
        assert "mtime" in dumped
        assert "language" not in dumped
        assert "title" not in dumped

    def test_model_dump_empty_when_all_none(self) -> None:
        m = DocumentMetadata()
        assert m.model_dump(exclude_none=True) == {}

    def test_all_fields_present_in_dump(self) -> None:
        m = DocumentMetadata(
            size_bytes=1, mtime=1.0, ctime=2.0,
            language="en", author="Alice", title="T", page_count=5,
        )
        dumped = m.model_dump(exclude_none=True)
        assert len(dumped) == 7


class TestExtractedDocument:
    def test_extraction_errors_defaults_to_empty_list(self) -> None:
        doc = ExtractedDocument(
            path=Path("/tmp/a.txt"), text="hi", metadata=DocumentMetadata()
        )
        assert doc.extraction_errors == []

    def test_path_stored_as_path_object(self) -> None:
        doc = ExtractedDocument(
            path=Path("/tmp/a.txt"), text="hi", metadata=DocumentMetadata()
        )
        assert isinstance(doc.path, Path)

    def test_string_path_coerced_to_path(self) -> None:
        doc = ExtractedDocument(
            path="/tmp/a.txt", text="hi", metadata=DocumentMetadata()
        )
        assert isinstance(doc.path, Path)

    def test_text_preserved_exactly(self) -> None:
        text = "hello\nworld\t!"
        doc = ExtractedDocument(path=Path("/a.txt"), text=text, metadata=DocumentMetadata())
        assert doc.text == text

    def test_extraction_errors_can_be_populated(self) -> None:
        doc = ExtractedDocument(
            path=Path("/a.txt"),
            text="partial",
            metadata=DocumentMetadata(),
            extraction_errors=["page 3 failed"],
        )
        assert doc.extraction_errors == ["page 3 failed"]
