# Oasis — Development Context

Running log of decisions, current state, and what's next. Updated with every change.

Last resynced against the repo: **2026-07-14** (813 tests, all passing; git `fc43b68` + uncommitted work below).

---

## Current State

Phases 1–5.1 complete: extraction, keyword index, vector index, hybrid retrieval, NL query parsing, CLI, and the evaluation harness. Phase 5.2 (HTTP API) is **specced but not implemented** — see `CLAUDE.md` § HTTP API.

### Package structure

```
src/oasis/
├── __init__.py          ← exports main() for the pyproject entry point
├── config.py
├── models.py
├── cli/app.py
├── extractors/          base, registry, text, pdf, docx, pptx, xlsx, csv
├── index/               db, keyword, vector, embeddings, chunker, walker, pipeline
├── llm/                 __init__, base, manager, ollama
└── query/               __init__, parser, retriever, reranker, snippets

tests/                   28 test modules, 813 tests
├── fixtures/            sample.{txt,md,pdf,docx,pptx,xlsx,csv}
eval/
├── corpus/              301 labeled files + MANIFEST.md
├── queries.yaml         83 labeled queries
├── run_eval.py, plot.py
└── results/             latest.json, history.jsonl, metrics_over_time.png
```

Notes on the tree (verified against disk, not memory — this section had drifted badly before the 2026-07-14 resync):
- **There is no `llm/claude.py`** — the Claude API path was removed in `e3046f5`; parsing is always local via Ollama and `anthropic` is not a dependency. Don't reintroduce a cloud provider: offline operation is a product requirement.
- **Every package now has an `__init__.py`.** `cli/`, `index/`, `extractors/`, and `tests/` were implicit namespace packages — fine under `uv_build`, but a known PyInstaller module-discovery weak spot, and Phase E ends in PyInstaller. Backfilled 2026-07-14.
- **Python is 3.14 everywhere**: `requires-python = ">=3.14"`, ruff `target-version = "py314"`, README "3.14+", venv 3.14. Previously four sources disagreed.

### Core models — `models.py`
- `DocumentMetadata` — Pydantic, all-optional: `size_bytes`, `mtime`, `ctime`, `language`, `author`, `title`, `page_count`. Each extractor fills only what it can know.
- `ExtractedDocument` — `path`, `text`, `metadata`, `extraction_errors: list[str]` (supports partial success, e.g. a PDF with one bad page).

### Extractors — `extractors/`
- **Protocol** (`base.py`): `extensions: frozenset[str]` + `extract(path) -> ExtractedDocument | None`. Returns `None` on failure, never raises, so the indexer can skip and continue.
- **Registry** (`registry.py`): `_EXTRACTOR_MAP` built at import; `get_extractor(path)` is an O(1) suffix lookup. Add an extractor by instantiating it in the dict comprehension.
- **Per format**, all capturing `size_bytes`/`mtime`/`ctime`:

| Module | Handles | Specifics |
|---|---|---|
| `text.py` | `.txt`, `.md` | UTF-8 only; `langdetect` on first 2000 chars |
| `pdf.py` | `.pdf` | pypdf; per-page exceptions caught individually; all-empty → scanned → `None` (OCR deferred); `page_count`, `title` |
| `docx.py` | `.docx` | python-docx; `author`, `title`; no page count |
| `pptx.py` | `.pptx` | python-pptx; per-shape exceptions caught; `page_count` = slide count |
| `xlsx.py` | `.xlsx` | openpyxl `read_only=True, data_only=True`; sheet name + tab-separated rows; `page_count` = sheet count; `wb.close()` in both paths |
| `csv.py` | `.csv` | stdlib `csv`; UTF-8 → latin-1 fallback; tab-separated rows |

Known gap: `eval/corpus/txt/edge-latin1-menu.txt` can't be read by the UTF-8-only text extractor. No query targets it, so eval scores are unaffected.

