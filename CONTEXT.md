# Oasis — Development Context

Running log of decisions, current state, and what's next. Updated with every change.

---

## Current State

### Completed

#### Workspace setup
- `.vscode/settings.json` — format-on-save via Ruff, import sorting, pytest integration
- `pyproject.toml` — Ruff configured: `line-length=100`, `target-version="py312"`, ruleset E/F/I/N/UP/B/SIM
- `.gitignore` — toptal Python + VSCode + macOS template, plus `.venv/`, `*.db`, `*.lance/`, `index_data/`, `.env`

#### Package structure
```
src/oasis/
├── __init__.py
├── models.py
├── cli/
│   └── __init__.py
├── extractors/
│   ├── __init__.py
│   ├── base.py
│   ├── docx.py
│   ├── pdf.py
│   ├── pptx.py
│   ├── registry.py
│   └── text.py
├── index/
│   └── __init__.py
└── query/
    └── __init__.py

tests/
├── __init__.py
├── fixtures/
│   ├── sample.docx
│   ├── sample.md
│   ├── sample.pdf
│   ├── sample.pptx
│   └── sample.txt
├── test_docx_extractor.py
├── test_extractors.py
├── test_pdf_extractor.py
├── test_pptx_extractor.py
└── test_registry.py
```

#### Core models — `src/oasis/models.py`
- `DocumentMetadata` — Pydantic model with all-optional fields: `size_bytes`, `mtime`, `ctime`, `language`, `author`, `title`, `page_count`. Each extractor populates only what it can know.
- `ExtractedDocument` — `path: Path`, `text: str`, `metadata: DocumentMetadata`, `extraction_errors: list[str]`. `extraction_errors` supports partial-success (e.g. a PDF where one page fails but the rest extracts).

#### Extractor protocol — `src/oasis/extractors/base.py`
```python
class Extractor(Protocol):
    extensions: frozenset[str]
    def can_handle(self, path: Path) -> bool: ...
    def extract(self, path: Path) -> ExtractedDocument | None: ...
```
- `extract` returns `None` on failure rather than raising — callers can skip bad files and continue.
- `extensions` is a class-level `frozenset`, not a property.

#### Extractor registry — `src/oasis/extractors/registry.py`
- `_EXTRACTORS: list[Extractor]` — ordered list of registered extractor instances.
- `get_extractor(path) -> Extractor | None` — iterates the list and returns the first where `can_handle(path)` is `True`.
- To add a new extractor: instantiate it and append to `_EXTRACTORS`.

#### Text extractor — `src/oasis/extractors/text.py`
- Handles `.txt`, `.md`.
- UTF-8 read; any I/O failure → `None` + warning log.
- `langdetect` on first 2000 chars; `LangDetectException` → `language=None`.
- Captures `size_bytes`, `mtime`, `ctime`.

#### PDF extractor — `src/oasis/extractors/pdf.py`
- Handles `.pdf`.
- Opens with `pypdf.PdfReader`; broad exception on open → `None` + warning.
- Iterates pages; per-page exceptions are caught individually (bad page ≠ bad file) — page logged at DEBUG, appended as empty string.
- If all pages yield empty text → scanned PDF → logs at INFO and returns `None`. OCR deferred to later.
- Captures `page_count`, `title` from `reader.metadata` (guards against missing metadata object).
- Captures `size_bytes`, `mtime`, `ctime`.
- `author` is not in the PDF info dict for this extractor — left `None`.

#### DOCX extractor — `src/oasis/extractors/docx.py`
- Handles `.docx`.
- Opens with `python-docx`; broad exception on open → `None` + warning.
- Iterates `doc.paragraphs`, filters blank lines, joins with `\n`.
- Captures `author`, `title` from `doc.core_properties`; empty string coerced to `None`.
- Captures `size_bytes`, `mtime`, `ctime`.
- `page_count` not available from python-docx — left `None`.

#### PPTX extractor — `src/oasis/extractors/pptx.py`
- Handles `.pptx`.
- Opens with `python-pptx`; broad exception on open → `None` + warning.
- Iterates slides → shapes → text frames → paragraphs; per-shape exceptions caught individually and logged at DEBUG so one broken shape doesn't kill the slide.
- Filters blank paragraphs before joining with `\n`.
- Captures `title`, `author` from `core_properties`; empty string coerced to `None`.
- Uses `page_count` to store slide count (reusing the same field as PDF page count).
- `language` not detected — left `None` (same as DOCX).
- Captures `size_bytes`, `mtime`, `ctime`.

#### Test fixtures — `tests/fixtures/`
- `sample.txt`, `sample.md` — plain text, English.
- `sample.pdf` — minimal text-native single-page PDF generated with raw bytes; pypdf extracts text and `/Title` metadata from it.
- `sample.docx` — created with python-docx; `title="Sample Document"`, `author="Test Author"`, three paragraphs.
- `sample.pptx` — created with python-pptx; two slides, `title="Sample Presentation"`, `author="Test Author"`.

#### Tests — 87 total, all passing
| File | Count | Covers |
|---|---|---|
| `test_extractors.py` | 22 | `TextExtractor` interface, `.txt`, `.md` |
| `test_pdf_extractor.py` | 16 | `PdfExtractor` interface, success path, corrupted/scanned/missing file |
| `test_docx_extractor.py` | 16 | `DocxExtractor` interface, success path, corrupted/missing file |
| `test_pptx_extractor.py` | 19 | `PptxExtractor` interface, success path (all slides, slide count, metadata), corrupted/missing file |
| `test_registry.py` | 14 | Dispatch for all registered types, `None` for unregistered, round-trips for all four formats |

---

## Key Decisions

| Decision | Reason |
|---|---|
| `DocumentMetadata` is a typed Pydantic model, not a free-form dict | Free-form dicts lose discoverability and type safety across module boundaries |
| `extract` returns `None` on failure, never raises | Indexer must be able to skip bad files and continue without crashing |
| `extensions` is a `frozenset` class attribute, not a property | Immutable, hashable, cheaper; signals it never changes at runtime |
| Models live in `src/oasis/models.py`, not in `extractors/base.py` | Shared models belong at the package root; avoids circular imports as more modules grow |
| `extraction_errors: list[str]` on `ExtractedDocument` | Supports partial-success extraction (e.g. PDF where one page is corrupted) |
| Registry dispatches via `can_handle()`, not a raw dict | Keeps the door open for extractors with non-trivial matching (e.g. MIME sniffing) |
| Language detection runs on `text[:2000]` | Avoids processing entire large files; first 2000 chars is sufficient |
| PDF per-page exceptions caught individually | One corrupted page shouldn't discard the rest of the document |
| Scanned PDFs return `None`, not `ExtractedDocument` with empty text | Empty text is useless to the indexer; OCR is deferred |

---

## Up Next

- `src/oasis/extractors/xlsx.py` — Excel extractor using `openpyxl`
- `src/oasis/index/` — SQLite + FTS5 indexing pipeline, change detection
