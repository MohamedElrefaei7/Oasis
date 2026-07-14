# Oasis

A natural-language file search tool with hybrid keyword + semantic retrieval.

## Stack
- Python 3.14, managed with `uv` (`requires-python = ">=3.14"`, ruff targets `py314` — keep these in sync)
- SQLite + FTS5 for keyword index and metadata
- LanceDB for vector store
- sentence-transformers (all-MiniLM-L6-v2) for embeddings
- Ollama (local) for natural language query parsing — **local only**. The Anthropic API path was removed; `anthropic` is not a dependency and there is no `llm/claude.py`. Do not reintroduce a cloud provider: running entirely offline is a product requirement, not an implementation detail.
- Typer + Rich for CLI
- FastAPI for the local HTTP API (`oasis serve`) — **specced, not yet built**; see § HTTP API. The client is a native SwiftUI app that spawns the server as a child process. There is no web UI and no HTML routes; every endpoint returns JSON (or SSE).
- pytest for tests, ruff for lint/format

## Architecture
- `src/oasis/extractors/` — one module per file format, uniform `Extractor` interface
- `src/oasis/index/` — indexing pipeline, change detection, both index backends
- `src/oasis/query/` — NL query parser, hybrid retrieval, reranking, score fusion
- `src/oasis/cli/` — Typer commands
- `tests/` — pytest, fixture files under `tests/fixtures/`

## Conventions
- Use Python 3.14+ syntax: built-in generics (`list[str]`, not `List[str]`), `X | None` not `Optional[X]`.
- Type hints everywhere. Use Pydantic for any data passed across module boundaries.
- Prefer pure functions; isolate side effects (filesystem, API calls) in dedicated modules.
- No print statements in library code — use Python's `logging` module.
- Tests for any new extractor or query parser change.

## Don't
- Don't add async until there's a measured need.
- Don't introduce new dependencies without checking if existing ones cover it.
- Don't write to the database directly from CLI handlers — go through the index layer.

## HTTP API

Spec for the `oasis serve` command — **not yet implemented**. Intended home: `src/oasis/api/` (`app.py`, `schemas.py`, `jobs.py`), mirroring the `cli/app.py` pattern. Response schemas are Pydantic models per the usual cross-boundary-data convention.

**The consumer is a native SwiftUI app that spawns this server as a child process and manages its lifetime.** That target — not a browser — drives every decision below. The server is a long-lived local service, not the CLI with a different transport, and the two differ in ways that matter:

| CLI assumption | Why it breaks as a service |
|---|---|
| Load models per invocation | A process that lives for hours must load once, at startup, and say when it's ready |
| `ensure_ollama()` on every search | Shells out to `ollama list` — a subprocess spawn per query |
| Block the terminal while indexing | `URLSession` times out at 60s by default; first-time `~/Documents` index is minutes |
| `last_results.json` for `open N` | Server-side session state; the client already has the paths |
| One thread, one connection | FastAPI runs `def` endpoints in a threadpool → `check_same_thread` errors |

### Process handshake (stdout port/token)

`oasis serve [--port N] [--db PATH]` binds Uvicorn to `127.0.0.1` only — never `0.0.0.0`, consistent with "local by default."

- **Port** — omit `--port` (or pass `0`) to bind an OS-assigned ephemeral port; pass an explicit port to pin one. Ephemeral-by-default lets multiple instances coexist and avoids a fixed port to collide on.
- **Token** — a fresh `secrets.token_urlsafe(32)` generated once per process start. Never written to disk, never reused across restarts.
- **Handshake line** — after the socket binds and before serving, the process writes exactly one line of JSON to stdout and flushes:
  ```json
  {"port": 51423, "token": "kx7F...", "pid": 40221}
  ```
  This is the only machine-readable line `oasis serve` ever writes to stdout; everything after is human-readable logging. The Swift app reads up to the first `\n` off the child's pipe and has everything it needs to talk to the server.
