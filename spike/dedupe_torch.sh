#!/usr/bin/env bash
#
# dedupe_torch.sh — collapse PyInstaller's duplicated libtorch_cpu.dylib.
#
# The freeze lands the same 237 MB dylib twice: once at `_internal/` (the copy
# dyld actually maps — libtorch_python.dylib's only LC_RPATH is
# `@loader_path/../..`, i.e. `_internal/`, verified with lsof on a live frozen
# server) and once at `_internal/torch/lib/`, which nothing ever opens. This
# replaces the dead copy with a relative symlink pointing at the live one.
#
# Idempotent, and safe to run against an already-deduped tree. Relative and
# inside the tree on purpose: the link has to survive being rsync'd into
# `Oasis.app/Contents/Resources/` and then codesigned, and an absolute or
# escaping link breaks both. PyInstaller already ships cross-directory symlinks
# of exactly this shape (`_internal/libtorch_python.dylib -> torch/lib/…`), so
# this is the tree's own idiom, not a new trick.
#
# Usage:  bash spike/dedupe_torch.sh [dist/serve_entry]
set -euo pipefail

ROOT="${1:-dist/serve_entry}"
INTERNAL="${ROOT}/_internal"
LIVE="${INTERNAL}/libtorch_cpu.dylib"
DEAD="${INTERNAL}/torch/lib/libtorch_cpu.dylib"

if [ ! -d "${INTERNAL}" ]; then
    echo "error: no _internal/ under ${ROOT} — freeze first: bash spike/build.sh" >&2
    exit 1
fi

if [ -L "${DEAD}" ]; then
    echo "note: already deduped (${DEAD} -> $(readlink "${DEAD}"))"
    exit 0
fi

if [ ! -f "${LIVE}" ] || [ ! -f "${DEAD}" ]; then
    echo "note: nothing to dedupe — expected both copies, found:"
    ls -l "${LIVE}" "${DEAD}" 2>&1 || true
    exit 0
fi

# Refuse to touch anything that isn't the dylib pair this is written for: the
# two copies differ only in LC_RPATH, so they are not byte-identical and a
# hash comparison would be the wrong check. Size + Mach-O type is the real one.
live_size=$(stat -f%z "${LIVE}")
dead_size=$(stat -f%z "${DEAD}")
if [ "${live_size}" != "${dead_size}" ]; then
    echo "error: sizes differ (${live_size} vs ${dead_size}) — not the known duplicate pair" >&2
    exit 1
fi
file -b "${DEAD}" | grep -q 'Mach-O.*dynamically linked shared library' || {
    echo "error: ${DEAD} is not a Mach-O dylib" >&2
    exit 1
}

rm -f "${DEAD}"
ln -s ../../libtorch_cpu.dylib "${DEAD}"

# A dangling link here would surface as a launch failure much later, so prove
# it resolves now.
[ -f "${DEAD}" ] || { echo "error: symlink is dangling" >&2; exit 1; }

echo "note: deduped libtorch_cpu.dylib — saved $(echo "${live_size}" | awk '{printf "%.0f MB", $1/1048576}')"
