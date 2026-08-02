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
| Retrieval quality (best config, raw) | Beat the standing best; never silently regress | ndcg@10 **0.5601**, mrr 0.5427, recall@10 0.6844 — restated 2026-07-25 on the pixi/OpenBLAS-CPU stack (was 0.5602; 2 of 80 queries reordered within top-10, see Recently done) |
| NL parsing layer | Net-positive on the matrix *before* it's default | **−0.108 ndcg@10** — disabled by default |
| Warm query latency | Establish a p95 budget, then hold it | **not yet measured** — measure via the HTTP service, warm |
| App startup → ready (dev spawn) | Fast enough that models-loading isn't the first impression | **measured 2026-07-17**: `t_handshake` ≈ **2–3.3 s** (spawn→handshake), `t_ready` ≈ **35–54 s** (handshake→`status:ready`, local-load-dominated, high variance). Long enough that the app must poll `/api/health` and never block — see `docs/APP_SEAM.md` |
| **Ship startup → ready** (the one that counts) | The number a stranger actually experiences | **11.63 s measured 2026-07-28** — Finder double-click → `/api/health` `ready`, **cold** (HF + tiktoken + torch caches moved aside), **offline** (Wi-Fi off, `curl` to huggingface.co and openaipublic both fail), weights loaded **from inside the bundle**. 6.85 s on an immediate relaunch. Handshake 1.73 s, warming 4.14 s |
| Distribution | One double-click, signed + notarized, zero deps | in progress (Tier 1) — self-contained `.app` **done** (server + weights + tiktoken embedded, 1.3 GB); signing, notarization and the DMG still owed |
 
Warm query latency is still a blank on purpose — it has never been measured and pretending a number exists is exactly the failure the eval discipline exists to prevent.
 
**Why there are two startup rows, and why only the second one counts.** The 2026-07-17 figure was measured with a warm HuggingFace cache and a live network, which is the one regime a downloaded app never runs in — it says nothing about a stranger's first launch, and its 35–54 s window in particular was a dev-machine artifact. The 2026-07-28 row is the shipped regime: nothing on the machine but the bundle, and no network to fall back on. It replaced a 35–54 s guess with **4.14 s** of actual model loading. Keep the old row only as the record of what a dev spawn costs.
 
### Non-goals (scope boundaries)
 
Naming these is what keeps an ambitious side project finishable.
 
