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
│   ├── csv.py
│   ├── pptx.py
│   ├── registry.py
│   ├── text.py
│   └── xlsx.py
├── index/
│   ├── __init__.py
│   ├── chunker.py
│   ├── db.py
│   ├── embeddings.py
│   ├── keyword.py
│   ├── pipeline.py
│   ├── vector.py
│   └── walker.py
├── llm/
│   ├── __init__.py
│   ├── base.py
│   ├── claude.py
│   └── ollama.py
└── query/
    ├── __init__.py
    ├── parser.py
    ├── retriever.py
    ├── reranker.py
    └── snippets.py

tests/
├── __init__.py
├── fixtures/
│   ├── sample.docx
│   ├── sample.md
│   ├── sample.pdf
│   ├── sample.pptx
│   ├── sample.csv
│   ├── sample.txt
│   └── sample.xlsx
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

#### CSV extractor — `src/oasis/extractors/csv.py`
- Handles `.csv`.
- Uses Python's built-in `csv` module — no new dependency.
- Tries UTF-8 first, falls back to latin-1 on `UnicodeDecodeError`; raises `ValueError` if both fail.
- Emits each non-empty row as tab-separated cell values, joined with `\n`.
- No title/author/page_count available from CSV format — all left `None`.
- Captures `size_bytes`, `mtime`, `ctime`.

#### XLSX extractor — `src/oasis/extractors/xlsx.py`
- Handles `.xlsx`.
- Opens with `openpyxl.load_workbook(read_only=True, data_only=True)`; broad exception on open → `None` + warning.
- Iterates all sheets; emits the sheet name as a line, then each non-empty row as tab-separated cell values.
- Captures `title`, `author` from `wb.properties` (`.title` / `.creator`); empty string coerced to `None`.
- Uses `page_count` to store sheet count (consistent with PPTX slide count).
- `language` not detected — left `None`.
- Captures `size_bytes`, `mtime`, `ctime`.
- Calls `wb.close()` in both success and exception paths to free the read-only zip handle.

#### Test fixtures — `tests/fixtures/`
- `sample.txt`, `sample.md` — plain text, English.
- `sample.pdf` — minimal text-native single-page PDF generated with raw bytes; pypdf extracts text and `/Title` metadata from it.
- `sample.docx` — created with python-docx; `title="Sample Document"`, `author="Test Author"`, three paragraphs.
- `sample.pptx` — created with python-pptx; two slides, `title="Sample Presentation"`, `author="Test Author"`.
- `sample.xlsx` — created with openpyxl; two sheets ("Sales" with header + data rows, "Notes" with text), `title="Sample Spreadsheet"`, `creator="Oasis Test"`.
- `sample.csv` — plain CSV; header row + four data rows with Month, Revenue, Units, Region columns.

#### Tests — 797 total, all passing
| File | Count | Covers |
|---|---|---|
| `test_extractors.py` | 22 | `TextExtractor` interface, `.txt`, `.md` |
| `test_pdf_extractor.py` | 16 | `PdfExtractor` interface, success path, corrupted/scanned/missing file |
| `test_docx_extractor.py` | 16 | `DocxExtractor` interface, success path, corrupted/missing file |
| `test_pptx_extractor.py` | 19 | `PptxExtractor` interface, success path (all slides, slide count, metadata), corrupted/missing file |
| `test_registry.py` | 17 | Dispatch for all registered types, `None` for unregistered, round-trips for all six formats |

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

**`pipeline.py`** — `index_directory(conn, root, *, force, extra_excludes, on_file, vector_index, embedder, on_chunks_progress) -> dict[str, int]`. Two-phase:
- **Phase 1** (walk → extract → keyword): same as before. If `vector_index` and `embedder` are both provided, successfully indexed docs are chunked (`chunk_document`) and queued in `_PendingDoc` records.
- **Phase 2** (embed → vector upsert): runs if `pending` is non-empty. Deletes stale vector chunks for each doc (`vector_index.delete_by_doc_id`), flattens all chunks, iterates in `EMBED_BATCH=64` batches — `embedder.embed(texts)` → `ChunkRow` objects → `vector_index.upsert_chunks(rows)`. Calls `on_chunks_progress(done, total)` after each batch (and once with `done=0` to announce total).
- Stats dict gains `"chunks"` key (always present, 0 when embedding disabled). `chunk_id` is `"{path}:{chunk_index}"` — stable across re-indexes, unique per document.
- `get_doc_id(path) -> int | None` added to `KeywordIndex` — used to retrieve the SQLite row ID after upsert so LanceDB rows can reference the same document.