- **Bind before you print.** To emit a real port, create the socket yourself — `socket.socket()` → `bind(("127.0.0.1", 0))` → `getsockname()[1]` → print handshake → `uvicorn.Server(config).run(sockets=[sock])`. Letting Uvicorn bind and then trying to read the port back off `server.servers[0].sockets[0]` is racy: there's no ordering guarantee that the socket exists before you look.
- **Auth** — every request carries `Authorization: Bearer <token>`. Missing or mismatched → `401`. The one exception is `GET /api/health` (below). Loopback binding alone isn't authn — other local users and processes on a shared machine can reach `127.0.0.1` — so the token is what actually protects the API.
- **Uvicorn's access log is disabled** (`access_log=False`). It logs `GET /api/search?q=... HTTP/1.1` per request, which puts **every search query the user types into the child's stdout** — the pipe the Swift parent holds, and from there potentially Console.app or a crash report. It never leaves the machine, so it isn't a security hole, but "local by default, no telemetry, privacy is a feature" is the product's whole pitch, and users' search text reveals both what's in their files and what they're anxious about finding. If request logging is wanted later, log method + path only and drop the query string. Error/lifecycle logging stays on.

**Parent-death watchdog.** The handshake hands the parent a `pid` so it can manage the child; this is the child's half of that contract. If the parent dies first — SwiftUI app crashes, force-quit, or killed in the Xcode debugger — the Python process survives holding SQLite and LanceDB handles, and the next launch spawns a *second* server against the same DB. This will happen repeatedly during development, not just in theory.

A daemon thread polls `os.getppid()` every second and calls `os._exit(0)` when it returns `1` — the parent has been reaped and the process re-parented to launchd. Ten lines, and it makes the server safe to orphan.

**The watchdog is gated on `--managed` (or `OASIS_MANAGED=1`), which the Swift app passes when it spawns the child.** Not on `--port`. Port pinning and parent management are orthogonal concerns, and conflating them breaks the case you'll actually want: debugging the Swift app against a fixed port, where you need *both* a pinned port and a live watchdog. An explicit flag says what it means — "I have a parent that owns my lifetime" — instead of inferring it from an unrelated setting.

### Model lifecycle

**This section exists because the obvious implementation is the wrong one.** Loading models inside the request handler is what you get by default, and it means the first search after app launch takes 5+ seconds — the user's first impression of the product. Load once, at startup, and tell the client when you're done.

Owned in app state, initialized exactly once:

| Object | Note |
|---|---|
| `OasisConfig` | `load_config()` once; `db_path` fixed for process lifetime |
| `SentenceTransformerEmbedder` | ~seconds to load; `_MODEL_CACHE` makes it once-per-process anyway |
| `CrossEncoderReranker` | same |
| `VectorIndex` | holds an open LanceDB table handle |
| `OllamaProvider \| None` | **`ensure_ollama()` runs once at startup, not per search.** The CLI calls it on every search, which is fine for a one-shot process and pathological for a server — it spawns `ollama list` per query. Cache the provider; cache `None` too, so an absent Ollama doesn't re-probe on every request. |

Both models are then **warmed with a throwaway inference** (`embed(["warmup"])`, `rerank("warmup", [one dummy result])`). Load time and first-inference time are separate costs — lazy kernel init, weight paging — and warming folds both into startup where the user is already waiting.

**Load on a background thread, not inline in lifespan.** Uvicorn does not accept connections until lifespan startup returns, so doing the work synchronously in the lifespan body makes `/api/health` *connection-refused* for the whole load — which defeats the point of having a `loading` state at all. Instead: lifespan spawns a daemon thread that loads + warms and sets a `threading.Event` when finished; lifespan returns immediately; `/api/health` reports `loading` until the event is set. Shutdown joins the thread and closes handles.

If loading fails (corrupt model cache, no disk), the thread records the error and health reports `status: "error"` with a message — the Swift app can then surface something better than a spinner that never stops.

### Concurrency model

**Every endpoint is `def` — except `/api/index/events`, which is `async def`.** That's one rule with one principled exception, not a rule with a hole in it.

- **Why `def` for everything else.** Every other handler does blocking, CPU-bound work — torch inference, SQLite, LanceDB. In `async def` that work runs *on the event loop*, stalling every other request and serializing the whole server. It's the most common FastAPI mistake and it's invisible until a second client shows up. `def` handlers run in Starlette's threadpool, which is exactly what blocking code wants.
- **Why `async def` for SSE.** The same reasoning inverts: SSE isn't CPU-bound, it's a long-lived *wait*, which is precisely what an event loop is for. A `def` SSE generator holds an anyio threadpool slot (default capacity 40) for the entire life of the stream, and blocking `queue.get()` inside it is a workaround for a problem that shouldn't exist. Use `async def` over an `asyncio.Queue`, fed from the index worker thread via `loop.call_soon_threadsafe(q.put_nowait, event)`. Disconnect detection comes from `await request.is_disconnected()`, heartbeats from an `asyncio.wait_for` timeout, and it consumes zero threadpool capacity. The subscriber-set fan-out design is unchanged.

