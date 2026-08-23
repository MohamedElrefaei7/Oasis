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
