import csv
import logging
from pathlib import Path

from oasis.models import DocumentMetadata, ExtractedDocument

logger = logging.getLogger(__name__)


class CsvExtractor:
    extensions: frozenset[str] = frozenset({".csv"})

    def extract(self, path: Path) -> ExtractedDocument | None:
        try:
            text = self._read(path)
        except Exception:
            logger.warning("Failed to open CSV %s", path, exc_info=True)
            return None

        try:
            stat = path.stat()
            return ExtractedDocument(
                path=path,
                text=text,
                metadata=DocumentMetadata(
                    size_bytes=stat.st_size,
                    mtime=stat.st_mtime,
                    ctime=stat.st_ctime,
                ),
            )
        except Exception:
            logger.warning("Failed to extract content from CSV %s", path, exc_info=True)
            return None

    def _read(self, path: Path) -> str:
        for encoding in ("utf-8", "latin-1"):
            try:
                with path.open(newline="", encoding=encoding) as f:
                    reader = csv.reader(f)
                    lines = [
                        "\t".join(cell for cell in row)
                        for row in reader
                        if any(cell.strip() for cell in row)
                    ]
                return "\n".join(lines)
            except UnicodeDecodeError:
                continue
        raise ValueError(f"Could not decode {path} with utf-8 or latin-1")