#### CLI — `src/oasis/cli/app.py` + `src/oasis/__init__.py`
Entry point: `oasis = "oasis:main"` in `pyproject.toml` → `__init__.py` → `app()`. Default `db_path` comes from `load_config().db_path` (resolves to `~/.oasis/index.db` unless overridden by TOML or env var).

**Embedding + vector index**: always enabled in `oasis index`. Before scanning, the command initializes `SentenceTransformerEmbedder()` (uses `_MODEL_CACHE` — only loads once per process) and `VectorIndex` (LanceDB at `{db_stem}.lance` next to the SQLite DB). A `_console.status()` spinner covers this ~0.5 s load.

**Progress bar**: unified `Progress` bar for both scan and embed phases. Non-verbose mode: scan task (indeterminate spinner) hides itself when embedding starts; embed task shows BarColumn + MofNCompleteColumn + TimeRemainingColumn + `_ChunksPerSecColumn`. The bar is not transient — final state stays visible. Verbose mode: per-file printing during scan; a lazily-created Progress bar for embedding.

`_ChunksPerSecColumn(ProgressColumn)` renders `task.speed` as `"N chunks/s"` (Rich automatically computes speed from `progress.update(..., advance=n)` calls).

**`oasis index <path>`**
- `--db PATH` — default from config
- `--force / -f` — re-index all files, ignoring change detection
- `--verbose / -v` — print each file as it's processed (status label + full path); without flag, shows a transient Rich spinner with the current filename
- Final summary: `N indexed  N skipped  N unsupported  N failed` (zero counts omitted)

**`oasis search <query>`**
- `--db PATH`, `--limit / -n` (default 10 = `DEFAULT_TOP_N`), `--mode / -m` (default `hybrid`)
- Three retrieval modes controlled by `SearchMode(str, enum.Enum)`:
  - **`keyword`**: FTS5 BM25 search via `KeywordIndex.search()`, porter stemming. Catches `OperationalError` for bad FTS5 syntax.
  - **`semantic`**: Loads `SentenceTransformerEmbedder` + `VectorIndex` (spinner), embeds query, runs cosine vector search, deduplicates to best chunk per path, highlights via `text_snippet`. Does not use FTS5 — immune to FTS5 syntax errors.
  - **`hybrid`** (default): Loads models (spinner), calls `hybrid_search(top_n=max(limit*2, 20))` for a larger candidate pool, reranks with `CrossEncoderReranker(top_n=limit)`. Catches `OperationalError` (FTS5 used internally). Best quality.
- `_render_results_table(rows)` — extracted helper; builds `#`/`File`/`Title`/`Snippet` table, `file://` hyperlinks, `relative_to(cwd)` path display.
- Footer: `N result(s)  ·  mode: {mode}  ·  db: {db_path}`
- After display, saves `[str(path), ...]` to `~/.oasis/last_results.json` for `oasis open`
- "No results." if empty; friendly error if DB doesn't exist; bold-yellow `MATCH_START`/`MATCH_END` highlighting in all modes

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

#### Phase 5.1 — Evaluation harness (`eval/`)
- **`eval/corpus/`** — 301-file labeled corpus across 7 formats (pdfs/docx/pptx/xlsx/csv/md/txt) + `MANIFEST.md`. Copied from Downloads with `cp -Rp` so **mtimes are preserved** — the corpus spreads mtimes deterministically over 2019-01..2026-06 by filename hash, which the date-filter queries depend on.
- **`eval/queries.yaml`** — 83 labeled queries. Each: `id`, `query`, `relevant: [{path, grade}]` (graded 3/2/1; grade ≥ 1 is relevant for binary metrics, grades feed NDCG), `tags`, optional `notes`. `relevant: []` marks adversarial "expected-empty" queries.
- **`eval/run_eval.py`** — builds a dedicated index (`eval/index/index.db` + `.lance`, gitignored) over the corpus, then for each query: parses it the same way the CLI does (`ensure_ollama()` → `parse_query`, raw fallback, fixed `today=2026-07-07`), calls `hybrid_search(top_n=20)` + `CrossEncoderReranker(top_n=10)`, maps absolute result paths back to corpus-relative keys, and scores with **ranx**.
  - Metrics: `precision@5`, `precision@10`, `recall@10`, `mrr`, `ndcg@10`.
  - Per-tag breakdown via qrels/run subsets; expected-empty queries reported separately (not in averages).
  - Writes `eval/results/latest.json` (overall + by_tag + per_query + expected_empty + config + git commit) and appends `eval/results/history.jsonl` (timestamp + commit + overall) for regression tracking.
  - Flags: `--reindex`, `--no-rerank` (score raw fusion), `--no-parse` (raw query), `--today`.
