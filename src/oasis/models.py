from pathlib import Path

from pydantic import BaseModel, Field


class DocumentMetadata(BaseModel):
    size_bytes: int | None = None
    mtime: float | None = None
    ctime: float | None = None
    language: str | None = None
    author: str | None = None
    title: str | None = None
    page_count: int | None = None


class ExtractedDocument(BaseModel):
    path: Path
    text: str
    metadata: DocumentMetadata
    extraction_errors: list[str] = Field(default_factory=list)
