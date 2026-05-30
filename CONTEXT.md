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
├── config.py
├── models.py
├── cli/
│   ├── __init__.py
│   └── app.py
├── extractors/
│   ├── __init__.py
│   ├── base.py
│   ├── docx.py
│   ├── pdf.py
│   ├── pptx.py
│   ├── registry.py
│   └── text.py
└── index/
    ├── __init__.py
    ├── db.py
    ├── keyword.py
    ├── pipeline.py
    └── walker.py

tests/
├── __init__.py
├── fixtures/
│   ├── sample.docx
│   ├── sample.md
│   ├── sample.pdf
│   ├── sample.pptx
│   └── sample.txt
├── test_config.py
├── test_docx_extractor.py
├── test_extractors.py
├── test_pdf_extractor.py
├── test_pipeline.py
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
    def extract(self, path: Path) -> ExtractedDocument | None: ...
```
- `extract` returns `None` on failure rather than raising — callers can skip bad files and continue.
- `extensions` is a class-level `frozenset`, not a property.

#### Extractor registry — `src/oasis/extractors/registry.py`
- `_EXTRACTOR_MAP: dict[str, Extractor]` — built at import time from each extractor's `extensions` frozenset.
- `get_extractor(path) -> Extractor | None` — O(1) dict lookup on `path.suffix.lower()`.
- To add a new extractor: instantiate it inside the dict comprehension.

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

#### Config — `src/oasis/config.py`
- `CONFIG_PATH: Path` — module-level constant pointing to `~/.config/oasis/config.toml`. Defined at module scope (not inside the class) so tests can `monkeypatch.setattr(config_module, "CONFIG_PATH", ...)` before constructing `OasisConfig()`.
- `OasisConfig(BaseSettings)` — pydantic-settings class with `env_prefix="OASIS_"`.
  - `db_path: Path` — SQLite database; default `~/.oasis/index.db`.
- `settings_customise_sources` — priority: init kwargs > `OASIS_*` env vars > TOML file > field defaults. Reads `CONFIG_PATH` at construction time so monkeypatching works.
- `load_config() -> OasisConfig` — convenience wrapper; just calls `OasisConfig()`.
- A missing or empty TOML file is handled gracefully (returns defaults).
- Additional config fields (`embedding_model`, `vector_path`, `llm_provider`, etc.) will be added when the vector/LLM features are implemented.

#### Index layer — `src/oasis/index/`

**`db.py`** — `open_db(db_path) -> sqlite3.Connection`: creates the directory if needed, sets `journal_mode=WAL` and `synchronous=NORMAL`, then runs the schema script. The schema adds `title TEXT` and `content TEXT` to `documents` (required by `content=documents` — FTS5 fetches those columns from the base table by rowid). Three triggers keep FTS in sync: `_ai` (after insert), `_ad` (after delete), `_au` (after update — deletes old entry then inserts new).

**`keyword.py`** — `KeywordIndex` class. All SQL in the index layer lives here and nowhere else.
- `_file_hash(size, mtime) -> str` — module-level helper; SHA-256 of `"{size}:{mtime}"`, truncated to 16 hex chars. Used by both `upsert` and `is_unchanged`.
- `upsert(doc)` — stores `_file_hash(m.size_bytes, m.mtime)` as `content_hash`, `INSERT … ON CONFLICT DO UPDATE` (fires the UPDATE trigger, keeping FTS in sync).
- `delete(path)` — `DELETE FROM documents WHERE path = ?`; the `documents_ad` trigger removes the FTS row automatically.
- `search(query, limit) -> list[Result]` — FTS5 `MATCH` joined to `documents`. Uses `snippet(…, char(2), char(3), …)` to avoid *any* string interpolation — SQLite's `char()` produces the sentinel chars from integer literals, so the SQL is fully parameterized. Raises `sqlite3.OperationalError` on bad FTS5 syntax.
- `count() -> int` — `SELECT COUNT(*) FROM documents`.
- `last_indexed_at() -> float | None` — `SELECT MAX(indexed_at) FROM documents`; used by `status` command.
- `is_unchanged(path, *, size, mtime) -> bool` — computes `_file_hash(size, mtime)` and compares against stored `content_hash`; skips any file that hasn't changed since last index.
- `Result` dataclass: `path`, `title`, `snippet`, `rank`.
- `MATCH_START = "\x02"`, `MATCH_END = "\x03"` — match highlight sentinels (match `char(2)`/`char(3)` in SQL).


**`walker.py`** — `walk(root, *, extra_excludes, respect_gitignore, exclude_dotfiles) -> Generator[Path]`. Exclusion is layered cheapest-first:
1. `_DIR_EXCLUDES` frozenset — O(1) name check, prunes `dirnames` in-place so `os.walk` never descends. Covers `.git`, `__pycache__`, `node_modules`, `.venv`, `venv`, `dist`, `build`, `target`, and a handful of tool caches.
2. Dotfile/dotdir skip — `name.startswith(".")` guard when `exclude_dotfiles=True` (default).
3. pathspec `gitignore` spec — `_DEFAULT_FILE_PATTERNS` (`.pyc`, `.DS_Store`, etc.) + `extra_excludes` + root-level `.gitignore`. Pattern style is `gitignore` (not the deprecated `gitwildmatch`).
- `followlinks=False` — never follows symlinks, preventing infinite loops.
- Nested `.gitignore` files are not yet loaded (only root-level).

**`pipeline.py`** — `index_directory(conn, root, *, force, extra_excludes, on_file) -> dict[str, int]`. Uses `walk(root, extra_excludes=extra_excludes)` instead of `rglob`. No `is_file()` guard needed — walker yields only files. `extra_excludes` is forwarded so callers can inject additional patterns without touching the walker defaults. Calls `path.stat()` once per file and passes both `st.st_size` and `st.st_mtime` to `is_unchanged` — change detection is hash-of-(size,mtime), not raw mtime comparison. `extractor.extract()` and `idx.upsert()` are each wrapped in `try/except Exception` — any unexpected raise increments `failed` and continues; the run is never aborted by a single bad file.

#### CLI — `src/oasis/cli/app.py` + `src/oasis/__init__.py`
Entry point: `oasis = "oasis:main"` in `pyproject.toml` → `__init__.py` → `app()`. Default `db_path` comes from `load_config().db_path` (resolves to `~/.oasis/index.db` unless overridden by TOML or env var).

**`oasis index <path>`**
- `--db PATH` — default from config
- `--force / -f` — re-index all files, ignoring change detection
- `--verbose / -v` — print each file as it's processed (status label + full path); without flag, shows a transient Rich spinner with the current filename
- Final summary: `N indexed  N skipped  N unsupported  N failed` (zero counts omitted)

**`oasis search <query>`**
- `--db PATH`, `--limit / -n` (default 20)
- FTS5 query with porter stemming ("extracting" matches "extracts")
- Rich Table: `#` rank (bold, `min_width=2` so it survives narrow terminals), File (relative to cwd, clickable `file://` hyperlink), Title, Snippet with bold-yellow match highlights
- After display, saves `[str(path), ...]` to `~/.oasis/last_results.json` for `oasis open`
- "No results." if empty; friendly error if DB doesn't exist; catches `OperationalError` for bad FTS5 syntax