- **Not cloud anything.** No sync, no hosted index, no remote LLM. Offline operation is a product requirement, not a setting. (Recorded already: the Claude API path was deleted; don't reintroduce a cloud provider.)
- **Not multi-user, not a server product.** The HTTP service is a single-user local seam for the app, loopback-only. It is not a deployment target.
- **Not iOS/Android — yet.** The clean HTTP contract keeps the door open, but mobile is out of scope until the Mac app is real.
- **Not competing with Spotlight on exact-filename or system-file lookup.** That's Spotlight's job and it's fine at it. Oasis owns the by-description, by-content case.
- **Not the Mac App Store.** Distribution is a free, directly-downloaded, signed + notarized `.app` (Tier 1), with Sparkle auto-update later (Tier 3) — which the App Store wouldn't permit anyway. This is what makes **App Sandbox a non-requirement**: sandboxing is mandatory for the App Store and for nothing else, and the app's two defining behaviors — spawning the server child outside its bundle, indexing arbitrary user-chosen folders — are precisely what it forbids. Recorded so the `ENABLE_APP_SANDBOX = NO` decision reads as architecture, not debt.
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

Last resynced against the repo: **2026-07-29** (939 tests — 936 fast + 3 `slow` — all passing, `ruff check .` clean, verified by running them. Two passes that day: the README rewritten against the true state, then a subtraction pass that cut 151 lines and one unused dependency — see both Recently done 2026-07-29 entries).

---

## Repository shape

**One branch: `master`, and the history is linear — zero merge commits, by construction.** Every arc so far (the pixi migration, the whole Swift app, the freeze spikes, the packaging steps) landed as ordinary commits on `master`; the one topic branch that ever existed, `app-wrapper-self-contained-bundle`, was fast-forwarded in and deleted on 2026-07-28 so the packaging work reads as one sequence rather than a side track. Local `master` and `Oasis/master` (GitHub) are kept identical.

Regenerable artifacts are gitignored and must stay that way: `dist/` (the frozen server the build phase embeds), `spike/pybuild/` (PyInstaller's `--workpath`, rebuilt on every `--clean` freeze), `app/.build-release/` (xcodebuild derived data). What *is* tracked is the recipe — `spike/build.sh` and `spike/serve_entry.py` — never its output.

## Current State

Phases 1–5.1 complete: extraction, keyword index, vector index, hybrid retrieval, NL query parsing, CLI, and the evaluation harness. Phase 5.2 (HTTP API): **all endpoints implemented** — skeleton/handshake/model-lifecycle/health/auth/error-envelope (2026-07-15), `GET /api/search` + `POST /api/open` + capability markers (2026-07-15), `GET /api/status` + `POST /api/index` + SSE + cancel (2026-07-16), stale reconciliation + no-vector backfill + job-bound cancel (2026-07-17), and **`POST /api/reset` (2026-07-17)**. The full `CLAUDE.md` § HTTP API contract is now built. **Phase 5.2 is closed** — tagged `service-layer-complete` (`734a84c`), the served retrieval path verified byte-identical to the direct eval harness, and the app spawn/handshake/readiness seam mapped + measured in `docs/APP_SEAM.md`. That doc is the entry point for the next phase, the native Swift app (Tier 1).

**Phase 6 (the Swift app) is underway.** Steps 1–6 are built and verified live: `app/Oasis/` spawns `oasis serve --managed`, reads the handshake, polls `/api/health`, renders warming/ready/failed, and tears the child down on both quit paths (step 1); the main window searches through `/api/search` into a result grid (step 2); and **Index New Folder now works end to end** — `NSOpenPanel` → `POST /api/index` → the SSE progress stream → progress sheet with cancel → terminal summary → document count refreshed, closing the index→searchable loop inside one session (step 3, 2026-07-26). **Reindex Current Folders** re-scans every root from `/api/status.indexed_roots` sequentially over a shared `IndexRunner`, surfacing the reconciliation sweep's `removed` count, and stops the whole sequence on cancel or failure (step 4, 2026-07-26). The **Indexed File Statistics** panel reads `/api/status` for real counts, size, last-indexed, semantic-search state, the folder list, and worded `reindex_recommended` / stale nudges (step 5, 2026-07-26). **Reset Indexing** wipes the index behind a destructive confirm that names the document count, then drops the app to the empty state (step 6, 2026-07-26). **The Spotlight-style summon landed 2026-07-27 (step 7)**: a global ⌘⌥O pops a floating, borderless `NSPanel` over other apps and on the current Space, typing works immediately, and Enter hands the query to the main window — which now survives being closed, because the app is menu-bar resident (`applicationShouldTerminateAfterLastWindowClosed = false`) and only Quit tears the server down. **Results now open** in whatever app owns them, through `POST /api/open` — by click, or by **arrow keys + Return without leaving the query line** (2026-07-27), which makes summon→type→arrow→Return a complete mouse-free path. **Settings landed 2026-07-27 (step 8), and with it every control in the app is live**: a standard `Settings` scene (⌘, + the menu item, for free) with General / Folders / Shortcuts / About — launch-at-login, a results-count preference, reveal-the-index-in-Finder, a Full Disk Access explainer, hotkey rebinding, and **manage indexed folders**, which is the recourse for a root deleted from disk and rides on the new `POST /api/index/remove-root`. See "Recently done (2026-07-27)", "(2026-07-26)" and "(2026-07-25)".

**The app is feature-complete for Phase 6 and the codebase has had a full refactor pass (2026-07-28)** — one search engine shared by the CLI and the server (the CLI's duplicate copy had drifted onto the eval-rejected rerank input), one `OasisAPI` client shared by the six Swift view models, one implementation of the capability derivation that `/api/health` and `/api/status` both promise not to disagree on, and two real over-matching bugs fixed in the folder filter. See "Recently done (2026-07-28)".

**Packaging has started, and the app now works on a machine that has never seen it (2026-07-28).** A Release build embeds the frozen server, both models and the tiktoken encoding in `Contents/Resources/`, and spawns the server pointed at them offline. Verified the only way it can be: HF/tiktoken/torch caches moved aside, **Wi-Fi off**, Finder double-click — **11.63 s to ready**, models proven open from inside the bundle by `lsof`, then an index and a search. 1.3 GB total. What that step could **not** settle is the Full Disk Access question: this Mac does not gate `~/Documents` for *any* app (proven with three fresh-identity control bundles), so the clean walk is uninformative and the spawned-server TCC fork stays open. See "The `.app` wrapper" entry; the rest of the arc — weights + offline, the `libtorch_cpu` dedup, deliberate signing, the DMG — is under Up Next › Packaging.

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

tests/                   38 test modules, 939 tests
├── fixtures/            sample.{txt,md,pdf,docx,pptx,xlsx,csv}
eval/
├── corpus/              301 labeled files + MANIFEST.md
├── queries.yaml         83 labeled queries
├── run_eval.py, plot.py, verify_served.py
└── results/             latest.json, history.jsonl, metrics_over_time.png

app/Oasis/Oasis/         25 Swift files — the macOS app (Phase 6)
├── OasisApp.swift       @main: Window + MenuBarExtra + Settings scenes, AppDelegate
├── OasisAPI.swift       ALL server addressing: URLs, authorized requests, sessions, error envelope
├── ServerController     spawn → handshake → health poll → teardown
├── AppSearchCoordinator app-level search + status state (outlives the window)
├── <feature>ViewModel   Search / Status / Index / Folders  ← one per server concern
├── <feature>Models      wire mirrors of api/schemas.py, field names read off the source
└── views                ContentView, ResultCard, StatisticsPanelView, IndexProgressView,
                         SettingsView, SummonPanel/View, ThumbnailLoader, LaunchAtLogin
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
- **`keyword.py`** — `KeywordIndex`; **all SQL in the project lives here**. `_file_hash(size, mtime)` → SHA-256[:16], used by both `upsert` and `is_unchanged` so skip logic and storage can't drift. `upsert` uses `INSERT … ON CONFLICT DO UPDATE` (fires the UPDATE trigger). `search(query, limit, *, after, before, folders, extensions)` — FTS5 `MATCH` + dynamic parameterized WHERE; `snippet(…, char(2), char(3), …)` keeps SQL free of string interpolation. The `folders` filter goes through **`folder_like_pattern()`** (separator boundary + escaped `%`/`_`, paired with `ESCAPE '\'`) — see the 2026-07-28 entry; a bare `LIKE 'prefix%'` matched siblings and treated a path's own `_` as a wildcard. Also `count`, `last_indexed_at`, `get_doc_id`, `delete`, `set_meta`/`get_meta`/`get_capabilities`, `docs_under(root)` — the sweep's scope query, whose authoritative filter is a Python separator-boundary `startswith`, never SQL `LIKE` (`_` is a LIKE single-char wildcard) — and `clear_documents()` / `clear_meta()`, the two halves of `POST /api/reset` (ordered separately around the vector drop for crash-safety). `Result`: `path`, `doc_id`, `title`, `snippet`, `rank`. `MATCH_START = "\x02"`, `MATCH_END = "\x03"`.

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
- **`retriever.py`** — `hybrid_search(conn, vector_index, embedder, parsed, *, top_n=10, candidate_limit=50) -> list[HybridResult]`. FTS5 (`build_fts_query`: semantic_query + quoted keywords) + vector (`build_vec_where`: extension/mtime/path filters) → dedupe vector to best chunk per doc → `_rrf` fusion (`RRF_K=60`) → assemble, preferring FTS5 snippet over raw chunk text. `build_kw_filters` extracts `after`/`before`/`folders`/`extensions` for `KeywordIndex.search`.
  - **The three `build_*` helpers are public**, and were renamed out of `_`-prefixed privacy on 2026-07-28: four modules outside `retriever.py` import them (`query/search.py`, `cli/app.py`, `eval/run_eval.py`, the tests), so the underscore was documentation that no longer described anything.
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
- **`query/search.py`** — **the one search engine.** `run_search(conn, vector_index, embedder, reranker, query, parsed, *, mode, limit)` returning `list[HybridResult]` for all three modes; `SearchMode(StrEnum)` lives here. Keyword score is `-rank` (FTS5 rank is negative-better), semantic score is `1 − distance` (cosine similarity), hybrid is RRF then CE. Hybrid reranks against the **user's raw query**, not `semantic_query` (the eval showed distillation corrupts meaning); identical when raw.
  - **Both front-ends call it.** The CLI migrated here 2026-07-28; until then it carried a second copy of the mode dispatch, and the copy had drifted — the CLI reranked on `semantic_query` while the API reranked on the raw query, so the two disagreed on the one input the eval measured. `vector_index`/`embedder` are `| None`, valid **only in keyword mode** (which touches neither, and is what lets `oasis search --mode keyword` answer without a model load — measured 2.3 s vs ~8 s); an assert guards the other two modes.
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
> *(Absolute values are pre-migration-stack, 2026-07-14, and stay as measured — all eight cells came from one frozen parse set. The current raw hybrid+CE canonical is 0.5601 ndcg@10 on the pixi/OpenBLAS-CPU stack; the **Δ** column, which is the finding, is unaffected by a two-query intra-top-10 reorder.)*
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

> **These four rows are a 2026-07-14 measurement set on the pre-migration stack** (PyPI torch 2.12, MPS) and are **left exactly as measured**. All four were produced together, so they are only comparable to each other; restating one cell from a later run would silently corrupt the between-mode comparison the table exists for.
>
> **The current canonical, on the shipped stack** (pixi / conda-forge torch 2.13 / OpenBLAS / CPU, 2026-07-25) is the `hybrid + CE rerank` row restated as **ndcg@10 0.5601, mrr 0.5427, recall@10 0.6844, p@5 0.2275, p@10 0.1338** — two of 80 queries reordered within their top-10 and nothing else moved. The other three modes have not been re-measured on the new stack; when they are, the whole table gets regenerated as a set rather than patched.

Reproduce: `pixi run -e dev python eval/run_eval.py --mode {keyword,semantic,hybrid} [--no-rerank] --no-parse --no-history --out eval/results/ablations/NAME.json`.

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

## Tests — 939, all passing

Run with `pixi run -e dev pytest` (fast: 936 + 3 `slow` deselected) or `pixi run -e dev pytest -m ''` (all 939). Slow tests load real models on CPU. (952 until 2026-07-29, when the subtraction pass deleted the 13 tests covering the dead `fts_snippet`/`get_snippet` path along with the functions themselves.)

> **`FORCE_COLOR` / `COLUMNS` in the environment produce 8 false failures.** Every CLI test that asserts on output text (`'1 result' in result.output`, `'3 indexed'`, `'No result #5'`, …) breaks if Rich decides to emit ANSI into Click's `CliRunner` capture. `FORCE_COLOR` makes it colour a non-TTY, and `COLUMNS=0` makes it pick a degenerate width and wrap mid-assertion — the output then reads `'\x1b[?25l\x1b[32m⠋\x1b[0m \x1b[2mParsing query…'` and the substring is genuinely absent. Nothing is wrong with the code. Some terminals and agent harnesses export both, so a suite that is green in one shell is 8-red in another; measured 2026-07-28, where it briefly looked like a regression on `master`. Reproduce a clean run with `env -u FORCE_COLOR -u COLUMNS pixi run -e dev pytest`. (The durable fix, if this recurs, is to pin the terminal in the test fixture rather than to trust the ambient environment.)

New 2026-07-25 (device + migration): `test_device.py` (11) — `resolve_device` precedence, `(model_name, device)` cache-key composition asserted without ever constructing an MPS context, and three `slow` tests that inspect the *loaded* model's real device rather than the string passed in. `test_cpu_cross_encoder_returns_finite_scores` is the ex-`xfail(strict=True)` tripwire on the realistic batch shape that Accelerate NaN'd.

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
| `test_snippets.py` | 35 | `test_extractor_edges.py` | 21 | `test_db.py` | 16 |
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

## Phase 5.2 — HTTP API (the design record; **fully implemented**)

Full contract in `CLAUDE.md` § HTTP API; the code is `src/oasis/api/` and every endpoint below is built and tested. **The consumer is a native SwiftUI app that spawns the server as a child process** — not the HTMX web UI on the old README roadmap. This section is kept as the *design record* — the reasoning that produced each decision, which the implementation docstrings state but do not argue. The heading used to read "specced, not implemented" and the body claimed `fastapi`/`uvicorn` weren't dependencies and `src/oasis/api/` didn't exist; that was true on 2026-07-14 and false from 2026-07-15 on. Corrected 2026-07-29.

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
- **Swift app, step 9 and beyond. Every control in the app is now live.** Steps 1 (lifecycle seam), 2 (main window + search), 3 (**Index New Folder**), 4 (**Reindex Current Folders**), 5 (the **statistics panel**), 6 (**Reset Indexing**), 7 (the **⌘⌥O summon panel + menu-bar residency**) and 8 (**Settings**) are done — see Recently done (2026-07-25), the four (2026-07-26) entries and the (2026-07-27) ones. ~~Opening a result via `POST /api/open`~~ and ~~keyboard result navigation~~ **both landed 2026-07-27**. The deferred follow-on is a **context menu** (Reveal in Finder / Copy path). ~~Two threads left open by step 4: a **manage-indexed-folders** affordance~~ **landed with step 8** — Settings ▸ Folders, on `POST /api/index/remove-root`; the deleted-root wedge now has a recourse. Still open from step 4: **`force: true`** as an explicit "full rebuild" for an embedder-dimension change. Also still open from step 3: the **Full Disk Access first-run flow** — step 8 added a *deep link* and an explainer under Settings ▸ General, which is guidance, not onboarding: there is still no first-run prompt and no re-try-after-granting, and the spawned-server TCC question belongs to the distribution arc. Then the `reindex_recommended` prompt. ~~One open thread from step 1: the bundled-binary spawn path for release (`RELEASE TODO` in `ServerController.resolveServerBinary()`)~~ **closed 2026-07-28** — a Release `.app` embeds and spawns its own frozen server (see the `.app` wrapper entry); the FDA question it was meant to answer is still open, because this machine doesn't gate `~/Documents`. The sandbox is **not** an open thread — `ENABLE_APP_SANDBOX = NO` is permanent and correct for a directly-distributed app (see Recently done, 2026-07-25). The genuine permission work is **Full Disk Access**, which TCC requires of unsandboxed apps too and which the `permission_denied` counter already anticipates — a Tier-1 first-run flow, not a sandbox question.
- ~~**Migrate the CLI's search command to `query/search.py:run_search()`**~~ **Done 2026-07-28.** The CLI's rerank input moved from `semantic_query` to the raw query in the process, matching the API — verified by running the same query through both and getting identical rankings.
- **📦 Packaging and bundling — the next arc, and the ground is now clear.** Deliberately **not** started; recorded here so the state is known when it is.
  - **Unblocked, and what proved it.** The real `oasis serve` freezes (2026-07-27 spike): PyInstaller `--onedir`, the recipe and its one non-obvious flag (`--collect-submodules tiktoken_ext`) recorded in that entry, launching in 1.6 s to handshake / 8.3 s to ready and serving all three modes plus indexing. **1.1 G** is the measured bundle floor for the server, weights excluded.
  - **What the 2026-07-28 pass did for it, concretely.** The tiktoken encoding is lazy, so the frozen binary no longer risks dying before its handshake on a plugin scan, and no entry point does network work at import. `OasisAPI.swift` means the app has **one** place that knows how to reach the server, which is what the dev-path → bundled-binary switch has to edit. The `build` pixi environment (`default` + pyinstaller, eval tooling verifiably absent from the bundle) is in the manifest.
  - ~~**Still owed, in rough order:** the bundled-binary spawn path~~ **The `.app` wrapper landed 2026-07-28 — see the entry below.** The Release build is self-contained: the frozen server is embedded in `Contents/Resources/`, spawned from there, and a Finder-launched `.app` indexes and searches with no pixi environment anywhere. ~~Still owed after it: weights inside the bundle + `HF_HUB_OFFLINE`~~ **also landed 2026-07-28** — both models and the tiktoken encoding are embedded and the bundled spawn runs offline against them, verified on a simulated cold machine (caches moved aside, Wi-Fi off): **11.63 s to ready**, 1.3 GB total. Still owed: ad-hoc signing done deliberately (the wrapper build gets Xcode's "Sign to Run Locally" for free, which is *not* the same as a considered signing story) and hardened-runtime-meets-dylibs; trimming the duplicated `libtorch_cpu.dylib` (237 M × 2 — the single largest recoverable win, deferred to the signing step because symlinking a dylib interacts with `codesign`); the DMG; and the spawned-server Full Disk Access / TCC question, which the wrapper experiment **could not settle on this machine** — see the verdict below.
  - **One unrelated item still open from the spike:** re-run the search-during-index regression on **lancedb 0.34.0**, or pin to 0.30.2. The concurrency result that makes `VectorIndex` a shared handle was measured on 0.30.2.
- **The real `~/.oasis` index has no embeddings** (built June 3, pre-vector). Re-index to populate `index.lance`. **Detection is now handled** — `/api/health` reports `documents: 877, schema_version: 0, semantic_ready: false, reindex_recommended: true` against it (verified live 2026-07-16), so the app gets a single server-derived boolean to act on instead of doing version math; what's left is the app-side UX for that prompt. **Repair no longer needs `--force` (2026-07-17): the no-vector backfill makes a plain `oasis index` embed unchanged-but-unvectored docs**, so the plain reindex the app will offer actually flips `semantic_ready` true (verified live against a copy of the real index).
- **🔴 Make the NL filters soft, and stop distilling the embedding query.** The measured headline finding (top of Evaluation): parsing costs −0.108 ndcg@10 / −0.135 recall@10 on hybrid+CE. Two fixes, both small:
  1. **Soft filters.** `build_vec_where` / `build_kw_filters` turn LLM guesses into hard `WHERE` exclusions, so one wrong guess zeroes a query's recall. Convert to a post-hoc score boost so a wrong guess costs a little and a right one still helps. This is an asymmetric-payoff problem, not a prompt problem — no amount of prompt-tuning fixes it.
  2. **Embed the original query, not `semantic_query`.** Distillation produced `'civic duty'` for the JFK inaugural and `'ocean pollution'` for coral bleaching. Keep `semantic_query` for the FTS5 arm (where it genuinely helps: keyword +0.040) and give the embedder the user's actual words.
  - Re-run the matrix after each change; the harness now supports it directly.
- **Consider a bigger parse model.** 12–19 of 83 parses fail outright with `llama3.2:3b`, non-deterministically, and the successful ones hallucinate filters. Worth measuring `llama3.1:8b` before concluding the feature is unsalvageable.
- **Fix `ensure_ollama()`'s health check** (`llm/manager.py`). It verifies "server answers HTTP" + "model appears in `ollama list`" and calls that available — but a provider that passes both can still 500 on every inference (exactly what a broken `llama-server` does). The CLI then silently falls back to raw on every search and **the user is never told the feature is dead**; the eval reported `llm_used: true` for 83/83 failures. The check should do one tiny real completion, cache the result, and treat a failure as unavailable. This is the difference between "Ollama isn't installed" (fine, expected) and "Ollama is lying to you" (currently indistinguishable).
- **Regenerate the comparison table once the parser runs.** The current keyword row is invalid as a baseline (see the warning under Evaluation).
- ~~**Pre-existing lint debt: 66 ruff errors**~~ **Cleared 2026-07-17** — `ruff check .` is clean whole-tree. All mechanical: `F401`/`I001`/`UP017`/`UP037`/`SIM300`/`F841` auto-fixed; `B904` (3 CLI `raise typer.Exit(1) from None`), `B905` (2 `zip(..., strict=False)`, behavior-preserving), and `UP042` (`SearchMode` → `StrEnum` in `cli/app.py`, matching `query/search.py`) fixed by hand; `B008` (Typer's `Option(...)` default pattern — a false positive) and `N806` (capitalized mock-class aliases in tests) silenced via `per-file-ignores`. No behavior change (930 tests green). **Still deferred:** `ruff format` would reformat 29 files — a separate, larger-churn pass, deliberately not bundled here.
- Nested `.gitignore` support in the walker (per-directory, not just root).
- UTF-8-only text extractor can't read latin-1 files (`edge-latin1-menu.txt` in the corpus).
- OCR fallback for scanned PDFs.

### ✅ CPU inference block — RESOLVED and shipped (2026-07-25)

> **Closed.** The migration landed, CPU is the default, and the matrix was re-measured on the shipped stack. Kept in full below because the diagnosis — Accelerate's SGEMV as a *silent* NaN source, and everything ruled out on the way — is the reason the constraint in `CLAUDE.md` ("never move torch to the PyPI half") exists. The remaining bundle items moved to the freeze work; they were never part of this block.


**Resolved: the blocker is Apple's Accelerate BLAS, not any torch version or Oasis code. conda-forge's OpenBLAS torch fixes it, and the fix survives PyInstaller freezing — proven finite both unfrozen and inside a frozen binary, with the bundled dylib's provenance verified.** The device plumbing stays uncommitted pending the four items in "Still owed" below; this entry records the diagnosis and the spike that resolves the shippability fork.

#### The trail

1. The Swift app spawns `oasis serve` as a child of a GUI process. The cross-encoder's first **MPS** inference aborts in Metal validation (`validateComputeFunctionArguments`, SIGABRT); the server dies and the app sees connection-refused. MPS works when spawned from a shell — even under `env -i` — so the trigger is the Metal device context an MPS subprocess inherits from a GUI parent, not a missing env var. Unfixable across arbitrary downloader Macs.
2. Decision B: default inference to **CPU** (portable, deterministic, the interim before Core ML/MLX). Engine plumbing built — `device.py` `resolve_device()`, `device` params on both wrappers, `(model_name, device)` cache keys — and green.
3. The CPU matrix re-run crashed at q042. The cross-encoder's **CPU** path is broken shape-dependently: some shapes SIGBUS in Accelerate's `cblas_sgemv` (`EXC_ARM_DA_ALIGN`), others silently return **all-NaN** logits (6/6 on a realistic-snippet batch; short inputs pass). Embedder CPU fine; plain torch CPU matmul fine; same inputs on MPS correct. So CPU traded a loud crash for a silent one — strictly worse, and the reason nothing shipped.

   | Input shape | CPU result (Accelerate) |
   |---|---|
   | Short pairs (`"whale"` / `"moby dick the whale"`), batch 1–3 | correct logits (9.73, 8.26) |
   | Realistic snippet lengths, batch ≥ 2 | **all-NaN logits** — 6/6 runs |
   | Some single-pair shapes (short snippet, batch 1) | **SIGBUS** — `EXC_ARM_DA_ALIGN` in `cblas_sgemv`, via torch CPU `addmm` → `linear` |

4. Nine builds tested against the realistic reproducer — torch 2.9.0 / 2.9.1 / 2.10.0 / 2.11.0 / 2.12.0 (clean reinstall) / 2.12.1 / 2.13.0 / 2.14.0.dev nightly, plus 2.13.0 on **Python 3.13** — i.e. every cp314 macOS-arm64 wheel on PyPI, plus nightly, plus the interpreter-downgrade escape route. **All NaN, all `BLAS_INFO=accelerate`.** Not a version regression: every stock arm64 wheel links Accelerate, and Accelerate on this macOS (Darwin 25.3) is the broken backend. The version lever leads nowhere.
5. conda-forge torch links **OpenBLAS** (`BLAS_INFO=open`), not Accelerate. The reproducer returns finite CPU scores unfrozen.
6. **Spike (below): OpenBLAS survives PyInstaller freezing.** Frozen binary reports `BLAS_INFO=open`, runs CPU, returns byte-identical finite scores; under `env -i` with no conda env reachable, dyld loads the **bundled** `dist/reproduce/_internal/libopenblas.0.dylib` — proven to be the frozen copy, not one leaking from the environment.

#### The freeze spike

Env `oasis-blas-test` (conda-forge torch 2.13.0, Python 3.14.6, `BLAS_INFO=open`), minimal reproducer only — not the real server. Same batch unfrozen and frozen: `[-10.2319, -11.1282, -11.1259]`, `all finite: True`, relevant snippet ranked first, both times.

- **Working recipe:** `pyinstaller --onedir --collect-all torch --collect-all sentence_transformers --collect-all transformers --collect-data tokenizers`. **No `--add-binary` needed** — the env's OpenBLAS lives in `$CONDA_PREFIX/lib`, reached via torch's `_C…so` `LC_RPATH @loader_path/../../../`; PyInstaller's macholib follows the rpath, copies `libopenblas` + `lib{blas,cblas,lapack}` into `_internal/`, and rewrites the rpath to `@loader_path/..`. The chain-rpath relocated intact. This was the predicted failure point and it resolved itself.
- **Gotcha that will bite the real server freeze:** frozen torch respawn-loops (PIDs churning ~3s, orphaning to PPID 1) because torch's multiprocessing resource-tracker re-execs the binary, and PyInstaller's hook only diverts it if the app calls `freeze_support()`. Fix: `multiprocessing.freeze_support()` at `__main__` **before** importing torch (so diverted helpers don't pay a full torch import). This is a property of freezing torch, not of the reproducer — carry it into the `oasis serve` freeze.
- **Size:** 827M (`dist/reproduce/`). ~237M is a duplicated `libtorch_cpu.dylib`; scipy+sklearn (51M) ride in via sentence-transformers; OpenBLAS itself is 13M. A real bundle trims well under 827M with excludes — the BLAS fix is near-free, the weight is torch.
- **Caveat:** Accelerate's `libBLAS`/`libLAPACK` remain mapped into the process (delay-loaded via system frameworks). torch does not route through them, but the bundle cannot be called "Accelerate-free."

#### Resolved vs still owed

**Resolved — the scary unknown:** a working CPU BLAS can be gotten into a PyInstaller bundle. OpenBLAS-via-conda is technically viable end-to-end through the freeze boundary.

**Still owed before the device-CPU flip commits:**

- ~~**Pixi-verification spike — gates every item below it.**~~ **Both caveats cleared 2026-07-25** — one lock over both halves, and the frozen pixi env keeps OpenBLAS with the recipe unchanged. See the spike entry below.
- ~~**Canonical matrix re-run on OpenBLAS CPU**~~ **Done 2026-07-25 — restated to 0.5601, BLAS proven neutral.** The MPS control and the CPU run are identical on all five metrics *and* on all 80 per-query score sets, so the OpenBLAS/device effect is exactly zero; the −0.0001 ndcg@10 / +0.0025 p@5 delta against the old canonical is the version stack, and it is two queries reordering inside their top-10. See Recently done.
- ~~**Real `oasis serve` freeze**~~ **GREEN 2026-07-27** — the whole server freezes, launches, handshakes, and serves real hybrid/semantic/keyword search *and* indexes through the frozen binary. LanceDB's Rust `.so` was not the wall; `tiktoken_ext` was. See the spike entry below.
- **Offline + weights bundling** — spike used the online HF cache; the ship must run `HF_HUB_OFFLINE` with weights inside the bundle.
- **`.app` wrapper + ad-hoc signing + hardened-runtime-meets-dylibs** — deferred distribution layer.

~~**Open decision — how OpenBLAS torch enters the project**~~ **Decided 2026-07-25: one pixi-managed environment** — see the next entry for the mechanism and the three findings that settled it. Device plumbing (`device.py`, wrapper params, cache keys, resolver + slow tests) remains green in the working tree and **uncommitted** until the migration lands and the matrix reproduces.

#### Carried over — still live

- **The self-announcing gate will fire on the swap.** `tests/test_device.py::test_cpu_cross_encoder_returns_finite_scores` is `slow` + `xfail(strict=True)` on the realistic shape (a toy batch would xpass and defeat the marker). Under Accelerate it xfails. **Under OpenBLAS it xpasses, and `strict=True` turns that into a hard failure** — by design: whoever lands the OpenBLAS torch is forced to remove the marker and flip the default in the same change. Caveat recorded in the test: SIGBUS shapes kill the process outright and pytest cannot catch those, so the catchable NaN shape is the one encoded.
- **Danger of the silent mode, for whoever re-runs the matrix.** NaN scores don't raise, and `sorted()` on NaN keys leaves order essentially untouched, so a NaN reranker degrades to "no reranking" *invisibly* — it would have quietly deleted the measured +14.7% cross-encoder lift with no error anywhere. One CPU eval run reported ndcg@10 0.4246 having reranked 74 times / 1079 pairs through a NaN cross-encoder; those numbers measure a broken reranker and were **not** published. Two invalid rows reached `eval/results/history.jsonl` and were scrubbed (`git checkout`); all diagnostic runs use `--no-history --out`.
- **Ruled out earlier, still ruled out:** install corruption (clean venv reinstall reproduces), and every thread-count knob (`torch.set_num_threads(1)`, `OMP_NUM_THREADS=1`, `VECLIB_MAXIMUM_THREADS=1`). BLAS backend is a build-time choice — there is no runtime toggle off Accelerate, which is exactly why the fix had to be a different *build*.
- **Interim dogfooding:** `OASIS_DEVICE=mps` still works for non-GUI launches (conda-forge's `cpu_generic` torch ships MPS). **On the migrated stack it is metric-identical to CPU** — same five aggregates, same 80 per-query scores — so it is now a diagnostic lever rather than a fallback. The old-stack MPS figures were ndcg@10 0.5602 / mrr 0.5427 / recall@10 0.6844 / p@5 0.2250 / p@10 0.1338; the migrated stack reads 0.5601 / 0.5427 / 0.6844 / 0.2275 / 0.1338 on *both* devices.
- **Still not an option: shipping MPS with a GUI-spawn workaround.** A broken CPU BLAS is a one-time build swap you fix; an MPS abort in a GUI-spawned subprocess is a Metal-context problem that cannot be fixed reliably across every downloader's Mac and macOS version. Unchanged by this result — and the whole episode remains an argument for the Tier-3 Core ML / MLX swap.

### ✅ Decision — dependency migration to a single pixi-managed environment (2026-07-25, LANDED)

**Decided: migrate to one pixi-managed environment. torch comes from conda-forge (OpenBLAS, `BLAS_INFO=open`); the PyPI-only remainder is resolved by uv *inside* pixi; one `pixi.lock` covers both halves.** This resolves the CPU-inference block above: OpenBLAS is the only packaged non-Accelerate torch, and a single env is what makes "tested equals shipped" hold across it. Option 2 (conda-only-for-the-release-build) and bare conda are both rejected, on mechanism, below. Device plumbing stays green and uncommitted until the migration lands and the matrix reproduces.

#### Three findings that decided it (each verified, not assumed)

1. **torch was never a uv-controlled dependency.** It's transitive via sentence-transformers; `pyproject.toml` never names it, and `uv.lock` pinning 2.12.0 is an accident of resolution, not intent. So "migration loses `uv.lock` as the source of truth for torch" is backwards — torch was *uncontrolled the whole time*, and the block exists precisely because an unpinned transitive dep silently picked an Accelerate wheel. Migration gains control that never existed rather than surrendering it.
2. **Option 2's skew silently disarms the xfail gate.** `test_cpu_cross_encoder_returns_finite_scores` is `xfail(strict=True)` so it fails loudly the moment a working BLAS arrives, forcing the CPU flip. That mechanism assumes tests run where the fix lives. Under option 2, tests run in the uv/Accelerate dev env where CPU is still broken, so the gate keeps xfailing forever in the one environment that runs it — the self-announcing tripwire, built for exactly this class of problem, never announces. Disqualifying on mechanism, not preference.
3. **Option 2's "keep uv for dev/test" benefit is nearly empty, and its last advantage evaporates.** `addopts = -m 'not slow'` means a plain `pytest` never imports torch; there are only 4 slow tests; there is no CI to port (no `.github/workflows`). The migration cost is an env spec and a README line. And conda-forge torch ships **MPS built and available** (`pytorch 2.13.0 cpu_generic_py314…`, `mps built: True`, `mps available: True`, `BLAS_INFO=open` — despite the `cpu_generic` name), so one conda env is a strict superset, running both the CPU-OpenBLAS ship path *and* the `OASIS_DEVICE=mps` canonical baseline. Option 2's only exclusive selling point (MPS-for-dev) is not exclusive.

#### Why pixi, not bare conda

Two deps can't come from conda-forge, forcing conda-core + PyPI-remainder either way:

- **lancedb** — conda-forge has 0.30.0; the project needs **≥0.30.2**, and that's load-bearing: the VectorIndex-not-thread-local concurrency result was *measured on 0.30.2*, so 0.30.0 would silently invalidate a foundational finding.
- **ranx** — absent from conda-forge (eval group).

Bare conda handles this only via an `environment.yml` `pip:` section, which is unlocked — re-creating the exact "lockfile isn't the source of truth" problem, now genuinely, because that pip section really is unpinned. **pixi closes it: one `pixi.lock` over both halves, with uv doing the PyPI resolution internally.** The reproducibility story ends up *better* than the start — torch pinned intentionally for the first time, both dependency universes under one lock. (Bare-conda trap noted for the record: conda-forge pytorch ships real `torch-2.13.0.dist-info`, so `uv pip install -e .` into the prefix won't clobber it — but `uv sync` in strict project mode *would* reinstall PyPI torch. pixi avoids the footgun.)

#### ~~Two pixi caveats — explicit unknowns~~ — **both cleared 2026-07-25**

Verified early, before the environment became load-bearing — same discipline as the OpenBLAS freeze spike. Full result in the spike entry below.

1. ~~pixi resolves conda-core + PyPI-remainder into one lock with **lancedb ≥0.30.2** and **ranx** present.~~ **Yes** — one `pixi.lock`, 426 conda + 36 PyPI entries, lancedb 0.34.0 and ranx 0.3.21 through the PyPI half.
2. ~~the resulting pixi env still **PyInstaller-freezes with OpenBLAS intact**~~ **Yes** — recipe unchanged, no `--add-binary`, frozen binary reports `BLAS_INFO=open` with the bundled dylib proven under `env -i`.

#### Sequencing for the owed matrix re-run — control before comparison

The swap changes more than BLAS: **torch 2.12→2.13, transformers→5.14.1, sentence-transformers→5.6.1, numpy→2.5.1** — four confounds. A straight CPU-OpenBLAS run compared against 0.5602 would attribute any delta to BLAS when it could be a version bump. So, in the new env, **run the MPS matrix first as a control**: if it reproduces ndcg@10 0.5602 / mrr 0.5427 / recall@10 0.6844 exactly, the version stack is neutral and any subsequent CPU delta is BLAS-attributable cleanly; if the MPS control already differs, a version-bump effect is caught before it's misread as OpenBLAS. Same logic as the served-vs-direct control: hold everything constant but the one variable under test.

#### Immediate next step (gates everything downstream)

**The pixi-verification spike** — the two caveats above. It gates the CPU flip, the real `oasis serve` freeze (fastapi/uvicorn + LanceDB Rust libs, plus `freeze_support()`), and therefore the app. Nothing about the migration commits until pixi is proven to resolve both halves under one lock *and* the env freezes clean.

**Status:** device plumbing (`device.py`, wrapper params, `(model_name, device)` cache keys, resolver + 4 slow tests) remains green in the working tree and **uncommitted**, pending the migration landing and the CPU matrix reproducing (or restating, rankings-identical) the canonical numbers.

### ✅ Pixi-verification spike — both caveats cleared, migration is viable (2026-07-25)

**Both gates passed. pixi resolves conda-core + PyPI-remainder into one lock, and the resulting env freezes with OpenBLAS intact using the bare-conda recipe unchanged.** The migration direction is confirmed; nothing was committed and `uv.lock` is untouched. Spike artifacts live in the scratchpad (`pixi-spike/`), disposable by design.

**One machine-level change:** pixi 0.73.0 installed via the official installer, which appended a PATH line to `~/.zshrc`.

#### Part 1 — one lock over both halves ✅

| | resolved | half |
|---|---|---|
| python | 3.14.6 | conda-forge |
| pytorch | 2.13.0 `cpu_generic_py314` — **`BLAS_INFO=open`**, MPS built **and** available | conda-forge |
| libopenblas | 0.3.33 | conda-forge |
| numpy | **2.4.6** (see the conflict below) | conda-forge |
| transformers / sentence-transformers | 5.14.1 / 5.6.1 | conda-forge |
| **lancedb** | **0.34.0** | **PyPI** |
| **ranx** | **0.3.21** | **PyPI** |

One `pixi.lock`, **426 conda entries + 36 PyPI entries**. `oasis` installed editable from the real tree (`import oasis` resolves to `/Users/mohamedelrefaei/oasis/src/oasis`), the whole engine imports (13 modules incl. `api.app`, `index.pipeline`, `query.reranker`), and the fast suite is green in the pixi env: **938 passed, 3 deselected, 16.7s**.

**The resolve conflict worth remembering — and pixi caught it rather than papering over it.** First `pixi install` failed hard: `ranx` → `numba>=0.54.1`, and every *released* numba caps `numpy<2.5`, while the conda solve had pinned conda-forge's default `numpy==2.5.1`. Pinning `numpy = ">=2.2,<2.5"` in the conda half resolves it (→ 2.4.6). This is the cross-half constraint propagation working: the conda pin flowed into the uv resolution and produced an error, where bare conda's `pip:` section would have let pip install a second numpy over the conda one and desync it from the torch ABI. **The failure mode pixi was chosen to prevent is the one it demonstrated on the first run.**

**Caveat that came out of it — lancedb resolved to 0.34.0, not 0.30.2.** The constraint was `>=0.30.2` as specified, and PyPI's latest is four minor versions ahead of the version the VectorIndex-not-thread-local concurrency result was *measured* on. That result is foundational (§ Concurrency › LanceDB), so **the search-during-index regression test should be re-run on 0.34.0** before the migration is trusted — or lancedb pinned to `==0.30.2` deliberately. Not a spike failure; a new owed item.

**Bonus, and it is the headline: the self-announcing gate fired.** `pytest -m slow tests/test_device.py` in the pixi env → `[XPASS(strict)] test_cpu_cross_encoder_returns_finite_scores` — a hard failure, by design. The cross-encoder returned **finite CPU logits in the real project code**, not just the reproducer, which is the first proof that OpenBLAS fixes Oasis itself rather than a minimal script. Whoever lands the migration must remove the marker and flip `DEFAULT_DEVICE` in the same change, exactly as the marker's reason text instructs. (Other 2 slow device tests passed.)

#### Part 2 — freeze on the pixi substrate ✅

**The recipe transferred verbatim. No `--add-binary` needed, no pixi-specific adjustment.** pixi's prefix (`.pixi/envs/default/lib`) sits at the same depth relative to `site-packages/torch/` as a conda prefix does, so torch's `LC_RPATH @loader_path/../../../` resolves identically and macholib relocated the chain unchanged: `libopenblas.0.dylib` (13 MB) plus the `lib{blas,cblas,lapack}` symlinks landed in `_internal/`, rpath rewritten to `@loader_path/..`.

| check | result |
|---|---|
| Frozen `BLAS_INFO` | **`open`** |
| Device / scores | `cpu`, `[-11.4411, -11.462, -9.0544]`, **all finite**, whale snippet ranked first |
| Unfrozen vs frozen | **byte-identical scores** |
| Provenance under `env -i` | loads **`dist/reproduce/_internal/libopenblas.0.dylib`** — the bundled copy, pixi prefix unreachable |
| `freeze_support()` | no respawn loop, steady single PID, **zero orphans**, exit 0 |

`multiprocessing.freeze_support()` at `__main__` before importing torch was applied as recorded; the respawn loop never appeared, so the fix carries to pixi unchanged.

**Size: 1.2 G vs the conda spike's 827 M — but the two aren't comparable, and the delta is not pixi's.** The conda spike env held only torch + sentence-transformers + transformers + pyinstaller; this one is the *full project* env. The overage is almost entirely the project's own PyPI half: **llvmlite 123 M** (ranx → numba → llvmlite), **pyarrow 120 M** (lancedb), `libicudata` 32 M, pandas 18 M, matplotlib 13 M. **llvmlite + matplotlib (~136 M) are eval-only and must never enter the shipped bundle** — which is the concrete argument for splitting the promoted manifest into pixi **features/environments** (`default` / `eval` / `build`) rather than one flat env; PyInstaller should freeze the `default` feature only. The duplicated `libtorch_cpu.dylib` (237 M × 2 = 474 M) reproduces exactly as in the conda spike — same known waste, unrelated to pixi.

#### What this does and doesn't settle

**Settled:** pixi is viable end-to-end — one lock over both dependency universes, the project runs and tests green in it, and the freeze boundary preserves OpenBLAS with no recipe change.

**Still open, unchanged by this spike:** the real `oasis serve` freeze (fastapi/uvicorn + **LanceDB Rust libs** — and note pyarrow's 120 M now has to come through it), the MPS-control-then-CPU matrix re-run, offline + weights bundling, and the `.app`/signing layer. **New:** re-run the search-during-index regression on lancedb 0.34.0, or pin to 0.30.2.

### ✅ Real `oasis serve` freeze spike — GREEN (2026-07-27)

**The full server freezes and serves.** A PyInstaller `--onedir` bundle of the real `oasis serve` — fastapi, uvicorn, LanceDB's Rust extension, torch/OpenBLAS, tiktoken, tokenizers, pydantic-core — launches, prints its handshake, reaches `ready`, and returns finite ranked results on all three search modes. It also **indexes**, so the LanceDB write path survived too. This was the gate for the entire distribution arc; the `.app` now has something to embed. Nothing committed except the `build` environment in `pixi.toml`; `spike/` and `dist/` are disposable.

#### The `build` environment (added to the manifest, worth keeping)

`build = { features = ["build"], solve-group = "main" }` — `default` + pyinstaller, nothing else. Verified by import inside it: **ranx, numba, llvmlite, matplotlib, pytest all absent**, and re-verified *after* the freeze by listing `_internal/` — no `llvmlite`, `numba`, `ranx`, `matplotlib`, `pytest`, `mypy`, `ruff`, or `PyInstaller` in the bundle. The feature split predicted in the pixi spike does what it was designed to do: the ~136 MB of eval tooling that contaminated the flat spike env is simply not reachable from this one.

#### The working recipe (exact)

```
pixi run -e build pyinstaller --onedir --noconfirm --clean \
  --distpath dist --workpath spike/pybuild --specpath spike \
  --collect-all torch \
  --collect-all sentence_transformers \
  --collect-all transformers \
  --collect-data tokenizers \
  --collect-all lancedb \
  --collect-submodules uvicorn \
  --collect-submodules oasis \
  --collect-submodules tiktoken_ext \
  --hidden-import tiktoken_ext.openai_public \
  spike/serve_entry.py
```

**No `--add-binary`. Not for OpenBLAS, and — the surprise — not for LanceDB either.**

#### The predicted wall wasn't the wall

**LanceDB collected cleanly on the first build.** The whole Rust surface is a single `_lancedb.abi3.so` (114 MB) living *inside* the package directory, so `--collect-all lancedb` sweeps it up as an ordinary package binary — there is no out-of-tree `.dylib` for macholib to miss. The Rust-extension rough spot that motivated the spike does not apply to how lancedb ships. `pydantic_core`, `tiktoken`, and `tokenizers` were likewise hook-covered and present without intervention. **uvicorn/fastapi/starlette are pure Python** and rode in the PYZ; `--collect-submodules uvicorn` is retained because uvicorn's protocol/loop/lifespan imports are dynamic and nothing in the run proves the graph would have found them without it.

**The one real failure was `tiktoken_ext`**, and it is a namespace-package problem, not a native one. `chunker.py` calls `tiktoken.get_encoding("cl100k_base")` **at import time**, and tiktoken resolves encodings by scanning `tiktoken_ext` — a namespace package whose members PyInstaller cannot see by following imports, because nothing imports them. First launch died before serving:

```
ValueError: Unknown encoding cl100k_base.
Plugins found: []
```

Fixed by `--collect-submodules tiktoken_ext --hidden-import tiktoken_ext.openai_public`. **This is the headline of the recipe** — the flag whose absence is fatal and whose need is invisible until runtime. Note it fires at *import* of `oasis.cli.app`, so it kills the process before the handshake ever prints: a frozen binary that dies silently with no handshake should be suspected here first.

#### `freeze_support()` behaved exactly as the OpenBLAS spike predicted

`multiprocessing.freeze_support()` runs in `spike/serve_entry.py` at `__main__` **before** importing `oasis.cli.app`. Result: **no respawn loop.** The parent PID held steady across the whole session, with exactly one child — `serve_entry -B -S -I -c from multiprocessing.resource_tracker import main;main(20)`, 46 MB RSS against the parent's 1.19 GB. That is the *diverted* helper, which is the visible proof the fix engaged: without `freeze_support()` that child would be a full re-exec of the app. Watched for 20 s across four samples — both PIDs stable, no churn.

Run **without `--managed`**, deliberately: the launching shell moves on, the process re-parents to PPID 1, and the watchdog would read that as a dead parent and reap a perfectly healthy server. (Confirmed in the run: `ps` shows PPID 1 throughout.)

#### What the searches actually proved

Against a copy of the eval index (300 docs), Bearer token from the handshake, `raw=true`:

| mode | n | scores | what it exercises |
|---|---|---|---|
| `hybrid` | 10 | `4.6400 → 3.6326 → …`, **finite, descending** | LanceDB read **+** torch/OpenBLAS CE rerank **+** FTS5, all three at once |
| `semantic` | 5–10 | `0.4828 → 0.4189 → …` | LanceDB vector read alone |
| `keyword` | 6–10 | `10.7372 → 10.3432 → …`, snippets on every hit | **SQLite FTS5 is compiled into the frozen interpreter** |

Every result carried a populated snippet with `match: true/false` spans — FTS5 `snippet()` working, which is the implicit test that mattered. Negative CE logits appear where they should (`-5.8877`, `-10.15` for off-topic hits): the score range is real cross-encoder output, not a degenerate constant, and **nothing is NaN** — the Accelerate failure mode is absent inside the bundle, consistent with `libopenblas.0.dylib` (13 MB) sitting in `_internal/` and the chain-rpath relocation from the earlier spike.

#### Bonus — the write path works too (not required for the verdict; it de-risks indexing from the bundle)

`POST /api/index` on a two-file folder through the frozen server: job accepted (`202`, `status: running`), documents **300 → 302**, and both new files came back **ranked first** for their distinctive token within seconds. Critically, one of them also surfaces in a **`mode=semantic`** search — a hit there can only come from the LanceDB vector table, so `merge_insert` genuinely wrote vectors, not just SQLite rows. `/api/status` afterwards: `vectors_built: true`, `semantic_ready: true`, `schema_version: 2`.

#### Cold-start and size — the numbers feeding the distribution story

| measure | value |
|---|---|
| launch → handshake | **1.6 s** |
| launch → `/api/health` `ready` | **8.3 s** (model load inside the bundle; ~21 s on a cold page cache immediately post-build) |
| first search / warm search | 0.7 s / 0.6 s |
| **`du -sh dist/serve_entry`** | **1.1 G** |

**1.1 G is the real bundle-size floor for the server**, and it is *not* the pixi spike's 1.2 G minus eval tooling — the eval half (llvmlite 123 M + matplotlib) is gone, but the server dragged pyarrow and the lancedb `.so` in behind it. Largest items: `torch/` 320 M, `libtorch_cpu.dylib` **237 M duplicated** (`_internal/` *and* `_internal/torch/lib/`, 474 M total — the same known PyInstaller waste as both prior spikes, and the single largest recoverable win), `pyarrow` 119 M, `lancedb` 115 M (114 M of it the Rust `.so`), `transformers` 50 M, `scipy` 35 M, `libicudata` 32 M, `sklearn` 16 M. Weights are **not** in this number — the run used the online HF cache, so bundling `all-MiniLM-L6-v2` plus the cross-encoder adds to it.

#### What this settles, and what it doesn't

**Settled:** the real server freezes. Collection is complete for every native dependency the search path touches, the recipe is known and short, and the frozen artifact serves and indexes against a real index. The distribution tunnel is unblocked.

**Explicitly out of scope here and still owed:** the `.app` wrapper, ad-hoc signing and hardened-runtime-meets-dylibs, `HF_HUB_OFFLINE` + weights inside the bundle, and trimming the duplicated `libtorch_cpu.dylib`. Also unchanged: re-run the search-during-index regression on lancedb **0.34.0** (the build env resolved to it, same caveat as the pixi spike).

### ✅ Weights + offline — the bundle works on a cold, disconnected machine (2026-07-28)

**The `.app` no longer depends on this machine's caches or on the network.** With `~/.cache/huggingface`, the tiktoken cache and `~/.cache/torch` all moved aside and **Wi-Fi powered off**, a Finder-launched `Oasis.app` reached ready in **11.63 s**, indexed a folder, and served a search — every model byte read from inside the bundle. That is the number in the North Star table now; every prior startup figure was warm-cache-online and doesn't describe a stranger's first launch.

#### Three artifacts, and the third is the one that hides

| artifact | source | lands in |
|---|---|---|
| embedder `all-MiniLM-L6-v2` | `~/.cache/huggingface/hub` | `Resources/models/hub/models--sentence-transformers--all-MiniLM-L6-v2` |
| reranker `ms-marco-MiniLM-L-6-v2` | same | `Resources/models/hub/models--cross-encoder--ms-marco-MiniLM-L-6-v2` |
| **tiktoken `cl100k_base`** | `$TMPDIR/data-gym-cache` | `Resources/tiktoken/9b5ad71b…` |

**tiktoken is not covered by `HF_HUB_OFFLINE`** — it is fetched from Microsoft's blob store, not HuggingFace — and the chunker only reaches for it while *indexing*. Miss it and the server starts, warms, reaches ready, and serves searches perfectly, then dies on the first index. **Measured, and this is the whole argument for the network-off test:** in a control run with the HF variables set correctly and `TIKTOKEN_CACHE_DIR` absent, tiktoken **silently downloaded** the encoding and the test passed anyway. A networked test cannot see this bug. (Same failure family as the `tiktoken_ext` freeze death — the artifact that isn't a "model" is the one that gets forgotten twice.)

Its cache filename is the **SHA-1 of the download URL**, so `embed_models.sh` derives it with `shasum` rather than hardcoding an opaque hex constant. Note the source is `$TMPDIR/data-gym-cache` — a per-user `/var/folders/…` path on macOS, *not* `~/.cache`, which is where you would look for it and not find it.

#### Bundled as data, not frozen in

A second build phase (`Scripts/embed_models.sh`, mirroring `embed_server.sh`: Release-only, `rsync -a --delete`, loud preflight failure naming the two commands that populate the caches). **Not `--add-data` into the PyInstaller freeze** — weights can then be updated without re-freezing, and `spike/build.sh` stays untouched.

The HF `hub/` layout is preserved verbatim so load-by-name resolves against it. Its snapshot→blob symlinks are **relative** (`../../blobs/<sha>`), so the tree survives relocation into the bundle — verified after the copy *and* after codesign, and the bundled trees `diff -rq` clean against the source cache.

#### The env vars — measured, not assumed

sentence-transformers **5.6.1** / transformers **5.14.1** / huggingface_hub **1.24.0**. Probed by loading both models with `HOME` pointed at an empty directory, so the real cache was unreachable:

| case | result |
|---|---|
| **no cache vars at all** (the control) | **FAIL** — `couldn't connect to huggingface.co … couldn't find them in the cached files`. This is what makes the rest meaningful: the check can fail |
| `HF_HOME=<res>/models` alone | works — `HF_HUB_CACHE` derives as `$HF_HOME/hub` |
| `HF_HUB_CACHE=<res>/models/hub` alone | works |

Neither `TRANSFORMERS_CACHE` nor `SENTENCE_TRANSFORMERS_HOME` was needed on these versions. What ships:

```
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
HF_HUB_CACHE      = <Resources>/models/hub    # read-only half → the bundle
HF_HOME           = ~/.oasis/hf               # writable half  → NOT the bundle
TIKTOKEN_CACHE_DIR= <Resources>/tiktoken
```

**`HF_HOME` and `HF_HUB_CACHE` are split on purpose.** Either alone resolves the models, but `HF_HOME` is also where the hub writes tokens, locks and its xet store — and **a write inside a signed bundle breaks its seal**, with signing as the very next step. So the read-only half points into the `.app` and the writable half sits next to the index. Verified: after a full cold run, `~/.oasis/hf` exists and is **empty** (nothing needed writing) and the bundle's model tree is byte-identical to before.

Set **only on the bundled spawn**. The dev path stays bare — it wants the machine's cache and the network — which is why `BinaryResolution` now carries a `ServerSource` instead of just a URL.

#### The cold-machine run

Scripted end-to-end with an EXIT trap plus an independent watchdog, so a failure anywhere could not leave the machine offline or without its caches.

| step | result |
|---|---|
| caches moved aside | `~/.cache/huggingface`, `$TMPDIR/data-gym-cache`, `~/.cache/torch`, `~/.oasis/hf` — all confirmed **gone** |
| network | Wi-Fi **Off**, no IPv4; `curl` to huggingface.co **and** openaipublic.blob.core.windows.net both return `000` |
| launch | Finder double-click, `spawned [bundled] …/Oasis.app/Contents/Resources/serve_entry/serve_entry` |
| **launch → ready** | **11.63 s** (handshake 1.73 s, warming 4.14 s); **6.85 s** on relaunch |
| weights provenance | `lsof` on the server caught both 90 MB `.safetensors` blobs open **from `Oasis.app/Contents/Resources/models/hub/…`** — direct proof, not inference |
| index (the tiktoken path) | `indexed=2 chunks=2 failed=0` — the chunker resolved `cl100k_base` from the bundle |
| search | 8 results, 647 ms, both probe files ranked **1st and 2nd** |
| leak check | none of the three caches recreated; **0** files written into the bundle |
| teardown | no orphaned `serve_entry` |

#### Size

| | |
|---|---|
| server (`serve_entry/`) | 1.1 G |
| models | 175 M |
| tiktoken | 1.6 M |
| **total `.app`** | **1.3 G** on disk (1.36 GB apparent, 7 799 files) |

**+177 MB for the weights.** The compressed download figure belongs to the DMG step; 1.3 G is the honest on-disk number, and the duplicated `libtorch_cpu.dylib` (237 M × 2) is still the single largest recoverable win.

#### Still owed after this

Ad-hoc signing done deliberately, the `libtorch_cpu` dedup, the DMG — and the FDA question, still open and still needing a valid test bed. This step does *make* one: the cold-machine harness (move caches aside, cut the network, launch, assert) is the same shape the FDA test needs, and the "prove the test can fail first" discipline is exactly what the FDA run was missing.

### ✅ The `.app` wrapper — self-contained bundle GREEN, FDA verdict INCONCLUSIVE (2026-07-28)

**The app no longer needs the dev environment.** A Release build embeds the frozen server, spawns *that*, and a double-clickable `Oasis.app` indexes and searches with no pixi env, no `CONDA_PREFIX`, and no library-path staging. The `RELEASE TODO` in `ServerController.resolveServerBinary()` is closed.

**The Full Disk Access question is *not* closed, and the reason is the finding**: this Mac does not gate `~/Documents` at all, for any app, so a clean walk here proves nothing about a stranger's machine. Details under "The FDA experiment" below.

#### What shipped

- **`app/Oasis/Scripts/embed_server.sh` + an `Embed Frozen Server` build phase.** `rsync -a --delete` of `dist/serve_entry/` (the recorded `spike/build.sh` recipe's output) into `Oasis.app/Contents/Resources/serve_entry/`. It does **not** re-freeze (minutes and 1.1 GB per build would make ⌘R unusable) and it **skips Debug** (embedding there would silently stop honouring `OASIS_SERVE_BIN`); `OASIS_EMBED_SERVER=1` overrides. `dist/` stays gitignored — the phase references the output, it never commits it. The target also sets `ENABLE_USER_SCRIPT_SANDBOXING = NO`, because the phase reads `dist/` at the repo root, outside the build directory.
- **`resolveServerBinary()` prefers the bundle, falls back to the environment.** `Bundle.main.resourceURL` + `serve_entry/serve_entry` — deliberately **not** `url(forAuxiliaryExecutable:)`, whose search path is the flat `Contents/MacOS/`, which has no room for the `_internal/` directory PyInstaller `--onedir` resolves its dylibs and data against. Bundled-first is what stops a shipped `.app` from ever falling through to a dev machine's environment; the Debug skip is what keeps the dev loop free of re-freezes. The spawn log now names which one won (`spawned [bundled] …` / `[dev/OASIS_SERVE_BIN]`).
- **The spawn is bare** — `child.environment` is never touched. That line is a comment instead of code on purpose: the pixi binary it replaces needed an activated environment, and that is the thing that bit the spawn repeatedly. `HF_HUB_OFFLINE` is deliberately **not** set; weights still come from the machine's HF cache.
- **`NSDocumentsFolderUsageDescription` / `NSDesktopFolderUsageDescription` / `NSDownloadsFolderUsageDescription`** added via `INFOPLIST_KEY_*` (the target uses `GENERATE_INFOPLIST_FILE`).
- **Re-froze against HEAD first.** The existing `dist/` predated the whole-codebase refactor commit, so the recipe was re-run once; the fresh binary was smoke-tested standalone under `env -i` (handshake, ready in 23 s) before embedding.

#### Proof the bundle is self-contained

Release build, ad-hoc signed ("Sign to Run Locally"), copied to `~/Applications/Oasis.app`, launched through **LaunchServices** (`open`) and through **Finder** — never Xcode, whose debug launch would have donated its own TCC/responsible-process context.

| check | result |
|---|---|
| `Contents/Resources/serve_entry/` present | yes — `serve_entry` + `_internal/`, **1.1 G** (whole `.app` also 1.1 G) |
| spawned process path | `/Users/…/Applications/Oasis.app/Contents/Resources/serve_entry/serve_entry serve --managed` — **the bundle's, not the pixi binary's** |
| app's parent | PPID 1 (launchd) — a real LaunchServices launch |
| handshake | **1.62–1.70 s** |
| handshake → ready | **7.19–7.42 s** (models from the HF cache) |
| indexing | `indexed=2 chunks=2 permission_denied=0` on a fresh folder, twice |
| search | `8 results … mode=hybrid llm_parsed=false server latency 394.0 ms`, with both probe files **ranked 1st and 2nd** |
| teardown | ⌘Q → no orphaned `serve_entry`, every time |

#### The FDA experiment — and why its clean result is *not* the good branch

The test ran the way it was meant to: `tccutil reset All Administrator.Oasis` first (the machine **had** accrued a real grant — `kTCCServiceSystemPolicyDownloadsFolder = 2` — which would have masked first-run behaviour), then a LaunchServices/Finder launch, then indexing.

**Measured, and reproducible across a quit-and-relaunch:**

- A plain folder (`~/oasis-fda-plain`) indexed clean. Expected.
- A folder under `~/Documents` indexed clean — **no prompt, no denial, `permission_denied=0`**, and on a second pass with the files touched, `indexed=2 chunks=2`, i.e. the child genuinely **opened and read file contents**, not just listed names.
- Repeated via **Reindex Current Folders**, which uses no folder picker at all, in a fresh process after another TCC reset. Still clean. **So the picker is not what granted it** — that alternative explanation is dead.
- `responsibility_get_pid_responsible_for_pid()` confirms the intended attribution: the app is its own responsible process, and **the spawned server's responsible process is the app**.

**The control that spoils it.** Three throwaway `.app` bundles with never-before-seen bundle identifiers, ad-hoc signed, each spawning a plain child that `opendir`s a list of paths:

| path | fresh app (parent **and** child) |
|---|---|
| `~/Documents`, `~/Documents/*`, `~/Desktop`, `~/Downloads` | **OK** — no prompt, silently auto-granted (`auth_value=2`, `auth_reason=2` "user consent" rows appear with no UI shown) |
| `~/Library/Safari`, `~/Library/Application Support/com.apple.TCC` | **DENIED**, `errno=1` |

Same outcome whether launched by `open` or by Finder, and **whether or not the Info.plist declared the usage strings**. So TCC is enabled and enforcing (SIP on, no configuration profiles, FDA correctly withheld) — but the **Desktop/Documents/Downloads tier is not gating anything on this Mac**. Oasis's clean `~/Documents` walk is therefore "there was nothing to inherit", not "the child inherited the app's access."

**One anomaly, measured twice, unexplained.** Oasis's spawned server walked `~/Library/Safari` — an FDA-only path — cleanly (`unsupported=57, permission_denied=0`), reproduced in a fresh process after a TCC reset with no picker, while every control app was denied that same path. Oasis holds **no TCC record at all** (neither user nor system database, no `kTCCServiceSystemPolicyAllFiles` row), so no grant explains it. Whatever the mechanism, it is a *reason to distrust a green result from this machine*, not evidence of the good branch.

#### The verdict, stated honestly

**Neither branch of the prompt's fork is established.** The clean walk is real but uninformative here; the architecture question — does a spawned child inherit the app's TCC grant, or does it need its own — remains open, and no fix should be picked on this evidence. What *is* established and does carry forward: **the child's responsible process is the app**, which is the mechanism by which a grant would inherit, and the two processes behaved identically on the one tier this machine does enforce (both denied).

**The one experiment that would settle it, and why it wasn't run:** grant Full Disk Access to `Oasis.app` in System Settings (requires admin authentication, so not automatable here), then index an FDA-gated path and see whether the child's access flips from denied to allowed. Better still, re-run the whole `~/Documents` test on a **second Mac** that reproduces the normal Desktop/Documents/Downloads gate — the control probe above (a fresh bundle id + `opendir`) is a 30-second way to check whether any given machine is a valid test bed.

#### Left on the machine

`~/Applications/Oasis.app` (the self-contained build). The index was restored to exactly its pre-test state — 300 documents, one root (`~/Downloads/corpus`) — by removing the three probe roots through `POST /api/index/remove-root`; probe apps, probe folders, and their TCC rows were deleted.

### Recently done (2026-07-29) — subtraction pass: cut what nothing needs

A read of `src/`, `eval/`, the 25 Swift files and the dependency manifests with one brief: **delete what isn't needed.** Net **−151 lines** across src + tests, one dependency gone from the shipped bundle, 952 → 939 tests (the 13 removed tested only deleted code). `ruff check .` clean, mypy unchanged at 34 pre-existing errors (**counted before and after** — this pass introduced none), full suite green including the 3 `slow`.

The bar was: *does anything in production reach this?* Tests referencing a symbol do **not** make it live — two of the four real finds were kept alive by nothing but their own tests.

#### `watchdog` was a declared dependency that nothing imports 📦

In both `pyproject.toml` and `pixi.toml` since the beginning; the only hits for the string anywhere in the repo are **this project's own parent-death thread** (`api/serve.py`, hand-rolled on `os.getppid()`) and Swift comments about it. The library was almost certainly added in anticipation of FSEvents-driven background indexing, which is a Tier-1 goal that hasn't been started. It was being resolved, installed, and **frozen into the 1.3 GB bundle** for nothing.

Removed and re-locked. `pixi lock` produced a **20-line diff, every line the watchdog package block** — no version churn anywhere else, which was the thing to check before touching the lock at all: the whole point of the single solve-group is that the numpy/torch the eval measured is the one that ships, and a re-solve that bumped them would have invalidated the measured matrix to save 3 MB. It didn't.

#### The FTS5 snippet path was dead, and its tests were the only thing holding it up 🪦

`get_snippet()` and `fts_snippet()` in `query/snippets.py` — ~40 lines including their own `SELECT snippet(documents_fts, …)` statement — had **zero production callers**. Nothing had called them for a long time: the keyword arm gets its snippet from `KeywordIndex.search`'s own `snippet()` column, and the semantic arm calls `text_snippet()`. What kept them looking alive was `test_snippets.py`, which imported and exercised both across 13 tests.

That is the failure mode worth naming: *a symbol with tests looks maintained*. Grep says "12 references", the module reads as load-bearing, and nobody checks whether any of the references is a caller rather than an assertion. Deleted the functions, the `SNIPPET_TOKENS` constant, the 13 tests, and the `conn` fixture + `_insert_doc` helper that existed only to feed them — which took the `sqlite3`/`open_db`/`MagicMock`/`pytest` imports out of that test module with them. `to_segments`, `text_snippet` and the property tests are untouched; that file is now 35 tests, all of live code.

**Also a small note on the SQL that went with it:** `fts_snippet` was the second place in the codebase issuing a `snippet(documents_fts, …)` query, which quietly violated "all keyword-index SQL lives in `KeywordIndex`". Deleting it restored the rule rather than requiring a migration to enforce it.

#### The two-store delete ordering was maintained by copy-paste 🐛-adjacent

`pipeline.reconcile()` (the stale sweep) and `POST /api/index/remove-root` both walk `(doc_id, path)` pairs and delete each from **both** stores, **vectors first, then the `documents` row** whose `_ad` trigger cleans FTS. Same six lines, in two modules, each with its own comment explaining the ordering — and the ordering is a correctness property: a doc left live in one arm and gone from the other serves stale hits from the survivor.

This is precisely the shape `CLAUDE.md` § Don't warns about, and it had already been noticed twice (both comments say "the sweep's order") without being fixed. Extracted `pipeline.delete_documents(idx, vector_index, docs) -> int`. What deliberately did **not** merge is the *predicate*: the sweep passes only docs the walk didn't see and is gated hard on a clean census, while remove-root passes every doc under the root with no gate at all — that asymmetry is load-bearing (a census gate on remove-root would break the deleted-root case it exists for) and is now stated in the helper's docstring instead of being implied by two similar loops.

Verified live, not just in tests: indexed a 3-file folder, deleted one file, reindexed → `Done — 0 indexed  2 skipped  1 removed`, and the deleted file was gone from the next search's results.

#### The CLI wrote its `index_directory` call twice

`cli/app.py`'s `index` command branches on `--verbose` into two display strategies, and each branch ended with its own copy of the same six-argument `index_directory(...)` call. The callbacks genuinely differ; the call doesn't. Hoisted into one local `run(on_file, on_chunks_progress)` closure so the argument list exists once and can't drift — the same failure this codebase already had between the CLI and the API's search paths. Display logic in both branches is byte-identical to before, and **both were exercised in a real terminal** (not just `CliRunner`, which doesn't drive Rich's live display): plain mode renders scan → embed → `3 indexed`, `--verbose` renders the per-file lines and the sweep summary.

#### Four comments that described a repo that no longer exists

Not cosmetic — each one actively misinforms a reader about what the code does now:

- `api/index.py`'s module docstring: *"This commit does exactly what index_directory already does (add + update) — it does **not** delete stale documents or backfill missing vectors."* Both landed on 2026-07-17. A reader trusting this would go looking for reconciliation that is already there, thirty lines away.
- `api/schemas.py` `IndexRequest`: *"`force` … this commit only passes it through … The next commit gives it stale-sweep semantics."* The next commit came and went; `force` governs embedding, not walking, and the sweep runs either way. Replaced with what's actually true.
- `api/state.py`: *"reset will need this"* about the connection-generation counter. `reset_index()` exists and calls `invalidate()` three lines away.
- `index/keyword.py` `add_indexed_root`: *"the stale-sweep reconciliation **planned** for full reindex"* — built, not planned.

The pattern is commit-relative narration ("this commit", "the next commit", "will need") in permanent docstrings. It reads as current tense forever and expires silently. Worth avoiding in new comments; a `git log` sentence belongs in a commit message or in this file, not in a docstring.

Same class, in this file: the `## Phase 5.2 — HTTP API` heading still said **"(specced, not implemented)"** with a body claiming `fastapi`/`uvicorn` weren't dependencies and `src/oasis/api/` didn't exist. True on 2026-07-14, false since 2026-07-15, and it sat directly above the sections describing the implemented server. Retitled to "the design record; **fully implemented**"; the decision table is kept because it carries the *reasoning*, which the docstrings state but don't argue.

#### Considered and deliberately kept

Listed so the next pass doesn't re-litigate them:

- **`VectorIndex.count()`** — no production caller, but it is a 2-line accessor that **seven tests** use to verify upsert/delete behaviour. Cutting it would push those tests onto the private `_table` handle: strictly worse. Kept.
- **`VectorResult.chunk_id`** — populated from the LanceDB projection on every search and read by no production code (only test assertions). It is the row's identity and the merge key's echo; dropping it from the `.select()` saves one string column per candidate and costs real churn in `test_vector.py`. Judged not worth it. Kept, and noted here as the most defensible remaining cut if bundle/latency work ever wants it.
- **`Chunk.token_count`** — written, never read outside tests. One int on a dataclass that is *about* tokens; removing it would touch ~7 tests to save nothing measurable.
- **`DocumentMetadata.ctime` / `author` / `page_count` / `language`** — written into `metadata_json` (and `language` into its own column) and never read back by search or display. This is *stored data*, not dead code: dropping it means a schema change and a reindex, for no gain, and the fields are the obvious raw material for a future "sort by author/date" facet.
- **The whole NL parsing layer** (`llm/`, `query/parser.py`, ~250 lines + 128 tests) — the single largest cuttable thing in the repo, measured net-negative, and off by default in two of three front-ends. **Not touched deliberately:** Tier 2's definition of done is *a decision, either direction*, and that decision needs the soft-filter and un-distilled-embedding experiments run first. Cutting it in a tidying pass would preempt the measurement and throw away the experiment's subject. See Up Next.
- **`_TERMINAL_STATUSES` (index.py) vs `TERMINAL_TYPES` (jobs.py)** — identical `frozenset({"done","cancelled","error"})`, but one is a *job status* and the other an *event type*; they coincide today and are not required to. Merging would save three lines and conflate two vocabularies.

#### What the pass confirmed is already clean

Worth recording so it isn't re-checked: **no junk is tracked in git** (no `.DS_Store`, `__pycache__`, `xcuserdata`, `.spec`, or build output among the 457 tracked files — `spike/serve_entry.spec` is generated by `build.sh` and correctly gitignored); **no orphan Swift types** (every type in the 25-file app is referenced outside its own file); **no dead symbols in `eval/`**; and `ruff check .` was and remains clean whole-tree.

One thing on disk, not in git, and **not deleted here because it is 1.5 GB and irreversible**: `.venv.pre-pixi-SAFE-TO-DELETE/`, left by the 2026-07-25 pixi migration. The migration is long since landed and verified, so it is almost certainly safe to `rm -rf`, but that is a call to make deliberately rather than as a side effect of a code-cleanup pass. (Also on disk: a stray `app/Oasis/.mypy_cache/`, 264 KB, from a mypy run in the wrong directory.)

### Recently done (2026-07-29) — README resynced to the true state

The `README.md` had drifted to its 2026-07-26 shape and was describing a project two arcs behind: it listed the HTTP API and the native app under **"Possible Future Roadmap"** (both are built), claimed "~940 tests" (952), and — the part that actually mattered — **sold NL parsing as a headline feature with no mention that the eval measured it at −0.108 ndcg@10 and that it is off by default.** That last one is a direct violation of the "Honesty over marketing — in the README" principle at the top of this file, and it was the reason to rewrite rather than patch.

Verified against the repo before writing, not from memory: `pixi run -e dev pytest` → **949 passed, 3 deselected** (952 total, matching this file), `ruff check .` clean, endpoint list read off the `@router` decorators in `src/oasis/api/`, CLI flags read off `cli/app.py`, `raw` defaults read off `api/search.py` (`True`) and `SearchViewModel.swift` (`"true"`) against the CLI's (`False`).

What changed:

- **A "Where the project actually is" table up top**, one row per piece, stating plainly that there is **no download yet** and running Oasis today means building it.
- **The macOS app got its own section** — the eight live features, how to build it, and the packaging status stated with its numbers (11.63 s cold offline to ready, 1.3 GB) *and* its debts (signing, notarization, DMG, the `libtorch_cpu` dedup, the unsettled FDA question).
- **A "Measured results" section** carrying the retrieval matrix, the current canonical restatement, the cross-encoder's real contribution, the keyword-row-is-a-strawman warning, and the **headline finding in full** — the −0.108 table, the two mechanisms with their per-query examples, and the asymmetric-payoff diagnosis. The résumé story is now on the front page instead of buried in this file.
- **The parsing layer is labeled off-by-default everywhere it appears** — the ASCII diagram's parser box, the Features bullet, the Stack row, and the quick-start examples, which now pass `--raw` with a note that the API and app already default to it and the CLI does not.
- **Latency: the "under a second" claim is gone.** Replaced with the one measured figure (394 ms server-side on a 300-doc index) and an explicit statement that no p95 budget exists.
- **`oasis serve` added to the commands table; a new HTTP API section** with the nine endpoints and the `verify_served.py` seam check.
- **Roadmap split next-up / later**, with the built items removed and the non-goals stated.
- Smaller truths folded in: incremental indexing now mentions stale reconciliation and no-vector backfill; the stack table gained FastAPI/uvicorn, SwiftUI, PyInstaller and the eval harness; design decisions gained the conda-forge/OpenBLAS reason the project is on pixi, the one-engine-three-front-ends rule, and why `ENABLE_APP_SANDBOX = NO` is architecture.

### Recently done (2026-07-28) — whole-codebase refactor pass

A read of all ~10.5 k lines (Python `src/` + `tests/`, the 25-file Swift app, `eval/`) with the brief "clean up, ready the ground for packaging — don't package". 946 → **952 tests**, ruff clean, Xcode clean. Two of the findings were real bugs, not tidiness.

#### The folder filter was over-matching, silently, in both arms 🐛

`KeywordIndex.search(folders=[…])` emitted `LIKE 'prefix%'`, and its own docstring claimed `LIKE 'prefix/%'`. Reproduced before fixing — a filter for `/tmp/a` returned **all four** of `/tmp/a/inside.txt`, `/tmp/ab/sibling.txt`, `/tmp/a_b/wildcard.txt`, `/tmp/axb/decoy.txt`:

- **No separator boundary** — a bare prefix says nothing about where the directory name ends, so `/tmp/a` swallowed `/tmp/ab`.
- **`_` and `%` are LIKE wildcards** and entirely ordinary in a filename, so a folder literally named `a_b` matched `axb`.

This is the *same trap* `docs_under` documents at length and dodges by filtering in Python — the sweep and remove-root were guarded, the **query path was not**. It can't be moved to Python here (the filter composes with the FTS5 `MATCH` in one statement), so `folder_like_pattern()` escapes instead and callers pair it with `ESCAPE '\'`. The LanceDB arm had the milder half of the same bug (it had the `/`, but escaped only `'`), and the two arms **must** agree or hybrid fuses two different answers to "under this folder". Both fixed, 7 regression tests, and the failure mode is worth stating: it returns *more* rows, never an error, so a folder filter that ignored its boundary looked exactly like one that worked.

#### The CLI carried a second copy of the search engine, and it had drifted 🐛

`cli/app.py` reimplemented all three retrieval modes that `query/search.py:run_search()` already owned — ~60 lines including a hand-rolled chunk dedup carrying four `# type: ignore`s, plus a duplicate `SearchMode` enum. `search.py`'s own docstring had said "the CLI still carries its own copy and migrates here separately"; the migration never happened, and in the meantime the copies diverged on the thing the eval actually measured: **the CLI reranked against the distilled `semantic_query`, the API against the user's raw words.** Distillation corrupts meaning — that is the finding — so the CLI was running the configuration the project measured and rejected.

Migrated. `run_search`'s `vector_index`/`embedder` became `| None`, valid **only in keyword mode**, which touches neither — that is what preserves `oasis search --mode keyword` answering in 2.3 s without a model load, and an assert stops the permission going further. Verified by running the same query through both front-ends: **identical rankings** (`a2, fill-18, fill-16, fill-17, fill-11`), which they would not have been before.

#### Duplication removed where it was already causing drift

- **`build_fts_query` / `build_kw_filters` / `build_vec_where` are public.** Four modules outside `retriever.py` imported them through the underscore — `query/search.py`, `cli/app.py`, `eval/run_eval.py`, the tests. The `_` was documentation that had stopped being true.
- **`semantic_ready` / `reindex_recommended` moved onto `IndexCapabilities`.** `/api/health` and `/api/status` both promise in their docstrings that they can't disagree; both maintained it by copy-paste. Now one implementation.
- **`db_size_bytes()` moved into `index/db.py`.** The API summed `.db` + `-wal` + `-shm`; `oasis status` stat'd the `.db` alone, so the CLI **under-reported a freshly-indexed database** — most of the new content sits in an uncheckpointed WAL. Two tools, one index, different numbers.
- **`stat_metadata()` in `extractors/base.py`** replaces the `path.stat()` + three-field copy written out six times. `mtime` is change-detection input and the vector store's date filter, so all six reading it identically is a correctness property.

#### Readied for packaging (without packaging)

- **The tiktoken encoding is lazy.** It loaded at *import* time, so every entry point that imported `oasis.cli.app` paid for it — `search`, `status`, and server startup, none of which chunk anything — and on a cold cache that is a **network download on the startup path**, which is wrong for an app that must work offline. It is also exactly where the frozen binary died: `Unknown encoding cl100k_base. Plugins found: []` raised while importing the CLI module, *before the handshake printed*, so the server failed with no output and no clue. Deferring moves any such failure to the indexing path where it is attributable. Confirmed: `import oasis.cli.app` no longer touches it.
- **`reranker.rerank` zips `strict=True`.** A short score array previously dropped results silently — the exact failure shape this model already produced once via NaN logits. Two tests went red on the change and both were right to: the `CrossEncoder` fake returned a fixed 3-element array regardless of input, so it had been testing a contract the real model doesn't have. The fake now returns one score per pair.
- **`XlsxExtractor` uses `try/finally`.** It closed the read-only workbook on the success path *and* again in the handler, so a failure after the close double-closed.

#### The Swift app: one client instead of six

Six view models hand-rolled the same authorized request. Three reached into **`IndexRunner`'s statics** for `endpoint`/`errorMessage` — which made the index runner an accidental dependency of the status panel and the folder list — three more carried private copies of the error-envelope decoder, and `ServerController` built its health URLs by string interpolation while everything else used `URLComponents`. New **`OasisAPI.swift`** owns addressing, authorized-request construction, session config, and the envelope, mirroring the Python rule that all SQL lives in `KeywordIndex`. None of the old code was wrong; it was six places to change for one edit — which is the shape of the problem when the server binary moves from a dev path into the `.app`.

Also: `ServerController` built a **fresh `URLSession` per `refreshHealth()`** (each with its own connection pool, never invalidated) — now one shared session behind a `fetchHealth(port:)` that both the poll and the refresh use. `ThumbnailLoader`'s cache was an **unbounded dictionary in a menu-bar-resident app**, so it grew for the life of the login session; now an `NSCache` (bounded, and it drops under memory pressure). `@ObservationIgnored` on the session because `@Observable` rewrites stored properties into computed ones and `lazy` can't apply.

**Verified by running the app** (temporary in-process harness, deleted): status, search, a punctuation-and-unicode query (`alpha & beta + #1 100% ünïcode` — the case naive URL building breaks on), `/api/open`'s 404, a full index job through the SSE stream, remove-root, and two consecutive `refreshHealth` calls on the shared session. All green.

#### Documentation caught up with the code

`StatisticsPanelView`'s roots comment still said removal was impossible because `indexed_roots` is append-only — untrue since the day before. `api/app.py`'s docstring still said search/index/reset/open "land in later commits". And Reindex's failure sheet now **names the recourse**: when the root that stopped the sequence is gone from disk, it points at Settings ▸ Folders (⌘,), which is the only screen where the user meets that wedge.

### Recently done (2026-07-27, newest)
- **Swift app step 8 — Settings. The last inert control is live, and the rail is complete.** A standard SwiftUI `Settings` scene, tabbed **General / Folders / Shortcuts / About**. New files `SettingsView.swift`, `SettingsModels.swift`, `FoldersViewModel.swift`, `LaunchAtLogin.swift`; `AppSearchCoordinator` grew the shared `StatusViewModel`; `SearchViewModel`'s pinned limit became a preference.
  - **The `Settings` scene, not a second `Window` — and `SettingsLink`, not a Button.** The scene is what installs **Oasis ▸ Settings… (⌘,)** and gives one window that reopens rather than duplicating; a `Window` scene would mean reimplementing all three. On the rail, `SettingsLink` is the supported opener — a hand-rolled `NSApp.sendAction(showSettingsWindow:)` leans on an undocumented selector that has already been renamed once across releases. Verified: ⌘, opens `com_apple_SwiftUI_Settings_window`, and a second ⌘, opens **no second window**.
  - **What is deliberately absent is the actual design decision.** Settings holds **per-user preference and recourse**, not knobs that re-litigate settled measurements. Search mode, rerank on/off and `raw`/NL-parsing are **not** exposed, because the eval already decided them and every alternative is *worse on the numbers* (NL parsing alone costs −0.108 ndcg@10). Shipping them would invite users into configurations this project measured and rejected, and then quietly attribute the bad results to Oasis. A preference is for something only the user can know — how many results they want to look at, which key is free on their keyboard — never for something the matrix already answered. The reasoning is written at the top of `SettingsModels.swift` so the next person adding a toggle meets it first.
  - **Folders is the tab with teeth, and it is the reason the endpoint landed first.** It lists `indexed_roots` and removes one through `POST /api/index/remove-root` — behind a confirm scoped to that folder, worded to answer the question users actually have: *"Its indexed documents will be deleted from Oasis… **The files on disk are untouched.**"* Reset's dialog is the model; this one is narrower.
  - **A missing root renders as missing, which is the whole feature made visible.** The row stats the path and, when the folder is gone, shows `folder.badge.questionmark` and "**Missing — this folder blocks Reindex until it's removed**". Without that the user meets the wedge as "Reindex keeps failing" with nothing on screen connecting the two.
  - **`StatusViewModel` hoisted from `ContentView`'s `@State` to the coordinator**, extending an argument the code already made. It was shared between the statistics panel and Reindex's roots because those must never disagree; Settings ▸ Folders is the third reader of that list **and the only writer**. Two instances would let the folder list the user just edited and the folder list on the main window behind it drift apart — and the main window is usually visible while it happens. Removal then refreshes health *and* status *and* pokes `search.indexDidChange()`, because a grid of results that no longer exist is worse than an empty one: clicking one would 404 through `/api/open`.
  - **Launch-at-login reads `SMAppService`, never a mirrored bool.** The user can revoke the login item in System Settings with the app not running, and a stored preference would then show a toggle that is on while the item is off. The setter registers and the getter re-reads the system, so a **failed** registration snaps the toggle back rather than lying — and the failure is surfaced, not swallowed, since running out of DerivedData is the common case in development. Measured live anyway: `notRegistered → register() → enabled → unregister() → notRegistered`, no error, even from a DerivedData build.
  - **Results-count is read per search, not captured.** `SearchViewModel` re-reads `UserDefaults` at request time, so a change applies to the *next search* rather than the next launch, with no observation plumbing between Settings and the model. `ResultLimit.current` owns the clamping — notably the absent-key case, where `UserDefaults.integer(forKey:)` returns **0** and would otherwise ask the server for zero results. Measured: 4 → 4, 12 → 12, 16 → 16, 8 → 8 results returned.
  - **The Shortcuts tab is a *fix*, not a nicety**, and the recorder is the payoff for step 7's finding: two GUI processes can register the same combination and **both** get `noErr`, so a third-party app's hotkey is invisible to any probe and shows up only as ⌘⌥O doing nothing. Rebinding is the entire recourse, which is why the binding shipped as a default rather than hardcoded.
  - **Full Disk Access is a deep link and an explainer, and is labelled as guidance.** It is not the FDA *solution* — whether the spawned server inherits the app's TCC grant belongs to the distribution arc — but it is harmless now and useful the moment anyone indexes under a protected location.
  - **Verified by running the app** (temporary in-process harness, since Accessibility is denied to `osascript` here; deleted afterwards, same pattern as the arrow-key verification). ⌘, opened the settings window (520×450, singleton). **Rebinding moved the real OS-level registration, not just the preference** — probed via `RegisterEventHotKey` inside the process, where `eventHotKeyExistsErr` means "this process holds it": before, `⌥⌘O=held ⌃⌘J=free`; after `setShortcut(⌃⌘J)`, `⌥⌘O=free ⌃⌘J=held`; after restore, back. Launch-at-login toggled both ways. **Folder removal, both cases:** the **wedge** root (indexed, then deleted from disk) removed cleanly — 3 roots → 2, 22 → 21 documents — and an ordinary root after it, 21 → 20; searching the removed roots' distinctive tokens returns **0 paths under either**, while the surviving root still returns its own. Removing an untracked root is the benign 404 refresh, list unchanged. Reveal-in-Finder confirmed by asking Finder for its selection: the index file. About reads `1.0 (1)` out of the bundle.

- **`POST /api/index/remove-root` — the recourse for a wedged root.** 941 → 946 tests. Deletes every stored document under one indexed root (keyword row + FTS via the `_ad` trigger + vectors via `delete_by_doc_id`), drops the root from `indexed_roots`, returns `{root, removed}`. `404` for an untracked root, `409` under the shared `job_lock`. Built and tested *before* the Settings UI that depends on it, because it is destructive.
  - **It exists because `indexed_roots` was append-only, and that turns out to wedge Reindex permanently.** Step 4's Reindex is stop-and-report, so a root the user deleted from disk `400`s and halts the refresh of *every other* folder. The only escape was `/api/reset` — wipe everything to drop one folder. This is the targeted version. The "roots are read-only, deliberately" entry from step 5 was right about its premise and is now superseded by removing the premise.
  - **The whole design decision is that it is UNCONDITIONAL**, and the temptation to make it otherwise is real, because the pipeline already contains something that looks like it. The stale sweep deletes stored docs the walk didn't see, and it is gated *hard* — not cancelled, zero walk errors, zero permission denials — because there "not seen" only means "deleted" if the census can be trusted. **remove-root answers a different question.** It is *"forget this folder"*, not *"reconcile this folder against disk"*: the user already decided. So no walk, no gate — and it must never grow one, because **the wedge case is a root whose files are gone**, where a walk cannot succeed by definition. A census gate would make the endpoint fail in exactly the situation it was written for. That inversion is the entry worth remembering, and `test_remove_root_when_directory_deleted_from_disk` is its guard.
  - **Reuses the sweep's scoped-delete helper, deliberately.** `docs_under(root)` filters in **Python, not SQL `LIKE`** — a real path's `_` is a single-char wildcard to `LIKE`, and a bare `startswith` puts `/tmp/ab` under `/tmp/a`. Both traps are one test (`test_sibling_prefix_is_not_removed`, whose fixture file is named `a_b.txt` precisely so a future SQL rewrite goes red). Per doc, both stores converge in the sweep's order — vectors, then the `documents` row — since a doc live in one arm and gone from the other returns stale hits. All SQL stayed in `KeywordIndex`; the endpoint gained no queries of its own.
  - **The marker is dropped last, and the order is the crash story.** Docs first, `remove_indexed_root` after: a crash with the rows gone but the root still listed leaves the operation **retryable**, while the reverse orphans rows under a root the user can no longer name — the one unrecoverable direction. Same reasoning shape as reset's markers → vectors → documents, aimed at a different failure.
  - **Matching is exact, never prefix-based**, so a *subdirectory* of a tracked root is a `404` rather than a partial delete of its parent's documents — asserted, since "delete everything under the path I typed" is the plausible wrong reading of a scoped-delete endpoint.
  - **Five adversarial tests, hermetic** (real uvicorn + SQLite + LanceDB, crc32-faked models, `assert "torch" not in sys.modules` on teardown): all three arms cleared and the root untracked with the other root untouched; sibling-prefix isolation; **the wedge case** — index, `rmtree` the folder, confirm `POST /api/index` now `400`s (the wedge, asserted as a precondition), then remove-root succeeds and the survivor reindexes clean; unknown root and subdirectory `404`; and `409` under a gated running job with nothing deleted.

### Recently done (2026-07-27, last)
- **Arrow keys + Return navigate and open the results — the mouse-free path is complete.** ↓ ↑ ← → move a highlight through the grid, Return opens it, Escape drops it. With step 7's summon that makes ⌘⌥O → type → ↓ → Return an end-to-end keyboard path from any app to an open document. `SearchViewModel` owns the selection; `ContentView` binds the keys; `ResultCard` grew a selected state.
  - **Focus never leaves the query line.** The handlers hang off the focused `TextField`, not off focusable grid items, which is the model Spotlight and every address bar use — and the reason is that the two things a user does here interleave: refine the query, look at the answers, refine again. Moving real focus into the grid would mean typing the next query requires getting focus *back*, re-establishing the caret every time. `onKeyPress` on the focused field sees the key **before** the field editor does, and returning `.ignored` hands it back — which is how ← → still move the caret when nothing is highlighted.
  - **Return is the one genuinely ambiguous key**, and it is resolved in one place rather than left to two handlers racing: highlighted → open it, nothing highlighted → run the search. `.onSubmit` stays as a backstop but never fires, because `onKeyPress(.return)` always returns `.handled`.
  - **The grid arithmetic lives in the model, the grid's shape in the view.** `moveSelection(_:columns:)` takes the column count as an argument — the layout is `ContentView`'s business, the stepping is not, and one implementation beats spelling it out at four key handlers. Row-major fill *is* the ranking, so ← → step by one and ↑ ↓ step by a whole row.
  - **Clamp, never wrap**, and **↑ from the top row clears the highlight** rather than sticking to it — that is the only way back out of the grid to a plain query that doesn't need the mouse. Wrapping from the last result to the first is disorienting in a grid.
  - **`didSet` on `state` resets the selection, in one place rather than beside six assignments.** An index into the previous result array is meaningless the moment the array is replaced, and the out-of-bounds crash it would otherwise cause is precisely the kind that only appears when a slow search lands under a fast finger. `selectedResult` is bounds-checked as well, belt and braces. Editing the query clears it too — guarded, since a redundant write to an `@Observable` property invalidates every view observing it, and that one fires per keystroke.
  - **`ScrollViewReader` follows the highlight.** Without it, ↓ past the fold moves an invisible selection and the grid looks frozen. Clicking a card also sets the highlight, so the keyboard carries on from where the mouse left it instead of jumping back to the top.
  - **Verified live** (built with `xcodebuild`, clean; a temporary harness posted genuine arrow events — function/numericPad modifier flags and the `0xF70x` characters real arrows carry — into the focused field, then was deleted). Over 8 results: **↓ → 0**, **↓ → 2** (a whole row), **→ → 3**, **← → 2**, **↑ → 0**, **↑ from the top row → none**; **8×↓ → 7** and **→ at the end → 7** (clamped, no wrap); **Escape → none** with the query still `'revenue'`; **Return with no selection → ran a search** (8 results); **Return with selection 2 → opened `sotu-1953-Eisenhower.txt` in TextEdit** — the owning app for that type, not Preview, which is the point of going through `open`; and **editing the query → selection none**.

### Recently done (2026-07-27, latest)
- **Clicking a result opens the file — `POST /api/open` is finally consumed.** New file `DocumentOpener.swift`; `ResultCard` became a button; `ContentView` owns the opener and renders failures. The search→open loop is closed: type a query, click the answer, the document opens in Preview (or whatever owns the type).
  - **Through the server, not `NSWorkspace.shared.open(_:)`** — and the one-line local alternative is exactly why the reason is written into the file. (1) **The index is the authority on what Oasis may open**: `api/open.py` looks the path up with `KeywordIndex.get_doc_id` *before* it shells out, and that lookup **is** the security boundary; opening locally moves the decision into the client and silently drops the check. (2) **404 and 410 are different answers** — `NSWorkspace` returns one boolean, while the server distinguishes "not in the index" from "indexed, and gone from disk", and only the second has a fix the user can act on. (3) One engine, many front-ends: `oasis open` and the app take the same path.
  - **A `Button`, not an `onTapGesture`.** The gesture looks identical and gives up everything AppKit hangs off a real control — the card becomes one accessibility element with a button trait and a label, and Space activates it. That is not a theoretical benefit: it is what let the change be **verified by pressing the actual control**, and it is what a screen-reader user needs. Single click, like Spotlight — a result list is a list of destinations, not a file browser where selecting and opening are separate acts.
  - **The hover affordance is load-bearing, not decoration.** Nothing else in that window responds to the pointer, so without it a clickable card is indistinguishable from a static one and the feature is undiscoverable.
  - **Double-fire is guarded per-path, not per-grid.** `DocumentOpener.opening` is a `Set<String>`: a second click on the same card while its request is in flight is dropped (launching a document twice is the classic bug, and the round trip is short enough to out-click), while the other seven cards stay live. The in-flight card shows a spinner, which is also what stops the user reaching for that second click.
  - **A failed open is a banner beside the results, not a modal.** An alert would demand a click to get back to a grid that is still perfectly usable. The 410 wording names the fix ("Reindex Current Folders to clear it from your results") because that is the state where the index and the disk have genuinely diverged.
  - **Verified live** (built with `xcodebuild`, clean; driven by a temporary in-process harness that pressed the real control through `AXUIElementPerformAction`, then deleted). **(a)** The card is exposed as `AXButton` with label `Open Metropolitan Transit Authority — Annual Report FY2023`; the press returned `.success`. **(b)** With **Preview not running** (`pgrep` 0 beforehand), pressing it logged `opened`, the server returned `204`, and `Preview` plus its QuickLook services appeared in `NSWorkspace.runningApplications` — the PDF really opened. **(c)** **410 path:** the same file was moved aside between the search and the press; the client logged `open 410 — indexed but missing from disk`, **no app launched**, and the file was restored byte-identical (4421 bytes, original mtime).

### Recently done (2026-07-27)
- **Swift app step 7 — the Spotlight-style summon: a global ⌘⌥O, a floating query panel, and menu-bar residency.** New files: `SummonShortcut.swift` (the `KeyboardShortcuts.Name` + a registration probe), `SummonPanel.swift` (the `NSPanel` subclass + its controller), `SummonView.swift` (the query line), `AppSearchCoordinator.swift` (the hand-off). `OasisApp.swift`, `ContentView.swift` and `SearchViewModel.swift` edited. **The `KeyboardShortcuts` package was not actually in the project** — it was added to `project.pbxproj` by hand (`XCRemoteSwiftPackageReference` + product dependency + Frameworks build file) and resolved to 1.17.0.
  - **`canBecomeKey` is the whole feature.** A borderless `NSWindow` returns `false`, and a window that is not key gets no keyboard events — the panel appears, shows a caret, and silently swallows every keystroke, with no error anywhere. The `NSPanel` subclass overrides it to `true` and deliberately leaves `canBecomeMain` `false`: key routes keystrokes, main marks the app's principal window, and a launcher wants the first without the second.
  - **`.nonactivatingPanel` + `orderFrontRegardless()` + `makeKey()`, not `makeKeyAndOrderFront`.** The style mask is what lets the panel take key focus *without* activating Oasis, so summoning from another app doesn't yank that app's windows down; `makeKeyAndOrderFront` will not raise a window belonging to an inactive app, which is the only case the panel exists for. Level `.floating` (system-wide, so it draws above every app's normal windows) and `collectionBehavior` `[.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]` (measured `321` at runtime), with `hidesOnDeactivate = false` — a utility panel that hides when its app deactivates is exactly wrong here.
  - **Search state was hoisted out of the window.** `SearchViewModel` was `@State` inside `ContentView`, so it died with the window and did not exist at all while the app sat resident with none open. It now lives on `AppSearchCoordinator`, owned by the `AppDelegate`, and `ContentView` reads it (`@Bindable` for the text binding, since there is no `@State` projection any more). `refreshRestingState()` grew a guard — it must not fire when a query is already up, or reopening the window would blank the results the hand-off just asked for.
  - **`Window`, not `WindowGroup`.** `openWindow(id:)` against a `WindowGroup` opens a *second* window every time; against a `Window` scene it restores the one that was closed. That is what makes "hotkey with no window open" land somewhere sensible. `OpenWindowAction` is only readable from a view, so it's captured from both `ContentView.onAppear` (fires at launch) and the `MenuBarExtra` **label** (the one view that exists for the whole process lifetime).
  - **🔴 `NSApp.activate()` is cooperative on macOS 14+ and silently refuses.** Measured: Enter in the panel ran the search and the main window came back on screen, but **Finder stayed frontmost** — the results rendered behind the app the user had just left. Two changes fixed it, and both matter: the hand-off now runs **before** the panel is dismissed (dismissing first surrenders the user-activation claim the key panel gave us), and activation goes through `activate(ignoringOtherApps: true)` from a deliberately deprecated wrapper. Cooperative activation is the right default for almost everything and exactly wrong for a summon.
  - **🔴 The hotkey probe reported a false collision, and the cause is a trap worth recording.** `KeyboardShortcuts.Name.init` writes its default into `UserDefaults` when nothing is stored, and the package's `userDefaultsSet` **calls `register(shortcut)` on the way past** — so merely *mentioning* `.summonOasis` registers the Carbon hotkey. A probe that reads the binding through the package is therefore probing against our own registration: `-9878` on first launch, "clean" on every launch after. The probe now reads `KeyboardShortcuts_summonOasis` straight out of `UserDefaults` and never touches the `Name`.
  - **What a hotkey probe can and cannot see — measured.** Two GUI processes were made to register ⌘⌥O simultaneously; **both got `noErr`**. `RegisterEventHotKey` does not report another *application's* hotkey, and `eventHotKeyExistsErr` (-9878) means only "this process already registered this combination". So a system reservation is detectable (`CopySymbolicHotKeys`, and it's the one that actually wins the key), a third-party app's is not — it manifests as ⌘⌥O doing nothing, and the fix either way is rebinding.
  - **Not-ready is handled, not refused.** Refusing the hotkey until the server is up produces the dead panel the step exists to avoid — ⌘⌥O does nothing for the first half-minute after launch and the user concludes it's broken. The panel opens during warmup with a status row ("Oasis is starting… press Return and your search will run as soon as it's ready"), Enter **holds** the query on the coordinator, and `ContentView` runs it the moment it reaches its ready branch.
  - **Residency: `applicationShouldTerminateAfterLastWindowClosed` → `false`.** Closing the window leaves Oasis in the menu bar with the hotkey live and the server child still up — which is also what keeps the summon fast, since the models stay warm. Window-close is not quit; only quit kills the server. A `MenuBarExtra` gives Open Oasis / Search… / Quit.
  - **Verified live, end to end**, driven by a temporary in-process harness (an app may post events to itself with no Accessibility permission) and read back out of `os.Logger`; the harness was deleted afterwards. **(a)** ⌘⌥O **registered cleanly** on a true first launch (the stored binding was deleted first; `keyCode 31 / carbonModifiers 2304` persisted). **(b)** With **Finder** frontmost and `NSApp.isActive=false`, the panel appeared `key=true`, `level=3`, `frame={{416, 623}, {680, 60}}` — centred, upper third — typing landed, Enter returned **8 results for "revenue"**, the panel dismissed and **Oasis came frontmost** with the window showing the results. **(c)** Escape dismissed with no search run. **(d)** Focus leaving the panel dismissed it with no search run. **(e)** **Closing the main window** left the app alive with the server `ready`; ⌘⌥O still produced a key panel, and Enter **reopened the window**, fronted Oasis and showed 8 results for "budget" — `pgrep -f "oasis serve"` non-empty throughout. **(f)** Hotkey **during warmup**: panel opened `key=true` at 93 pt (the status row), Enter held the query (`pending=revenue`) and dismissed cleanly; on `ready` the held query ran automatically → 8 results. **(g)** **Quit** (`NSApp.terminate`, the same path as ⌘Q and the menu item) logged the teardown and left `pgrep -f "oasis serve"` empty and no Oasis process.

### Recently done (2026-07-26, last)
- **Swift app step 6 — Reset Indexing. The button rail is complete** (Settings aside, which opens a window rather than touching the index).
  - **The confirm names the stakes, not "Are you sure?"** A destructive `.confirmationDialog` reading "This permanently removes all N indexed documents and their search data. You'll need to reindex your folders. This can't be undone." — N read live off `/api/status`, so it can't quote a stale figure. One clear destructive confirm, matching the CLI's `--yes`; no type-to-confirm theatre. Zero-documents-but-a-root gets its own wording, since clearing the folder list is still irreversible.
  - **The dialog is the human confirmation; `{"confirm": true}` is the API's guard.** Two separate mechanisms on purpose — the server has no interactive prompt, so a bare body must not be able to nuke the index. The `400` that flag prevents is still surfaced rather than swallowed: reaching it would mean a bug on the app side.
  - **The button is only live when reset would actually succeed** — the two disable conditions map exactly onto the server's two refusals. An index job running → the server would `409` (reset takes the same job lock), so it's disabled during index/reindex *and* the `409` is still handled defensively. No index file on disk → the server would `404`, so it's disabled there too. Note it stays **enabled** at `200` with zero documents: an index holding no documents can still hold a recorded root, and clearing that is a real thing to want. `404` is handled as a no-op refresh, not an error — nothing was destroyed and nothing is wrong.
  - **`indexed_roots` clears on reset — verified, nothing to flag.** `reset_index()` clears the meta table, so post-reset `/api/status` is `200` with `documents: 0` and `indexed_roots: []`. No stale roots pointing at an empty index, and Reindex correctly falls back to disabled.
  - **🔴 The bug this step existed to catch: an emptied index rendered as "No matches for <query>".** Step 4's `indexDidChange()` re-runs the live query after any index change, which is right — but `performSearch` mapped *every* empty result set to `.noMatches`, because the empty-index distinction lived only in `refreshRestingState()`. So resetting with results on screen told the user to "try different words" when the real problem was that they had just deleted their entire index. `performSearch` now checks `documents` before choosing: zero results over zero documents is the **onboarding** state (APP_SEAM.md §6e), not a failed query. Reset is the only action that reaches this — indexing can't take a populated index to empty — which is exactly why it survived three steps.
  - **Verified live, six checks** (built with `xcodebuild`, clean; UI driven and read back through the Accessibility API): **(a)** on a never-indexed DB both Reindex *and* Reset render `enabled=false`; **(b)** after indexing 507 documents across 2 roots the dialog quoted "**all 507 indexed documents**" and the irreversibility; **(c)** **cancelling the dialog did nothing** — 507 documents intact, zero `/api/reset` requests in the log; **(d)** confirming reset returned `204` in ~90 ms → documents `0`, roots `[]`, and the app dropped to the empty state in **both** places: the statistics panel's clean empty *and* the search area's onboarding prompt (log line `empty result over an empty index — onboarding, not no-matches`) even with a query still in the box; **(e)** with a real index job running (POST logged, progress sheet up) Reset rendered `enabled=false`; **(f)** the full loop — reset → empty → Index New Folder → 500 documents / 7 MB / 1 folder / Semantic search Ready, and a query returned 8 results again. Quit left no orphaned `oasis serve`.

### Recently done (2026-07-26, latest)
- **Swift app step 5 — the Indexed File Statistics panel reads `/api/status`.** Replaces step 2's stub of em-dashes with the real payload: documents, size, last-indexed, semantic-search state, the indexed-folders list, and two worded nudges. No new machinery — one GET, one decode, one panel.
  - **New files:** `StatusModels.swift` (full `StatusResponse` mirror + the decoder below), `StatusViewModel.swift` (`loading / loaded / empty / failed`), `StatisticsPanelView.swift`. `ContentView` wires it; the two-field `StatusResponse` step 4 had put in `ServerModels.swift` is gone, and `IndexViewModel` now reads its roots off the shared `StatusViewModel` — **one `/api/status` reader in the app**, because the panel's data and the roots Reindex re-scans are the same read and must never disagree.
  - **🔴 `.iso8601` is not sufficient for `last_indexed_at`, and it fails by value, not by schema.** `CLAUDE.md` § Wire conventions established that every datetime carries a UTC offset *so that* Swift's `.iso8601` strategy can decode it — necessary, but not sufficient: `.iso8601` is `ISO8601DateFormatter` with `.withInternetDateTime` alone, which **rejects fractional seconds**, and `last_indexed_at` is built from a float Unix mtime. Measured on the real index: `2026-07-27T00:34:28.109631+00:00`. A timestamp landing exactly on a whole second serializes without a fractional part and decodes fine, one that doesn't fails — and because `Date?` sits inside `StatusResponse`, that failure takes the **whole panel** down, not one field. `JSONDecoder.oasisStatus` accepts both forms. Worth knowing wherever the app decodes a server datetime next.
  - **Both empty shapes render calmly, and they are genuinely different.** `404` (no index on disk) carries no payload; `200` with `documents: 0` does — and can still carry **roots**, because the pipeline records a root *before* it walks, so indexing a folder with nothing indexable in it lands exactly there. `State.empty(StatusResponse?)` keeps that distinction, and the panel shows the folder plus "Indexed, but no supported files were found." Flattening the two would have hidden the one state where naming the folder matters most.
  - **`reindex_recommended` is worded, not printed** — the payoff of the capability markers. The boolean is server-derived (the client does no version math), but the granular fields are kept so the app can say *which* problem it is: no vectors at all ("searches fall back to keywords only"), vectors at the wrong width ("built for a different embedding model"), or an older format. **Running it against a copy of the real pre-vector `~/.oasis` index exposed a contradiction worth fixing:** that index has no recorded roots, so Reindex is *disabled* — while the callout said "Use Reindex Current Folders above", pointing at a greyed-out button, for exactly the population the nudge targets. The call-to-action now switches to **Index New Folder** when there are no roots.
  - **`stale_documents: nil` renders as "Stale count not computed (large index)", never as zero.** Over `STALE_SCAN_CAP` (5000) the server skips the per-file stat scan; reporting that as a clean index is a lie the user can't detect. `> 0` becomes a second nudge ("N files no longer on disk — reindex to clean up"), which is the reconciliation sweep's pre-image; `0` says nothing.
  - ~~**Roots are read-only, deliberately.**~~ **Superseded 2026-07-27 by `POST /api/index/remove-root`.** The reasoning held exactly as long as its premise did: `indexed_roots` was append-only server-side, so there was no endpoint to remove one and a client-side "hide" would be undone by the next index. Building the endpoint removed the premise. The row layout this entry describes — each root its own row with the trailing edge free — is what Settings › Folders hangs its remove control on, as predicted.
  - **Second bug found by running it: "Last indexed: in 0 seconds."** A just-finished index lands within milliseconds of `now` — sometimes a hair ahead, since the server writes the timestamp and the app reads it back across the same instant — and `RelativeDateTimeFormatter` renders that as a *future* time. Anything inside a minute now reads "Just now".
  - **Verified live, six states** (built with `xcodebuild`, clean; UI read back through the Accessibility API): **(a) populated** — 513 documents, 4 MB, "2 hours ago", Semantic search **Ready**, "13 files no longer on disk", folders r_alpha + r_beta, `db_path` muted; **(b) refresh on index change** — indexing a new folder moved it to 520 / 4.2 MB / 3 folders with `r_delta` appearing, no relaunch; **(c) never indexed** — 404, calm "Nothing indexed yet" (confirmed no `index.db` on disk, so it really was the 404 path); **(d) `documents: 0` with a root** — calm, plus "Folders 1 / r_nothing / Indexed, but no supported files were found."; **(e) `reindex_recommended: true`** against a **copy** of the real 877-document pre-vector index (144.5 MB, "1 day ago") — Semantic search **Not ready** and the orange callout with the no-vectors wording; **(f) `stale_documents: null`** — a copy padded past the cap to 5,513 documents rendered "Stale count not computed (large index)". The real `~/.oasis` was copied, never opened in place, and is byte-for-byte unchanged.

### Recently done (2026-07-26, later)
- **Swift app step 4 — Reindex Current Folders, over a shared single-job runner.** Re-scans every folder the index already covers: new and changed files picked up, deleted ones swept, missing vectors backfilled. Mostly reuse — the commit's real content is the **refactor that made it reuse**.
  - **`IndexRunner.swift` (new) is now the only place the SSE machinery lives.** Step 3's `IndexViewModel` was one class doing POST + stream + state; the single-job core (POST `/api/index` → consume to a terminal event → return an outcome) is now a runner both actions call. This was done *before* writing reindex, deliberately: the framing, the **`.lines`-drops-empty-lines** workaround, the snapshot-vs-progress handling and the one-shot re-attach are the fiddly parts with a landmine in them, and a copy-paste for a second caller is how that landmine grows back.
  - **One view model, because Index New Folder is the N = 1 case of the same sequence.** `Operation` is `.indexFolder(root)` or `.reindexAll(roots)`; everything downstream — sheet, cancel, dismiss, terminal summary — is shared. The server holds a single-job lock and 409s a second POST, so multi-root is inherently **sequential**: run a root, await its terminal event, advance. That is the only structural difference between the two actions.
  - **`force: false` for reindex, and that is the whole point.** A `force: false` run still does the *full walk*, which is where reindex's value is — the reconciliation sweep deletes what the walk no longer sees and the backfill embeds unvectored docs. `force` governs only *re-embedding unchanged files*: the expensive part, and waste unless the embedding model changed. Measured on the 500-file root: `indexed=0 skipped=500 chunks=0` on an unchanged pass. A `force: true` full rebuild stays a future affordance for an embedder-dimension change, deliberately not built as a toggle.
  - **Roots come from `/api/status.indexed_roots`, never guessed.** Empty means "unknown coverage" — including the legacy pre-root-tracking index that has documents but no recorded roots — and both cases get the same honest answer: the button is **disabled** with "No indexed folders yet — use Index New Folder." A guessed common prefix would aim the stale sweep at a tree it was never measured against. New `StatusResponse` in `ServerModels.swift` decodes only `documents` + `indexed_roots`; the rest of the panel's fields land when that panel is wired.
  - **Stop-and-report, not skip-and-continue.** A root that fails ends the operation; the summary names the failed root, keeps the earlier roots' real stats, and says "**N folders not reached**" so a short list is never mistaken for a complete one. **Consequence worth knowing:** a recorded root that has been deleted from disk now blocks reindex of the *other* roots until root removal exists — deliberate for v1 (silently skipping a root the user believes is being refreshed is worse), but it is the first thing a "manage indexed folders" screen has to fix. ~~until root removal exists~~ **`POST /api/index/remove-root` landed 2026-07-27** and is the recourse; Settings › Folders is the screen.
  - **Cancel stops the operation, not the folder.** Cancel POSTs for the current job, shows "Cancelling…", and `runSequence` declines to advance when the terminal `cancelled` arrives. Remaining roots are reported untouched.
  - **`removed` is surfaced per-root and aggregated**, and the row is shown on a reindex even at zero — this is the first place the reconciliation sweep becomes visible to the user, so "0 removed" is information, not an omission.
  - **Fixed a step-3 wart while wiring the refresh:** `onIndexCompleted` called `refreshRestingState()`, which unconditionally overwrites the result area — so finishing an index while results were on screen blanked them. Now `SearchViewModel.indexDidChange()` re-runs the live query instead (new content appears, swept content vanishes) and only falls back to the resting state when the query box is empty.
  - **Verified live, six checks** (built with `xcodebuild`, clean; fresh `OASIS_DB_PATH`; UI driven and read back through the Accessibility API): **(a) empty-roots** — Reindex renders `enabled=false` with the hint, then flips to enabled once a root exists; **(b) sequence UI** — "**Folder 2 of 2**" over the current root path, a running "1 folder done — 13 indexed, 0 removed" line, and a determinate embed bar (0.128 → 0.384); **(c) change detection** — a file added after the first index came back searchable after reindex (`indexed=1 skipped=12`); **(d) deletion via the sweep** — a deleted file gave `removed=1`, surfaced per-root as "r_alpha — 1 indexed, 1 removed", and is gone from **both** arms (`documents` row count 0, `documents_fts MATCH 'pelican'` 0); **(e) cancel mid-sequence** — accepted 20:34:37.947 → terminal `cancelled` 20:34:38.154 (**207 ms**), partial stats `indexed=500 chunks=576` of 1000, and **no POST for a third root**; **(f) stop-on-failure** — deleting the *first* root made reindex stop with the server's own 400 ("Not a directory: …/r_alpha"), the failed root marked, and "**1 folder not reached**". Quit left no orphaned `oasis serve`.
  - **One measurement worth keeping:** an all-unchanged reindex of 513 documents across 2 roots took **~0.4 s end to end** (both jobs), because `force: false` skips re-embedding. That is what makes "reindex everything" a reasonable button to press casually.

### Recently done (2026-07-26)
- **Swift app step 3 — Index New Folder: picker → `POST /api/index` → SSE progress → cancel → summary → count refresh.** The first button in the app that does something, and the first UI consumer of the async-index machinery built server-side on 2026-07-16/17. **No tests — verification is running it**, and it was, including the two paths that only exist at runtime (a live phase transition and a mid-flight cancel).
  - **New files:** `IndexModels.swift` (wire mirrors of `IndexRequest`/`JobResponse`/`CancelRequest` and the five SSE events, plus the frame parser), `IndexViewModel.swift` (`@MainActor @Observable` — the state machine, kickoff, stream consumption, cancel), `IndexProgressView.swift` (the sheet). `ContentView.swift` wires the rail button *and* the `.empty` onboarding prompt — which now carries its own **Index a Folder…** button — into the same flow. One additive method on `ServerController`: `refreshHealth()`.
  - **🔴 The bug that only running it could find: `AsyncBytes.lines` silently drops empty lines, and in SSE the empty line *is* the frame terminator.** Consumed through `.lines`, a perfectly healthy stream yields field lines that are never flushed — no event ever decodes, the stream just ends, and the sheet reports "lost the progress stream" while the server is happily publishing. The first live run did exactly that (two re-attach attempts, then failure, at 20:01:27). Fixed by splitting lines off the raw byte sequence in `SSEFrameParser.consume(_:)`; the reason is commented at both the parser and the consumption loop, because `.lines` is the obvious thing to reach for and it is wrong here.
  - **SSE framing, not JSON-per-line.** `data:` content accumulates until a blank line closes the message, `:`-prefixed lines are dropped as comments (the **`: ping` every 15 s** would otherwise produce a decoder error per heartbeat), and `event:`/`id:`/`retry:` are ignored — dispatch is on the `type` inside the JSON. The heartbeat is also what keeps the connection alive, so the stream session's request timeout is **60 s** (four missed pings), never something shorter that would fight it; `timeoutIntervalForResource` is set to 24 h so a long first index can't be capped by the default.
  - **`phase` drives the bar, not `total`-being-null** — the discriminator exists precisely so "scanning, count unknown" reads differently from "done but empty". `scan` → indeterminate + "Scanning… N files"; `embed` → determinate `done/total` chunks; `reconciling` → indeterminate "cleaning up". Progress counts are absolute, so the view just renders the latest event and a dropped one self-heals.
  - **Snapshot decoded exactly like a progress update**, never assumed to be a preamble: on connect it may already be `running`, `done`, `cancelled` or `error`. That same property is what makes recovery exact — when the stream ends without a terminal event the consumer **re-attaches once** and reads the snapshot rather than guessing, and only then gives up with an honest message instead of hanging a spinner.
  - **Cancel is cooperative and the UI settles on the *event*, not the click.** The button POSTs `/api/index/cancel {job_id}` (the id from the 202 — the contract requires it, and it is what stops a cancel aimed at job N from killing N+1 once auto-reindex exists), shows "Cancelling…" on `202`, and **keeps consuming the stream**; a `409` means the job already ended and is treated the same way. Tearing the stream down on click would throw away the partial stats the cancel is about to produce. Measured live: cancel accepted 20:06:10.803 → terminal `cancelled` 20:06:11.941, a **1.1 s** window that is the pipeline finishing its current embed batch.
  - **`done` refreshes the count, and that is what closes the index→searchable loop.** The readiness poll runs once and stops, so the held `HealthResponse` is a snapshot from *before* the first index; without a re-fetch a freshly-indexed folder still shows "nothing indexed". `ServerController.refreshHealth()` re-reads `/api/health` and republishes `.ready`, then the search view model re-derives its resting state. Verified: `documents` went null → 400 → 1600 → 1625 → 1628 across four runs, the `.empty` onboarding state cleared to `.idle` on the first one, and a query for content indexed **seconds earlier** returned it — no restart.
  - **`permission_denied > 0` is surfaced with a Full Disk Access hint**, which is where the pipeline's separate counter finally pays off in UI: a `chmod 000` corpus indexed 3 files and rendered "**4 files skipped — grant Full Disk Access in System Settings ▸ Privacy & Security to index protected folders**" instead of a silent partial success. The onboarding flow itself is still a later step.
  - **Sheet, not a non-blocking panel**, for v1 — the server supports search-during-index via the shared `VectorIndex`, so search-live-underneath is a possible refinement, but the sheet is simpler and unambiguous about what's happening.
  - **Verified live end to end** (built with `xcodebuild`, clean; app run against a scratch `OASIS_DB_PATH` so the empty state was real; UI driven and read back through the Accessibility API): empty state → picker → 400-file corpus → **`Scanning… 1,187 files`** captured mid-walk with an indeterminate bar, then the embed bar reading real determinate values climbing **0.055 → 0.359 → 0.662 → 0.966** — the phase transition confirmed from the rendered UI, not from logs. Terminal summaries render `indexed / skipped / chunks / removed`; a cancelled run settled on partial stats (indexed 1200, chunks 1280 of ~4300) with the "work already finished was kept" note; a re-run of that same folder exercised the **no-vector backfill** (773 re-embedded, 427 skipped). Quit left **no orphaned `oasis serve`**.
  - **The TCC question (flagged in the prompt, and it did *not* bite here): the child server walked `~/Documents` clean — `permission_denied=0`.** The app picks the folder but the **spawned server** does the walking, and macOS attributes file access per process, so "does the picker's grant extend to the child" was the open risk. It didn't fire in dev: a probe folder under `~/Documents` (a TCC-protected location) indexed 3/3 with zero denials. **Caveat on how much that proves** — the app was launched from a terminal that already holds Documents access, so the responsible-process attribution in this run is the terminal's, not a double-clicked `.app`'s. It says the dev loop is unblocked; it does **not** settle FDA for the shipped, signed app, which remains the Tier-1 first-run item.

### Recently done (2026-07-25)
- **Migrated to pixi, flipped inference to CPU (OpenBLAS), re-measured the matrix, re-confirmed lancedb.** One commit, five parts, two of them gates that ran *before* anything committed. This is the end of the CPU-inference block that started with the app's MPS abort.
  - **`pixi.toml` + `pixi.lock` replace `uv.lock`** as the top-level dependency manager. uv did not leave — pixi uses it internally to resolve the PyPI half; `uv_build` remains the build backend. torch now comes from conda-forge (`pytorch 2.13.0 cpu_generic_py314`, `BLAS_INFO=open`); lancedb and ranx come from PyPI. One lock covers both halves: **426 conda + 36 PyPI entries**.
  - **Feature-split, and the split is load-bearing.** `default` = runtime only and is the PyInstaller freeze target; `test`/`eval`/`build` carry pytest+httpx+mypy+ruff, ranx+matplotlib, and pyinstaller. Verified by import that **pytest, ranx, matplotlib, PyInstaller, llvmlite and numba are all absent from `default`** — in the flat spike env, eval-only tooling put ~136 MB of llvmlite+matplotlib into the bundle. `default` and `dev` share **one solve-group**, so the numpy/torch that ships is by construction the one the suite and the eval ran against (verified: `numpy 2.4.6` in both).
  - **numpy is capped `<2.5`, and the cap is the eval feature's fault.** `ranx → numba`, and every released numba requires `numpy<2.5` while conda-forge defaults to 2.5.1. pixi surfaced this as a hard resolve failure on the first `pixi install`; bare conda's unlocked `pip:` section would have let pip install a second numpy over the conda one and desync it from torch's ABI. **The failure mode pixi was chosen to prevent is the one it demonstrated on the first run.**
  - **Matrix re-measured — GATE, and it moved.** Canonical restated to **ndcg@10 0.5601, mrr 0.5427, recall@10 0.6844, p@5 0.2275, p@10 0.1338** (was 0.5602 / 0.5427 / 0.6844 / 0.2250 / 0.1338). Read as a 2×2 against the old canonical:
    - **The BLAS/device effect is exactly zero.** The MPS control and the CPU run agree on all five aggregates *and* on all **80 per-query score sets** — 0 differences. OpenBLAS CPU is metric-equivalent to MPS. This was the question the migration existed to answer.
    - **The whole delta is the version stack** (torch 2.12→2.13, transformers→5.14.1, sentence-transformers 5.5→5.6.1, numpy→2.4.6), and it is **2 of 80 queries reordering inside their top-10**: q046 (p@5 0.4→0.6, ndcg 0.9236→0.9515) and q075 (ndcg 1.0000→0.9639), which nearly cancel. **`recall@10` is identical on every one of the 80 queries** (no document entered or left any result set) and **`mrr` is identical on every one** (no top-1 changed). The p@5 move is exactly one grid step, 1/(80×5).
    - Each device was reindexed separately so both runs are end-to-end on their own device, making the MPS control directly comparable to the old canonical's configuration. Three independent runs produced the same numbers.
  - **lancedb re-confirmed on the version actually shipped.** pixi resolves **0.34.0**; the VectorIndex-not-thread-local finding was measured on 0.30.2, four minors back, and it is a *silent* failure mode, so it was re-measured rather than assumed. The regression test passed **10/10**, and all four rows of the original table reproduce: reads during `merge_insert` safe (505 reads, 0 exceptions), shared handle climbed 12→212, a separately-opened handle stayed pinned at 12, `checkout_latest()` recovered it to 212. **0.34.0 kept as a measured decision.**
  - **`DEFAULT_DEVICE = "cpu"`, and the `xfail(strict=True)` marker is gone** — it fired `XPASS(strict)` the moment the pixi env came up, which is precisely what it was built to do, and it did so against the *real project code* rather than a reproducer. It is now a plain assertion on the realistic shape, kept as the tripwire if a future swap ever reintroduces a PyPI torch. `device.py`'s docstring now records that CPU is only safe *because* torch links OpenBLAS.
  - **Fixed a pre-existing test-pollution bug found by running the full suite.** `test_cli.py` and `test_cli_edges.py` fake both models but cleared only the *reranker* cache, leaving a `MagicMock` parked in `oasis.index.embeddings._MODEL_CACHE` under `("all-MiniLM-L6-v2", "cpu")` where it outlived the fixture. Harmless until a test that builds a *real* embedder ran later in the same session — which `-m ''` now does, so `test_real_embedder_loads_on_cpu` got handed the mock and failed. Both fixtures now clear both caches. Latent since the device plumbing was written; nothing to do with the flip. **941 passed** (full suite incl. `slow`), ruff clean.
- **Swift app step 2 — the single-page main window (search wired, controls inert).** The lifecycle gate from step 1 now opens onto the real window: query bar → `/api/search` → a 2-column grid of up to 8 result cards (QuickLook thumbnail + title + highlighted snippet), with the right-hand control rail laid out but **deliberately no-op**. First authenticated call the app makes — this is where step 1's stashed `token` finally gets used.
  - **New files:** `SearchModels.swift` (mirrors `api/schemas.py` — `SearchResult`, `Segment`, `SearchResponse`, `ErrorResponse`; field names read off the source, not invented), `SearchViewModel.swift` (`@MainActor @Observable`, the `SearchState` machine), `ThumbnailLoader.swift`, `ResultCard.swift`, and a rewritten `ContentView.swift`. One additive line on `ServerController`: a `health` computed property so callers read `documents` off the readiness poll instead of re-fetching.
  - **Two "nothing" states kept distinct**, which is the whole point of APP_SEAM §6e: `documents` null-or-0 → `.empty` → onboarding pointing at Index New Folder; index has content but the query matched nothing → `.noMatches(query)`; content but no query yet → `.idle`, blank. A wiped index must never render as broken, and it doesn't.
  - **Request built with `URLComponents` + `queryItems`**, never interpolation — queries carry spaces, punctuation and unicode, and `"?q=\(query)"` breaks on the first `&` or space. Params pinned to `mode=hybrid`, `limit=8`, `raw=true` (the eval-measured best path; the app never asks for an NL parse). Whitespace-only queries are dropped client-side rather than spending a request to be 400'd. Non-2xx is mapped through the `{error:{code,message}}` envelope to a legible sentence.
  - **In-flight search is cancelled when a new one starts**, so a slow earlier response can't paint over a newer one. Enter-to-submit, not search-as-you-type: each search is a real round trip through torch inference.
  - **`LazyVGrid` fills row-major, which *is* the required rank order** (left→right, top→bottom), so handing it the server's array unmodified satisfies the ordering for free. Nothing sorts or regroups — the server's order is the rank.
  - **Thumbnails via `QLThumbnailGenerator`, with `NSWorkspace.icon(forFile:)` as the guaranteed fallback** (unsupported types and indexed-then-deleted files are expected, not exceptional — never an empty box). Cached by path, with an in-flight task map so two cards never race the same generation. QuickLook auto-links on import; no project-setting change this commit.
  - **The segment wire format is now exercised end-to-end across the language boundary.** Cards fold `[{text, match}]` into an `AttributedString` by appending runs — **no index arithmetic anywhere**, which is exactly why segments were chosen over `{start,end}`: Python indexes by codepoint, Swift by grapheme cluster, `AttributedString` by UTF-16, and any offset would need a conversion sitting precisely where nobody tests. Verified against 8 real results (15 match runs): concatenating `text` in order reproduces each snippet, no empty segments, no adjacent segments sharing a `match` value — the canonical form in `CLAUDE.md` § Snippet format holds on the wire, and a synthetic emoji/ZWJ/CJK/combining-mark snippet folds losslessly.
  - **The five controls are inert by design** — Index New Folder, Reindex Current Folders, Reset Indexing, Settings, and the Statistics panel are positioned and styled real buttons whose actions are `// TODO: step N` no-ops, so the window is the true shape while the wiring stays scoped to search. The one honest live value is the Statistics panel's document count, free from the health payload; the rest of the panel is stubbed pending `/api/status`.
  - **Verified live** (built with `xcodebuild`, clean): the gate opens to the main window on ready; **both resting states confirmed from real health payloads** — a fresh DB via `OASIS_DB_PATH` gave `documents=null → .empty`, the real index gave `documents=877 → .idle` (`documents: 0` takes the same branch by construction). The search round trip was verified by decoding **real server responses through the app's own `SearchModels.swift`** (compiled standalone, no duplicated structs): 8 results decode including the snake_case keys, a zero-match query decodes as `results: []` and not an error, the 401 envelope decodes to its `message`, and a null `title` falls back to the filename. **Not verified: the in-window interaction itself** — driving the query bar needs keystroke injection, which macOS refused (`osascript is not allowed to send keystrokes`; Accessibility isn't granted for the terminal). Typing a query and watching the grid is the one thing still to eyeball by hand.
- **Swift app step 1 — the server lifecycle seam** (first Swift commit; `app/Oasis/Oasis/`). Spawn → handshake → health poll → teardown, and the three-state UI over it (`warming` / `ready` / `failed`). Deliberately nothing else: no search, no action buttons, no menu bar, no floating panel. Implements `docs/APP_SEAM.md` §§1–6; **no tests — verification is running it and watching**, and it was.
  - **New files:** `ServerController.swift` (`@MainActor @Observable`, owns the child), `ServerModels.swift` (`Handshake`, `HealthResponse`), `ContentView.swift` (replaced the template stub), `OasisApp.swift` (+`AppDelegate`), `app/README-dev.md`, `app/.gitignore` (Xcode ignores scoped to `app/` so the root `.gitignore` stays Python; `xcshareddata/` is deliberately **not** ignored — the shared scheme carries `OASIS_SERVE_BIN`).
  - **Spawn by absolute path from `OASIS_SERVE_BIN`, with no `$PATH` fallback.** Not a style choice: this machine has a stale `oasis` on `PATH` predating `serve`, so a fallback would launch the wrong binary and fail confusingly. Unset/non-executable → `.failed` with a message naming the variable. The bundled-binary release path (`Bundle.main`, APP_SEAM §1) is a marked `RELEASE TODO`, not implemented.
  - **Both pipes drained continuously, only the first stdout line parsed.** stderr goes to `os.Logger` (`com.oasis.app`/`server`) and is never parsed — an undrained stderr pipe fills its OS buffer and *blocks the child*, which presents exactly as a startup hang. The first stdout line is decoded as the handshake; a later line is logged as a doc deviation and never re-parsed (APP_SEAM §2).
  - **Three independent ways to never hang before the handshake:** `terminationHandler` (child exited → `.failed`, §6a), a 15 s timeout (measured spawn→handshake is 2–3.3 s), and EOF-without-a-line. All settle one idempotent continuation; whichever fires first wins.
  - **`loading` is never failure and there is no short ready-timeout** (§3, §6c). The only ceiling is a 120 s *soft* timeout → `.failed` + Retry, set far above the measured 35–54 s window. The `.warming` view shows live elapsed seconds, so every launch re-measures §4.
  - **`.ready` renders real payload fields** (`documents`, `reindex_recommended`), not just "a 200 came back" — that's what proves the round trip decoded. `documents: null` and `documents: 0` both render as "no content yet" (§6e); neither is an error.
  - **Teardown is belt *and* suspenders, because neither mechanism covers the other's case.** `applicationWillTerminate` → SIGTERM covers ⌘Q; it does **not** run under Xcode's ⌘. (SIGKILL), which is covered by the `--managed` watchdog (`getppid() == 1` → exit). Commented at the teardown site. A run-generation counter keeps a superseded run (Retry, quit) from clobbering the state of the run that replaced it.
  - **Two Xcode template settings changed**, both documented in `app/README-dev.md`, and both settled rather than deferred: **`SUPPORTED_PLATFORMS = macosx`** (the template was multiplatform iOS/xrOS; `Process` and `NSApplicationDelegateAdaptor` are macOS-only) and **`ENABLE_APP_SANDBOX = NO`** — see below, since that one is an architectural decision, not a build tweak.
  - **`ENABLE_APP_SANDBOX = NO` is permanent and correct by design, not dev debt.** App Sandbox is mandatory only for **Mac App Store** distribution, a stated non-goal; Oasis ships signed, notarized and directly downloaded. The two things the app fundamentally does — **spawn the server child** (a binary outside the app bundle) and **index arbitrary user-chosen folders** — are exactly what the sandbox exists to forbid, so sandbox-off *is* what a directly-distributed local file-search tool looks like. It costs nothing on either axis that matters: **not a privacy tradeoff** (the sandbox governs what the app can reach on *this machine*, not what leaves it, and nothing leaves it regardless — loopback-only, token-gated, no telemetry, `access_log=False`; the privacy north star is about the network boundary and is untouched), and **not a barrier to notarization** (an unsandboxed Developer ID app notarizes fine — notarization wants the hardened runtime and a valid signature, not the sandbox — so Tier 1's "signed + notarized, one double-click" stays fully open). Re-enabling it would be a **re-architecture** of both the child-spawn and arbitrary-folder indexing, not a flag flip: out of scope unless the App Store is ever pursued, which it is not.
  - **Full Disk Access survives as the real requirement, independent of that decision.** TCC gates the protected directories (Desktop/Documents/Downloads/…) for **unsandboxed apps too**, so indexing `~/Documents` needs FDA either way — it was always coming, and the `permission_denied`-vs-`failed` split already built into the pipeline exists precisely to drive that first-run flow. Stays a Tier-1 first-run item.
  - Xcode user state (`xcuserdata`) was `git rm --cached`'d — it had been staged.
  - **Verified live, four paths** (built with `xcodebuild`, run against the real `~/.oasis` index): (a) **happy path** — spawn → handshake → `loading` → `ready`, `documents=877 reindex_recommended=true`, i.e. the true state of the real pre-vector index, decoded and displayed; (b) **`OASIS_SERVE_BIN` unset** → `.failed`, and `pgrep` confirms **nothing was spawned** (the no-`PATH`-fallback rule holds); (c) **env var pointing at a non-executable** → `.failed` naming the path; (d) **both teardown paths** — SIGKILL to the app (the Xcode ⌘. case) left **no orphan**, the watchdog reaped the child within 5 s, and SIGTERM (the ⌘Q case) also left none. The Retry button's respawn was not exercised by click, though its `stop()`+`start()` internals are the same ones (c)/(d) covered.
  - **New measurement, and it diverges from the doc: `t_ready` was 7.3 s and 8.7 s, against APP_SEAM §4's measured 35–54 s.** `t_handshake` (2.23 s, 3.22 s) is squarely in the doc's 2.0–3.3 s band, so the divergence is specific to the warming window and is almost certainly **warm vs. cold**: the doc's runs used a fresh temp DB and a cold model cache, these reused the machine's warm HF cache and page cache. **`APP_SEAM.md` is left unedited** — its numbers are labelled as cold single-samples and its own caveat already calls them order-of-magnitude, and the architectural conclusion (never block on warm-up, never impose a short ready-timeout) is unchanged either way. The app's copy states the range with the cold upper bound rather than the warm number, so a cold start never reads as broken.

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
- **`GET /api/status`** — the token-gated (auth-required, unlike `/api/health`) detail view the app's manage-index screen reads. Reuses `get_capabilities()` (no DB logic duplicated) and derives `semantic_ready` / `reindex_recommended` from the **same implementation** health uses — `IndexCapabilities.semantic_ready(live_dimension)` / `.reindex_recommended(live_dimension)`, moved onto the dataclass 2026-07-28. Until then "byte-identically" was maintained by copy-paste in two endpoints. New `StatusResponse` (inherits `ApiModel`, so the datetime serializer applies) adds paths/sizes health omits: `db_size_bytes` (sums `.db` + `-wal`/`-shm`), `last_indexed_at` (UTC-offset ISO), `db_path`, `indexed_roots`, and `stale_documents`. **404 (not 200) when no index exists at `db_path`** — health always 200s ("is the server up"); status describes the index, and no-index is its not-found. Empty index (0 docs) is a 200. `stale_documents` counts stored paths gone from disk (`KeywordIndex.count_stale()`, one `stat` per doc) but is **capped**: over `STALE_SCAN_CAP` (5000) the scan is skipped and the field is `null` ("not computed", distinct from `0` = "computed, none stale"). **`indexed_roots` persistence added to the pipeline**: `index_directory` now calls `KeywordIndex.add_indexed_root(str(root))` (abspath'd, JSON list, deduped) before the walk, so even a cancelled/permission-denied run registers the root — this is the load-bearing prerequisite for the full-reindex stale-sweep in Up Next (never guess roots from a common prefix). Sync `def` (blocking stats → threadpool), on `protected_router` before the catch-all. 9 new tests (`test_api_status.py`), incl. stale-cap-skips-scan (spy proves no per-file stat) and reindex_recommended parity with `/api/health` on a legacy index. **Verified live** against the real `~/.oasis` index: `documents: 877, schema_version: 0, semantic_ready: false, reindex_recommended: true, stale_documents: 0` (all 877 June-3 paths still exist on disk — verified independently; `indexed_roots: []`, since that index predates root tracking).
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