- **`eval/plot.py`** — matplotlib chart of `ndcg@10` / `precision@5` / `mrr` over `history.jsonl` → `eval/results/metrics_over_time.png`.
- Deps added to the `eval` group: `ranx`, `pyyaml`, `matplotlib`.
- **First-run baseline (Ollama unavailable → raw mode, rerank on):** ndcg@10 **0.455**, mrr **0.440**, recall@10 **0.555**, precision@5 **0.188**, precision@10 **0.111**. Low precision@k is structural — most queries have a single relevant doc, capping precision@5 at 0.2; MRR/NDCG are the meaningful headline numbers.
- **Findings surfaced by the eval (not yet fixed):**
  - Without Ollama, queries run raw so `file_types`/`date_range`/`folders` filters never apply, and query punctuation reaches FTS5 verbatim — apostrophes/commas (`amazon's…`, q072–075/q080) throw `fts5: syntax error`, and because `hybrid_search` wraps FTS + vector in one try, the whole call fails and the semantic arm is lost too. Real robustness gap in `hybrid_search`; the harness catches `OperationalError` per-query and scores those as empty.
  - `txt/edge-latin1-menu.txt` can't be read by the UTF-8 text extractor (skipped as failed). No query targets it, so scores are unaffected.

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
| Eval calls `hybrid_search()` directly, not the CLI | Avoids subprocess overhead and exercises the real retrieval path; the reranker is applied on top exactly as the CLI's hybrid mode does |
| Eval corpus copied with `cp -Rp` (mtimes preserved) | Date-filter queries judge relevance by mtime; the corpus encodes dates in mtimes (2019–2026), so losing them would silently break ~18 date-tagged queries |
| Qrels/run keyed by corpus-relative POSIX paths | `queries.yaml` labels are relative (`pdfs/…`); results are absolute — normalize results via `relative_to(CORPUS_DIR)` so ranx lines them up |
| Expected-empty queries (`relevant: []`) scored separately | ranx has no notion of "should return nothing"; averaging them would be meaningless, so they're reported as a diagnostic (num_results/top_paths) instead |
| Empty result sets get a `{"__no_results__": 0.0}` sentinel in the run | ranx requires every qrels query to appear in the run; a grade-0 sentinel yields a clean 0 for that query without crashing |
| Per-query FTS `OperationalError` caught and scored as empty | Adversarial/punctuation queries must not abort the whole eval; mirrors the CLI degrading to a syntax tip |
| Dedicated eval index under `eval/index/` (gitignored) | Keeps eval reproducible and isolated from the user's real `~/.oasis` index; only metric artifacts (`latest.json`, `history.jsonl`, plot) are committed |

---

## Tests — 794 total, all passing
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
| `test_chunker.py` | 35 | Empty/whitespace input, single-chunk short text, token count accuracy, multi-chunk long text, sequential indices, first/last chunk sizes, overlap shared-token invariant, full reconstruction, exact boundary values, custom parameters, unicode/CJK, invalid argument errors |
| `test_embeddings.py` | 25 | `_load_model` caching (same instance, call count), multi-model isolation, `embed` shape/dtype/empty, encode call args (batch_size, show_progress_bar, convert_to_numpy), all texts in one call, protocol compliance |
| `test_vector.py` | 58 | `_build_schema` fields + fixed-size list type, `VectorIndex` construction (connect path, exist_ok), `upsert_chunks` merge_insert chain + row serialization, `search` metric/select/limit/where/results, `delete_by_doc_id` SQL predicate, `count`, integration tests (real LanceDB) covering upsert, overwrite, delete, where filter, limit, empty table, persistence across open |
| `test_pipeline.py` (updated) | 31 | All original tests + `"chunks"` key always present, vector/embedder optional, embed called for indexed files, delete before upsert, `chunks` stat sums across docs, `on_chunks_progress` callback (first call done=0, last done==total, total matches stat), skipped files not re-embedded |
| `test_reranker.py` | 36 | `_clean` strips `MATCH_START`/`MATCH_END` markers, preserves plain text; `_load_model` caching (same instance, call count, separate models); `CrossEncoderReranker` construction (default/custom model name, shared cache); `rerank` return type/count, empty input, single result; score replacement + descending sort, correct result moved to top, other fields preserved; `predict` called once with `show_progress_bar=False`, pairs have query first and clean snippet second; `top_n` truncation keeps highest-scored, None returns all, 0 returns empty |
| `test_cli.py` (updated) | 44 | All original tests + `--mode keyword/semantic/hybrid` flags, footer shows mode name, `-m` short flag, invalid mode exits non-zero, default mode is hybrid |
| `test_cli_edges.py` (updated) | 27 | All original edge tests + keyword/hybrid mode exits 1 on bad FTS5 syntax, semantic mode ignores FTS5 syntax (exit 0), no-results in keyword and hybrid modes |
| `test_retriever.py` | 33 | `_rrf` unit tests (reciprocal score formula, rank ordering, doc-in-both-lists bonus, empty input), `hybrid_search` return type/shape, result fields (path, doc_id, title, snippet, score), ranking correctness (sorted by score, dual-list doc outranks single-list), kw-only and vec-only docs included, path deduplication, chunk dedup keeps best-scoring chunk, embedder called once with query text, vector search limit forwarded, constants |
| `test_snippets.py` | 40 | `_extract_terms` (simple, multi-word, boolean operators stripped, quoted phrases expanded, empty/operators-only), `_highlight_terms` (wraps match, case-insensitive, multiple terms, empty terms no-op, no match), `fts_snippet` (returns string, contains markers, None when doc absent, None on OperationalError, custom num_tokens, constant), `text_snippet` (returns string, no ellipsis on short text, highlights terms, no-match starts from beginning, empty query, truncation, leading/trailing ellipsis), `get_snippet` (uses FTS when available, falls back when doc not in index, fallback highlights terms, falls back on OperationalError) |

