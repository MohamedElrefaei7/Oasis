#!/usr/bin/env bash
#
# make_dmg.sh — wrap the signed Oasis.app in a read-only compressed disk image.
#
# Runs LAST. The image is a snapshot: anything done to the bundle after this
# (re-signing, a stray Finder write) is not in the DMG, and a DMG built before
# signing ships an unsigned app.
#
# `-format UDZO` (zlib) rather than one of the better-compressing formats
# (ULFO/lzfse, ULMO/lzma). UDZO is readable by every macOS anyone will run this
# on; the newer codecs save a few percent in exchange for a mount failure on an
# older system, which is a bad trade for a first release. `-imagekey
# zlib-level=9` takes the compression that is available.
#
# The `/Applications` symlink is what makes the window a drag-install without
# any Finder/AppleScript window styling (which is all `create-dmg` adds, and it
# is not installed here).
#
# Usage:  bash packaging/make_dmg.sh /path/to/Oasis.app [out.dmg]
set -euo pipefail

APP="${1:?usage: make_dmg.sh /path/to/Oasis.app [out.dmg]}"
OUT="${2:-packaging/Oasis.dmg}"
VOLNAME="Oasis"

[ -d "${APP}" ] || { echo "error: no bundle at ${APP}" >&2; exit 1; }

# Refuse to package an app that would fail on the other side. Cheap, and the
# one check that catches "built the DMG before signing".
codesign --verify --deep --strict "${APP}" >/dev/null 2>&1 \
    || { echo "error: ${APP} fails codesign --verify --deep --strict — sign it first" >&2; exit 1; }

# --- functional gate: the server must actually answer a query ---------------
#
# The signature check above is a *structural* check — it proves the bytes are
# sealed, not that they work. On 2026-08-31 a bundle that passed it perfectly
# contained a five-week-stale server that reached `status: "ready"` and then
# returned HTTP 500 for every search. `ready` means the models loaded; it says
# nothing about whether retrieval works. The only check that catches that is
# running a real query and looking at the results.
#
# So: boot the bundled server, wait for ready, and make it answer. Refuses to
# package on anything less, the same way it refuses to package an unsigned
# bundle. Structure checks plus a functional check.
#
# Set OASIS_SKIP_SMOKE=1 to bypass — deliberate escape hatch, mirroring
# OASIS_EMBED_SERVER in embed_server.sh, for the case where this machine has no
# index to query against. Bypassing means packaging something whose search has
# not been exercised, so it says so loudly.
if [ "${OASIS_SKIP_SMOKE:-0}" = "1" ]; then
    echo "==> WARNING: OASIS_SKIP_SMOKE=1 — packaging WITHOUT verifying search works"
else
    echo "==> smoke-testing the bundled server (search must return results)"
    SERVER="${APP}/Contents/Resources/serve_entry/serve_entry"
    [ -x "${SERVER}" ] || { echo "error: no bundled server at ${SERVER}" >&2; exit 1; }

    smoke="$(mktemp -d -t oasis-smoke)"
    "${SERVER}" serve > "${smoke}/handshake.json" 2> "${smoke}/server.log" &
    smoke_pid=$!
    # Kill the child on every exit path, including the failure `exit 1`s below;
    # an orphaned server holding the LanceDB handle is its own bug.
    trap 'kill "${smoke_pid}" 2>/dev/null || true; rm -rf "${smoke}"' EXIT

    for _ in $(seq 1 120); do [ -s "${smoke}/handshake.json" ] && break; sleep 1; done
    [ -s "${smoke}/handshake.json" ] || {
        echo "error: bundled server never wrote its handshake line" >&2
        tail -20 "${smoke}/server.log" >&2; exit 1; }

    PORT="$(python3 -c "import json;print(json.load(open('${smoke}/handshake.json'))['port'])")"
    TOKEN="$(python3 -c "import json;print(json.load(open('${smoke}/handshake.json'))['token'])")"

    status=""
    for _ in $(seq 1 180); do
        status="$(curl -sf "http://127.0.0.1:${PORT}/api/health" \
            | python3 -c "import json,sys;print(json.load(sys.stdin).get('status',''))" 2>/dev/null || true)"
        [ "${status}" = "ready" ] && break
        [ "${status}" = "error" ] && break
        sleep 1
    done
    [ "${status}" = "ready" ] || {
        echo "error: bundled server never reached ready (last status: '${status:-none}')" >&2
        tail -20 "${smoke}/server.log" >&2; exit 1; }

    # An empty index cannot demonstrate that retrieval works, so it is a
    # failure of the check rather than a pass — silently packaging on a machine
    # with nothing indexed is how you get a green build that proved nothing.
    docs="$(curl -sf "http://127.0.0.1:${PORT}/api/health" \
        | python3 -c "import json,sys;print(json.load(sys.stdin).get('documents') or 0)")"
    [ "${docs}" -gt 0 ] || {
        echo "error: the index at this server's db_path has 0 documents — nothing to search." >&2
        echo "error: index something first, or set OASIS_SKIP_SMOKE=1 to package unverified." >&2
        exit 1; }

    n="$(curl -sf -H "Authorization: Bearer ${TOKEN}" \
        --get --data-urlencode "q=the" "http://127.0.0.1:${PORT}/api/search?limit=5" \
        | python3 -c "import json,sys;print(len(json.load(sys.stdin).get('results',[])))" 2>/dev/null || echo 0)"
    [ "${n}" -gt 0 ] || {
        echo "error: search returned no results against a ${docs}-document index — refusing to package." >&2
        echo "error: this is the check that catches a frozen server drifted from the index schema." >&2
        tail -30 "${smoke}/server.log" >&2; exit 1; }

    echo "==> smoke test passed: ${n} results from a ${docs}-document index"
    # `wait` after the kill so the shell reaps the child quietly. Without it
    # job control prints "Terminated: 15" into the packaging output, which
    # looks like a failure in a script whose whole job is to refuse to package
    # broken things.
    kill "${smoke_pid}" 2>/dev/null || true
    wait "${smoke_pid}" 2>/dev/null || true
    trap - EXIT
    rm -rf "${smoke}"
fi

root="$(mktemp -d -t oasis-dmgroot)"
trap 'rm -rf "${root}"' EXIT

echo "==> staging"
# -a to preserve the symlinks the frozen server resolves dylibs through, and
# the signature that seals them.
rsync -a "${APP}" "${root}/"
ln -s /Applications "${root}/Applications"

rm -f "${OUT}"
echo "==> building ${OUT}"
hdiutil create \
    -volname "${VOLNAME}" \
    -srcfolder "${root}" \
    -fs HFS+ \
    -format UDZO \
    -imagekey zlib-level=9 \
    -ov \
    "${OUT}"

echo
echo "==> ${OUT}: $(du -h "${OUT}" | cut -f1) compressed"
