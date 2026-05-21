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
├── __init__.py          ← exports main() for pyproject.toml entry point
├── models.py
├── cli/
│   ├── __init__.py
│   └── main.py
├── extractors/
│   ├── __init__.py
│   ├── base.py
│   ├── docx.py
│   ├── pdf.py
│   ├── pptx.py
│   ├── registry.py
│   └── text.py
├── index/
│   ├── __init__.py
│   ├── db.py
│   ├── pipeline.py
│   └── store.py
└── query/
    ├── __init__.py
    └── search.py

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

#### Index layer — `src/oasis/index/`

**`db.py`** — `open_db(db_path) -> sqlite3.Connection`: creates the directory if needed, sets `journal_mode=WAL` and `synchronous=NORMAL`, then runs the schema script. The schema adds `title TEXT` and `content TEXT` to `documents` (required by `content=documents` — FTS5 fetches those columns from the base table by rowid). Three triggers keep FTS in sync: `_ai` (after insert), `_ad` (after delete), `_au` (after update — deletes old entry then inserts new).

**`keyword.py`** — `KeywordIndex` class. All SQL in the index layer lives here and nowhere else.
- `upsert(doc)` — SHA-256 content hash (16 hex chars), `INSERT … ON CONFLICT DO UPDATE` (fires the UPDATE trigger, keeping FTS in sync).
- `delete(path)` — `DELETE FROM documents WHERE path = ?`; the `documents_ad` trigger removes the FTS row automatically.
- `search(query, limit) -> list[Result]` — FTS5 `MATCH` joined to `documents`. Uses `snippet(…, char(2), char(3), …)` to avoid *any* string interpolation — SQLite's `char()` produces the sentinel chars from integer literals, so the SQL is fully parameterized. Raises `sqlite3.OperationalError` on bad FTS5 syntax.
- `count() -> int` — `SELECT COUNT(*) FROM documents`.
- `is_unchanged(path, mtime) -> bool` — single-row mtime lookup, used by pipeline for skip logic.
- `Result` dataclass: `path`, `title`, `snippet`, `rank`.
- `MATCH_START = "\x02"`, `MATCH_END = "\x03"` — match highlight sentinels (match `char(2)`/`char(3)` in SQL).

**`store.py`** — gutted of SQL; thin re-export stub only. All callers now use `KeywordIndex`.

**`pipeline.py`** — `index_directory(conn, root, *, force, on_file) -> dict[str, int]`. Instantiates `KeywordIndex(conn)` internally; calls `idx.is_unchanged()` and `idx.upsert()`. Returns `{indexed, skipped, failed, unsupported}`. `on_file` callback decouples progress display from pipeline logic.

#### Query layer — `src/oasis/query/search.py`
Gutted of SQL; re-exports `Result as SearchResult`, `MATCH_START`, `MATCH_END` from `keyword.py` for backward compatibility.

#### CLI — `src/oasis/cli/main.py` + `src/oasis/__init__.py`
Entry point: `oasis = "oasis:main"` in `pyproject.toml` → `__init__.py` → `app()`.

**`oasis index <path>`**
- `--db PATH` (default: `~/.oasis/index.db`)
- `--force / -f` — re-index all files, ignoring mtime
- Rich spinner shows current file name while running; final summary shows indexed/skipped/failed/unsupported counts.

**`oasis search <query>`**
- `--db PATH`, `--limit / -n` (default 20)
- FTS5 query with porter stemming ("extracting" matches "extracts")
- Rich Table: File (relative to cwd if possible), Title, Snippet with bold-yellow match highlights
- Friendly error if DB doesn't exist; catches `OperationalError` for bad FTS5 syntax

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
| `documents` table has `title` and `content` columns even though not in the original spec | `content=documents` in FTS5 requires those columns to exist in the base table; FTS fetches them by rowid |
| FTS match markers use `\x02`/`\x03` (non-printable) not `[`/`]` | Rich uses `[...]` for markup — printable bracket markers would corrupt the display |
| `is_unchanged` checks mtime only, not hash | Avoids reading the file just to skip it; hash is stored for future use |
| `on_file` callback instead of embedding Rich in pipeline | Keeps pipeline testable and decoupled from display concerns |
| `INSERT ... ON CONFLICT DO UPDATE` for upsert | Fires UPDATE trigger (not DELETE+INSERT), so FTS sync is correct for modified files |
| All SQL consolidated in `KeywordIndex` | Single place to audit for injection; parameterized queries enforced by convention at class boundary |
| `snippet(…, char(2), char(3), …)` instead of f-string | Eliminates the last string interpolation in SQL — sentinel chars produced by SQLite integer literals, not Python string formatting |

---

## Up Next

- `src/oasis/extractors/xlsx.py` — Excel extractor using `openpyxl`
- `oasis index` — filter out hidden dirs (`.git`, `__pycache__`) using pathspec
- Vector index layer — LanceDB + sentence-transformers for semantic search