---

#### Chunker — `src/oasis/index/chunker.py`
- `Chunk` dataclass: `chunk_index: int`, `text: str`, `token_count: int`.
- `chunk_document(text, *, chunk_size=500, overlap=50) -> list[Chunk]`
  - Empty / whitespace-only text → `[]`.
  - Raises `ValueError` if `chunk_size <= 0` or `overlap >= chunk_size`.
  - Encodes the full text with tiktoken `cl100k_base`, slides a window of `chunk_size` tokens stepping by `chunk_size - overlap` each time, decodes each window back to text.
  - Short text (< `chunk_size` tokens) produces exactly one chunk.
  - The last chunk may be smaller than `chunk_size`.
- `_ENC` is a module-level `tiktoken.Encoding` instance (loaded once at import time; tiktoken caches the encoding file on disk).
- Constants `CHUNK_SIZE = 500`, `OVERLAP = 50` are exported for callers that want the defaults without magic numbers.

| Decision | Reason |
|---|---|
| tiktoken `cl100k_base` encoding | Close to Claude's token counts; already a project dependency; stable and well-tested |
| `@dataclass` for `Chunk`, not Pydantic | Internal to the index layer; no cross-boundary serialization needed; keeps it lightweight |
| Module-level `_ENC` | Avoids re-loading the encoding on every call; safe because tiktoken encodings are thread-safe and immutable |
| `text.strip()` empty check before encoding | Avoids inserting meaningless empty chunks for documents that are all whitespace |

---

## Up Next

- Nested `.gitignore` support in walker (load per-directory, not just root)
#### Embedding interface — `src/oasis/index/embeddings.py`
- `EmbeddingModel` Protocol: `dimension: int` + `embed(texts: list[str]) -> np.ndarray`.
- `SentenceTransformerEmbedder(model_name, batch_size)` — wraps `sentence-transformers`.
  - `_load_model(name)` — module-level cache (`_MODEL_CACHE: dict[str, SentenceTransformer]`); the transformer is loaded at most once per model name per process.
  - `embed([])` returns `np.empty((0, dimension), dtype=float32)` without calling the model.
  - All texts passed to a single `model.encode(texts, batch_size=..., show_progress_bar=False, convert_to_numpy=True)` call — sentence-transformers handles internal batching.
  - Uses `get_embedding_dimension()` (v5 API; `get_sentence_embedding_dimension()` is deprecated).
- `DEFAULT_MODEL = "all-MiniLM-L6-v2"` (384-dim), `BATCH_SIZE = 32`.

| Decision | Reason |
|---|---|
| Module-level `_MODEL_CACHE` dict | Loading a transformer costs ~seconds + hundreds of MB; must not repeat it per embedder instance or per request |
| Single `encode()` call, not a loop | sentence-transformers already splits into batches internally; looping one-by-one would kill throughput |
| `show_progress_bar=False` | Output would corrupt CLI / log output in production use |
| `assert dim is not None` on `get_embedding_dimension()` | Returns `None` for degenerate model configs; fail fast with a clear message instead of a confusing downstream shape error |