### Index layer — `index/`
- **`db.py`** — `open_db(path)`: creates dirs, `journal_mode=WAL`, `synchronous=NORMAL`, runs schema. `documents` carries `title`/`content` because FTS5 `content=documents` fetches them by rowid. Three triggers (`_ai`/`_ad`/`_au`) keep FTS in sync.
- **`keyword.py`** — `KeywordIndex`; **all SQL in the project lives here**. `_file_hash(size, mtime)` → SHA-256[:16], used by both `upsert` and `is_unchanged` so skip logic and storage can't drift. `upsert` uses `INSERT … ON CONFLICT DO UPDATE` (fires the UPDATE trigger). `search(query, limit, *, after, before, folders, extensions)` — FTS5 `MATCH` + dynamic parameterized WHERE; `snippet(…, char(2), char(3), …)` keeps SQL free of string interpolation. Also `count`, `last_indexed_at`, `get_doc_id`, `delete`. `Result`: `path`, `doc_id`, `title`, `snippet`, `rank`. `MATCH_START = "\x02"`, `MATCH_END = "\x03"`.
- **`walker.py`** — `walk(root, *, extra_excludes, respect_gitignore, exclude_dotfiles, on_error)`. Layered cheapest-first: `_DIR_EXCLUDES` frozenset (prunes `dirnames` in place, never descends) → dotfile skip → pathspec `gitignore` spec. `followlinks=False`. Root-level `.gitignore` only. `on_error` is forwarded to `os.walk(onerror=…)` — **without it os.walk silently swallows directory-level errors**, so an unreadable tree yields nothing and looks identical to an empty one.
- **`chunker.py`** — `chunk_document(text, *, chunk_size=500, overlap=50) -> list[Chunk]`. tiktoken `cl100k_base` (module-level `_ENC`), sliding window stepping `chunk_size - overlap`. Empty/whitespace → `[]`; raises `ValueError` on bad args.
- **`embeddings.py`** — `EmbeddingModel` Protocol (`dimension`, `embed`). `SentenceTransformerEmbedder` wraps sentence-transformers with a module-level `_MODEL_CACHE` (load once per model name per process). Single `encode()` call, `show_progress_bar=False`. `DEFAULT_MODEL="all-MiniLM-L6-v2"` (384-dim).
- **`vector.py`** — `VectorIndex` over LanceDB. `vector` column is `pa.list_(pa.float32(), dimension)` (fixed-size list, required for ANN). `upsert_chunks` → `merge_insert("chunk_id")`; `search(vec, limit, where)` cosine, maps `_distance` → `score`; `delete_by_doc_id`, `count`.
- **`pipeline.py`** — `index_directory(conn, root, *, force, extra_excludes, on_file, vector_index, embedder, on_chunks_progress, cancel) -> dict[str, int]`. Two phases: walk→extract→keyword (queues `_PendingDoc`), then embed→vector upsert in `EMBED_BATCH=64` batches, deleting stale chunks per doc first. `chunk_id = "{path}:{chunk_index}"` — stable across re-indexes. Stats keys always present, including `chunks` and `permission_denied`.
  - `cancel: threading.Event | None` — checked per file and between embed batches; returns partial stats. Committed work persists (indexing is incremental, so the next run resumes).
  - `permission_denied` counted separately from `failed` — see the Full Disk Access notes under Phase 5.2.