**`oasis open <n>`**
- Opens result `#n` from the last search in the system default application (`open` on macOS)
- Reads `~/.oasis/last_results.json`; errors clearly if no recent search, `n` out of range, or file no longer exists

**`oasis status`**
- `--db PATH`
- Key/value table: Documents (count), DB size (human-readable), Last indexed (timestamp or `—`), DB path
- Prints helpful message if no index exists yet

**`oasis reset`**
- `--db PATH`, `--yes / -y` — skip confirmation
- Prompts for confirmation by default (`typer.confirm`, abort=True on decline)
- Deletes the DB file plus WAL/SHM companions (`*.db-wal`, `*.db-shm`) if present

---

## Key Decisions

| Decision | Reason |
|---|---|
| `DocumentMetadata` is a typed Pydantic model, not a free-form dict | Free-form dicts lose discoverability and type safety across module boundaries |
| `extract` returns `None` on failure, never raises | Indexer must be able to skip bad files and continue without crashing |
| `extensions` is a `frozenset` class attribute, not a property | Immutable, hashable, cheaper; signals it never changes at runtime |
| Models live in `src/oasis/models.py`, not in `extractors/base.py` | Shared models belong at the package root; avoids circular imports as more modules grow |
| `extraction_errors: list[str]` on `ExtractedDocument` | Supports partial-success extraction (e.g. PDF where one page is corrupted) |
| Registry dispatches via extension dict, not `can_handle()` loop | O(1) lookup; all four extractors had identical `can_handle()` bodies — eliminated the method entirely |
| Language detection runs on `text[:2000]` | Avoids processing entire large files; first 2000 chars is sufficient |
| PDF per-page exceptions caught individually | One corrupted page shouldn't discard the rest of the document |
| Scanned PDFs return `None`, not `ExtractedDocument` with empty text | Empty text is useless to the indexer; OCR is deferred |
| `documents` table has `title` and `content` columns even though not in the original spec | `content=documents` in FTS5 requires those columns to exist in the base table; FTS fetches them by rowid |
| FTS match markers use `\x02`/`\x03` (non-printable) not `[`/`]` | Rich uses `[...]` for markup — printable bracket markers would corrupt the display |
| `is_unchanged` compares hash-of-(size,mtime), not raw mtime | Detects size changes (e.g. file written at same second), still avoids reading file bytes; `_file_hash` is also used in `upsert` so skip logic and storage are always in sync |
| `on_file` callback instead of embedding Rich in pipeline | Keeps pipeline testable and decoupled from display concerns |
| `try/except Exception` around `extract()` and `upsert()` in pipeline | Extractor protocol says return None on failure, but defensive wrapping ensures any unexpected raise is caught, logged, and counted as failed — the run never aborts |
| Pipeline test DB placed in a dotdir (`.db/`) inside `tmp_path` | Walker excludes dotdirs by default, so the DB files don't appear as unsupported files in pipeline tests |
| `CONFIG_PATH` is a module-level constant, not embedded in the class | `settings_customise_sources` reads it at construction time, so `monkeypatch.setattr(config_module, "CONFIG_PATH", ...)` works in tests without subclassing |
| Priority: init kwargs > env vars > TOML > defaults | Env vars are the standard override mechanism; TOML is the persistent user config; init kwargs allow programmatic override in tests |
| `INSERT ... ON CONFLICT DO UPDATE` for upsert | Fires UPDATE trigger (not DELETE+INSERT), so FTS sync is correct for modified files |
| All SQL consolidated in `KeywordIndex` | Single place to audit for injection; parameterized queries enforced by convention at class boundary |
| `snippet(…, char(2), char(3), …)` instead of f-string | Eliminates the last string interpolation in SQL — sentinel chars produced by SQLite integer literals, not Python string formatting |
| Walker uses `os.walk` + in-place `dirnames` pruning, not `Path.rglob` | `rglob` loads every path into memory; `os.walk` with pruning never descends into excluded dirs, saving both memory and I/O |
| Exclusion is layered (set → dotfile → pathspec) | Set lookup is O(1); pathspec only runs after cheap guards pass, keeping the hot path fast |
| `gitignore` pattern style instead of deprecated `gitwildmatch` | pathspec 0.12+ deprecates `gitwildmatch`; `gitignore` is the successor with identical semantics |

