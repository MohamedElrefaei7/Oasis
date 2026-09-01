#!/usr/bin/env bash
#
# source_stamp.sh — the identity of the Python source that went into a freeze.
#
# Exists because `dist/serve_entry/` and `src/oasis/` are two artifacts that are
# supposed to be the same program, with nothing enforcing it. On 2026-08-31 the
# frozen server was five weeks behind the source: it launched, reported
# `status: "ready"`, and returned HTTP 500 for *every* search, because the
# index had moved to schema v3 and the frozen reranker predated it. Nothing in
# the build said a word. This script is half the fix; the check in
# `embed_server.sh` is the other half.
#
# **The stamp is a content hash, not a commit hash.** A bare `git rev-parse
# HEAD` is a false green on a dirty tree — re-freezing mid-work with
# uncommitted changes is the normal case, and a hash that matches HEAD while
# the working tree differs is exactly the state this is supposed to catch. So
# the comparison value is a hash of the bytes under `src/oasis/`, which is what
# actually got frozen. HEAD and the dirty flag ride along as the second and
# third fields for the error message only — they make a mismatch legible to a
# human ("you froze at abc1234-dirty, you're now at def5678-clean") and are
# never compared.
#
# Output: one line, three space-separated fields.
#
#     <sha256-of-src-tree> <head-or-nogit> <clean|dirty>
#
# Usage:  bash spike/source_stamp.sh /path/to/repo-root
set -euo pipefail

REPO_ROOT="${1:?usage: source_stamp.sh /path/to/repo-root}"
SRC="${REPO_ROOT}/src/oasis"

[ -d "${SRC}" ] || { echo "error: no source tree at ${SRC}" >&2; exit 1; }

# Hash every file under src/oasis, path included so a pure rename still moves
# the stamp. Excluded: __pycache__ (build noise, not source — a .pyc appearing
# after a test run would otherwise invalidate a perfectly good freeze) and
# .DS_Store (Finder writes it just by looking at the directory).
#
# LC_ALL=C on the sort so the order is byte order rather than the shell's
# locale. Without it the same tree hashes differently under different
# LANG settings, and the check fails builds for no reason at all — which is
# how a guard like this gets disabled instead of fixed.
content="$(
    cd "${SRC}" && find . -type f \
        ! -path '*/__pycache__/*' \
        ! -name '*.pyc' \
        ! -name '.DS_Store' \
        -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 shasum -a 256 \
    | shasum -a 256 \
    | cut -d' ' -f1
)"

head="$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo nogit)"

dirty=clean
if ! git -C "${REPO_ROOT}" diff --quiet HEAD -- src/oasis 2>/dev/null; then
    dirty=dirty
fi

printf '%s %s %s\n' "${content}" "${head}" "${dirty}"
