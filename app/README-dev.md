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

Prerequisite for that path existing at all: `uv sync` at the repo root.

> **Release note.** The shipped app will spawn the PyInstaller `oasis` binary
> bundled inside the `.app` instead (`APP_SEAM.md` §1). That branch is marked
> with a `RELEASE TODO` in `ServerController.resolveServerBinary()` and is
> deliberately not implemented yet.

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

## Verifying teardown

The thing worth checking after every run: **no orphaned `oasis serve` process**.

```sh
pgrep -fl "oasis serve"     # should print nothing once the app is gone
```

Two independent mechanisms keep that true, because neither covers the other's
case:

- **⌘Q (clean quit)** → `applicationWillTerminate` sends SIGTERM, uvicorn shuts
  down gracefully.
- **⌘. (Stop in Xcode)** → Xcode SIGKILLs the app, so
  `applicationWillTerminate` never runs. The child was spawned with `--managed`,
  so its parent-death watchdog notices `getppid() == 1` and exits within ~1 s.

## Project settings worth knowing about

Two settings on the Xcode template had to change for any of this to work:

- **`ENABLE_APP_SANDBOX = NO`.** A sandboxed app can't exec an arbitrary binary
  outside its bundle, and can't reach `~/.oasis/index.db`. Re-enabling the
  sandbox is a real decision for the bundled-binary release (it needs
  `com.apple.security.network.client` for the loopback connection, plus a story
  for the index location and Full Disk Access) — not something to flip back on
  casually.
- **`SUPPORTED_PLATFORMS = macosx`.** The template was multiplatform
  (iOS/xrOS); `Process`, `NSApplicationDelegateAdaptor`, and spawning a child at
  all are macOS-only.