---

## Tests — 332 total, all passing
| File | Count | Covers |
|---|---|---|
| `test_extractors.py` | 20 | `TextExtractor` interface + extraction |
| `test_pdf_extractor.py` | 16 | `PdfExtractor` interface + success/failure paths |
| `test_docx_extractor.py` | 16 | `DocxExtractor` interface + success/failure paths |
| `test_pptx_extractor.py` | 18 | `PptxExtractor` interface + success/failure paths |
| `test_registry.py` | 14 | Registry dispatch + round-trips for all four formats |
| `test_extractor_edges.py` | 30 | Empty files, non-UTF8, blank DOCX/PPTX, missing metadata, large text, unicode, raw markdown |
| `test_models.py` | 10 | `DocumentMetadata` defaults + `model_dump`, `ExtractedDocument` field coercion |
| `test_walker.py` | 29 | Baseline exclusions, dotfiles, gitignore, patterns, generator contract |
| `test_walker_edges.py` | 18 | Symlinks, no-extension files, extra exclusion dirs, multi-pattern excludes, gitignore comments/read-error, lazy generator |
| `test_keyword.py` | 22 | `_file_hash`, `is_unchanged`, `count`, `delete`, `search` (match, stemming, sentinels, limit, rank) |
| `test_keyword_edges.py` | 18 | `_file_hash(None, None)`, `last_indexed_at`, FTS re-upsert update, bad FTS5 syntax, unicode, empty text, title match, delete+re-upsert, rank ordering |
| `test_db.py` | 18 | `open_db` dir creation, WAL mode, row factory, idempotency, FTS INSERT/UPDATE/DELETE triggers, data persistence, UNIQUE constraint |
| `test_pipeline.py` | 18 | All stat branches, force flag, `on_file` callback, extractor errors, one failure doesn't abort the run |
| `test_human_size.py` | 15 | `_human_size` for all unit boundaries (B, KB, MB, GB, TB), fractional values, return type |
| `test_config.py` | 11 | `CONFIG_PATH`, `db_path` default + TOML load + env var priority + unknown-field validation error, `load_config()` |
| `test_cli.py` | 34 | All five commands, error paths, verbose/force/limit flags, confirmation prompt, `subprocess.run` mock, last-results persistence |
| `test_cli_edges.py` | 21 | Bad FTS5 syntax (exit 1 + tip message), WAL/SHM deletion on reset, corrupted JSON, out-of-range `n`, status exact count, summary zero-count omission, last-results not written on no-match |
| `test_integration.py` | 20 | End-to-end: files → pipeline → search; stemming; walker exclusions; incremental re-index |

---

## Up Next

- Nested `.gitignore` support in walker (load per-directory, not just root)
- Vector index layer — LanceDB + sentence-transformers for semantic search