### Query layer — `query/`
- **`parser.py`** — `ParsedQuery(BaseModel)`: `semantic_query` (non-empty), `file_types` (normalized to lowercase-with-dot), `date_range: DateRange | None`, `folders`, `keywords`, `confidence` (clamped 0–1). `DateRange.after/before` accept anything `dateparser` resolves (`PREFER_DAY_OF_MONTH=first`, `PREFER_DATES_FROM=past`), tz stripped. `parse_query(text, llm, *, today=None)` injects today's date into the user prompt; `_SYSTEM_PROMPT` holds field docs, file-type synonyms, relative-date rules, and 6 worked examples.
- **`retriever.py`** — `hybrid_search(conn, vector_index, embedder, parsed, *, top_n=10, candidate_limit=50) -> list[HybridResult]`. FTS5 (`_build_fts_query`: semantic_query + quoted keywords) + vector (`_build_vec_where`: extension/mtime/path filters) → dedupe vector to best chunk per doc → `_rrf` fusion (`RRF_K=60`) → assemble, preferring FTS5 snippet over raw chunk text. `_build_kw_filters` extracts `after`/`before`/`folders`/`extensions` for `KeywordIndex.search`.
  **The two arms fail independently** (each in its own `try`). An FTS5 syntax error degrades the call to semantic-only; a vector/embedder failure degrades it to keyword-only; only if *both* fail does it raise, re-raising the keyword error as the one with an actionable message. Single-list RRF is well-defined and just preserves that list's order. This was worth +23% ndcg@10 — see Evaluation.
- **`reranker.py`** — `CrossEncoderReranker` (`cross-encoder/ms-marco-MiniLM-L-6-v2`, shared `_MODEL_CACHE`). `rerank(query, results, *, top_n)` scores `(query, clean_snippet)` pairs, replaces `score` via `dataclasses.replace`, sorts desc. `_clean` strips `\x02`/`\x03` before scoring.
- **`snippets.py`** — `fts_snippet` (FTS5 `snippet()` on column 2, `None` on missing doc or `OperationalError`), `text_snippet` (pure-Python fallback, centers on first match, `…` when truncated), `get_snippet` (FTS first, fallback second). `_extract_terms` strips boolean operators; `_highlight_terms` wraps matches in the sentinels. `SNIPPET_TOKENS = 40`.

### LLM — `llm/`
- **`base.py`** — `LLMProvider(Protocol)`, `@runtime_checkable`: `complete(prompt, response_model, *, system=None) -> T`.
- **`ollama.py`** — `OllamaProvider` via `instructor.from_openai(openai.OpenAI(base_url=…, api_key="ollama"), mode=JSON)`. JSON mode forced so any model works regardless of tool-calling support. `DEFAULT_MODEL="llama3.2:3b"`.
- **`manager.py`** — `ensure_ollama(model)` → `OllamaProvider | None`, silently. `_server_running` (urlopen, 1s), `_model_available` (`ollama list`), `_start_server` (`shutil.which` → spawn `ollama serve` → poll to `_STARTUP_TIMEOUT=5.0`).

### CLI — `cli/app.py`
Entry: `oasis = "oasis:main"`. Default `db_path` from `load_config()`.

| Command | Notes |
|---|---|
| `index <path>` | `--db`, `--force/-f`, `--verbose/-v`. Embedding always on; models load behind a spinner. Unified Rich `Progress`: indeterminate scan task hides when the embed task (Bar + MofN + TimeRemaining + `_ChunksPerSecColumn`) starts. Summary omits zero counts. |
| `search <query>` | `--db`, `--limit/-n` (10), `--mode/-m` (`keyword`\|`semantic`\|`hybrid`, default hybrid), `--raw`. Hybrid over-fetches `max(limit*2, 20)` then reranks to `limit`. Footer: `N result(s) · mode · [parsed] · db`. Saves paths to `~/.oasis/last_results.json`. |
| `open <n>` | Opens result #n from `last_results.json` via `subprocess.run(["open", …])`. |
| `status` | Documents, DB size, last indexed, DB path. |
| `reset` | `--yes/-y`; deletes DB + `-wal`/`-shm`. |

NL parsing: every non-`--raw` search calls `ensure_ollama()` behind a spinner; on `None` or any exception, falls back to `ParsedQuery(semantic_query=query)` — search never breaks because the LLM is absent. `llm_parsed` (not `parsed is None`) drives the footer badge.

### Config — `config.py`
`CONFIG_PATH` is module-level (so tests can monkeypatch before construction). `OasisConfig(BaseSettings)`, `env_prefix="OASIS_"`, `db_path` default `~/.oasis/index.db`. Priority: init kwargs > env > TOML > defaults. Missing/empty TOML is fine.

