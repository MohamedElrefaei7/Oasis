from pathlib import Path
from typing import Protocol

from oasis.models import ExtractedDocument


class Extractor(Protocol):
    extensions: frozenset[str]

    def can_handle(self, path: Path) -> bool: ...

    def extract(self, path: Path) -> ExtractedDocument | None: ...
