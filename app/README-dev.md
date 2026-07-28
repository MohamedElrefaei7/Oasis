# Running the Oasis app in development

The app spawns `oasis serve --managed` as a child process, reads a one-line JSON
handshake off its stdout, and polls `/api/health` until it's ready. The contract
it implements is [`docs/APP_SEAM.md`](../docs/APP_SEAM.md); that doc is the spec,
this file is just the setup.

Open `app/Oasis/Oasis.xcodeproj`, target **My Mac**, Run.

## The one prerequisite: `OASIS_SERVE_BIN`

The app spawns the server **by absolute path, read from the `OASIS_SERVE_BIN`
environment variable**. If it's unset or doesn't point at an executable, the app
goes straight to its failed state and says so — it does not fall back to `$PATH`,
deliberately: this machine has a stale `oasis` on `PATH` that predates the
`serve` command, and falling back would launch the wrong binary and fail in a
much more confusing way.

It is already set in the **shared** scheme (`Oasis.xcscheme`, committed) to:

```
OASIS_SERVE_BIN = /Users/mohamedelrefaei/oasis/.venv/bin/oasis
```

If your checkout lives elsewhere, edit it under **Product ▸ Scheme ▸ Edit
Scheme… ▸ Run ▸ Arguments ▸ Environment Variables**. Note that an app launched
by Xcode does *not* inherit your shell environment, so exporting the variable in
`.zshrc` will not work — it has to be on the scheme.

Prerequisite for that path existing at all: `pixi install` at the repo root. The
binary then lives at `.pixi/envs/default/bin/oasis` — **note the path changed
with the pixi migration (2026-07-25)**; a scheme still pointing at the old
`.venv/bin/oasis` will fail with `.failed`, naming the stale path.

`OASIS_SERVE_BIN` is the **fallback**, not the first choice —
`resolveServerBinary()` prefers a server embedded in the bundle and only reads
the environment when there isn't one. That ordering is what keeps a shipped
`.app` from ever falling through to a dev machine's environment, and it costs
the dev loop nothing because the embed build phase skips Debug (below).

## Release: the embedded server

A Release build is self-contained — it carries its own frozen server and spawns
that, with no pixi environment anywhere in the picture.

```sh
bash spike/build.sh                                   # from the repo root, once
xcodebuild -project app/Oasis/Oasis.xcodeproj -scheme Oasis -configuration Release build
```

- **`spike/build.sh`** is the recorded PyInstaller `--onedir` recipe. It writes
  `dist/serve_entry/` (~1.1 GB: the `serve_entry` executable plus the
  `_internal/` directory it resolves dylibs and data against). `dist/` is
  gitignored and stays that way — the build phase references the output, it is
  never committed.
- **The `Embed Frozen Server` build phase** (`app/Oasis/Scripts/embed_server.sh`)
  `rsync`s that directory into `Oasis.app/Contents/Resources/serve_entry/`.
  `ServerController` resolves `Bundle.main.resourceURL` + `serve_entry/serve_entry`
  and spawns it — **not** `url(forAuxiliaryExecutable:)`, whose search path is
  the flat `Contents/MacOS/`, which has no room for `_internal/`.
- **It does not re-freeze, and it skips Debug.** Freezing is minutes and a
  gigabyte; doing it per build would make ⌘R unusable, and embedding in Debug
  would silently stop honouring `OASIS_SERVE_BIN`. Set `OASIS_EMBED_SERVER=1` to
  embed in Debug anyway. Re-freeze by hand whenever the Python side changes —
  automating that (on a source hash, not unconditionally) is a later step.
- **`ENABLE_USER_SCRIPT_SANDBOXING = NO`** on the target, because the phase reads
  `dist/` at the repo root, outside the build directory.

### Weights and the offline environment

A second phase, `Scripts/embed_models.sh`, copies the three artifacts the server
would otherwise fetch over the network into `Contents/Resources/`:

| artifact | from | to |
|---|---|---|
| `all-MiniLM-L6-v2` | `~/.cache/huggingface/hub` | `Resources/models/hub/models--sentence-transformers--…` |
| `ms-marco-MiniLM-L-6-v2` | same | `Resources/models/hub/models--cross-encoder--…` |
| tiktoken `cl100k_base` | `$TMPDIR/data-gym-cache` | `Resources/tiktoken/<sha1-of-url>` |