#### Vector store — `src/oasis/index/vector.py`
- `ChunkRow` dataclass: `chunk_id: str`, `doc_id: int`, `text: str`, `vector: np.ndarray`, `extension: str`, `mtime: float`, `path: str`.
- `VectorResult` dataclass: `chunk_id: str`, `doc_id: int`, `text: str`, `path: str`, `score: float` (mapped from `_distance`).
- `VectorIndex(db_path, dimension)` — opens or creates a LanceDB database and the `chunks` table. Schema built dynamically: `vector` column typed as `pa.list_(pa.float32(), dimension)` (a `FixedSizeList`).
- `upsert_chunks(records)` — converts `vector` to a Python list (`float32`), calls `merge_insert('chunk_id').when_matched_update_all().when_not_matched_insert_all().execute(rows)`. No-ops on empty list.
- `search(query_vector, *, limit=10, where=None) -> list[VectorResult]` — cosine metric; selects `chunk_id`, `doc_id`, `text`, `path`, `_distance`; optional SQL `where` filter (e.g. `"extension = '.txt'"`). Zero/NaN query vectors return empty results (cosine of zero vector is undefined in LanceDB).
- `delete_by_doc_id(doc_id)` — `tbl.delete(f"doc_id = {doc_id}")`.
- `count() -> int` — `tbl.count_rows()`.

| Decision | Reason |
|---|---|
| `pa.list_(pa.float32(), dimension)` for vector column | LanceDB requires fixed-size list for ANN index; dimension is injected at construction time |
| Cosine metric | All-MiniLM-L6-v2 embeddings are not normalized by default; cosine handles arbitrary magnitudes |
| `_distance` mapped to `score` on `VectorResult` | Lower is better for cosine/L2; callers score-fuse with BM25 scores and need a consistent field name |
| No-op on `upsert_chunks([])` | Avoids a no-op merge_insert call; LanceDB merge_insert with empty input would still open a write transaction |
| `from __future__ import annotations` | Defers evaluation of type hints; avoids circular import issues as the module graph grows |

---

#### Hybrid retriever — `src/oasis/query/retriever.py`
- `HybridResult` dataclass: `path: Path`, `doc_id: int`, `title: str | None`, `snippet: str`, `score: float`.
- `_rrf(ranked_lists: list[list[str]]) -> dict[str, float]` — pure Reciprocal Rank Fusion; `score(d) = Σ 1/(RRF_K + rank_i)` for each ranked list where d appears. `RRF_K = 60`.
- `hybrid_search(conn, vector_index, embedder, query, *, top_n=10, candidate_limit=50) -> list[HybridResult]`:
  1. FTS5 via `KeywordIndex.search(query, limit=50)` → document-level ranked list
  2. `embedder.embed([query])[0]` → query vector; `vector_index.search(vec, limit=50)` → chunk-level results
  3. Deduplicate vector results to best chunk per doc (lowest `_distance`)
  4. RRF over the two path-keyed ranked lists → fused scores
  5. Assemble `HybridResult` objects, picking `doc_id`/`title`/`snippet` from FTS5 if available, else from vector; sort by score descending; return top N
- `snippet`: FTS5 snippet with `MATCH_START`/`MATCH_END` markers when the doc matched keyword search; raw chunk text otherwise (compatible with `_highlight_snippet` in CLI).
- `KeywordIndex.Result` gained `doc_id: int` field (SQL updated to include `d.id`); needed to populate `HybridResult.doc_id` for FTS5-only hits.

| Decision | Reason |
|---|---|
| RRF over score normalization | BM25 and cosine distances are on different scales; rank-based fusion avoids calibration entirely |
| `k=60` | Standard constant from the original RRF paper; large enough to dampen rank differences while preserving rank-1 bonus |
| Chunk dedup before RRF | Multiple chunks from one doc would otherwise inflate that doc's rank; dedup first makes both lists operate at the same granularity (document-level) |
| FTS5 snippet preferred over chunk text | FTS5 snippet has highlight markers and is trimmed to the matching context; chunk text is raw |
| `candidate_limit=50` for both searches | Large enough to surface relevant docs; small enough to keep latency predictable |

---

#### Cross-encoder reranker — `src/oasis/query/reranker.py`
- `CrossEncoderReranker(model_name)` — wraps `sentence_transformers.CrossEncoder`. Model loaded once per name via `_MODEL_CACHE` (same pattern as `SentenceTransformerEmbedder`).
- `rerank(query, results, *, top_n=None) -> list[HybridResult]` — builds `(query, clean_snippet)` pairs for every result, calls `model.predict(pairs, show_progress_bar=False)`, replaces each result's `score` field with the cross-encoder logit via `dataclasses.replace()`, sorts descending, returns first `top_n` (or all if `top_n=None`).
- `_clean(text)` — strips `MATCH_START`/`MATCH_END` FTS5 markers before scoring (cross-encoder should see plain text, not `\x02`/`\x03`).
- `DEFAULT_CE_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"` — trained on MS MARCO passage ranking; strong quality/speed tradeoff at 6 layers.
- `np.atleast_1d(np.asarray(...))` guards against scalar output from single-pair predict calls.