### Evaluation harness — `eval/`
- Corpus copied with `cp -Rp` so **mtimes survive** — they encode 2019-01…2026-06 by filename hash, and ~18 date-tagged queries judge relevance by mtime.
- `run_eval.py` builds a dedicated index under `eval/index/` (gitignored), parses each query exactly as the CLI does (fixed `today=2026-07-07`), runs `hybrid_search(top_n=20)` + rerank to 10, maps results back to corpus-relative keys, scores with **ranx**. Flags: `--reindex`, `--no-rerank`, `--no-parse`, `--today`.
- Writes `results/latest.json` + appends `results/history.jsonl`; `plot.py` charts ndcg@10/precision@5/mrr over history.

**Current baseline** (2 runs in history; Ollama unavailable → raw mode, rerank on):

| metric | before arm split | **after** | rel |
|---|---|---|---|
| ndcg@10 | 0.4546 | **0.5602** | +23.2% |
| mrr | 0.4403 | **0.5427** | +23.3% |
| recall@10 | 0.5552 | **0.6844** | +23.3% |
| precision@5 | 0.1875 | **0.2250** | +20.0% |
| precision@10 | 0.1113 | **0.1338** | +20.2% |

Low precision@k is structural — most queries have a single relevant doc, capping p@5 at 0.2. **MRR and NDCG are the headline numbers.**

The jump is entirely from splitting `hybrid_search`'s try blocks (below). 10 of 83 queries contain an apostrophe or comma; without Ollama they run raw, that punctuation reaches FTS5 verbatim, and the resulting `fts5: syntax error` used to kill the whole call. Those queries scored a flat 0. They now degrade to semantic-only and return 10 results each. **No ranking logic changed** — this is purely recovering searches that were being thrown away.

---

## Tests — 813, all passing

| File | N | File | N | File | N |
|---|---|---|---|---|---|
| `test_parse_query.py` | 91 | `test_walker.py` | 29 | `test_registry.py` | 17 |
| `test_vector.py` | 58 | `test_keyword.py` | 29 | `test_llm_providers.py` | 17 |
| `test_retriever.py` | 58 | `test_embeddings.py` | 25 | `test_keyword_edges.py` | 17 |
| `test_cli.py` | 54 | `test_cli_edges.py` | 22 | `test_human_size.py` | 16 |
| `test_snippets.py` | 40 | `test_extractor_edges.py` | 21 | `test_db.py` | 16 |
| `test_parser.py` | 37 | `test_ollama_manager.py` | 20 | `test_pdf_extractor.py` | 15 |
| `test_reranker.py` | 36 | `test_integration.py` | 20 | `test_docx_extractor.py` | 15 |
| `test_chunker.py` | 35 | `test_extractors.py` | 20 | `test_config.py` | 11 |
| `test_pipeline.py` | 31 | `test_walker_edges.py` | 19 | `test_models.py` | 10 |
| | | `test_pptx_extractor.py` | 18 | | |

---

## Key Decisions

### Data & extraction
| Decision | Reason |
|---|---|
| `DocumentMetadata` typed Pydantic, not a dict | Free-form dicts lose discoverability and type safety across boundaries |
| `extract` returns `None`, never raises | Indexer must skip bad files and continue |
| `extensions` a `frozenset` class attr, not a property | Immutable, hashable, cheaper; signals it never changes |
| Models at package root, not in `extractors/base.py` | Shared models belong at the root; avoids circular imports |
| `extraction_errors: list[str]` | Supports partial-success extraction |
| Registry dispatches on extension dict, not `can_handle()` loop | O(1); all four extractors had identical `can_handle` bodies |
| Language detection on `text[:2000]` | Sufficient signal without processing whole files |
| PDF per-page exceptions caught individually | One bad page shouldn't discard the document |
| Scanned PDFs → `None`, not empty text | Empty text is useless to the indexer; OCR deferred |

