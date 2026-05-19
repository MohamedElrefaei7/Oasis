from pathlib import Path
from typing import Protocol

from pydantic import BaseModel


class FileMetadata(BaseModel):
    size_bytes: int
    mtime: float
    extension: str


class ExtractedDocument(BaseModel):
    path: Path
    content: str
    language: str | None
    metadata: FileMetadata


class Extractor(Protocol):
    @property
    def supported_extensions(self) -> list[str]: ...

    def extract(self, path: Path) -> ExtractedDocument: ...
