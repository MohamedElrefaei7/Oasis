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