#### SQLite
- **Connections are thread-local.** `sqlite3` objects raise on `check_same_thread` when touched from a thread other than the creating one, and the threadpool guarantees that the moment two requests overlap. Hold connections in a `threading.local()`, opened on first use per thread via `open_db()`. Do *not* pass `check_same_thread=False` and share one connection — that trades a loud error for silent races.
- **WAL already permits concurrent readers** (`open_db` sets `journal_mode=WAL`), so N reader threads is fine as-is.
- **Writes are serialized behind the job lock.** Indexing is the only writer and only one job runs at a time, so there's no write-write contention to design around.

#### LanceDB — verified, not assumed
Measured against **lancedb 0.30.2** (two reader threads issuing `search`/`count` against a shared `VectorIndex` while a writer thread ran 200 sequential `merge_insert` calls):

| Question | Result |
|---|---|
| Concurrent reads safe during `merge_insert`? | **Yes** — 689 reads, zero exceptions |
| Does a **shared** handle see the writer's new rows? | **Yes** — readers watched `count()` climb 12 → 210 mid-write |
| Does a **separately opened** handle see them? | **No** — pinned at open (reader stuck at v2/5 rows while writer reached v3/55) |
| Can a pinned handle catch up? | **Yes** — `table.checkout_latest()`, verified 5 → 55 |

Consequences for this design:

- **Search-during-index returns fresh results — but only because app state shares the one handle the index job writes through.** The writing handle advances its own version pointer; readers on that same object ride along. This works by construction, not by luck, but it *is* load-bearing.
- **Do not make `VectorIndex` thread-local.** This is the trap: the SQLite rule above tempts you into it by analogy, and it would be silently, permanently wrong. Each thread's handle would pin at startup, and every search would return results frozen at process launch — **no error, no exception, just stale answers forever**. The exact opposite of the SQLite rule, for the exact opposite reason. One shared `VectorIndex` in app state, always.
- **`checkout_latest()` is the escape hatch** if a code path ever ends up holding a handle it didn't write through (a second `VectorIndex`, a handle surviving `reset`). Prefer not creating that situation.
- **Model objects are shared, not per-thread.** sentence-transformers and CrossEncoder are safe for concurrent `encode`/`predict`; loading per thread would blow up memory for no benefit.

**Regression test (required):** start an index job, fire searches concurrently, assert newly-indexed content becomes findable before the job finishes. That test is what keeps a future "let's make VectorIndex thread-local for consistency with SQLite" refactor from quietly breaking search.

### Endpoints

All routes are prefixed `/api`. Responses are `application/json` (except SSE); POST bodies are JSON.

| Method | Path | Auth | Response |
|---|---|---|---|
| `GET` | `/api/health` | **no** | `HealthResponse` |
| `GET` | `/api/status` | yes | `StatusResponse` |
| `GET` | `/api/search` | yes | `SearchResponse` |
| `POST` | `/api/index` | yes | `202` + `JobResponse` (or `409`) |
| `GET` | `/api/index/events` | yes | `text/event-stream` |
| `POST` | `/api/index/cancel` | yes | `202` (or `404`) |
| `POST` | `/api/reset` | yes | `204` |
| `POST` | `/api/open` | yes | `204` |

**No `/api/v1`, deliberately.** Client and server are PyInstaller'd into the same `.app` and ship as one artifact, so they cannot skew — there is never an old client talking to a new server, which is the only thing a version prefix buys. Adding one would be cargo cult. Revisit only if the server ever ships separately from the app (a Homebrew formula, a shared daemon across apps).

#### Wire conventions

**Datetimes: ISO 8601 with an explicit UTC offset, always.** Every datetime on the wire — `last_indexed_at`, `parsed.date_range.after`/`.before` — is serialized as UTC with a `Z`/`+00:00` designator. Never naive.

