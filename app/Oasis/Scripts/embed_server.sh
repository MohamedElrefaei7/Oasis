#!/bin/bash
#
# embed_server.sh — put the frozen `oasis serve` inside the .app.
#
# Copies the PyInstaller `--onedir` output (`dist/serve_entry/`, produced by
# `bash spike/build.sh` from the repo root) into
# `Oasis.app/Contents/Resources/serve_entry/`, preserving the layout PyInstaller
# requires: the `serve_entry` executable with its `_internal/` directory as a
# sibling. `ServerController.resolveServerBinary()` looks for exactly that path.
#
# Two things this deliberately does NOT do:
#
#   * It does not re-freeze. The freeze is minutes and ~1.1 GB; running it per
#     build would make every ⌘R unusable. Freeze once with the recorded recipe,
#     rebuild the app as often as you like. (Automating re-freeze-on-build is a
#     later step, and wants a source-hash check, not an unconditional rebuild.)
#
#   * It does not run for Debug. A dev ⌘R keeps spawning the pixi binary via
#     OASIS_SERVE_BIN, which is the whole point of the fallback in
#     resolveServerBinary(): no re-freeze per dev iteration. Set
#     OASIS_EMBED_SERVER=1 to embed in Debug anyway.
#
set -euo pipefail

REPO_ROOT="${SRCROOT}/../.."
SERVER_DIST="${OASIS_SERVER_DIST:-${REPO_ROOT}/dist/serve_entry}"
DEST_DIR="${TARGET_BUILD_DIR}/${CONTENTS_FOLDER_PATH}/Resources"
DEST="${DEST_DIR}/serve_entry"

if [ "${CONFIGURATION}" != "Release" ] && [ "${OASIS_EMBED_SERVER:-0}" != "1" ]; then
    echo "note: ${CONFIGURATION} build — skipping server embed (dev runs use OASIS_SERVE_BIN; set OASIS_EMBED_SERVER=1 to override)"
    # Leave no stale server behind from a previous Release build in the same
    # build dir: a Debug .app that still carried one would silently stop
    # honouring OASIS_SERVE_BIN.
    rm -rf "${DEST}"
    exit 0
fi

if [ ! -x "${SERVER_DIST}/serve_entry" ]; then
    echo "error: no frozen server at ${SERVER_DIST}/serve_entry"
    echo "error: freeze it first, from the repo root:  bash spike/build.sh"
    exit 1
fi

mkdir -p "${DEST_DIR}"

# --delete so a re-freeze that drops a file doesn't leave it behind; rsync's
# incrementality is what keeps a rebuild from re-copying 1.1 GB.
rsync -a --delete "${SERVER_DIST}/" "${DEST}/"

echo "note: embedded frozen server → ${DEST} ($(du -sh "${DEST}" | cut -f1))"
