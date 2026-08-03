# Oasis

**Does finding the right file feel like looking for an oasis in the Sahara desert? Look no further, and find the file you forgot you made.**

Oasis is a natural-language file search system that runs entirely on your machine. Ask it the way you'd ask a person, like — *"that tax PDF from last spring"*, *"powerpoints I made about ML last month"*, or *"the spreadsheet with Q3 revenue"* — and it finds the right file for you.

Oasis works by running hybrid keyword and semantic retrieval over the *contents* of your files, fusing the two rankings, and reranking the survivors with a cross-encoder. Everything happens locally.

It ships as three things over one engine: a **CLI**, a **loopback HTTP service**, and a **native macOS app**.

---

## Why Oasis?

Most built-in OS search indexes shallowly and ranks poorly. `grep` can't help when you only half-remember what was actually in the file. Spotlight search on mac forgets the content from formats it doesn't natively understand. Where Oasis differs, is that it indexes the *contents* of PDFs, Word docs, slide decks, spreadsheets, CSVs, and Markdown files — then lets you query them the way you actually remember them, not the way the machine does.

---

## Where the project actually is

| Piece | State |
|---|---|
| Extraction, keyword index, vector index, hybrid retrieval + rerank | **Done**, measured, 988 tests |
| Filename search | **Done** (2026-08-02) — names are their own weighted FTS column *and* their own embedded chunk; **+0.068 ndcg@10**. See [Measured results](#measured-results) |
| Evaluation harness (`eval/`) | **Done** — 300-file labeled corpus, 83 queries, reproducible matrix |
| Natural-language query parsing | **Built, and disabled by default** — the eval measured it as a **−0.108 ndcg@10 regression**. See [Measured results](#measured-results) |
| Local HTTP API (`oasis serve`) | **Done** — every endpoint implemented, served rankings verified byte-identical to the eval harness |
| Native macOS app (SwiftUI) | **Feature-complete** — search, indexing, stats, reset, ⌘⌥O summon panel, menu-bar residency, Settings. Every control is live |
| Packaging | **In progress** — a Release `.app` is self-contained and works offline on a cold machine (11.63 s to ready, 1.3 GB). **Not yet signed, notarized, or distributable** |

There is **no download yet**. Running Oasis today means building it from this repo.

---

## Quick startup guide (CLI)

```bash
git clone https://github.com/MohamedElrefaei7/Oasis.git
cd Oasis
pixi install

# Index a folder (incremental — re-runs skip unchanged files)
pixi run oasis index ~/Documents

# Search in plain English (examples)
pixi run oasis search --raw "tax pdf from last spring"
pixi run oasis search --raw "powerpoints about machine learning last month"
pixi run oasis search --raw "spreadsheet with quarterly revenue"

# Directly open result #2 from the last search
pixi run oasis open 2
```

`--raw` skips the LLM parsing layer, and it is **the best-measured configuration** — see [Measured results](#measured-results). The HTTP API and the macOS app already default to it; the CLI still parses unless you pass the flag.

---

## The macOS app

The app is the actual deliverable; the CLI proves the engine. It spawns `oasis serve --managed` as a child process, reads a one-line JSON handshake off its stdout, polls `/api/health` until the models are warm, and tears the child down on quit. The retrieval code is never re-implemented — one engine, three front-ends.

What works today, live:

- **Search** over `/api/search` into a result grid, with highlighted snippets and thumbnails.
- **A Spotlight-style summon** — a global **⌘⌥O** pops a borderless floating panel over whatever you're in, on the current Space. Type, arrow, Return. The app is menu-bar resident, so closing the window doesn't quit it.
- **Open results in their real app** by click or by keyboard, without ever leaving the query line.
- **Index New Folder** — `NSOpenPanel` → `POST /api/index` → live SSE progress with cancel → terminal summary.
- **Reindex Current Folders**, sequentially over every known root, surfacing the stale-reconciliation sweep's removal count.
- **Indexed File Statistics** — real counts, index size, last-indexed, semantic-search readiness, the folder list, and worded "you should reindex" nudges.
- **Reset Indexing** behind a destructive confirm that names the document count.
- **Settings** (⌘,) — General / Folders / Shortcuts / About: launch-at-login, results-count preference, reveal-index-in-Finder, a Full Disk Access explainer, hotkey rebinding, and add/remove indexed folders.

Building it: open `app/Oasis/Oasis.xcodeproj` and Run. Dev builds spawn the server from `OASIS_SERVE_BIN` (already set in the committed scheme); Release builds embed a frozen server and spawn *that*. Setup lives in [`app/README-dev.md`](app/README-dev.md), and the spawn/handshake/readiness contract is specified in [`docs/APP_SEAM.md`](docs/APP_SEAM.md).

**Packaging status, honestly.** A Release `.app` embeds the PyInstaller-frozen server, both models, and the tiktoken encoding in `Contents/Resources/`, and runs entirely from inside the bundle. Verified the only way it can be — HuggingFace / tiktoken / torch caches moved aside, **Wi-Fi off**, launched by Finder double-click: **11.63 s cold to ready** (1.73 s handshake, 4.14 s model warming), 6.85 s on relaunch, weights proven open from inside the bundle by `lsof`, then a real index and a real search. Total 1.3 GB. Still owed before anyone else can run it: a deliberate signing story, hardened runtime, notarization, a DMG, and deduping the doubled 237 MB `libtorch_cpu.dylib`. The spawned-server Full Disk Access question is also still open — the test Mac doesn't gate `~/Documents` for *any* app, so a clean result there proves nothing.

---

## How it actually works

```
  "powerpoints about ML from last month"
                  │
                  ▼
      ┌───────────────────────┐
      │  NL parser (Ollama)   │   ← OFF by default (measured net-negative)
      │  → ParsedQuery        │     file_types / date_range / folders
      └───────────────────────┘
                  │
        ┌─────────┴─────────┐
        ▼                   ▼
  ┌───────────┐       ┌───────────┐
  │  SQLite   │       │  LanceDB  │
  │  + FTS5   │       │  vectors  │
  │  BM25     │       │  cosine   │
  │  + filter │       │  + filter │
  ├───────────┤       ├───────────┤
  │ filename  │       │ filename  │
  │ path      │       │ content   │
  │ title     │       │ chunks    │
  │ content   │       │           │
  └───────────┘       └───────────┘
        │                   │
        └────────┬──────────┘
                 ▼
         ┌──────────────┐
         │   RRF (k=60) │
         └──────────────┘
                 ▼
         ┌──────────────┐
         │ Cross-encoder│
         │   reranker   │
         └──────────────┘
                 ▼
            top results
```

Every component runs completely locally — embeddings, LLM, and storage. No telemetry, no cloud sync, no API keys required (which also means it's free to use)! The service doesn't even keep an access log, so your queries aren't written down anywhere.

---

## Features

- **Three search modes** — keyword (BM25 with porter stemming), semantic (dense vectors), or hybrid (RRF fusion + cross-encoder rerank). Pick with `--mode`.
- **Filenames are searched too, in both arms** — the name is split into real words (`Q3ReportFinal.pdf` → `Q3 Report Final`, `trec3` → `trec 3`), indexed as its own weighted FTS column, *and* embedded as its own vector chunk. So a file whose name is the only place your search term appears still comes back — including files with no extractable text at all, like a scanned PDF, which were previously invisible to semantic search.
- **Natural language queries** — semantic + hybrid retrieval understands a plain English sentence directly. There is *also* a local-LLM parsing layer (Ollama, `llama3.2:3b`) that extracts file types, date ranges, and folder hints into a typed schema — it's built and tested, but it's off by default because it measured worse (see below).
- **Incremental indexing** — `(size, mtime)` hash skips files that haven't changed since the last run. Deleted files are reconciled out of all three stores on the next pass; documents indexed before the vector store existed get their embeddings backfilled without `--force`.
- **Format coverage** — `.txt`, `.md`, `.pdf`, `.docx`, `.pptx`, `.xlsx`, `.csv`. Partial-success extraction means one corrupted page doesn't lose the whole document (Which is quite often the case).
- **Local by default** — every dependency runs on your machine, and nothing ever leaves it. The only socket Oasis opens at all is loopback: its own API, and a probe to `localhost:11434` to see whether Ollama is around.
- **CPU inference by default** — portable and deterministic, via conda-forge torch linked against OpenBLAS. Override with `OASIS_DEVICE`.

---

## Commands

| Command | What it does |
|---|---|
| `oasis index <path>` | Walks a directory and indexes every supported file. `--force` rebuilds; `--verbose` prints each and every file. |
| `oasis search <query>` | Searches the indexed files. `--mode keyword\|semantic\|hybrid`, `--limit N`, `--raw` to bypass NL parsing. |
| `oasis open <n>` | Opens result `#n` from the last search in the system default app. |
| `oasis status` | Document count, DB size, last-indexed timestamp. |
| `oasis reset` | Deletes the index (gives a confirmation prompt; append `--yes` to skip). |
| `oasis serve` | Runs the loopback HTTP API. `--port` (omit for an ephemeral port), `--managed` to exit when the parent process dies. |

---

## The local HTTP API

`oasis serve` binds loopback only, prints a one-line JSON handshake (port + bearer token) on stdout, and loads models in the background so a client can render a warming state instead of blocking. Every response error is a single envelope shape; every route but health requires the token.

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Model lifecycle (`loading` / `ready` / `error`), document count, `semantic_ready`, `reindex_recommended`. Auth-exempt. |
| `GET /api/search` | The retrieval path. `q`, `mode`, `limit`, `raw` (defaults `true`). |
| `GET /api/status` | Counts, index size, last-indexed, indexed roots, stale-document count. |
| `POST /api/index` | Starts an indexing job (202 + job id); mutually exclusive with reset. |
| `GET /api/index/events` | SSE progress stream — phases, coalesced counts, heartbeats, terminal event. |
| `POST /api/index/cancel` | Cancels the running job; partial work stays committed and searchable. |
| `POST /api/index/remove-root` | Forgets an indexed root — the recourse for a root deleted from disk. |
| `POST /api/reset` | Wipes the index behind an explicit confirm. |
| `POST /api/open` | Opens an indexed file in its default app. |

`eval/verify_served.py` re-runs the whole eval through `GET /api/search` and asserts the served rankings are byte-identical to calling `hybrid_search` directly — the seam is checked, not assumed.

---

## Measured results

Every claim below is reproducible with `eval/run_eval.py` over a 300-file labeled corpus and 83 queries (80 scored, 3 expected-empty), with `today` pinned to `2026-07-07`.

**Retrieval matrix** (raw mode, 2026-08-02, all rows measured together on the shipped stack so they're comparable to each other):

| mode | ndcg@10 | mrr | recall@10 | p@5 |
|---|---|---|---|---|
| keyword (BM25) | 0.1835 | 0.2025 | 0.1792 | 0.0625 |
| hybrid (RRF) | 0.6531 | 0.6317 | **0.7963** | 0.2650 |
| **hybrid + CE rerank** (shipping) | 0.6280 | 0.6331 | 0.7442 | 0.2300 |
| semantic (vector) | **0.6994** | **0.7177** | 0.7817 | **0.2700** |

**The keyword row is a strawman** — raw mode feeds a whole sentence to FTS5, which ANDs every term, so most keyword queries return nothing. The keyword-vs-hybrid gap is inflated and shouldn't be quoted.

### Indexing filenames: +0.068 ndcg@10, and it's all in one place

Until 2026-08-02 a filename was only reachable as part of the raw absolute path in one unweighted FTS column, and the semantic arm never saw it at all. Three things changed — a `filename` FTS column holding the *humanized* name (camelCase and letter/digit boundaries split, directories and extension dropped), that same text embedded as **its own vector chunk**, and the cross-encoder shown the name alongside the snippet. Ablated one at a time from the old configuration:

| configuration | ndcg@10 | recall@10 | mrr |
|---|---|---|---|
| before (flat weights, no name chunk, name-blind reranker) | 0.5601 | 0.6844 | 0.5427 |
| + BM25 column weights | 0.5601 | 0.6844 | 0.5427 |
| + filename vector chunk | 0.6169 | 0.7567 | 0.6039 |
| + name-aware reranker (shipping) | **0.6280** | 0.7442 | **0.6331** |

**The vector chunk is the entire effect.** In the semantic arm alone it is worth **+0.206 ndcg@10** (0.4939 → 0.6994) — by far the largest single retrieval change ever measured in this project. The BM25 column weights, by contrast, changed **nothing**: swept from flat 1.0 to 32× filename, including dropping the path column to zero, every setting produced identical hybrid numbers, because RRF consumes only rank order and the cross-encoder re-scores the top 20 from scratch. They are kept only for `--mode keyword`, which has no reranker downstream and does improve (0.1776 → 0.1835).

**It is not a uniform improvement: 25 of 80 queries got better, 19 got worse, 36 didn't move.** The gains are large and the losses are small, which is why the average moves so far, but the losses have a shape worth knowing. Filenames help when a name is *distinctive* (`anscombe quartet`, `excel file tracking marathon training`, and every `filename-only` query — that tag went 0.3155 → **1.0000**; the `csv` tag, whose files have descriptive names and unreadable bodies, went 0.3385 → 0.6942). They hurt when many files share a name prefix and something *other than the name* is meant to discriminate: `"contracts from 2023"` has seven `contract-*.docx` files that now all match strongly on the name, crowding out the two the date labels want — and in raw mode there is no date filter to break the tie. Boosting names necessarily boosts near-duplicates that share one.

Two honest caveats:

1. **The eval corpus flatters this change.** Its filenames were authored to be self-describing (`manual-espresso-machine-em500.pdf`, `paper-okapi-at-trec3.pdf`). Real directories are full of `Document (1).pdf` and `IMG_4032.HEIC`, where a filename carries no signal, so **+0.206 is an upper bound**, not an expected value.
2. **The cross-encoder is now net-negative and was not before.** It used to be the only step converting RRF's recall into ranking (+0.072 ndcg over raw fusion). With the name chunk present, raw fusion scores 0.6531 and reranking pulls it *down* to 0.6280 — and plain semantic search beats both at 0.6994. That is not acted on here: this change was about filenames, the reranker's fate deserves its own measurement, and one corpus with unusually descriptive filenames is thin evidence for deleting a component. It is flagged in CONTEXT.md § Up Next as the next thing to settle.

### The headline finding: NL parsing makes retrieval *worse*

The project was premised on the bet that an LLM parsing queries into structured filters would beat feeding the sentence to hybrid retrieval. **The eval measured that bet and it lost.**

Measured 2026-07-14, **before filenames were indexed** — both columns predate that change, so the Δ is still a fair comparison but the absolute values are the old ones:

| mode | ndcg@10 raw | ndcg@10 parsed | Δ |
|---|---|---|---|
| keyword (BM25) | 0.1768 | 0.2163 | **+0.040** |
| semantic (vector) | 0.4937 | 0.4156 | −0.078 |
| hybrid (RRF) | 0.4884 | 0.4246 | −0.064 |
| **hybrid + CE** | **0.5602** | 0.4522 | **−0.108** |

19 of 80 queries went from finding the answer to finding *nothing*, via two verified mechanisms:

1. **Hallucinated hard filters exclude the gold document.** `"ffmpeg convert video"` → `file_types: ['.mp4','.mov','.avi']` — it confused the *topic* of the document with the *type* of it; the answer is a `.md` file and Oasis doesn't even index `.mp4`. Recall → 0.
2. **Distilling the query for the embedder destroys meaning.** `"speech asking citizens to serve their country rather than be served"` → `'civic duty'`, which embeds nowhere near the JFK inaugural. `"rising ocean temperatures are killing the reef"` → `'ocean pollution'`, which is factually a different phenomenon.

The root cause is an **asymmetric payoff, not a bad prompt**: a correct hard filter helps marginally, a wrong one zeroes recall, and a 3B model is wrong often enough that the expected value is strongly negative. No prompt-tuning fixes that shape. So the layer is off by default until it's made soft (score boost, not `WHERE` exclusion) and re-measured — and if it stays negative, it gets deleted with the numbers written up.

Latency is deliberately blank. One shipped-bundle search measured 394 ms server-side on a 300-document index; a real p95 budget has **not** been established, and pretending otherwise is exactly the failure the eval discipline exists to prevent.

---

## Stack

| Layer | Tool |
|---|---|
| Language | Python 3.14+, managed with `pixi` (conda-forge + PyPI under one lock) |
| Keyword index | SQLite + FTS5 (BM25, porter stemmer) |
| Vector index | LanceDB (cosine, embedded) |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` (384-dim) |
| Reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| NL query parsing | Ollama + `instructor` (JSON mode) — off by default |
| CLI | Typer + Rich |
| HTTP service | FastAPI + uvicorn, loopback-only, bearer token, SSE for progress |
| macOS app | SwiftUI (25 files), menu-bar resident, spawns the server as a child |
| Packaging | PyInstaller `--onedir`, embedded in `Oasis.app/Contents/Resources/` |
| Inference device | CPU by default (conda-forge torch, OpenBLAS); override with `OASIS_DEVICE` |
| Eval | ranx over a labeled corpus; results appended to `eval/results/history.jsonl` |
| Tests | pytest — **988 tests**, all passing (985 fast + 3 that load real models) |

Run them with `pixi run -e dev pytest` (fast) or `pixi run -e dev pytest -m ''` (everything).

---

## Design decisions

A few of the trade-offs that shaped the project:

- **Reciprocal Rank Fusion instead of score normalization.** BM25 and cosine distance are on different scales; rank-based fusion sidesteps any needed calibration entirely, with the constant k=60 being the standard for ranking.
- **Cross-encoder rerank only on the top ~20.** Cross-encoders are pretty accurate but slow, so reranking a small candidate pool gets a large majority of the quality benefit at a fraction of the latency.
- **One engine, three front-ends.** The CLI, the HTTP service, and the app are thin shells over the same retrieval code. When the CLI was found carrying its own drifted copy of the search path, it was deleted rather than maintained.
- **Local LLM for NL parsing, not cloud.** Privacy is a feature, as people are working with their own files. The Claude API path that once existed was removed outright — offline operation is a product requirement, not a setting.
- **Schema-first query parsing.** The LLM fills in a typed `ParsedQuery` (Pydantic), not free-form JSON. Validation therefore catches bad outputs before they reach the retrieval layer.
- **Partial-success extraction.** A bad page in a PDF doesn't kill the whole document (as formatting in PDFs is wacky sometimes) — failures are caught per-page, logged, and tracked in `extraction_errors` on the extracted document.
- **CPU inference on conda-forge torch, not the PyPI wheel.** Every stock macOS-arm64 torch wheel links Apple's Accelerate BLAS, whose SGEMV path returns *all-NaN* cross-encoder logits on this macOS — and NaN scores don't raise, they silently degrade reranking to no reranking. conda-forge's OpenBLAS build fixes it, which is the entire reason the project is on `pixi`.
- **No App Sandbox.** The app is directly distributed, not App Store. Sandboxing is mandatory for the Store and nothing else, and it forbids exactly the two things this app is: spawning a server child outside its bundle, and indexing arbitrary user-chosen folders. `ENABLE_APP_SANDBOX = NO` is architecture, not debt.

---

## Roadmap

**Next up**
- **Signing, notarization, and a DMG** — the last gap between "builds on my machine" and "a stranger can download it".
- **Make the NL filters soft, or cut the layer.** Convert hard `WHERE` exclusions into post-hoc score boosts, embed the user's actual words instead of the distillation, try a larger parse model — then re-run the matrix and take the decision either way.
- **Fix `ensure_ollama()`'s health check** — it currently calls a provider "available" if the server answers HTTP and lists the model, which a provider that 500s on every inference also does. It should do one tiny real completion.
- **First-run Full Disk Access flow**, and a prompt for `reindex_recommended`.

**Later**
- Background, incremental indexing via FSEvents, surviving reboots.
- Sparkle auto-update.
- Core ML / MLX embeddings so the bundle is smaller and faster on Apple Silicon.
- More formats — email (`.mbox`, `.msg`), images via CLIP, code via tree-sitter.
- OCR fallback for scanned PDFs.
- Per-directory `.gitignore` loading (currently root-level only).
- Latin-1 text files (the extractor is UTF-8 only).

**Explicit non-goals:** no cloud sync, no hosted index, no remote LLM; not multi-user; not mobile; not the Mac App Store; not competing with Spotlight on exact-filename lookup.