This is a boundary concern, not an internal one. Keeping naive `datetime` inside the parser is *right* and stays: SQLite `mtime` is a UTC Unix timestamp, and naive avoids implicit timezone arithmetic (already recorded in Key Decisions). But a naive datetime is wrong on the wire, because Swift's `JSONDecoder.DateDecodingStrategy.iso8601` uses `ISO8601DateFormatter` with `.withInternetDateTime`, which **requires** a timezone designator — `"2026-06-01T00:00:00"` simply fails to decode. Two conventions in one response is worse still: `/api/status` sending `Z` while `parsed.date_range` sends naive means no single decoding strategy works, and the first date chip the app tries to render is where you'd find out.

Attach UTC at serialization time (a Pydantic `field_serializer` on the API schema, not a change to `ParsedQuery`) so the Swift side gets one uniform strategy.

#### Error envelope

Every error, from every endpoint, is:
```jsonc
{ "error": { "code": "...", "message": "..." } }
```
with the matching status: `400` bad input, `401` bad/missing token, `404` not found, `409` conflict, `410` gone, `422` validation, `500` unexpected, `503` still loading.

**FastAPI will not do this on its own** — the envelope only holds if three exception handlers are registered:

| Source | FastAPI's default shape | Handler |
|---|---|---|
| `HTTPException` | `{"detail": "..."}` | normalize to envelope |
| `RequestValidationError` (the `422`) | `{"detail": [{...}, {...}]}` — a *list* | flatten to one message |
| Unhandled `Exception` | HTML traceback, or a bare `500` | envelope + log, never leak internals |

The `422` is the one to watch: it's the only error FastAPI generates without being asked, so it's the one that slips through and it's the one whose shape is least like the envelope. **Test it explicitly** — assert a deliberately malformed request comes back in envelope shape, not just that it's a 422. Otherwise the single shape the Swift decoder is written against is precisely the shape your validation errors don't use.

#### `GET /api/health`
The readiness probe. **No auth** — it's the endpoint the Swift app polls immediately after reading the handshake, before it can meaningfully do anything else, and it must work while the app is still deciding whether the server came up at all. Safe to leave open because it exposes nothing sensitive: no paths, no query text, no results. (`db_path` deliberately lives on `/api/status`, which does require the token.)

```jsonc
{
  "status": "loading",   // "loading" | "ready" | "error"
  "version": "0.1.0",    // importlib.metadata.version("oasis")
  "documents": 1042,     // null while loading, or when no index exists
  "error": null          // message when status == "error"
}
```
Always `200` — `status` carries the state. The Swift app keeps its search box disabled until `status == "ready"`.

Every other endpoint returns `503` while `status != "ready"`, rather than blocking until models finish. An honest "not yet" beats a request that hangs for 8 seconds.

#### `GET /api/status`
`404` if no index exists at the configured `db_path`.
```jsonc
{
  "documents": 1042,
  "db_size_bytes": 88473600,
  "last_indexed_at": "2026-07-10T14:32:00Z",  // ISO 8601, null if never indexed
  "db_path": "/Users/you/.oasis/index.db"
}
```

#### `GET /api/search`
| Param | Type | Default | Notes |
|---|---|---|---|
| `q` | `str` | required | raw query text |
| `mode` | `"keyword" \| "semantic" \| "hybrid"` | `"hybrid"` | same `SearchMode` enum as the CLI |
| `limit` | `int` | `10` | mirrors `DEFAULT_TOP_N` |
| `raw` | `bool` | `false` | skip NL parsing, same as `--raw` |

**Bad FTS5 syntax `400`s in `keyword` mode only.** `hybrid` degrades to semantic-only and returns `200` with results; `semantic` never parses the query as an expression, so it can't hit this at all.

This follows from `hybrid_search`'s arms failing independently (fixed in `query/retriever.py`; they used to share one `try`, so an apostrophe in the query took the semantic arm down with it and the whole search returned nothing). Hybrid has a working fallback, so a broken keyword arm is a degradation, not an error — surfacing it as `400` would tell the Swift app the search failed when it has perfectly good results to render. Keyword mode has nothing to fall back to, so it still `400`s with the "wrap phrases in double quotes" tip in `error.message`.

`500` only when **both** arms fail — `hybrid_search` re-raises the keyword error in that case, since nothing survived.