| Decision | Reason |
|---|---|
| Replace `score` with CE logit | `score` always reflects current ordering basis; callers don't need to distinguish RRF score vs CE score |
| `show_progress_bar=False` | Suppress tqdm output that would corrupt CLI / log output |
| `dataclasses.replace()` instead of mutation | Returns new immutable snapshots; original list unchanged for callers that need it |
| `top_n` optional | Reranking top-20 and then taking top-10 is a common pattern; caller decides the final window |

---

#### Snippet generation — `src/oasis/query/snippets.py`
- `SNIPPET_TOKENS = 40` — FTS5 token budget passed to `snippet()` (≈200 chars for English prose).
- `_extract_terms(query) -> list[str]` — extracts plain word tokens from an FTS5 query expression, skipping boolean operators (`AND`, `OR`, `NOT`, `NEAR`); quoted phrases are expanded to individual words.
- `_highlight_terms(text, terms) -> str` — wraps all occurrences of `terms` in `text` with `MATCH_START`/`MATCH_END` via case-insensitive regex substitution.
- `fts_snippet(conn, query, doc_id, *, num_tokens=SNIPPET_TOKENS) -> str | None` — queries `documents_fts` with `snippet(documents_fts, 2, char(2), char(3), '…', num_tokens)` filtered by `rowid = doc_id`; returns `None` when the doc isn't in the index or if an `OperationalError` is raised (bad FTS5 syntax).
- `text_snippet(text, query, *, length=200) -> str` — pure-Python fallback: locates the first regex match of any extracted term, centers a `length`-char window on it, prepends/appends `'…'` when truncated, then calls `_highlight_terms` on the excerpt.
- `get_snippet(conn, query, doc_id, fallback_text, *, length=200) -> str` — tries `fts_snippet` first; falls back to `text_snippet(fallback_text, ...)` on `None`.

| Decision | Reason |
|---|---|
| FTS5 `snippet()` preferred over pure-Python | SQLite's built-in snippet function is aware of porter stemming and FTS5's internal token positions; it finds the best window without re-tokenizing |
| `char(2)`/`char(3)` for highlight markers | Matches `MATCH_START`/`MATCH_END` constants without any string interpolation in SQL |
| `OperationalError` caught in `fts_snippet` | Bad FTS5 syntax raises at execute time; caller shouldn't crash — fall back to plain-text gracefully |
| Column index 2 in `snippet()` | `documents_fts` columns are `path`(0), `title`(1), `content`(2); we want snippets from the document body |
| `_extract_terms` strips operators before regex | Regex-matching `AND` or `NOT` against document text would produce false highlights |
| `text_snippet` centers on first match | Users scan from the top; showing the first occurrence is the most natural fallback |

---

#### NL query schema + parser — `src/oasis/query/parser.py`
- `DateRange(BaseModel)` — `after: datetime | None`, `before: datetime | None`. Validator rejects `before <= after` when both are set.
- `ParsedQuery(BaseModel)` — the schema the LLM fills in:
  - `semantic_query: str` — distilled NL string for the embedding model; stripped, must be non-empty.
  - `file_types: list[str]` — normalised to lowercase with leading dot (e.g. `"PDF"` → `".pdf"`); empty strings dropped.
  - `date_range: DateRange | None` — optional time window; accepts dict coercion from LLM JSON.
  - `folders: list[str]` — subtree hints mentioned by the user.
  - `keywords: list[str]` — exact terms to feed to FTS5.
  - `confidence: float` — LLM self-reported confidence, clamped `[0, 1]` by `ge`/`le` constraints.
- `_SYSTEM_PROMPT` — module-level constant with field descriptions, file-type synonym mappings (powerpoint→.pptx, spreadsheet→.xlsx, etc.), relative date resolution rules (last month, last year, this year, yesterday, last week, since <month>), and 6 worked examples illustrating the full output format.
- `parse_query(text, llm, *, today=None) -> ParsedQuery` — injects today's date into the user prompt (`"Today is {iso}.\nQuery: {text}"`), delegates to `llm.complete(prompt, ParsedQuery, system=_SYSTEM_PROMPT)`, and returns the validated `ParsedQuery` instance. `today` defaults to `date.today()`.

