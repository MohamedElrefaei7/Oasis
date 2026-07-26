# App ↔ Server Seam

The contract the Swift app implements to spawn, handshake with, and stay coherent
against the `oasis serve` child process. This is the one place the two languages
meet, and where the Swift risk concentrates: the app spawns the server, reads a
one-line handshake off stdout, and has to survive a background model-load window
of **tens of seconds** before search works.

**Every claim here marked _measured_ was verified by an actual spawn on this
machine (2026-07-17, `service-layer-complete` / commit `734a84c`), transcripts at
the bottom.** Claims marked _reasoned_ are read from `src/oasis/api/serve.py` /
`app.py` and not independently forced.

---

## 1. The spawn _(measured)_

```
oasis serve --managed [--port N] [--db PATH]
```

- The shipped app spawns the **bundled `oasis` executable directly** (a
  PyInstaller binary), not `pixi run oasis` — no wrapper process between the app
  and the server, so stdout carries only what the server writes.
- **`--managed` arms the parent-death watchdog** (§5). The app always passes it.
- `--port` omitted (or `0`) → an OS-assigned ephemeral loopback port, so multiple
  instances never collide. Pass an explicit port only when debugging against a
  fixed one; port pinning and `--managed` are orthogonal.
- `--db` defaults to `~/.oasis/index.db`. The app passes its own container path.
- The server binds **`127.0.0.1` only** — never `0.0.0.0`. Loopback + the bearer
  token (below) is the whole security boundary; there is no network exposure.

## 2. The handshake — _proven by capture (measured)_

After binding the socket and **before** serving, the child writes **exactly one
line** to stdout and flushes:

```json
{"port": 51235, "token": "lp5pg91B7BJ-HNR_2PuxRUZfYE-xZivCDh1_HBsgKN4", "pid": 19805}
```

Measured facts the app's parser depends on, all confirmed across three spawns:

- **The first line on stdout parses as the handshake JSON**, keys exactly
  `{port, token, pid}`. `token` is a 43-char `secrets.token_urlsafe(32)`.
- **Nothing precedes it on stdout, and nothing follows it** until shutdown
  (`stdout_after_handshake = 0` on every spawn). The app reads up to the first
  `\n`, parses it, and never has to read stdout again.
- **All logging goes to stderr** — uvicorn's `Started server process` /
  `Application startup complete`, the HuggingFace `sending unauthenticated
  requests` warning, `Loading weights: …` progress bars, and (on failure) the
  full model-load traceback. The app routes the child's **stderr to a log file**,
  never to the handshake parser.

This is the real risk in "read one JSON line off stdout": if any library or
uvicorn log landed on stdout ahead of or interleaved with the handshake, the
parser would break. It does not. Handshake purity is a hard invariant of
`serve.py` (`sys.stdout.write(handshake)` is the only stdout write; uvicorn is
configured with `access_log=False` and logs to stderr).

**App requirement:** read stdout line-by-line, parse the **first** line as JSON.
If the first line isn't valid handshake JSON, treat it as a startup failure (§6a)
— do not scan further lines hoping the handshake appears later; it never will.

## 3. Readiness state machine — keyed on `/api/health.status`

```
spawning ──► reading handshake ──► polling /api/health ──► ready   (enable search)
                                         │  status:loading  ↘
                                         │  (show warming UI)  error (real error + retry)
                                         └──────────────────────►
```

- `GET /api/health` needs **no auth** and always returns **200**; the state is in
  the `status` field: `"loading" | "ready" | "error"`.
- **`loading` is a normal 200, not a failure.** The models load on a background
  thread (uvicorn accepts connections immediately — that's why the handshake and
  health work at `t_handshake`, long before `t_ready`). The app shows a "warming
  up" UI while `status == "loading"`.
- **Poll health; never block a request against the load window.** A search issued
  before `ready` would sit through the entire model load. With `t_ready` measured
  at **35–54 s** (§4) that is perilously close to `URLSession`'s ~60 s default
  `timeoutIntervalForRequest` — a blocked first request can *time out and read as
  a crash*. Every non-health endpoint returns **503** until ready anyway, so
  polling health is the only correct gate.
- The app keeps the search box **disabled until `status == "ready"`**.

## 4. Measured latencies — real models, this machine _(measured)_

The two numbers the project has never had. `t_handshake` = spawn → handshake line
on stdout; `t_ready` = handshake → first `/api/health` returning `status: ready`
(the warm-load window).

| Spawn | `t_handshake` | `t_ready` | notes |
|---|---|---|---|
| online (HF hub reachable) | 3.25 s | **35.27 s** | includes an HF-hub network check |
| offline (`HF_HUB_OFFLINE=1`, cached) | 2.67 s | **53.84 s** | bundled-app-representative: no network |
| error path (forced) | 1.98 s | 57.79 s → `error` | load fails, not ready |

- **`t_handshake` ≈ 2–3.3 s** — Python interpreter start + import (typer → lazy
  `serve` → fastapi + routers, **no torch**) + socket bind + write. The app can
  read the handshake and start polling health within a few seconds of spawn.
- **`t_ready` ≈ 35–54 s, high run-to-run variance**, dominated by **local**
  weight loading + the throwaway warmup inference (embedder + cross-encoder), not
  network — the offline run was *slower* than the online one, so the HF-hub round
  trip is not the bottleneck. This is a genuinely long warming window and is
  exactly why the background-load + `loading`-state design exists: blocking the
  first request through it would flirt with the URLSession timeout.