```jsonc
{
  "results": [
    {
      "path": "/Users/you/Documents/q3-report.pdf",
      "title": "Q3 Revenue Report",
      "doc_id": 88,
      "score": 0.0163,
      "snippet": [
        {"text": "revenue", "match": true},
        {"text": " grew 12% in Q3 driven by ", "match": false},
        {"text": "enterprise renewals", "match": true}
      ]
    }
  ],
  "mode": "hybrid",
  "parsed": {
    "semantic_query": "machine learning",
    "file_types": [".pptx"],
    "date_range": {"after": "2026-06-01T00:00:00Z", "before": "2026-07-01T00:00:00Z"},
    "folders": [],
    "keywords": [],
    "confidence": 0.9
  },
  "llm_parsed": true,
  "latency_ms": 147.2,
  "db_path": "/Users/you/.oasis/index.db"
}
```
`results: []` (not `404`) on zero matches — an empty result set is a valid answer, not an error.

- **`parsed`** — the full `ParsedQuery`, not just the `llm_parsed` boolean. It's free (the object already exists) and it's what lets the Mac app render filter chips — `.pptx · last month` — showing the user what Oasis understood from their sentence. That turns the NL parsing from opaque magic into something legible and correctable. Always present; when `llm_parsed` is `false` it's the fallback `ParsedQuery(semantic_query=q)`.
- **`llm_parsed`** — did the LLM actually run (mirrors the CLI's `·  parsed` footer badge). Distinct from `parsed` being non-null, same as in `cli/app.py`.
- **`latency_ms`** — server-side, measured around retrieval only, warm. This is the honest number for the README benchmarks table: timing the CLI measures model loading, which a running server never pays. Add it now — retrofitting it means re-running the whole eval to regenerate the numbers.

#### `POST /api/index`
```jsonc
{ "path": "/Users/you/Documents", "force": false }
```
**Returns `202` immediately with a job handle — it does not block.** The CLI's blocking behavior is wrong here, and the "indexing is incremental so re-runs are fast" rationale only covers re-runs. The case that decides whether the app feels good is first-time indexing of `~/Documents` on first launch: minutes, not seconds. Blocking means (1) `URLSession`'s default 60s `timeoutIntervalForRequest` kills it outright, (2) no progress — a ten-minute spinner is indistinguishable from a hang, and (3) no cancel.

```jsonc
{ "job_id": "b1f4...", "status": "running" }
```
- Single-job lock: `409` with the in-flight `job_id` if a job is already running. One writer, always.
- `400` if `path` doesn't exist or isn't a directory.
- The job runs on a background thread; progress goes out over SSE (below). The pipeline's existing `on_file` and `on_chunks_progress` callbacks publish to the subscriber queues via `loop.call_soon_threadsafe` — that callback design was already right, and blocking would throw the data away.

**`permission_denied` is a distinct stat, not a `failed`.** This is the first thing every real user hits: the app launches, spawns the server, indexes `~/Documents`, and macOS denies it because Full Disk Access was never granted. Folded into `failed: N` it's indistinguishable from a corrupt PDF, so the app can only say "indexed 0 files" — which reads as *Oasis is broken* rather than *Oasis needs permission*, and the user has no idea the fix is two clicks away in System Settings.

A separate `permission_denied: int` counter lets the Swift app detect `indexed == 0 and permission_denied > 0` and show the Grant Full Disk Access flow instead of a useless empty state. The counter appears in the pipeline stats dict, the `done` event, the `snapshot` event, and `IndexResponse`; the CLI surfaces it in its summary line.

**Implemented (`index/walker.py`, `index/pipeline.py`).** It was not the one-line `except PermissionError` it looks like — two things had to change, and both were verified against a real `chmod 000`:
1. **`walk()` takes an `on_error` callback**, forwarded to `os.walk(onerror=…)`. Without it `os.walk` *silently swallows* directory-level errors, so an unreadable tree yields nothing and is indistinguishable from an empty one. This is the case that matters: **macOS denies Full Disk Access at the directory level**, so the files are never yielded and a per-file handler never fires. Measured: a locked directory produced 0 files and 0 errors before the change.
2. **`doc is None` is probed, not assumed to mean "broken."** The Extractor protocol *requires* extractors to swallow their own I/O errors and return `None` (`text.py` catches `Exception`), so `PermissionError` never reaches the pipeline for the read path — a `chmod 000` file returned `None` and was counted as `failed`. `_is_unreadable(path)` re-opens the file on the failure path only (cheap: it never runs on the happy path) to tell "can't read" from "read fine, content is broken."

`except PermissionError` also guards `path.stat()` and `extractor.extract()`, ahead of the broad handlers — `PermissionError` is an `OSError`, so the existing `except OSError` would otherwise claim it first.

#### `GET /api/index/events`
`text/event-stream`. **The one `async def` endpoint** (see Concurrency model). Emits a `snapshot` event immediately on connect reflecting current state, then streams. **Re-attach is a first-class case, not an afterthought** — the app gets backgrounded, the connection drops, the user comes back and needs to see the job that's still running. The snapshot-first design means a late subscriber is in the same position as one that connected at `t=0`.

```
event: snapshot
data: {"job_id": "b1f4...", "status": "running", "phase": "scan", "done": 214, "total": null}

event: progress
data: {"job_id": "b1f4...", "phase": "scan", "done": 215, "total": null, "path": "/Users/you/Documents/notes.md"}

event: progress
data: {"job_id": "b1f4...", "phase": "embed", "done": 1280, "total": 4310, "path": null}

event: done
data: {"job_id": "b1f4...", "indexed": 812, "skipped": 220, "unsupported": 8, "failed": 2, "permission_denied": 0, "chunks": 4310}

: ping
```
- **`total` is `null` during `scan`.** The walk is a lazy generator, so the file count isn't known until it finishes — the same reason `cli/app.py` gives the scan task `total=None` and only the embed task a real total. Don't fake it; the client renders indeterminate for scan, determinate for embed.
- **Terminal events**: `done` (with the final stats dict), `cancelled`, or `error` (`{code, message}`). The stream closes after any of them.
- **Fan-out**: each subscriber gets its own `asyncio.Queue` off a subscriber set, so N connections don't steal each other's events.
- **Thread → loop**: the index job runs on a worker thread and must not touch an `asyncio.Queue` directly. It publishes via `loop.call_soon_threadsafe(q.put_nowait, event)`, capturing the loop with `asyncio.get_running_loop()` when the job starts. This is the only thread/loop boundary in the server — keep it in one place (`jobs.py`).
- **Heartbeat**: `await asyncio.wait_for(q.get(), timeout=15)`; on `TimeoutError` emit a `: ping` comment and loop. Dead connections surface promptly and intermediary buffering can't stall the stream.
- **Disconnect**: `await request.is_disconnected()` each iteration; drop the subscriber's queue from the set on exit so a backgrounded app doesn't leak an unbounded queue behind it.
- If no job is running, the snapshot reports the last completed job (or `status: "idle"`) and the stream closes rather than hanging open.

**Progress events are coalesced in the publisher — do not emit one per callback.** `on_file` fires once per file, and a first-time index of `~/Documents` can be 100k files. That's 100k SSE events driving a progress bar that redraws at 60fps at best: pure waste, and the producer outruns the consumer, so a slow client grows an unbounded `asyncio.Queue` behind it until something dies. The callback keeps the *latest* progress state and publishes at most every ~100ms (roughly 6 frames — well under redraw budget and invisible to the user). Terminal events (`done`/`cancelled`/`error`) always publish immediately and are never coalesced. As a backstop, subscriber queues are bounded (`asyncio.Queue(maxsize=…)`): on overflow, **drop intermediate `progress` events, never terminal ones** — a stale progress number self-corrects on the next tick, a dropped `done` hangs the client's spinner forever. Progress is lossy by nature; completion is not.

#### `POST /api/index/cancel`
```jsonc
{ "job_id": "b1f4..." }
```
`202` if the running job matches; `404` if no job is running or `job_id` doesn't match the in-flight one (guards against cancelling a job the client didn't know had already finished and been replaced). Sets a `threading.Event`; the pipeline checks it between files and aborts cleanly, emitting `cancelled` over SSE with the partial stats. Work already committed stays committed — indexing is incremental, so a cancelled run just means the next one picks up the rest.