| Decision | Reason |
|---|---|
| `field_validator` for extension normalisation | LLMs produce inconsistent extension formats ("PDF", "pdf", ".pdf"); normalise at parse time so callers never need to handle variants |
| `confidence` field | Lets the caller decide whether to fall back to a simpler query if the LLM was uncertain; doesn't affect retrieval logic here |
| `DateRange` as a nested model, not two flat fields | Keeps the datetime pair together; makes it easy to pass as a unit to a SQL `WHERE mtime BETWEEN` clause |
| Today's date injected per-call, not baked into the system prompt | System prompt can be cached/constant; only the user message changes per request |
| `today=None` optional override | Makes date-sensitive regression tests deterministic without monkeypatching `date.today` |
| Six worked examples in the system prompt | LLMs follow examples more reliably than prose rules alone; examples cover all field combinations |

#### LLM provider abstraction — `src/oasis/llm/`

**`base.py`** — `LLMProvider(Protocol)` with `@runtime_checkable`. Single method:
```python
def complete(self, prompt: str, response_model: type[T], *, system: str | None = None) -> T
```
Both providers satisfy the Protocol structurally (no inheritance required).

**`ollama.py`** — `OllamaProvider`:
- Wraps `openai.OpenAI(base_url=..., api_key="ollama")` via `instructor.from_openai(..., mode=JSON)`.
- JSON mode is forced explicitly so any Ollama model works regardless of tool-calling support.
- `DEFAULT_MODEL = "llama3.2:3b"`, `DEFAULT_BASE_URL = "http://localhost:11434/v1"`.
- `complete()` passes `model`, `messages`, `response_model` to `client.chat.completions.create()`.

Both providers share a module-private `_build_messages(prompt, system)` helper.

| Decision | Reason |
|---|---|
| `JSON` mode for Ollama | Smaller models (3b) don't reliably use function calling; JSON mode works on any Ollama model that supports `format=json` |
| `openai.OpenAI` client for Ollama | Ollama exposes an OpenAI-compatible API; no extra package needed |
| `api_key="ollama"` placeholder | Ollama's API accepts any non-empty key; "ollama" is the conventional placeholder |
| `max_tokens` only on Claude | OpenAI-compatible APIs (Ollama) don't require it; Anthropic's API does |
| `@runtime_checkable` on Protocol | Allows `isinstance(provider, LLMProvider)` checks at runtime for guard clauses |
| `from __future__ import annotations` | Defers annotation evaluation; consistent with rest of codebase |


| Test file | Count | Covers |
|---|---|---|
| `test_llm_providers.py` | 17 | Protocol conformance, constants, construction (default/custom args, base URL, JSON mode, placeholder API key), `complete()` (return type, single create call, response_model forwarded, messages structure with/without system, model passed) |
| `test_parse_query.py` | 91 | Prompt-structure tests (system prompt contents, today date injected, query label, correct class forwarded); 25 regression cases (each validated as ParsedQuery, called once, query in prompt); prompt-engineering invariants |
| `test_ollama_manager.py` | 22 | `_server_running` (200 → true, OSError → false), `_model_available` (in list → true, absent/error/timeout → false), `_start_server` (no binary → false, spawns correct command, devnull I/O, polls until ready, times out), `ensure_ollama` (happy path, server-not-starting → None, auto-start triggered, model-absent → None, default/custom model forwarded) |
| `test_cli.py` (updated) | 50 | All prior tests + `ensure_ollama` called on every search, `parse_query` called with raw query text, footer shows "·  parsed" when LLM available, no "·  parsed" when Ollama unavailable, parse exception falls back gracefully |
| `test_retriever.py` (updated) | 40 | All prior tests + `file_types` filters FTS5 results by path suffix, passes `WHERE extension IN (...)` to vector search, empty/None → no where clause, vec-only match through filter, blocked extension returns empty |

#### Ollama lifecycle — `src/oasis/llm/manager.py`
- `_server_running() -> bool` — tries `urllib.request.urlopen("http://localhost:11434/", timeout=1)`; any exception → False.
- `_model_available(model) -> bool` — runs `ollama list` via subprocess, checks `model in stdout`; any error → False.
- `_start_server() -> bool` — checks `shutil.which("ollama")`, spawns `ollama serve` (stdout/stderr → DEVNULL), polls `_server_running()` every 0.25 s up to `_STARTUP_TIMEOUT=5.0` s.
- `ensure_ollama(model=DEFAULT_MODEL) -> OllamaProvider | None` — calls `_server_running()`, auto-starts if not running, checks model availability, returns `OllamaProvider` or `None` silently.