### Index
| Decision | Reason |
|---|---|
| `documents` has `title`/`content` despite not being in the original spec | FTS5 `content=documents` requires them in the base table |
| Sentinels are `\x02`/`\x03`, not `[`/`]` | Rich uses `[...]` for markup; printable markers would corrupt display |
| `is_unchanged` hashes `(size, mtime)`, not raw mtime | Catches same-second writes that change size; still never reads file bytes; shared `_file_hash` keeps skip and storage in sync |
| `INSERT … ON CONFLICT DO UPDATE` | Fires the UPDATE trigger, so FTS stays correct for modified files |
| All SQL in `KeywordIndex` | One place to audit for injection |
| `snippet(…, char(2), char(3), …)` not f-string | Removes the last string interpolation from SQL |
| `os.walk` + in-place pruning, not `Path.rglob` | `rglob` materializes every path; pruning never descends into excluded dirs |
| `gitignore` pattern style, not `gitwildmatch` | pathspec 0.12+ deprecates the latter |
| tiktoken `cl100k_base` | Close to Claude's tokenization, already a dep, stable |
| Module-level `_ENC` / `_MODEL_CACHE` | Loading costs seconds and hundreds of MB; must not repeat per instance |
| `pa.list_(pa.float32(), dim)` for vectors | LanceDB needs fixed-size lists for ANN |
| Cosine metric | MiniLM embeddings aren't normalized; cosine handles arbitrary magnitudes |
| `on_file` callback instead of Rich in the pipeline | Keeps the pipeline testable and decoupled from display |

### Retrieval & parsing
| Decision | Reason |
|---|---|
| RRF over score normalization | BM25 and cosine are on different scales; rank fusion avoids calibration entirely |
| `k=60` | The constant from the original RRF paper |
| Chunk dedup before RRF | Multiple chunks per doc would inflate its rank; dedup puts both lists at document granularity |
| Replace `score` with the CE logit | `score` always reflects the current ordering basis |
| CE rerank only the top ~20 | Cross-encoders are accurate but slow; most of the quality at a fraction of the latency |
| Local LLM, not cloud | Privacy is the product; also no API key, no network |
| Schema-first parsing into `ParsedQuery` | Validation catches bad LLM output before it reaches retrieval |
| Extension normalization in a `field_validator` | LLMs emit "PDF"/"pdf"/".pdf"; normalize once so callers never branch |
| Today's date injected per-call | System prompt stays constant/cacheable |
| Naive datetimes internally | SQLite `mtime` is a UTC Unix timestamp; naive avoids implicit tz arithmetic |
| `semantic_query` for embedding, `keywords` for FTS5 | Embedding the full FTS5 query would dilute the semantic signal |
| `--raw` instead of `--no-parse` | Positive framing beats a double negative |
| `ensure_ollama()` returns `None` silently | Ollama being absent is normal; search must still work |

### Eval
| Decision | Reason |
|---|---|
| Calls `hybrid_search()` directly, not the CLI | No subprocess overhead; exercises the real retrieval path |
| Corpus copied with `cp -Rp` | Date queries judge by mtime; losing mtimes would silently break ~18 queries |
| Qrels/run keyed by corpus-relative POSIX paths | Labels are relative, results absolute — normalize so ranx lines them up |
| Expected-empty queries scored separately | ranx has no notion of "should return nothing"; averaging them is meaningless |
| `{"__no_results__": 0.0}` sentinel for empty runs | ranx requires every qrels query in the run; yields a clean 0 without crashing |
| Per-query `OperationalError` caught and scored empty | Adversarial queries mustn't abort the eval |
| Dedicated gitignored index under `eval/index/` | Reproducible, isolated from the user's real `~/.oasis` |

---

## Phase 5.2 — HTTP API (specced, not implemented)

Full contract in `CLAUDE.md` § HTTP API. **The consumer is a native SwiftUI app that spawns the server as a child process** — not the HTMX web UI on the README roadmap. The spec is organized around where a long-lived local service diverges from the CLI. No code yet: `fastapi`/`uvicorn` aren't dependencies and `src/oasis/api/` doesn't exist.

Decisions worth carrying:

| Area | Decision |
|---|---|
| Handshake | Loopback-only, ephemeral port; one JSON line `{port, token, pid}` to stdout after bind, before serving; `Bearer` token on every request but `/api/health`. Create the socket manually — reading the port back off Uvicorn is racy. |
| Model lifecycle | Config, embedder, cross-encoder, `VectorIndex`, `ensure_ollama()` all initialized **once** in app state and warmed with a throwaway inference. Loading runs on a **background thread**, not inline in lifespan — Uvicorn doesn't accept connections until lifespan startup returns, so a sync load makes `/api/health` connection-refused for its whole duration. `ensure_ollama()` cached (incl. `None`); per-search it's an `ollama list` subprocess spawn per query. |
| Async indexing | `POST /api/index` → `202` + `job_id` (`409` if one's running); SSE progress; `POST /api/index/cancel` sets a `threading.Event`. Blocking is wrong: first-time `~/Documents` is minutes, `URLSession` times out at 60s, and a ten-minute spinner is indistinguishable from a hang. |
| SSE throttling | Coalesce progress in the publisher, ≥~100ms apart — `on_file` fires per file and a first index can be 100k of them. Bounded queues; drop intermediate `progress` on overflow, **never** terminal events. Progress is lossy by nature; completion isn't. |
| Concurrency | Endpoints are `def` **except** SSE, which is `async def` (a long-lived wait, not CPU-bound — a `def` generator would pin an anyio threadpool slot for the life of the stream). SQLite connections thread-local; WAL already allows concurrent readers; writes serialized behind the job lock. |
| Wire format | Datetimes always ISO 8601 **with explicit UTC offset** — Swift's `.iso8601` strategy requires a tz designator, so naive fails to decode. Serialize at the boundary; keep naive internally. Error envelope `{error: {code, message}}` needs three registered handlers — FastAPI's `422` shape (`{"detail": [...]}`) is the one that'd otherwise slip through. |
| Snippets | Segments `[{text, match}]`, not `{start, end}` offsets. The first draft shipped an off-by-two offset (`[42,52)` → `"e renewals"`) that survived two readings — offsets are unreviewable by eye. Segments also dodge codepoint-vs-UTF-16-vs-grapheme mismatch across Python/Swift. |
| `POST /api/open` | Takes `path`, not `n` — `n` would reintroduce `last_results.json` as server-side session state, fine for a sequential CLI and wrong for a server. `resolve()` then validate via `get_doc_id()` before shelling out. |
| Watchdog | Daemon thread polls `os.getppid()`, exits when it returns `1`. Gated on an explicit `--managed`/`OASIS_MANAGED=1` — **not** on `--port`, since port pinning and parent management are orthogonal (you'll want a pinned port *with* a watchdog when debugging the Swift app). |
| Privacy | `access_log=False` — Uvicorn logs `GET /api/search?q=…`, putting every user query into the child's stdout, the parent's pipe, and potentially Console.app. |
| Versioning | No `/api/v1`, deliberately — client and server ship as one PyInstaller'd artifact and can't skew. |

### LanceDB concurrency — measured, not assumed
Probed against **lancedb 0.30.2**: two reader threads (`search` + `count`) against a shared `VectorIndex` while a writer thread ran 200 sequential `merge_insert` calls.

| Question | Result |
|---|---|
| Concurrent reads safe during `merge_insert`? | **Yes** — 689 reads, zero exceptions |
| Does a **shared** handle see the writer's new rows? | **Yes** — readers watched `count()` climb 12 → 210 mid-write |
| Does a **separately opened** handle see them? | **No** — pinned at open (reader stuck at v2/5 rows while the writer reached v3/55) |
| Can a pinned handle catch up? | **Yes** — `table.checkout_latest()`, verified 5 → 55 |

**Therefore `VectorIndex` must NOT be thread-local.** Search-during-index returns fresh results *only because* app state shares the one handle the index job writes through. The SQLite thread-local rule invites the opposite by analogy, and it would be silently, permanently wrong — every search thread's handle would pin at startup and return results frozen at process launch, with no error ever raised. Regression test required: index concurrently with searches, assert new content becomes findable.

### Pipeline support for the HTTP API — **done** (2026-07-14)
Landed ahead of the API so `api/` can be written against a stable pipeline:

1. **`cancel: threading.Event | None`** on `index_directory()`, checked per file and between embed batches, returning partial stats.
2. **`permission_denied` as a distinct stat.** This was *not* the one-line `except PermissionError` it looked like — two separate mechanisms were hiding it, both found by testing against a real `chmod 000` rather than reasoning about it:
   - **`os.walk` silently swallows directory-level errors** without `onerror`. Measured: a locked directory yielded 0 files and raised 0 errors, so the pipeline never saw a file to fail on. macOS denies Full Disk Access **at the directory level**, so a per-file handler would never fire in the one scenario the counter exists for — the result would be `indexed: 0, permission_denied: 0`, the exact useless empty state. Fixed by adding `on_error` to `walk()`.
   - **Extractors are contractually required to swallow their own I/O errors and return `None`** (`text.py` catches `Exception`), so `PermissionError` never reaches the pipeline on the read path — a `chmod 000` file came back as `None` and was counted `failed`. Fixed with `_is_unreadable(path)`, which re-opens the file **only on the failure path** to tell "can't read" from "read fine, content is broken."
   - `except PermissionError` also guards `path.stat()` and `extract()`, ahead of the broad handlers (`PermissionError` is an `OSError`, so `except OSError` would otherwise claim it).
   - CLI surfaces it: a yellow `N permission denied` in the summary, plus a Full Disk Access hint when `permission_denied > 0 and indexed == 0`.

---

## Up Next

- **Implement the HTTP API** (`src/oasis/api/`) against the spec in `CLAUDE.md`. The pipeline is ready; `fastapi`/`uvicorn` still need adding.
- **Pre-existing lint debt: 66 ruff errors**, unrelated to any recent change and untouched. Mostly `F401` (15, unused imports), `I001` (14, import sorting), `N806` (13, non-lowercase locals in tests), `UP017` (7, `datetime.timezone.utc` → `datetime.UTC`). Also 6 `B008` on `typer.Option` defaults, which are **false positives** — that's Typer's documented pattern and should be added to `ignore`. `ruff format` would additionally reformat 29 files. Worth one dedicated cleanup commit; deliberately not bundled into feature work.
- Nested `.gitignore` support in the walker (per-directory, not just root).
- UTF-8-only text extractor can't read latin-1 files (`edge-latin1-menu.txt` in the corpus).
- OCR fallback for scanned PDFs.

### Recently done (2026-07-14)
- **Split `hybrid_search`'s try blocks** — the eval's open finding, now fixed. Worth **+23% ndcg@10** (0.455 → 0.560) with no ranking change; it purely recovers the 10 punctuation queries that were scoring 0. Amended the API contract to match: hybrid `200`s and degrades, only keyword mode `400`s.
- **Pipeline `cancel` + `permission_denied`** (see Phase 5.2 above).
- **Python version reconciled to 3.14** across all four sources: `requires-python = ">=3.14"`, ruff `target-version = "py314"`, README "3.14+", venv already 3.14. Chosen over 3.13 because it's what actually runs and all tests pass on it; **verified PyInstaller 6.21.0 resolves cleanly under `>=3.14`**, so Phase E isn't boxed in. The bump surfaced exactly 1 new lint error (`UP043` in `walker.py`), fixed.
- **`CLAUDE.md` Stack corrected** — removed the Anthropic API (deleted in `e3046f5`) and "FastAPI + HTMX for web UI", which contradicted the HTTP API section's native-SwiftUI premise three screens below it.
- **`__init__.py` backfilled** for `cli/`, `index/`, `extractors/`, `tests/`. Implicit namespace packages are fine under `uv_build` but are a known PyInstaller module-discovery weak spot, and Phase E ends in PyInstaller.