They are ordinary Resources, **not `--add-data` into the freeze**, so weights can
be updated without re-freezing. The HF `hub/` layout is preserved verbatim
(`snapshots/`, `blobs/`, `refs/`) because load-by-name resolves against it; its
snapshot→blob symlinks are relative, so the tree survives the move.

Populate the caches once if the build phase complains — it prints these:

```sh
pixi run -e default python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; SentenceTransformer('all-MiniLM-L6-v2'); CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"
pixi run -e default python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"
```

**Watch the tiktoken one.** It is fetched from Microsoft's blob store, so no
`HF_*` variable covers it, and the chunker only touches it while *indexing* — a
bundle missing it starts, warms, reaches ready and searches fine, then dies on
the first index. Its cache lives under `$TMPDIR` (a `/var/folders/…` path), not
`~/.cache`.

### The spawn environment differs by source

- **Dev (`OASIS_SERVE_BIN`): bare.** `child.environment` is left alone. The
  frozen binary needs no activation — PyInstaller relocated every dylib via
  rpath into `_internal/`, proven by running it under `env -i` — and a dev run
  *wants* the machine's cache and the network. Environment-independence is the
  payoff of freezing and the thing the pixi binary never had.
- **Bundled: pointed at the bundle, offline.**

  ```
  HF_HUB_OFFLINE=1  TRANSFORMERS_OFFLINE=1
  HF_HUB_CACHE       = <Resources>/models/hub   # read-only half → the bundle
  HF_HOME            = ~/.oasis/hf              # writable half  → NOT the bundle
  TIKTOKEN_CACHE_DIR = <Resources>/tiktoken
  ```

  `HF_HOME` alone would also resolve the models (`HF_HUB_CACHE` defaults to
  `$HF_HOME/hub`), but it is *also* where the hub writes tokens, locks and its
  xet store — and a write inside a signed bundle breaks its seal. Hence the
  split. Measured against sentence-transformers 5.6.1 / transformers 5.14.1 /
  huggingface_hub 1.24.0; `TRANSFORMERS_CACHE` and `SENTENCE_TRANSFORMERS_HOME`
  were not needed.

### Testing it the only way that means anything

A test with your HF cache present passes whether or not the bundling works. To
make it able to fail: move `~/.cache/huggingface`, `$TMPDIR/data-gym-cache` and
`~/.cache/torch` aside, turn Wi-Fi off, then Finder-launch the `.app` and
confirm it reaches ready, **indexes** (the tiktoken path) and searches. Measured
that way: **11.63 s launch → ready**, 1.3 GB bundle.

> **Not yet done:** deliberate signing, the `libtorch_cpu.dylib` dedup (237 M × 2,
> the largest recoverable win), and the DMG.

## What you should see

| State | What it means |
|---|---|
| **Starting the Oasis server…** | Process spawned, waiting for the handshake. ~2–3 s. |
| **Warming up… _N_ s elapsed** | Handshake read; `/api/health` says `loading`. **35–55 s is normal** (`APP_SEAM.md` §4) — the models load on a background thread. This is not a hang. |
| **Oasis is ready** | `/api/health` returned `status: ready`; the document count on screen is read out of that payload. |
| **Oasis couldn't start** | A real failure, with the reason and a Retry button. |

The child's stderr (uvicorn logs, the HuggingFace warning, the
`Loading weights: 100%` bars) is drained to `os.Logger` under subsystem
`com.oasis.app`, category `server`, and shows up in the Xcode console.

## The global summon (⌘⌥O)

Step 7 added a Spotlight-style panel on a **global hotkey, default ⌘⌥O**, plus a
menu-bar item. Two things about it change how you run the app in development:

- **The app no longer quits when you close the window.**
  `applicationShouldTerminateAfterLastWindowClosed` is `false`, so closing the
  main window leaves Oasis resident in the menu bar with the hotkey still
  registered — and the server child still running. That is the point: the
  summon has to work when no Oasis window is open. **Only Quit (⌘Q, or the
  menu-bar item) kills the server.** If you close the window and expect the app
  to be gone, it isn't; check the menu bar.