> **Pipeline support for this is already in place** — `index_directory()` takes `cancel: threading.Event | None = None` and checks it in the per-file loop and between embed batches, returning partial stats. Committed work stays committed; indexing is incremental, so the next run resumes where the cancelled one stopped.

#### `POST /api/reset`
```jsonc
{ "confirm": true }   // required — replaces the CLI's interactive typer.confirm prompt
```
`400` if `confirm` isn't `true` (no interactive prompt over HTTP, so confirmation must be explicit). `409` if an index job is running — reset takes the same single-job lock. `404` if no index exists. `204` on success.

Deletes the SQLite DB, its `-wal`/`-shm` companions, and the `.lance` directory. **Then rebuilds app state's `VectorIndex`** — the cached instance holds a handle to a table that no longer exists. Construct a new `VectorIndex` (which re-runs `create_table(exist_ok=True)`); `checkout_latest()` is not enough here, since the versions it would check out are gone with the directory. Same for the thread-local SQLite connections: mark them stale so each thread reopens on next use.

#### `POST /api/open`
```jsonc
{ "path": "/Users/you/Documents/q3-report.pdf" }
```
**Takes a path, not `{"n": 2}`.** An index into "the most recent search" is server-side session state — the `last_results.json` hack, which is a reasonable shortcut for a CLI (one user, one terminal, strictly sequential) and wrong for a server. The client already has the paths from the search response; it should send one. This also makes the endpoint idempotent and order-independent.