- Caveat: these are single-samples-per-config on a developer machine under normal
  load; treat them as an **order-of-magnitude** ("seconds to handshake, tens of
  seconds to ready"), not a p95 budget. A real budget comes later from the
  shipped, bundled app. The architectural conclusion — *warm-up is long enough
  that the app must never block on it* — holds regardless of the exact number.

## 5. Shutdown contract _(watchdog reasoned from `serve.py`; not force-tested)_

- `--managed` starts a daemon thread that polls `os.getppid()` every 1 s and calls
  `os._exit(0)` when it returns `1` — i.e. the parent died and the child was
  re-parented to launchd. This stops an orphaned server from holding the SQLite /
  LanceDB handles and a second launch spawning a duplicate against the same DB.
- **The watchdog is the backstop, not the primary path.** Reparenting to launchd
  (and thus `getppid() == 1`) can lag, so the app should **also terminate the
  child explicitly on quit** (it holds the `pid` from the handshake and the
  process handle). Explicit terminate on quit + watchdog as the safety net.

## 6. Failure modes and required app handling

| # | Situation | Signal | App must |
|---|---|---|---|
| a | Child exits before the handshake (bad env, port bind failure) | EOF on stdout with no valid JSON line; process exits | Surface **"server failed to start"** — don't hang waiting for a line that isn't coming. _(reasoned; the healthy inverse is measured)_ |
| b | Handshake fine, then `status: "error"` | `/api/health` → 200 `status:"error"` with a real `error` message | Show the **real error + a retry**, not a warming spinner forever. **_(measured — forced live, §transcript)_** |
| c | Long load window | `status:"loading"` for tens of seconds | Warming UI; `loading` is **not** failure. **_(measured: 35–54 s)_** |
| d | Child dies after `ready` | process handle reports exit | Detect via the process handle, offer **restart**. _(reasoned)_ |
| e | First-frame index state | `/api/health` `documents` + `reindex_recommended` on first `ready` | Key the first frame off **real index state** (below). **_(measured: fresh DB → `documents:null`)_** |

### 6e — the first-frame index state, and "two states both mean empty"

On first `ready`, `/api/health` carries the hooks for the first-run flows:

- `documents: null` and `reindex_recommended: false` → **never indexed** ("build
  your index" onboarding). _Measured_ on a fresh temp DB: the `.db` file doesn't
  exist yet, so health reports `documents:null, semantic_ready:false,
  reindex_recommended:false`.
- `documents > 0` and `reindex_recommended: true` → an index that predates
  vectors or the current schema → **"reindex recommended"** prompt.
- `documents: 0` → an index that **exists but is empty** — e.g. right after
  `POST /api/reset`. **A wiped index must render identically to a never-indexed
  one** ("build your index"), not as something broken. Note the two shapes that
  both mean empty:
  - **never indexed:** `/api/health` `documents:null`; `/api/status` → **404**.
  - **reset / emptied:** `/api/health` `documents:0`; `/api/status` → **200** with
    `documents:0`.
  The app treats *both* as "no content yet, offer to index," and must not show the
  404 as an error. (`reindex_recommended` is `false` in both — 0 documents is
  "index me," never "reindex me.")

---

## Spawn transcripts (measured evidence)

### Healthy spawn (fresh never-indexed DB)

```
spawn: .venv/bin/oasis serve --managed --db <tmp>/index.db

t_handshake = 3.252s
stdout line 1 (raw): '{"port": 51235, "token": "lp5pg91B7BJ-HNR_2PuxRUZfYE-xZivCDh1_HBsgKN4", "pid": 19805}'
  -> parses as JSON handshake: True  keys=['pid', 'port', 'token']  token=43 chars
t_ready (handshake->ready) = 35.266s
/api/health @ ready: documents=None semantic_ready=False reindex_recommended=False error=None

stdout lines after handshake: 0   ← stdout is exactly one line, the handshake
stderr lines: 10 (logs go here, not stdout):
  +3.268s stderr: INFO:     Started server process [19805]
  +3.268s stderr: INFO:     Waiting for application startup.
  +3.268s stderr: INFO:     Application startup complete.
  +7.399s stderr: Warning: You are sending unauthenticated requests to the HF Hub. …
  +7.848s stderr: Loading weights:   0%|          | 0/103 [00:00<?, ?it/s]
```

### Error path (forced model-load failure: `HF_HUB_OFFLINE=1` + empty cache)

```
spawn: .venv/bin/oasis serve --managed --db <tmp>/index.db
env+: HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HOME=<empty> SENTENCE_TRANSFORMERS_HOME=<empty>

t_handshake = 1.981s
stdout line 1 (raw): '{"port": 49654, "token": "UC6eQp9balT_cXNEtOyaLJO4uZBbuGrgY7sYMXQC_aI", "pid": 19829}'
  -> parses as JSON handshake: True  keys=['pid', 'port', 'token']
t_ready (handshake->error) = 57.786s
/api/health @ error: documents=None semantic_ready=False reindex_recommended=False
  error=We couldn't connect to 'https://huggingface.co' to load the files, and
        couldn't find them in the cached files. …

stdout lines after handshake: 0   ← still clean; the traceback went to stderr
stderr lines: 103:
  +1.992s stderr: INFO:     Started server process [19829]
  +1.993s stderr: INFO:     Application startup complete.
  +5.058s stderr: Model loading failed
  +5.058s stderr: Traceback (most recent call last):
```

Both spawns: **first stdout line = handshake JSON, zero stdout after it, all logs
and tracebacks on stderr** — the seam the Swift app depends on is clean.
