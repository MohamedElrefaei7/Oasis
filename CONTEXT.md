## Project Goals & North Star
 
*Where the project is trying to end up, and how to tell whether it got there. Written to survive contact with the eval — the headline finding (NL parsing regresses retrieval) is reckoned with here, not papered over.*
 
### North star
 
**A native macOS app that finds a file by what you remember about it — its contents, topic, and rough kind — running entirely on-device, and beats Spotlight decisively on the "I forgot what I named it" case.** Not a faster `grep`, not a filename matcher. The user describes the file in a sentence; Oasis returns it. Everything indexes and searches locally; nothing leaves the machine.
 
That sentence is the whole project. Every horizon below is a step toward it, and every non-goal is something that isn't it.
 
### The core hypothesis, and what "natural language" actually means now
 
The project was premised on a specific bet: that an LLM parsing a query into **structured filters** (`file_types`, `date_range`, `folders`) plus a distilled `semantic_query` would beat feeding the raw sentence to hybrid retrieval. **The eval measured that bet and it lost** — parsing costs −0.108 ndcg@10 / −0.135 recall@10 on the best configuration (hybrid+CE). See Evaluation for the mechanism (hard filters are an asymmetric-payoff trap; a 3B model's wrong guess zeroes recall).
 
This does **not** put the north star in question, and the distinction is load-bearing enough to state plainly:
 
- **"Natural-language search" is intact and already works.** Typing a natural sentence and getting the right file back via semantic + hybrid + cross-encoder retrieval *is* natural-language search, and it measures 0.56 ndcg@10 / 0.68 recall@10 today with the parsing layer switched off. The user types English; the system understands it semantically. That is the product.
- **"LLM-parses-queries-into-filters" is a separate, testable bet — currently net-negative.** It is a hypothesis under active test (soft filters, un-distilled embedding, a larger parse model), not a foundation the project stands on. If it never becomes net-positive, it gets cut, and the north star is unaffected.
Conflating those two would hand the project an identity crisis it doesn't need. The retrieval engine is the proven core; the parsing layer is a research question sitting on top of it.
 
### Goal tiers
 
**Tier 0 — the proven core (done, defend it).**
Local hybrid retrieval (BM25 + dense vectors, RRF-fused, cross-encoder reranked) over 7 document formats, with a reproducible eval harness that quantifies every change. *Definition of done: met.* The standing goal here is regression defense — no change ships that drops the measured matrix without a recorded, deliberate reason.
 
**Tier 1 — the native macOS app (the actual ultimate deliverable).**
The CLI proves the engine; the app is the product. *Definition of done:*
- A signed, notarized `.app` a user double-clicks — no terminal, no Python install, no `pip`.
- A Spotlight-style summon (global hotkey + menu-bar presence) and a results list that opens files in their default app.
- Background, incremental indexing that survives reboots (FSEvents-driven), with a first-run Full Disk Access flow that explains itself.
- The engine reached over the local HTTP service (Phase 5.2), never re-implemented — one retrieval codebase, two front-ends (CLI, app).
**Tier 2 — make the NL layer earn its place, or kill it.**
The parsing layer stays disabled by default (`--raw` is the recommended path) until it is measured net-positive on the matrix. *Definition of done: a decision, either direction.*
- Soft filters (score boost, not `WHERE` exclusion) so a wrong guess costs a little instead of all recall.
- Embed the user's actual words; reserve the distillation for the FTS5 arm, where it genuinely helps (keyword +0.040).
- If, after those and a larger parse model, it's still negative → cut it, and write that up. A removed feature with a measured reason is a stronger portfolio artifact than a feature that quietly hurts.
**Tier 3 — polish, distribution, and Apple-Silicon fit (long horizon).**
Auto-update (Sparkle), a real onboarding, latency budgets that are *measured* not assumed, and — once the bundle size or inference speed demands it — swapping the embedding/reranking models to Core ML or MLX so the shipped app is smaller and faster on M-series without touching the HTTP contract.
 
### Measurable success criteria
 
The project is doing well when these hold, and they are the only things that count as "better" — no unmeasured claim is a goal.
 
| Dimension | Target | Current |
|---|---|---|
| Retrieval quality (best config, raw) | Beat the standing best; never silently regress | ndcg@10 **0.5602**, mrr 0.5427, recall@10 0.6844 |
| NL parsing layer | Net-positive on the matrix *before* it's default | **−0.108 ndcg@10** — disabled by default |
| Warm query latency | Establish a p95 budget, then hold it | **not yet measured** — measure via the HTTP service, warm |
| App startup → ready | Fast enough that models-loading isn't the first impression | **measured 2026-07-17**: `t_handshake` ≈ **2–3.3 s** (spawn→handshake), `t_ready` ≈ **35–54 s** (handshake→`status:ready`, local-load-dominated, high variance). Long enough that the app must poll `/api/health` and never block — see `docs/APP_SEAM.md` |
| Distribution | One double-click, signed + notarized, zero deps | not started (Tier 1) |
 
Warm query latency is still a blank on purpose — it has never been measured and pretending a number exists is exactly the failure the eval discipline exists to prevent. **Startup→ready is now measured** (2026-07-17, via the app-seam spawn harness); it's an order-of-magnitude figure on a dev machine, not yet a p95 budget from the shipped bundle.
 
### Non-goals (scope boundaries)
 
Naming these is what keeps an ambitious side project finishable.
 
- **Not cloud anything.** No sync, no hosted index, no remote LLM. Offline operation is a product requirement, not a setting. (Recorded already: the Claude API path was deleted; don't reintroduce a cloud provider.)
- **Not multi-user, not a server product.** The HTTP service is a single-user local seam for the app, loopback-only. It is not a deployment target.
- **Not iOS/Android — yet.** The clean HTTP contract keeps the door open, but mobile is out of scope until the Mac app is real.
- **Not competing with Spotlight on exact-filename or system-file lookup.** That's Spotlight's job and it's fine at it. Oasis owns the by-description, by-content case.
- **Not the HTMX web UI** on the old README roadmap. Superseded by the native app; kept only as a mental note that the service could render HTML if ever wanted.
- **OCR, nested `.gitignore`, latin-1 text** and similar remain deferred niceties, not goals — logged in Up Next, not here.
### Guiding principles
 
1. **Measured, not assumed.** Every "improvement" is a number in `history.jsonl` or it didn't happen. The project already killed its headline feature on this principle; that's the bar.
2. **Local-first, privacy as a feature.** On-device by default, no telemetry, search text never leaves the machine (down to `access_log=False` so queries don't even hit a log).
3. **One engine, many front-ends.** CLI, HTTP service, and app are thin shells over the same retrieval code. A capability is added once, in the core.
4. **Honesty over marketing** — in the README, on the résumé, everywhere. The defensible claim is the one you can reproduce.
### The portfolio goal (explicit)
 
This is a career-driving project, and its résumé value is *not* "I built a Mac app." It's evidence of empirical engineering judgment: **built a rigorous IR evaluation harness, used it to discover that a planned headline feature was a −0.11 ndcg@10 regression, diagnosed the cause as an asymmetric-payoff problem per-query, and made the evidence-based call to disable it.** That story — a measured reversal and a diagnosis — is rarer and worth more than any architecture name-drop, and it should be the spine of how the project is presented. The cross-encoder's measured +14.7% and the RRF-buys-recall / CE-buys-precision split are the supporting technical claims, each fully reproducible.
 
Do not, anywhere, claim NL parsing improves retrieval. The measured claim is the opposite, and the opposite is the better story.

# Oasis — Development Context

Running log of decisions, current state, and what's next. Updated with every change.

Last resynced against the repo: **2026-07-17** (930 tests, all passing, torch-free by default).

---

## Current State

Phases 1–5.1 complete: extraction, keyword index, vector index, hybrid retrieval, NL query parsing, CLI, and the evaluation harness. Phase 5.2 (HTTP API): **all endpoints implemented** — skeleton/handshake/model-lifecycle/health/auth/error-envelope (2026-07-15), `GET /api/search` + `POST /api/open` + capability markers (2026-07-15), `GET /api/status` + `POST /api/index` + SSE + cancel (2026-07-16), stale reconciliation + no-vector backfill + job-bound cancel (2026-07-17), and **`POST /api/reset` (2026-07-17)**. The full `CLAUDE.md` § HTTP API contract is now built. **Phase 5.2 is closed** — tagged `service-layer-complete` (`734a84c`), the served retrieval path verified byte-identical to the direct eval harness, and the app spawn/handshake/readiness seam mapped + measured in `docs/APP_SEAM.md`. That doc is the entry point for the next phase, the native Swift app (Tier 1).

### Package structure

```
src/oasis/
├── __init__.py          ← exports main() for the pyproject entry point
├── config.py
├── models.py
├── cli/app.py
├── api/                 __init__, schemas, state, app, search, open, status, index, jobs, reset, serve  ← Phase 5.2
├── extractors/          base, registry, text, pdf, docx, pptx, xlsx, csv
├── index/               db, keyword, vector, embeddings, chunker, walker, pipeline
├── llm/                 __init__, base, manager, ollama
└── query/               __init__, parser, retriever, reranker, snippets, search

tests/                   32 test modules, 879 tests
├── fixtures/            sample.{txt,md,pdf,docx,pptx,xlsx,csv}
eval/
├── corpus/              301 labeled files + MANIFEST.md
├── queries.yaml         83 labeled queries
├── run_eval.py, plot.py, verify_served.py
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
- **`db.py`** — `open_db(path)`: creates dirs, `journal_mode=WAL`, `synchronous=NORMAL`, runs schema. `documents` carries `title`/`content` because FTS5 `content=documents` fetches them by rowid. Three triggers (`_ai`/`_ad`/`_au`) keep FTS in sync. Also a `meta(key, value)` table and `SCHEMA_VERSION = 2` (see Index capabilities below; 1 → 2 on 2026-07-16 when stored paths became guaranteed-absolute, so relative-path indexes read as < 2 and get flagged reindex-needed). `open_db` only *ensures* the meta table exists — it deliberately never infers markers from heuristics, because "absent" has to keep meaning "not known to be searchable" for every index built before the table existed.
- **`keyword.py`** — `KeywordIndex`; **all SQL in the project lives here**. `_file_hash(size, mtime)` → SHA-256[:16], used by both `upsert` and `is_unchanged` so skip logic and storage can't drift. `upsert` uses `INSERT … ON CONFLICT DO UPDATE` (fires the UPDATE trigger). `search(query, limit, *, after, before, folders, extensions)` — FTS5 `MATCH` + dynamic parameterized WHERE; `snippet(…, char(2), char(3), …)` keeps SQL free of string interpolation. Also `count`, `last_indexed_at`, `get_doc_id`, `delete`, `set_meta`/`get_meta`/`get_capabilities`, `docs_under(root)` — the sweep's scope query, whose authoritative filter is a Python separator-boundary `startswith`, never SQL `LIKE` (`_` is a LIKE single-char wildcard) — and `clear_documents()` / `clear_meta()`, the two halves of `POST /api/reset` (ordered separately around the vector drop for crash-safety). `Result`: `path`, `doc_id`, `title`, `snippet`, `rank`. `MATCH_START = "\x02"`, `MATCH_END = "\x03"`.

#### Index capabilities — telling "legacy index" apart from "no results" (2026-07-15)

The problem: an index built before vectors existed returns nothing from semantic search, which is indistinguishable from "your query matched nothing" — so the app's first run reads a stale index as *broken*. Markers make it detectable.

- **`IndexCapabilities`** (frozen dataclass in `keyword.py`, deliberately **not** an ApiModel — it's internal): `schema_version` (0 when absent), `vectors_built`, `embedding_model`, `embedding_dimension`, `document_count`. `get_capabilities()` is a **pure DB read** — it knows nothing about any live embedder, so the "were these vectors built at the dimension we now use?" comparison belongs to the caller, not here.
- **Absence is conservative**: no marker → `vectors_built=False` → needs-reindex. **No backfill** of existing indexes; the pipeline populates markers going forward.
- **`pipeline.py`** writes markers via `_write_capability_markers()` on *successful completion only*: `schema_version` always; `vectors_built`/`embedding_model`/`embedding_dimension` only when the embed phase actually ran. **Markers are only ever set, never cleared** — this is load-bearing: an incremental re-run with nothing new to embed skips the embed phase entirely, and must not downgrade an index whose vectors are fine (regression-tested). A cancelled or crashed run leaves markers absent → conservatively reads as needs-reindex.
- `embedding_dimension` comes from the `EmbeddingModel` Protocol; the model *name* isn't on the Protocol, so it's read opportunistically via `getattr(embedder, "model_name", None)` — `SentenceTransformerEmbedder._model_name` became public `model_name` for this.
- A corrupt marker (non-integer) reads as absent rather than crashing `/api/health`.
- **`walker.py`** — `walk(root, *, extra_excludes, respect_gitignore, exclude_dotfiles, on_error)`. Layered cheapest-first: `_DIR_EXCLUDES` frozenset (prunes `dirnames` in place, never descends) → dotfile skip → pathspec `gitignore` spec. `followlinks=False`. Root-level `.gitignore` only. `on_error` is forwarded to `os.walk(onerror=…)` — **without it os.walk silently swallows directory-level errors**, so an unreadable tree yields nothing and looks identical to an empty one.
- **`chunker.py`** — `chunk_document(text, *, chunk_size=500, overlap=50) -> list[Chunk]`. tiktoken `cl100k_base` (module-level `_ENC`), sliding window stepping `chunk_size - overlap`. Empty/whitespace → `[]`; raises `ValueError` on bad args.
- **`embeddings.py`** — `EmbeddingModel` Protocol (`dimension`, `embed`). `SentenceTransformerEmbedder` wraps sentence-transformers with a module-level `_MODEL_CACHE` (load once per model name per process). Single `encode()` call, `show_progress_bar=False`. `DEFAULT_MODEL="all-MiniLM-L6-v2"` (384-dim).
- **`vector.py`** — `VectorIndex` over LanceDB. `vector` column is `pa.list_(pa.float32(), dimension)` (fixed-size list, required for ANN). `upsert_chunks` → `merge_insert("chunk_id")`; `search(vec, limit, where)` cosine, maps `_distance` → `score`; `delete_by_doc_id`, `count`, `doc_ids_with_vectors()` (one bulk doc_id projection — the backfill's existence check, computed once per run, never per doc).
- **`pipeline.py`** — `index_directory(conn, root, *, force, extra_excludes, on_file, vector_index, embedder, on_chunks_progress, cancel, on_reconcile) -> dict[str, int]`. **Absolutizes `root` once at entry** (`os.path.abspath` — lexical, no symlink following, no-op on absolute roots) so no relative path can ever reach the `documents` table; done in the pipeline, not the CLI, so every caller (CLI, API, eval) inherits it. Two phases: walk→extract→keyword (queues `_PendingDoc`), then embed→vector upsert in `EMBED_BATCH=64` batches, deleting stale chunks per doc first. `chunk_id = "{path}:{chunk_index}"` — stable across re-indexes. Stats keys always present, including `chunks`, `permission_denied`, and `removed`.
  - `cancel: threading.Event | None` — checked per file and between embed batches; returns partial stats. Committed work persists (indexing is incremental, so the next run resumes).
  - `permission_denied` counted separately from `failed` — see the Full Disk Access notes under Phase 5.2.
  - **Stale sweep + no-vector backfill (2026-07-17)** — see Recently done: complete-clean-census-gated deletion of unseen stored docs under the root; unchanged files skipped only when already vectored. `on_reconcile` fires when the sweep starts (API maps it to SSE `phase: "reconciling"`).

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

### HTTP API — `api/` (skeleton only, 2026-07-15)

Implements the non-endpoint half of the `CLAUDE.md` § HTTP API spec. `fastapi` + `uvicorn` added as dependencies; `httpx` added to the dev group for `TestClient`.

- **`schemas.py`** — `ApiModel` base (all API schemas inherit it): wildcard `field_serializer(mode="wrap", when_used="json")` that serializes any `datetime` as UTC ISO 8601 with explicit offset (Swift's `.iso8601` decoder requires the designator; internals stay naive). `HealthResponse`, `ErrorDetail`/`ErrorResponse` (`{"error": {"code","message"}}`).
- **`state.py`** — `AppState` dataclass: `token`, `config`, `db_path`, `embedder`, `reranker`, `vector_index` (**one shared instance, never thread-local** — comment points at the LanceDB version-pinning trap), `llm` (cached `ensure_ollama()` result, `None` included), `status: loading|ready|error`, `error`, `ready: threading.Event`. Thread-local SQLite: module-level `threading.local()` + `get_conn(db_path)` opening via `open_db()` on first use per thread; `invalidate()` bumps a generation counter so every thread reopens (used by reset). **`get_conn` serializes `open_db()` behind a lock** — two threads opening a fresh DB concurrently race on the WAL pragma/schema DDL (`database is locked`, caught by the thread-local test); only paid on first open, never on the cached path. **`reset_index()`** (2026-07-17) does the `POST /api/reset` swap under the caller's `job_lock`: clear meta markers → `rmtree` the `.lance` dir + install a fresh `VectorIndex` as the shared instance → clear documents → `invalidate()`. Order keeps the markers honest at every crash point; the fresh handle is what subsequent searches bind while in-flight readers drain against the old (dropped) one and degrade.
- **Route-existence is not probeable without the token.** An auth-gated catch-all (`/api/{_rest:path}`, registered **last** in `create_app` — Starlette matches in registration order, so anything added after it is shadowed) makes unknown `/api/*` paths behave exactly like real protected routes: `401` without a token, `404` (envelope) with one. Without it, unknown paths fell through to Starlette's default 404 *before* auth ever ran, letting a tokenless caller map the API. For the same reason `openapi_url=None` disables `/openapi.json` + `/docs`, which enumerate every route unauthenticated. Both verified live.
- **`app.py`** — `create_app(token=…, db_path=…)` factory. Lifespan spawns a **daemon loader thread** (never inline: Uvicorn refuses connections until lifespan returns, which would make `/api/health` connection-refused for the whole load): `load_config` → embedder → reranker → `VectorIndex` → `ensure_ollama()` once → warm both models (`embed(["warmup"])` + one dummy rerank) → `status="ready"` + set the Event; on exception, records message + `status="error"`. `GET /api/health` (no auth, always 200, reports `documents` only when ready and the DB file exists — `open_db` would otherwise *create* it as a side effect). `require_auth` (Bearer, `secrets.compare_digest`, 401) + `require_ready` (503) bundled as `PROTECTED` on `protected_router` — every future endpoint attaches there, health stays off it. Three exception handlers hold the envelope: `HTTPException` (dict details with `code`/`message` pass through, else status→code map), `RequestValidationError` (FastAPI's list-under-`detail` flattened to one message), catch-all `Exception` (logs, returns 500, never leaks the traceback).
- **`serve.py`** — `run_serve()`: binds `127.0.0.1` itself (`port or 0` → ephemeral; reading the port back off uvicorn is racy), `secrets.token_urlsafe(32)`, prints exactly one JSON handshake line `{"port","token","pid"}` to stdout and flushes before serving, then `uvicorn.Server(...).run(sockets=[sock])` with `access_log=False` (query strings would land in the parent's stdout pipe). `--managed` (or `OASIS_MANAGED=1`, via typer `envvar`) starts the parent-death watchdog: daemon thread polling `os.getppid()` every 1s, `os._exit(0)` at ppid 1.
- **CLI**: `oasis serve [--port N] [--db PATH] [--managed]` added to `cli/app.py` as a thin wrapper that imports `api/serve.py` *lazily*, so no other command pays the fastapi/uvicorn import.
- **Verified live**: handshake parses as JSON, health flipped loading→ready (877 docs from the real `~/.oasis` index), unknown `/api/*` paths come back in envelope shape.
- `[tool.pytest.ini_options]` now registers a `slow` marker (real-model tests); the API tests fake embedder/reranker/vector index so the suite never loads PyTorch.

### `GET /api/search` — implemented (2026-07-15)

- **`raw` defaults to `True` — parsing is opt-in.** This deliberately diverges from the CLI (which parses unless `--raw`): the eval measured NL parsing as −0.108 ndcg@10 / −0.135 recall@10 on hybrid+CE, so parsing-off is the best-measured path and the endpoint's default. `raw=false` uses the **cached** `state.llm` from startup (never `ensure_ollama()` per request — that's an `ollama list` subprocess per query); `None` provider or any parse exception falls back to `ParsedQuery(semantic_query=q)` with `llm_parsed=false`. Search never fails because the LLM is absent.
- **Error contract**: keyword mode `400`s on bad FTS5 syntax (envelope, "wrap phrases in double quotes" tip); hybrid degrades to the surviving arm and `200`s (only a both-arms failure escapes as `500`); semantic never parses the query as FTS5. Verified live with `"rock 'n roll, can't touch"`.
- **`query/search.py`** — `run_search(conn, vector_index, embedder, reranker, query, parsed, *, mode, limit)` returning `list[HybridResult]` for all three modes; `SearchMode(StrEnum)` lives here too (duplicates the CLI's enum until the CLI migrates to this module in a later commit — deliberate temporary duplication). Keyword score is `-rank` (FTS5 rank is negative-better), semantic score is `1 − distance` (cosine similarity), hybrid is RRF then CE. Hybrid reranks against the **user's raw query**, not `semantic_query` (the eval showed distillation corrupts meaning); identical when raw.
- **`query/snippets.py: to_segments(marked) -> list[tuple[str, bool]]`** — pure sentinel-string→segments converter (no API/torch imports, property-testable in isolation). Guarantees: concatenation == input minus sentinels; no empty segments; adjacent equal-match runs merged; stray/unterminated sentinels stripped as unmatched (matches the CLI renderer). Zero-gap adjacent spans (real: `_highlight_terms` on `"aa"` with term `"a"`) merge, so the sentinel round-trip is canonical rather than byte-for-byte for those.
- **`api/search.py`** — sync `def` (threadpool, per § Concurrency), on its own `APIRouter` included into `protected_router` at `app.py` module level (before `create_app`'s catch-all — registration-order invariant). `latency_ms` = `perf_counter()` around retrieval+rerank **only**, excluding the LLM parse, matching what the eval times. `ParsedQuerySchema.from_domain()` mirrors the domain `ParsedQuery` at the boundary so `ApiModel`'s serializer attaches UTC offsets to `date_range` (the domain model stays naive). Params: `q` (required, min_length=1), `mode` (SearchMode, default hybrid), `limit` (ge=1, default 10), `raw` (default true) — `Annotated[…, Query(…)]` style, which also avoids ruff B008. Whitespace-only `q` → `400` (would otherwise 500 in `ParsedQuery` validation).
### `POST /api/open` — implemented (2026-07-15)

Body `{path}`; opens an indexed file via `subprocess.run(["open", …])` (list form, never `shell=True`). Sync `def` (subprocess blocks → threadpool), on `protected_router`.

- **`get_doc_id()` is the security boundary** — only files Oasis actually indexed can be opened, so an arbitrary path is a 404 rather than a launch. `404` not indexed → `410` indexed but gone from disk (distinct so the app can offer a reindex instead of saying "never heard of it") → `204` on success.
- **🔑 Single-form lookup, matching storage's normalization exactly (2026-07-16).** The pipeline now applies `os.path.abspath` once to the index root, so stored paths are always absolute and never symlink-rewritten. The lookup does the identical thing — `os.path.abspath(req.path)`, **no `resolve()`** — because whatever normalization storage uses, lookups must use the same one; resolving the request per-file would follow symlinks that storage did not, reintroducing the exact mismatch. Consequences:
  - A request equal to the stored form (which is all the real client ever sends — it echoes `/api/search` paths verbatim) → `204`, including stored forms that *contain* a symlink (`oasis index /tmp/notes` stores `/tmp/notes/…`, and that exact form opens).
  - A request via a symlink **alias** not equal to the stored form → `404` — open doesn't chase aliases. Fail-closed and deliberate; each behavior has its own test.
  - This replaced the earlier dual-form (`raw` + `resolve()`) lookup, which existed only because storage used to be inconsistent about absoluteness. Note kept for the record: `/Users → /System/Volumes/Data/Users` never bit — `/Users` is a *firmlink*, which `resolve()` leaves alone; `/tmp → /private/tmp` is the real symlink case.
- **Relative request paths** are defensively absolutized against the server's CWD and in practice `404` (storage never contains relative keys, see the pipeline fix). The earlier `400` special-case is gone — there's no longer any ambiguity for it to guard.

- **Discovered live**: the real `~/.oasis/index.db` (877 docs, built June 3) **has no vector data** — it predates vector indexing, so semantic/hybrid return keyword-arm-only results against it, and the server's `VectorIndex` startup created an empty `~/.oasis/index.lance` today. A re-index populates it. Worth remembering for the app's first-run story: an old keyword-only index looks like "semantic search is broken."

### `POST /api/index` + SSE + cancel — implemented (2026-07-16)

`api/jobs.py` (`JobStatus`, `IndexJob` dataclass, `EventBroker`, event builders) + `api/index.py` (the three routes). `AppState` gained `index_job`, `job_lock`, `broker`; `app.py` lifespan captures the running loop and hands it to the broker (the one place `get_running_loop()` is valid). See the **Recently done (2026-07-16)** entry below for the full decision list (race-free single-job lock, retained-finished-job for re-attach, catch-everything job thread, done-vs-cancelled after return, register-before-snapshot, absolute-count self-healing progress, throttle-events-but-update-stats, terminal-never-dropped, `call_soon_threadsafe` bridge, Bearer-header SSE auth, events-through-`ApiModel`, `phase` semantics) and the `root`/cancel-`409` contract deviations reconciled into `CLAUDE.md`.

### Config — `config.py`
`CONFIG_PATH` is module-level (so tests can monkeypatch before construction). `OasisConfig(BaseSettings)`, `env_prefix="OASIS_"`, `db_path` default `~/.oasis/index.db`. Priority: init kwargs > env > TOML > defaults. Missing/empty TOML is fine.

### Evaluation harness — `eval/`
- Corpus copied with `cp -Rp` so **mtimes survive** — they encode 2019-01…2026-06 by filename hash, and ~18 date-tagged queries judge relevance by mtime.
- `run_eval.py` builds a dedicated index under `eval/index/` (gitignored), parses each query exactly as the CLI does (fixed `today=2026-07-07`), runs `hybrid_search(top_n=20)` + rerank to 10, maps results back to corpus-relative keys, scores with **ranx**. Flags: `--reindex`, `--no-rerank`, `--no-parse`, `--today`.
- Writes `results/latest.json` + appends `results/history.jsonl`; `plot.py` charts ndcg@10/precision@5/mrr over history.
- **`verify_served.py`** — one-off seam check (not a history point): reuses everything above and swaps only the retrieval call to prove `GET /api/search` returns rankings byte-identical to direct `hybrid_search`+CE. Writes `results/served_verification.json`. Run when the served path or `run_search`'s over-fetch could have drifted from the harness.

> ### 🔴 HEADLINE FINDING: NL parsing makes retrieval **worse**, not better
>
> Measured 2026-07-14, once Ollama actually worked for the first time. All four rows share **one frozen parse set** (run 1 populates the cache incl. failures; runs 2–4 replay `83 hits / 0 misses`), so retrieval strategy is the only variable.
>
> | mode | ndcg@10 raw | ndcg@10 parsed | Δ | recall@10 raw | recall@10 parsed | Δ |
> |---|---|---|---|---|---|---|
> | keyword (BM25) | 0.1768 | 0.2163 | **+0.040** | 0.1792 | 0.2156 | +0.036 |
> | semantic (vector) | **0.4937** | 0.4156 | −0.078 | 0.6167 | 0.4990 | −0.118 |
> | hybrid (RRF) | 0.4884 | 0.4246 | −0.064 | 0.6500 | 0.5260 | −0.124 |
> | **hybrid + CE** | **0.5602** | 0.4522 | **−0.108** | **0.6844** | 0.5490 | **−0.135** |
>
> **The best configuration Oasis has is `--raw`** — i.e. the NL parsing layer switched off. Parsing only helps keyword mode, and only because it rescues BM25 from being fed a whole sentence.
>
> **Why — two independent mechanisms, both verified per-query.** 19 of 80 queries went from finding the answer to finding *nothing* (several `recall 1.00 → 0.00`):
>
> 1. **Hallucinated hard filters exclude the gold document.** Of 71 successful parses, **24 set `file_types`, 18 set `date_range`, 5 set `folders`** — far more than the query set actually mentions.
>    - `"ffmpeg convert video"` → `file_types: ['.mp4','.mov','.avi']`. It confused *the topic of the document* with *the type of the document*; the gold doc is `md/tldr-ffmpeg.md`, and Oasis doesn't even index `.mp4`. Recall → 0.
>    - `"the storage system google built for structured data at scale"` → `file_types: ['.txt']`, invented from nothing. Gold is a `.pdf`. Recall → 0.
>    - `"powerpoint about onboarding new employees"` → correct `.pptx`, but *also* invented `date_range: after 2026-06-30` for a query with no date in it. Recall → 0.
> 2. **`semantic_query` distillation destroys or corrupts meaning**, even with no filters set:
>    - `"speech asking citizens to serve their country rather than be served"` → `'civic duty'`. A near-verbatim description of the JFK inaugural, reduced to two generic words that embed nowhere near it. 1.00 → 0.00.
>    - `"rising ocean temperatures are killing the reef"` → `'ocean pollution'`. **Factually a different topic** — bleaching is thermal, not pollution. 1.00 → 0.00.
>    - `"ownership borrow checker"` → `'borrow checker'`, dropping the one word that titles the gold doc (`rust-book-ownership.md`).
>
> **The root cause is an asymmetry, not a bad prompt.** Filters are *hard* constraints (`WHERE extension IN …`) bolted onto a system whose whole value is fuzzy matching. A correct filter helps marginally (slight reorder); a wrong filter is catastrophic (excludes the answer, recall → 0). llama3.2:3b is wrong often enough that the expected value is strongly negative. Prompt-tuning cannot fix an asymmetric payoff — **the filters need to become soft (a score boost) rather than hard (an exclusion), and the embedder should see the original query, not the distillation.**
>
> Also: **12/83 parses fail outright** (`InstructorRetryException`) and fall back to raw silently — ~15% of queries in this run, 19/83 in an earlier one. Non-deterministic.
>
> **Do not put "NL parsing improves retrieval" on a résumé.** The measured claim is the opposite. The defensible story is: *"built an eval harness, discovered the headline feature was a net −0.11 ndcg@10 regression, and diagnosed why."* That is a better story than the one that was planned.
>
> ### Earlier warning (now resolved — kept for the audit trail)
>
> **`ensure_ollama()` reports success for a provider that fails every call.** It checks that the server answers HTTP and that the model is listed; it never checks that *inference works*. On this machine both checks pass and all 83/83 parses raise: Homebrew's `ollama` 0.30.0 ships without the `llama-server` binary, so every request 500s. The harness recorded `llm_used: true` while measuring raw queries. **Every eval number ever produced by this project is a raw-mode number.** (Fixed in the harness: `llm_used` now means "the LLM actually parsed something", with `llm_parse_ok`/`llm_parse_failed` counts beside it. Not yet fixed in `llm/manager.py` — see Up Next.)
>
> **Consequence: the keyword row of the comparison table is a strawman.** Raw mode feeds the whole natural-language sentence to FTS5, which ANDs every term, so a 19-word query needs all 19 words in one document. **62 of 80 keyword queries return zero results.** Distilling that sentence into `semantic_query` + `keywords` is the parser's entire job. So:
> - The measured `hybrid+CE vs keyword` gap (+38.3 ndcg points, +217%) is **inflated and must not be published or put on a résumé.** It measures BM25-fed-a-sentence, not BM25.
> - `hybrid (RRF)` scoring *below* `semantic` alone (0.4884 vs 0.4937 ndcg) is likely the same artifact — RRF is fusing a near-dead keyword arm.
> - Only the `semantic` row is unaffected by the parser being down.
>
> **Nothing here is quotable until Ollama actually runs and the table is regenerated.**

**Measured — raw mode only** (`--no-parse`; identical to LLM-on because 83/83 parses fail). Corpus 301 files, 80 scored queries, `today=2026-07-07`:

| mode | ndcg@10 | mrr | recall@10 | p@5 | p@10 |
|---|---|---|---|---|---|
| keyword (BM25) | 0.1768 | 0.1931 | 0.1792 | 0.0600 | 0.0312 |
| semantic (vector) | 0.4937 | 0.4950 | 0.6167 | 0.1950 | 0.1200 |
| hybrid (RRF) | 0.4884 | 0.4632 | 0.6500 | 0.1975 | 0.1275 |
| **hybrid + CE rerank** | **0.5602** | **0.5427** | **0.6844** | **0.2250** | **0.1338** |

Reproduce: `uv run python eval/run_eval.py --mode {keyword,semantic,hybrid} [--no-rerank] --no-parse --no-history --out eval/results/ablations/NAME.json`.

The one solid read: **the cross-encoder is doing real work** — it's the only step that converts RRF's better recall (0.65 vs 0.617) into better ranking (+0.072 ndcg over raw fusion), and it's parser-independent.

**Regression history** (2 rows; both raw mode, rerank on):

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

## Tests — 930, all passing

New 2026-07-17 (`POST /api/reset`): `test_api_reset.py` (8) — reset-while-indexing → 409 with the index untouched (job-lock mutual exclusion); **the barrier-driven search-racing-reset test** (a search held in-flight holding the OLD `VectorIndex` handle, released into the dropped table after reset → 200 well-formed, never 500 — mutation-tested: narrowing the vector arm's `except` makes it fail with the exact LanceDB file-not-found `RuntimeError`); reset → empty status/search → reindex → new content found via **both** hybrid and semantic (the semantic hit proves the *rebuilt* handle is in use); crash-between-stores simulated (clear_meta + rmtree, documents intact) reads honestly as `reindex_recommended: true`; plus 400-without-confirm, 401, 404-no-index, and 204-idempotent.

New 2026-07-17 (seen-set point-of-entry check): `test_pipeline.py` +1 — verified the stale sweep's seen-set is keyed on the **walk yield** (`pipeline.py`, `seen.add(str(path))` — inside the walk loop, before `get_extractor` dispatch and long before `extractor.extract()` runs), not on extraction success. This matters because the census gate (cancel / walk-errors / permission_denied) does NOT cover this case: extractors contractually swallow their own I/O errors and return `None` without touching any of those counters, so a readable-but-momentarily-unextractable file would pass the gate and, if keyed on extraction success, be wrongly swept. Confirmed already correct — no code change needed. Guard test: full-index A/B/C, bump C's mtime (so it's no longer "unchanged" and extraction is attempted), monkeypatch C's extractor to return `None` this run, reindex → `removed: 0`, C's row/vectors/FTS all survive untouched, distinct from the permission-denied-subtree and cancelled-walk sweep-skip tests.

New 2026-07-17 (commit 2, reconciliation + backfill): `test_pipeline.py` +10 — the seven adversarial sweep tests (deleted-file swept from all three stores; sibling-prefix isolation; permission-denied subtree survives; cancelled reindex deletes nothing; rename + new-exclude reconciled; cross-root isolation) over real SQLite + real LanceDB with a counting fake embedder, and the three backfill tests (plain reindex repairs a keyword-only index; fully-vectored unchanged corpus re-embeds zero — spied; markers recorded post-backfill). `test_api_index.py` +1 — SSE `reconciling` phase (throttle disabled) + `removed: 1` in `done`, swept doc absent from both arms.

New 2026-07-17 (commit 1, hardening): `test_api_index.py` grew 2 (wrong/stale `job_id` → 409 with the running job's cancel event untouched — the auto-reindex-race guard; finished-job id → 409) and a module-scoped guard asserting `torch` never lands in `sys.modules`; `test_pipeline.py` grew the instrumented cancel→resume idempotency test (`COUNT(*) == COUNT(DISTINCT path) ==` walked indexable files).

New 2026-07-16: `test_api_index.py` (16) — Part A: concurrent-start exclusivity (barrier + call-spy proves the pipeline runs exactly once), pipeline-failure → `error` event + not wedged, re-attach after completion → terminal snapshot, and the **search-during-index shared-`VectorIndex` regression** (token indexed by the job thread findable via the search thread's shared handle — goes red if `VectorIndex` is made thread-local). Part B: fan-out and terminal-never-dropped-on-overflow (against the `EventBroker` directly), coalescing (progress ≪ files) with terminal always delivered, absolute-count self-heal (`done` stats == true final), no-lost-terminal across the register/snapshot gap, heartbeat `: ping` on a quiet stream, disconnect removes the subscriber, SSE is `async def` + Bearer-header-not-query-param auth, event datetimes carry a UTC offset. Part C: cancel mid-index → `cancelled` (not `done`) with partial stats and committed docs still searchable, cancel-with-no-job → `409`, not-wedged-after-cancel. **SSE tests run against a real uvicorn server** (TestClient buffers streaming responses; see the endpoint note above).

New 2026-07-15: `test_api_skeleton.py` (15) — loading→ready health transitions, load-failure → `status:"error"`, auth (401 envelope, missing/wrong token, health exempt), 503-while-loading envelope, **422 in envelope shape** (the FastAPI-generated one most likely to slip through), 500 envelope with no traceback leaked, thread-local SQLite (two threads, distinct connections, no `check_same_thread`), `invalidate()` reopens.

New 2026-07-16: `test_api_status.py` (9) — no index → 404 envelope, empty index → 200 with 0 docs / `stale_documents` 0 (not null) / no reindex, populated index cross-checked field-by-field against `get_capabilities()` with a UTC-offset `last_indexed_at`, `stale_documents` counts a deleted file, **stale-scan cap** (monkeypatched low) reports `null` and a `count_stale` spy proves the per-file scan never ran, `indexed_roots` present + abspath'd (plus an end-to-end `index_directory` test that a relative root is stored absolutized), no-token → 401 envelope, and reindex_recommended parity with `/api/health` on a legacy index.

Also new 2026-07-15 (updated 2026-07-16 to the single-form contract): `test_api_open.py` (10) — 204 with exact `subprocess.run` args, **stored-form-with-symlink → 204 / symlink-alias → 404** (open matches the exact stored abspath form, never chases aliases), traversal → 404, relative → 404 (defensive abspath; the 400 special-case is gone), unindexed → 404, indexed-but-deleted → 410, 404≠410, 401/422 envelopes, and `subprocess.run` never called on any non-204 path. `test_capabilities.py` (16) — meta round-trip + ON-CONFLICT-updates-in-place, fresh/legacy/vectored `get_capabilities()`, pipeline marker writes with and without an embedder, embedder without `model_name`, **incremental re-run doesn't downgrade `vectors_built`**, corrupt marker reads as absent, and the `/api/health` cases (semantic_ready true → reindex_recommended false, legacy → reindex_recommended true with documents > 0, dimension mismatch → semantic_ready false → reindex_recommended true, **0 documents → reindex_recommended false** ("index me" ≠ "reindex me"), loading → defaults).

New 2026-07-16: `test_pipeline.py` grew the relative-root regression pair — two runs with a relative root from different CWDs into the same DB must keep **both** documents (proven to fail with count == 1, the silent overwrite, when the abspath line is removed), and a relative-root index must store only absolute paths.

Also new 2026-07-15: `test_api_search.py` (18) — real SQLite+LanceDB over a 3-doc corpus with crc32-deterministic fake embeddings (no PyTorch): all three modes well-formed, raw-default skips the LLM entirely (spied `parse_query` + counting fake provider), `raw=false` happy/error/no-LLM paths, keyword-400 vs hybrid-200 vs semantic-200 on `"rock 'n roll"`, UTC-offset datetimes in `parsed.date_range`, zero matches → `[]`, limit/over-fetch via a top_n-capturing fake reranker, 422/401 envelopes, segment canonical-form on the wire. And `test_snippets.py` grew 8 `to_segments` tests including a seeded 600-case property test (canonical round-trip byte-for-byte; arbitrary sentinel soup keeps all invariants; re-parse idempotent; CJK/emoji/ZWJ/combining marks).

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
| `test_pipeline.py` | 52 | `test_walker_edges.py` | 19 | `test_models.py` | 10 |
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

- ~~**Implement the remaining HTTP API endpoints**~~ **All Phase 5.2 endpoints landed** — skeleton, `/api/search`, `/api/open`, `/api/status`, `/api/index` + SSE + cancel, and **`/api/reset` (2026-07-17)**. The search-during-index and search-racing-reset regressions are both built (`test_api_index.py`, `test_api_reset.py`). The `CLAUDE.md` § HTTP API contract is fully implemented; remaining work is the Swift app, not the server.
  - ~~**Design for the NEXT index commit — stale reconciliation + no-vector backfill**~~ **Both landed 2026-07-17** — see Recently done. The sweep gates inside the pipeline on cancel + census cleanliness (equivalent to the planned `status == "done"` gate, decided at the only place holding the seen-set); backfill embeds unchanged-but-unvectored docs. Deferred refinements recorded there: partial-chunk-set detection (a crash mid-embed reads as vectored; `--force` is the escape hatch) and per-subtree permission-denied exclusion (the coarse whole-sweep skip is the deliberate first cut).
- **Migrate the CLI's search command to `query/search.py:run_search()`** — the API already uses it; the CLI still carries its own copy of the mode dispatch (deliberate temporary duplication, one-engine direction). Decide then whether the CLI's rerank query moves from `semantic_query` to the raw query to match the API.
- **The real `~/.oasis` index has no embeddings** (built June 3, pre-vector). Re-index to populate `index.lance`. **Detection is now handled** — `/api/health` reports `documents: 877, schema_version: 0, semantic_ready: false, reindex_recommended: true` against it (verified live 2026-07-16), so the app gets a single server-derived boolean to act on instead of doing version math; what's left is the app-side UX for that prompt. **Repair no longer needs `--force` (2026-07-17): the no-vector backfill makes a plain `oasis index` embed unchanged-but-unvectored docs**, so the plain reindex the app will offer actually flips `semantic_ready` true (verified live against a copy of the real index).
- **🔴 Make the NL filters soft, and stop distilling the embedding query.** The measured headline finding (top of Evaluation): parsing costs −0.108 ndcg@10 / −0.135 recall@10 on hybrid+CE. Two fixes, both small:
  1. **Soft filters.** `_build_vec_where` / `_build_kw_filters` turn LLM guesses into hard `WHERE` exclusions, so one wrong guess zeroes a query's recall. Convert to a post-hoc score boost so a wrong guess costs a little and a right one still helps. This is an asymmetric-payoff problem, not a prompt problem — no amount of prompt-tuning fixes it.
  2. **Embed the original query, not `semantic_query`.** Distillation produced `'civic duty'` for the JFK inaugural and `'ocean pollution'` for coral bleaching. Keep `semantic_query` for the FTS5 arm (where it genuinely helps: keyword +0.040) and give the embedder the user's actual words.
  - Re-run the matrix after each change; the harness now supports it directly.
- **Consider a bigger parse model.** 12–19 of 83 parses fail outright with `llama3.2:3b`, non-deterministically, and the successful ones hallucinate filters. Worth measuring `llama3.1:8b` before concluding the feature is unsalvageable.
- **Fix `ensure_ollama()`'s health check** (`llm/manager.py`). It verifies "server answers HTTP" + "model appears in `ollama list`" and calls that available — but a provider that passes both can still 500 on every inference (exactly what a broken `llama-server` does). The CLI then silently falls back to raw on every search and **the user is never told the feature is dead**; the eval reported `llm_used: true` for 83/83 failures. The check should do one tiny real completion, cache the result, and treat a failure as unavailable. This is the difference between "Ollama isn't installed" (fine, expected) and "Ollama is lying to you" (currently indistinguishable).
- **Regenerate the comparison table once the parser runs.** The current keyword row is invalid as a baseline (see the warning under Evaluation).
- ~~**Pre-existing lint debt: 66 ruff errors**~~ **Cleared 2026-07-17** — `ruff check .` is clean whole-tree. All mechanical: `F401`/`I001`/`UP017`/`UP037`/`SIM300`/`F841` auto-fixed; `B904` (3 CLI `raise typer.Exit(1) from None`), `B905` (2 `zip(..., strict=False)`, behavior-preserving), and `UP042` (`SearchMode` → `StrEnum` in `cli/app.py`, matching `query/search.py`) fixed by hand; `B008` (Typer's `Option(...)` default pattern — a false positive) and `N806` (capitalized mock-class aliases in tests) silenced via `per-file-ignores`. No behavior change (930 tests green). **Still deferred:** `ruff format` would reformat 29 files — a separate, larger-churn pass, deliberately not bundled here.
- Nested `.gitignore` support in the walker (per-directory, not just root).
- UTF-8-only text extractor can't read latin-1 files (`edge-latin1-menu.txt` in the corpus).
- OCR fallback for scanned PDFs.

### Recently done (2026-07-17)
- **Phase 5.2 closeout — tag, served-path verification, app-seam map** (three parts, no product code):
  - **Tag `service-layer-complete`** (annotated, at `734a84c`, pushed to `Oasis`) — the last single-language / single-`pytest` baseline before Swift. What makes later two-toolchain bisection possible.
  - **Served path verified equivalent to the direct harness** (`eval/verify_served.py`, result in `eval/results/served_verification.json` — a one-off seam check, deliberately **not** in `history.jsonl`). Reuses `run_eval.py`'s machinery wholesale (query load, eval index, qrels, `_relpath`, ranx) and swaps only the retrieval seam: direct `hybrid_search(top_n=20)`+CE-to-10 vs the same index through `GET /api/search?mode=hybrid&limit=10&raw=true` (in-process `TestClient`, real models). Result at the canonical raw-hybrid config: **all five metrics byte-identical (Δ = 0.0), 0/83 ranking mismatches**, and candidate over-fetch matches (20/20 → rerank to 10 — the reconcile the prompt flagged: `run_search`'s `max(limit*2,20)` equals the harness's `CANDIDATE_TOP_N`). Direct block also reproduced the canonical numbers exactly (ndcg 0.5602, mrr 0.5427, recall 0.6844). The `/api/search` seam is safe to build the Swift app on.
  - **App spawn/handshake/readiness seam mapped and measured** → `docs/APP_SEAM.md`, every claim verified by an actual spawn (transcripts pasted in the doc), not read off `serve.py`. Measured: **stdout is pure** — first line is the `{port,token,pid}` handshake, **zero** stdout after it, all uvicorn/HF/model logs and even the failure traceback on **stderr** (the seam the "read one JSON line" parser depends on); `t_handshake` ≈ **2–3.3 s**, `t_ready` ≈ **35–54 s** (local-load-dominated, high variance — long enough that a blocked first request flirts with URLSession's ~60 s timeout, which is the concrete case for poll-health-never-block); the **error path forced live** (`HF_HUB_OFFLINE=1` + empty cache → `/api/health` 200 `status:error` with the real message). Fills the startup→ready row in the North Star table. Watchdog/shutdown (§5) and the child-dies-after-ready path reasoned from `serve.py`, marked as such.
- **`POST /api/reset`** — the last Phase 5.2 endpoint. Deletes the index and stands up an empty one without a restart. The deletion is trivial; the commit is the **swap** — four commits made `VectorIndex` one shared handle every search reads and the index job writes through (the reason search-during-index is fresh), and reset destroys and replaces that handle while searches may be mid-flight against it. This is the inverse of the invariant those commits protected.
  - **Endpoint** (`api/reset.py`, on `protected_router`): body `{confirm: bool}` → `400` unless `confirm: true` (no interactive prompt over HTTP; a bare/empty body must not nuke the index — `confirm` defaults False so `{}` is a clean 400, not 422). Takes the **same `job_lock` as `/api/index`** and `409`s while a job runs — reset drops the handle the job writes through, so they're mutually exclusive; the whole reset runs under the lock. `404` if no index at `db_path`. `204` on success. Post-reset: `/api/status` → `200` 0-docs, `/api/search` → `200 []`, next `/api/index` repopulates.
  - **The swap** (`AppState.reset_index()`, under the lock): (1) in-flight searches hold the OLD handle for their call's life and degrade cleanly when reset `rmtree`s its `.lance` files — LanceDB raises a `RuntimeError` (IO error), which the hybrid **vector arm already catches** (`except Exception`, comment added); only *subsequent* searches read the new handle, which is why `api/search.py` reads `state.vector_index` fresh per request (verified it isn't captured at startup). (2) Rebuild not `checkout_latest` — the versions are gone with the dir; a fresh `VectorIndex` is installed as the shared instance. (3) **Deletion order markers → vectors → documents**: no `vectors_built` marker outlives its vectors, so a crash mid-reset reads as the conservative "reindex needed", never "semantic-ready with no vectors". (4) SQLite cleared **in place** (`KeywordIndex.clear_meta()` + `clear_documents()`, the `_ad` trigger clearing FTS) rather than unlinked — safer for in-flight readers than deleting a file under open connections, and leaves `/api/status` a true empty index rather than a 404 — then `invalidate()`d. *(This is the one deviation from the earlier CLAUDE.md text, which said "delete the .db file / 404"; clear-in-place is the safer swap and CLAUDE.md is reconciled.)*
  - **8 tests** (`test_api_reset.py`); the barrier-driven search-racing-reset is the one the commit turns on — **mutation-tested**: narrowing the vector arm's `except` makes it fail with the exact LanceDB file-not-found `RuntimeError`, proving the drop genuinely races a live reader and the broad catch is load-bearing. **Live-verified** (real models): populate→reset→204→empty status/search→reindex→found via both hybrid *and* semantic (rebuilt handle in use); a 400-file index running + reset → 409, job continued to `done`; 34 searches fired across a reset, zero 500s.
- **Stale-document reconciliation + no-vector backfill** — the pipeline-semantics commit the hardening commit cleared the way for. Both live in `index_directory`; the API/job layer only reads new stats.
  - **Part A — the sweep.** On a complete clean walk of a root, stored docs under that root not seen in the walk (deleted, moved, newly excluded) are deleted from SQLite (+FTS via the `_ad` trigger) and vectors (`delete_by_doc_id`), converged per doc. Runs on *any* complete walk, not just `--force` — force governs embedding, never walking, so an incremental reindex's seen-set is still a full census. **Census gate (fail-safe: in doubt, delete nothing):** skipped entirely when cancelled, any walk `on_error` fired, or `permission_denied > 0` — the last is the landmine (a `chmod 000` subdir completes the walk without seeing the subtree; sweeping would mass-delete it). Scoping is a Python separator-boundary filter (`startswith(root + os.sep)` in `KeywordIndex.docs_under`), never SQL `LIKE` (`_` is a LIKE wildcard). Sweep placed after the embed phase so a mid-embed cancel also never sweeps.
  - **Part B — backfill.** Unchanged files are skipped **iff** already vectored: `VectorIndex.doc_ids_with_vectors()` (one bulk doc_id projection, computed once pre-walk) feeds the skip check; unchanged-but-unvectored docs re-extract + embed. A plain `oasis index` now repairs a pre-vector index — `--force` no longer required. **Known limitations, logged not fixed:** (1) "has any vectors" counts a partial chunk set (crash mid-embed) as vectored, so that case still needs `--force`; chunk-count comparison is a future refinement. (2) The inverse wart, found live: a doc whose extracted text chunks to nothing (18 of the real index's 877) can *never* become vectored, so it re-extracts on every plain reindex — harmless (zero embed calls, ~seconds) but it keeps `indexed` nonzero on an otherwise no-op run. A "no chunks" marker would close it; same family as (1).
  - **Live verification (2026-07-17), all three scenarios:** (a) small root, delete a file, plain reindex → `done` stats `removed: 1`, swept doc absent from keyword/semantic/hybrid; (b) `chmod 000` a subdir holding an indexed doc, reindex → `permission_denied: 1, removed: 0`, doc still searchable; (c) **the real `~/.oasis` repair on a copy**: plain `oasis index ~/Documents` (no `--force`) re-embedded all 509 unchanged pre-vector docs (36,340 chunks, 6m43s, 218 chunks/s) → `/api/health` flipped to `semantic_ready: true, reindex_recommended: false` (877 docs — the 368 Downloads-rooted docs untouched, cross-root isolation on real data); a second plain reindex embedded zero chunks and took 10s.
  - **Part C — surfaced.** Stats gain `removed` (always present; `0` when sweep skipped/found nothing) — in the pipeline dict, `_ZERO_STATS`, SSE `snapshot`/`done`, and the CLI summary (`N removed`). New SSE `phase: "reconciling"` via the pipeline's `on_reconcile` callback; often a blink and throttle-droppable — the durable signal is `removed` in the terminal stats. `CLAUDE.md` § `/api/index` documents gate/scoping/backfill.
  - **11 new tests** (10 pipeline + 1 API/SSE), each written to go red on a real bug: deleted-file swept from all three stores; sibling-prefix isolation (`/x/a` never sweeps `/x/ab`); **permission-denied subtree NOT swept**; cancelled reindex sweeps nothing despite a genuinely stale row; rename and new-exclude reconciled (what disk-existence `count_stale` can't see); cross-root isolation; plain-reindex backfill of a keyword-only index; fully-vectored unchanged corpus re-embeds **zero** (spied — backfill must not degenerate into always-re-embed); markers correct post-backfill; SSE `reconciling` phase + `removed: 1` + both arms clean.
- **Indexing hardened ahead of the stale-sweep commit** (three parts, no stored data touched — this commit exists to make the deletion commit safe):
  1. **`POST /api/index/cancel` now requires `{job_id}`.** A bodyless "cancel whatever is running" loses a race FSEvents auto-reindex (Tier 1) will introduce on purpose: a cancel tap aimed at job N arriving after N finished and N+1 auto-started would silently kill N+1. `202` only when the id names the currently-running job; `409` for a stale id, a finished job's id, or no job running (same status code as before — only the meaning sharpened). A mismatched id never touches the running job's cancel event (regression-tested via the event itself: 409 + `cancel.is_set()` false). `CLAUDE.md` cancel entry reconciled.
  2. **The fast suite is hermetic again — and provably so.** `oasis.api.app`'s import chain was pulling PyTorch at *import time* via module-level `from sentence_transformers import …` in `embeddings.py`/`reranker.py`, even though every API test fakes the model classes (no real weights ever loaded — the 45s fixture budget was compensating for LanceDB churn plus the one-time torch import, not weight loads). Both modules now bind the class lazily on first `_load_model` call, with a module-level `None` sentinel under the same public name so all 27 existing `patch("…SentenceTransformer"/"…CrossEncoder")` sites keep working torch-free (real class under `TYPE_CHECKING` for annotations). **Measured: the entire 910-test default suite finishes in ~10s with `torch`/`sentence_transformers` absent from `sys.modules` at exit**; a module-scoped guard in `test_api_index.py` keeps it that way. The readiness budget went back to 10s (matching `test_api_skeleton`); readiness is sub-second in practice.
  3. **Resume idempotency instrumented — invariant HOLDS, nothing to fix.** New `test_pipeline.py` test: full index → `--force` reindex cancelled at 10 files → resume to completion, over a root whose indexable-file count is measured by an independent `walk()`. Result: `COUNT(*) == COUNT(DISTINCT path) == walked_indexable` exactly (48 == 48 == 48). The live "resumed to 403 docs over a 400-file root" is thereby reconciled as a mis-description of the root, not a bug: "400-file" was an approximation and 403 was the true indexable count (a distinct-path phantom is structurally impossible under the UNIQUE path column, and the path-form collision class was closed by the abspath-at-entry fix). The stale sweep's 1:1 stored-docs↔indexable-files assumption is safe to build on.

### Recently done (2026-07-16)
- **`POST /api/index` + SSE events + cancel** — an async, observable, cancellable index job wrapping the existing `index_directory` (add + update only; **no** stale-delete or vector-backfill yet — those are the next commit). Three routes on `protected_router` (before the catch-all): `POST /api/index` → `202 {job_id, status}` / `409` if running / `400` non-directory; `GET /api/index/events` (the one `async def`) → SSE; `POST /api/index/cancel` → `202` / `409`. New `api/jobs.py` (`JobStatus`, `IndexJob`, `EventBroker`, event builders) + `api/index.py`; `AppState` gained `index_job`, `job_lock`, `broker`; lifespan binds the running loop to the broker. Decisions worth keeping straight:
  - **Race-free single-job lock.** Check-and-set (is one running? → install the new job) is atomic under `job_lock`, held *only* across that, never across the run. The `409` guard keys on `status == "running"`, not existence — a **finished job is retained** in state so a subscriber connecting after completion gets a terminal snapshot (re-attach is first-class), and a new POST overwrites it.
  - **Job thread catches everything** → a wedged `running` (409 forever, no terminal event, SSE clients hung) is the failure mode prevented: on exception, `status=error` + terminal `error` event; always sets `finished_at`. **done vs cancelled is decided after the pipeline returns** (it returns partial stats either way) by branching on `job.cancel.is_set()` — the exact signal the next commit's stale sweep gates on.
  - **SSE: register-before-snapshot** (a terminal firing in the gap lands in the queue, not lost); **absolute-count progress** (lossy delivery self-heals; a delta stream would desync permanently); **stats updated every file but events throttled ≥100ms** *before* fan-out (a 100k-file index would otherwise schedule 100k×N loop callbacks); **terminal never dropped, progress is** (bounded queue evicts stale progress to make room for a terminal); **`call_soon_threadsafe` is the one thread→loop bridge**; **Bearer *header* auth** (Swift `URLSession` can set it — not a query-param token); **events serialized through `ApiModel`** so `started_at`/`finished_at` carry a UTC offset; **`phase` (`scan`/`embed`)**, not "`total` is null", distinguishes still-scanning from done-but-empty.
  - **Contract note (deviates from the earlier spec text, now reconciled in `CLAUDE.md`):** request body is **`root`** (not `path` — `/api/index` walks a directory, `/api/open` opens a file); **cancel takes no body and `409`s** when no job runs (not `{job_id}`/`404`) — one job slot, so "job-state conflict" is one code across start and cancel. *(Superseded 2026-07-17: cancel now requires `{job_id}` — see Recently done below. The `409` semantics stand; only the body was added.)*
  - **16 tests** (`test_api_index.py`), incl. the two that matter most: concurrent-start exclusivity (barrier + spy proves the pipeline runs exactly once) and no-lost-terminal across the register/snapshot gap. **SSE tests run against a real uvicorn server, not `TestClient`** — Starlette's `TestClient` buffers streaming responses, so a lone `snapshot` on an idle stream never flushes and the "read snapshot, then act" pattern deadlocks; a live server streams incrementally as the Swift client will. **Verified live** (`oasis serve`, real models): `snapshot → progress(scan, total null) → progress(embed, total known) → done`; a token indexed by the job thread became findable via the shared `VectorIndex`; second POST → `409`; cancel → `cancelled` with partial stats (96/400) and committed docs still searchable; reattach after completion → terminal `done` snapshot; re-run after cancel resumed to 403 docs.
- **`GET /api/status`** — the token-gated (auth-required, unlike `/api/health`) detail view the app's manage-index screen reads. Reuses `get_capabilities()` (no DB logic duplicated) and derives `semantic_ready` / `reindex_recommended` byte-identically to health, so the two never drift. New `StatusResponse` (inherits `ApiModel`, so the datetime serializer applies) adds paths/sizes health omits: `db_size_bytes` (sums `.db` + `-wal`/`-shm`), `last_indexed_at` (UTC-offset ISO), `db_path`, `indexed_roots`, and `stale_documents`. **404 (not 200) when no index exists at `db_path`** — health always 200s ("is the server up"); status describes the index, and no-index is its not-found. Empty index (0 docs) is a 200. `stale_documents` counts stored paths gone from disk (`KeywordIndex.count_stale()`, one `stat` per doc) but is **capped**: over `STALE_SCAN_CAP` (5000) the scan is skipped and the field is `null` ("not computed", distinct from `0` = "computed, none stale"). **`indexed_roots` persistence added to the pipeline**: `index_directory` now calls `KeywordIndex.add_indexed_root(str(root))` (abspath'd, JSON list, deduped) before the walk, so even a cancelled/permission-denied run registers the root — this is the load-bearing prerequisite for the full-reindex stale-sweep in Up Next (never guess roots from a common prefix). Sync `def` (blocking stats → threadpool), on `protected_router` before the catch-all. 9 new tests (`test_api_status.py`), incl. stale-cap-skips-scan (spy proves no per-file stat) and reindex_recommended parity with `/api/health` on a legacy index. **Verified live** against the real `~/.oasis` index: `documents: 877, schema_version: 0, semantic_ready: false, reindex_recommended: true, stale_documents: 0` (all 877 June-3 paths still exist on disk — verified independently; `indexed_roots: []`, since that index predates root tracking).
- **Relative-path data-integrity fix** — one commit, three parts, one invariant: *whatever normalization storage uses, every lookup uses the identical one* (`os.path.abspath`, lexical, no symlink following).
  1. **Pipeline (the actual bug):** `index_directory` absolutizes `root` once at entry. Before, a relative root stored relative keys — CWD-ambiguous, and two runs from different CWDs stored the same string for *different* files, silently overwriting each other via the UNIQUE path column's ON CONFLICT (document loss). Regression-tested, and the test was proven load-bearing (count == 1 with the abspath line removed). Consequence, accepted: the first reindex of a formerly-relative-root index re-inserts every document once (keys changed), which is also what clears the colliding rows; absolute-rooted indexes are untouched (abspath is a no-op).
  2. **`/api/open` collapsed to a single-form lookup.** With storage always absolute, the dual-form `resolve()` logic became wrong, not just unnecessary — resolving the request would follow symlinks storage didn't. Now: `abspath(req.path)` → `get_doc_id` (404) → exists (410) → open (204). Contract: matches the exact stored form, does not chase symlink aliases (alias → 404, fail-closed; the real client echoes stored paths verbatim). The relative→400 special-case is gone with the ambiguity that justified it.
  3. **`SCHEMA_VERSION` 1 → 2** (schema now guarantees absolute paths), and `/api/health` grew `schema_version` + server-derived `reindex_recommended` (= `documents > 0 AND (schema_version < current OR NOT semantic_ready)`; 0 docs is "index me", not "reindex me").
  - **Verified live end-to-end:** the real 877-doc pre-vector index reports `schema_version: 0, reindex_recommended: true`; force-reindexing a copy (509 files re-embedded to 36,340 chunks — `--force` required, since skipped files never reach the embed phase) flipped it to `schema_version: 2, semantic_ready: true, reindex_recommended: false` with `all-MiniLM-L6-v2` @ 384.

### Recently done (2026-07-15)
- **`POST /api/open` + index capability markers** (one commit, two independent parts). Open validates against the index (`404`/`410`/`204`) with a dual-form path lookup that survives symlinks in either direction. Capabilities add a `meta` table, `SCHEMA_VERSION`, `get_capabilities()`, pipeline marker writes, and four new `/api/health` fields incl. `semantic_ready` (which catches the dimension-mismatch case). 25 new tests. **Verified live end-to-end**: the real 877-doc pre-vectors index reports `semantic_ready: false`; reindexing into a copy of it flipped that to `true` with `embedding_model: all-MiniLM-L6-v2, embedding_dimension: 384`. Found a pre-existing relative-path pipeline bug in the process (see Up Next).
- **`GET /api/search`**: raw-by-default (the eval's finding made policy), keyword-400/hybrid-200 error contract, segment snippets via pure `to_segments`, `run_search` extracted to `query/search.py`, `latency_ms` measured warm around retrieval only. 26 new tests. Verified live against an indexed eval corpus in all three modes. Details under "`GET /api/search`" above.
- **HTTP API server skeleton** (`src/oasis/api/`): handshake, background model loading + warmup, `/api/health`, bearer auth, readiness gating, error envelope, auth-gated 404 catch-all, `oasis serve` command, 15 tests. Details under "HTTP API — `api/`" above. Remaining endpoints are separate commits.

### Recently done (2026-07-14)
- **Ollama actually works now, for the first time.** Homebrew's `ollama` 0.30.0 formula ships without the `llama-server` binary — the server answers HTTP and `ollama list` works, but every inference 500s. Replaced with the `ollama-app` cask (0.32.0) and removed the formula. **Side effect to be aware of: `brew install --cask` autoremoved `mongosh` entirely** (`brew install mongosh` to restore). Note `ollama` is no longer on `PATH` — the cask only installs the CLI symlink after you click through its first-run GUI — which `manager.py` needs for `ollama list`.
- **Test suite was not hermetic.** `test_cli_edges.py` mocked the embedder, LanceDB, and the cross-encoder but *not* `ensure_ollama`, so the FTS5-syntax-error tests depended on the machine's Ollama state. They passed for months only because the local Ollama was broken, and failed the moment it was fixed (a working LLM rewrites the deliberately-malformed query into a valid one, so the test silently stops testing anything). `test_cli.py` already patched it; `test_cli_edges.py` now does too.
- **Eval harness: `--mode`, parse cache, honest `llm_used`.** `--mode {keyword,semantic,hybrid}` mirrors the CLI so keyword/semantic baselines are measurable at all (previously impossible — it only ever called `hybrid_search`). Rerank defaults to on for hybrid, off otherwise, matching the CLI rather than measuring an ablation the product never runs. `--no-history`/`--out` keep mode comparisons out of the regression time series. Scores are **positional**, because FTS5's `rank` is *negative* (more negative = better) while vector distance is positive — feeding either raw to ranx, which sorts descending, would silently invert the ranking and make that mode look catastrophic.
- **Split `hybrid_search`'s try blocks** — the eval's open finding, now fixed. Worth **+23% ndcg@10** (0.455 → 0.560) with no ranking change; it purely recovers the 10 punctuation queries that were scoring 0. Amended the API contract to match: hybrid `200`s and degrades, only keyword mode `400`s.
- **Pipeline `cancel` + `permission_denied`** (see Phase 5.2 above).
- **Python version reconciled to 3.14** across all four sources: `requires-python = ">=3.14"`, ruff `target-version = "py314"`, README "3.14+", venv already 3.14. Chosen over 3.13 because it's what actually runs and all tests pass on it; **verified PyInstaller 6.21.0 resolves cleanly under `>=3.14`**, so Phase E isn't boxed in. The bump surfaced exactly 1 new lint error (`UP043` in `walker.py`), fixed.
- **`CLAUDE.md` Stack corrected** — removed the Anthropic API (deleted in `e3046f5`) and "FastAPI + HTMX for web UI", which contradicted the HTTP API section's native-SwiftUI premise three screens below it.
- **`__init__.py` backfilled** for `cli/`, `index/`, `extractors/`, `tests/`. Implicit namespace packages are fine under `uv_build` but are a known PyInstaller module-discovery weak spot, and Phase E ends in PyInstaller.