**Validate the path against the index before shelling out.** Loopback + token is good, but this hands request input to a subprocess, so treat it as untrusted regardless of who can reach the port:
1. `Path(req.path).resolve()` — normalize away `..` and symlinks.
2. `KeywordIndex.get_doc_id(resolved)` — the path must be a document Oasis actually indexed. This is stricter and simpler than a root-prefix check: an exact lookup against the `documents` table has no prefix arithmetic to get subtly wrong (`/Users/you/Documents` must not match `/Users/you/Documents-private`), and it reuses a method that already exists.
3. Only then `subprocess.run(["open", str(resolved)], check=False)`.

Status codes fall out of that cleanly: `404` if the path isn't in the index (never was a result), `410` if it is indexed but the file is gone from disk (was a result, has since moved) — worth keeping distinct, since the second case is the one where the app should offer to re-index. `204` on success.

### Snippet format (segments)

Keyword/hybrid retrieval marks matches with `MATCH_START`/`MATCH_END` sentinels (`\x02`/`\x03`, see `KeywordIndex`) so the CLI can render bold-yellow via Rich; semantic snippets get the same markers from `text_snippet()` → `_highlight_terms()`. Those control characters have no place on the wire, so the API sends **an ordered list of segments** — one shape for all three search modes:

```jsonc
"snippet": [
  {"text": "revenue", "match": true},
  {"text": " grew 12% in Q3 driven by ", "match": false},
  {"text": "enterprise renewals", "match": true}
]
```

**Segments, not `{start, end}` offsets.** Offsets look simpler and aren't. An earlier draft of this spec carried `{"start": 42, "end": 52}` for `"renewals"` in exactly the example above — off by two (`text[42:52]` is `"e renewals"`; the real spans are `[33,43)` and `[44,52)`). It was hand-written, read twice, and shipped wrong, which is the whole argument: offset math is unreviewable by eye and desynchronizes silently. Segments have no offsets to get wrong, and they dodge the encoding question entirely — Python indexes by codepoint, Swift's `String` by grapheme cluster, `NSRange`/`AttributedString` by UTF-16, so *any* integer offset needs a conversion on the Swift side (`text.unicodeScalars.index(_:offsetBy:)`), sitting precisely where nobody tests: emoji in filenames, accented text, CJK. Both consumers fold over the list instead — Rich appends styled runs, `AttributedString` appends attributed runs.

Canonical form:
- Concatenating every `text` in order reproduces the snippet exactly (sentinels stripped).
- No empty segments.
- No two adjacent segments share the same `match` value — merge them.
- An unmatched snippet is a single `match: false` segment; an empty snippet is `[]`.

**Property test (required).** The conversion from sentinel string → segments gets a property test, not just examples:
- `"".join(s.text for s in segs)` equals the input with `MATCH_START`/`MATCH_END` removed.
- Round-trip: re-inserting sentinels around every `match: true` segment reproduces the original raw string byte-for-byte.
- Every `match: true` segment is non-empty and corresponds to a real sentinel-delimited span.
- No adjacent segments with equal `match`.

Run it over **real Unicode, not just ASCII** — CJK, emoji (including ZWJ sequences), combining marks — plus the degenerate cases: match at position 0, match at end of string, adjacent matches with no gap, unbalanced sentinels, empty text. This is the class of bug that a reader will not catch and a generated case will.