- **The binding is rebindable and persisted**, under
  `KeyboardShortcuts_summonOasis` in `~/Library/Preferences/Administrator.Oasis.plist`.
  Delete that key to get first-launch behaviour back:

  ```sh
  defaults delete ~/Library/Preferences/Administrator.Oasis KeyboardShortcuts_summonOasis
  ```

The app logs the registration outcome once at launch, category `hotkey`:

```
summon hotkey (keyCode 31 / carbonModifiers 2304) — registered cleanly
```

**If ⌘⌥O does nothing and the log says it registered cleanly, another app has
it.** That is not a bug you can detect from inside Oasis:
`RegisterEventHotKey` does not report other applications' hotkeys — measured,
two GUI processes registered ⌘⌥O simultaneously and both got `noErr`. The probe
catches *system* reservations (via `CopySymbolicHotKeys`) and nothing else. The
fix is to rebind, which is why the shortcut has a default rather than a fixed
binding.

A registered hotkey is a Carbon `RegisterEventHotKey` and needs **no
Accessibility permission** — unlike an event tap. Nothing to grant on first run.

## Verifying teardown

The thing worth checking after every run: **no orphaned `oasis serve` process**.

```sh
pgrep -fl "oasis serve"     # should print nothing once the app is gone
```

Two independent mechanisms keep that true, because neither covers the other's
case:

- **⌘Q (clean quit)** → `applicationWillTerminate` sends SIGTERM, uvicorn shuts
  down gracefully. **Closing the window is not this** — see the summon section
  above; the app stays resident and so does the server.
- **⌘. (Stop in Xcode)** → Xcode SIGKILLs the app, so
  `applicationWillTerminate` never runs. The child was spawned with `--managed`,
  so its parent-death watchdog notices `getppid() == 1` and exits within ~1 s.

## Project settings worth knowing about

Two settings differ from the Xcode template. Both are settled decisions, not
temporary ones.

- **`ENABLE_APP_SANDBOX = NO` — permanent, and what this architecture *is*.**
  App Sandbox is mandatory only for Mac App Store distribution, which is a
  stated non-goal; Oasis ships as a signed, notarized, directly-downloaded
  `.app`. The two things the app fundamentally does — **spawn the server child**
  (a binary outside the app bundle) and **index arbitrary user-chosen folders** —
  are both things the sandbox exists to forbid. Sandbox-off isn't a shortcut
  taken to get a dev build running; it's the correct configuration for a
  directly-distributed local file-search tool.

  Two things it does *not* cost:
  - **Not a privacy tradeoff.** The sandbox governs what the app can reach *on
    this machine*, not what leaves it — and nothing leaves it either way:
    loopback-only binding, bearer-token auth, no telemetry, `access_log=False`
    so queries aren't even written to a log. Orthogonal to the privacy north
    star, which is about the network boundary.
  - **Not a barrier to notarization.** An unsandboxed Developer ID app
    notarizes fine; notarization requires the hardened runtime and a valid
    signature, not the sandbox. Tier 1's "signed + notarized, one double-click"
    stays fully open.

  Re-enabling the sandbox would mean re-architecting both the child-spawn and
  the arbitrary-folder indexing — out of scope unless the App Store is ever
  pursued, which is a non-goal.

- **`SUPPORTED_PLATFORMS = macosx`.** The template was multiplatform
  (iOS/xrOS); `Process`, `NSApplicationDelegateAdaptor`, and spawning a child at
  all are macOS-only.

### Full Disk Access is still required

Independent of the sandbox decision. TCC gates the protected directories
(Desktop, Documents, Downloads, and friends) for **unsandboxed apps too**, so
indexing `~/Documents` needs Full Disk Access no matter how the sandbox flag is
set. This was always coming, and the server already anticipates it: the pipeline
counts `permission_denied` separately from `failed` precisely so the app can
show a "Grant Full Disk Access" flow instead of a useless "indexed 0 files"
empty state. It stays a Tier-1 first-run item.