#### `oasis search` — NL query parsing
- Every search invokes `ensure_ollama()` behind a "Parsing query…" spinner.
- If an `OllamaProvider` is returned, `parse_query(query, llm)` is called; on any exception, falls back silently to the raw query.
- `effective_query = parsed.semantic_query` (strips file-type/date words from the query before embedding and FTS5).
- `file_types = parsed.file_types` — passed to `hybrid_search(file_types=...)` and as a vector `WHERE` clause in semantic mode; post-filters FTS5 results in keyword mode.
- Footer shows `·  parsed` when NL parsing succeeded.

#### `hybrid_search` — `file_types` parameter
- New optional `file_types: list[str] | None = None` parameter.
- FTS5 results are post-filtered: `Path(r.path).suffix.lower() in file_types`.
- Vector search receives `where=f"extension IN ({quoted})"` when `file_types` is set.

| Decision | Reason |
|---|---|
| `ensure_ollama` returns `None` silently (no print) | Ollama being absent is a normal state — search should always work, just without NL parsing |
| Auto-start via `ollama serve` | Users who have Ollama installed shouldn't need to remember to start it manually |
| `_STARTUP_TIMEOUT = 5.0 s` | Enough time for a warm start; cold starts take longer but are rare once the daemon is running |
| Post-filter FTS5 by suffix, SQL filter for vector | FTS5 doesn't have an extension column; vector index does (via `extension` column in LanceDB schema) |
| `effective_query` replaces raw query for both FTS5 and embedding | Stripped semantic_query omits file-type/date words that confuse FTS5 stemming and embed noise |

#### 4.4 – 4.7 wiring

**4.4 Dateparser coercion** — `DateRange.after`/`before` now accept any string that `dateparser` can resolve (ISO dates, "January 2024", year-only, etc.) in addition to `datetime` objects. Validator calls `dateparser.parse(v, settings={RETURN_AS_TIMEZONE_AWARE: False, PREFER_DAY_OF_MONTH: first, PREFER_DATES_FROM: past})` and strips timezone info. Invalid strings raise `ValidationError`. The LLM produces concrete ISO dates; this coercion is a safety net.

**4.5 Structured retrieval** — `hybrid_search()` now takes a `ParsedQuery` instead of a raw string:
- `_build_fts_query(parsed)` — appends `parsed.keywords` to `semantic_query` (multi-word keywords quoted as phrases). FTS5 ANDs all terms.
- `_build_vec_where(parsed)` — builds a LanceDB SQL WHERE clause from `file_types` (extension IN), `date_range` (mtime >=/<), and `folders` (path LIKE with `~` expansion).
- `_build_kw_filters(parsed)` — extracts `after`/`before`/`folders`/`extensions` kwargs for `KeywordIndex.search()`.
- `KeywordIndex.search()` now accepts `after`, `before`, `folders`, `extensions` keyword args; builds a dynamic parameterized WHERE clause appended to the base FTS5 query.
- `semantic_query` drives the embedding call; all other fields are filters only.

**4.6 Fallback** — `parsed` is always a valid `ParsedQuery` in the CLI. If `ensure_ollama()` returns `None` or `parse_query()` raises any exception, the code falls back to `ParsedQuery(semantic_query=raw_query)`. The search never breaks because the LLM is unavailable.

**4.7 `--raw` flag** — `oasis search --raw` skips `ensure_ollama()` and `parse_query()` entirely, using the raw query string directly as `semantic_query`. Footer does not show `·  parsed`. Useful for debugging or when NL parsing overhead is unwanted.

| Decision | Reason |
|---|---|
| `PREFER_DAY_OF_MONTH: first` for dateparser | "January 2024" should produce 2024-01-01 so the range `[Jan 1, Feb 1)` captures the whole month |
| Always strip timezone from parsed datetimes | SQLite `mtime` is a UTC Unix timestamp; naive datetimes avoid implicit timezone arithmetic bugs |
| `semantic_query` only for embedding, not keywords | Keywords are for exact FTS5 matching; embedding on the full FTS5 query would dilute the semantic signal |
| `_build_*` helpers as module-level functions | The CLI imports them directly for keyword/semantic modes; keeps the logic testable in isolation |
| `llm_parsed` bool tracks LLM usage, not ParsedQuery identity | `parsed` is never None now; only the "did the LLM actually run" flag drives the footer badge |
| `--raw` flag instead of `--no-parse` | Positive framing — "raw mode" is a clear mental model vs. a double negative |

## Up Next