import logging
from pathlib import Path

import openpyxl

from oasis.models import DocumentMetadata, ExtractedDocument

logger = logging.getLogger(__name__)


class XlsxExtractor:
    extensions: frozenset[str] = frozenset({".xlsx"})

    def extract(self, path: Path) -> ExtractedDocument | None:
        try:
            wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        except Exception:
            logger.warning("Failed to open XLSX %s", path, exc_info=True)
            return None

        try:
            lines: list[str] = []
            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                lines.append(sheet_name)
                for row in ws.iter_rows(values_only=True):
                    row_text = "\t".join(str(cell) for cell in row if cell is not None)
                    if row_text.strip():
                        lines.append(row_text)

            text = "\n".join(lines)
            props = wb.properties
            title: str | None = props.title or None
            author: str | None = props.creator or None
            sheet_count = len(wb.sheetnames)
            wb.close()

            stat = path.stat()
            return ExtractedDocument(
                path=path,
                text=text,
                metadata=DocumentMetadata(
                    size_bytes=stat.st_size,
                    mtime=stat.st_mtime,
                    ctime=stat.st_ctime,
                    title=title,
                    author=author,
                    page_count=sheet_count,
                ),
            )
        except Exception:
            logger.warning("Failed to extract content from XLSX %s", path, exc_info=True)
            wb.close()
            return None
