#!/bin/bash
#
# embed_models.sh — put the model weights and the tiktoken encoding inside the .app.
#
# These are the three artifacts the server would otherwise fetch over the
# network on first use. A bundle without them works on this machine (which has
# the caches) and fails on a stranger's, which is exactly the class of bug that
# never shows up in local testing.
#
#   1. sentence-transformers/all-MiniLM-L6-v2   (oasis.index.embeddings.DEFAULT_MODEL)
#   2. cross-encoder/ms-marco-MiniLM-L-6-v2     (oasis.query.reranker.DEFAULT_CE_MODEL)
#   3. tiktoken cl100k_base                     (oasis.index.chunker.ENCODING_NAME)
#
# **The third is the one that hides.** It is not a "model", it is fetched from
# Microsoft's blob store rather than HuggingFace — so `HF_HUB_OFFLINE=1` does
# not cover it — and the chunker only touches it during *indexing*. Miss it and
# the server starts fine, reaches ready, serves searches, and then dies on the
# first index. Measured: with the HF vars set correctly but no
# TIKTOKEN_CACHE_DIR, tiktoken silently downloaded the encoding and the test
# passed anyway. Only a network-off test catches this.
#
# Copied as ordinary Resources, NOT `--add-data`'d into the frozen binary:
# weights can then be updated without re-freezing, and the freeze recipe in
# spike/build.sh stays stable.
#
# Layout produced (ServerController points the child at it):
#
#   Contents/Resources/models/hub/models--sentence-transformers--all-MiniLM-L6-v2/
#   Contents/Resources/models/hub/models--cross-encoder--ms-marco-MiniLM-L-6-v2/
#   Contents/Resources/tiktoken/<sha1-of-the-encoding-url>
#
# The HF `hub/` layout (snapshots/ + blobs/ + refs/) is preserved verbatim so
# load-by-name resolves against it. Its snapshot→blob symlinks are *relative*
# (`../../blobs/<sha>`), so the tree survives being moved into the bundle —
# verified, not assumed.
#
set -euo pipefail

HF_SRC="${OASIS_HF_CACHE:-${HOME}/.cache/huggingface/hub}"
DEST_RES="${TARGET_BUILD_DIR}/${CONTENTS_FOLDER_PATH}/Resources"

MODELS=(
    "models--sentence-transformers--all-MiniLM-L6-v2"
    "models--cross-encoder--ms-marco-MiniLM-L-6-v2"
)

# tiktoken names its cache entries by the SHA-1 of the download URL, so the
# filename is derived rather than hardcoded — a hardcoded hash would be an
# opaque constant that silently stops matching if the encoding ever changes.
TIKTOKEN_URL="https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
TIKTOKEN_FILE="$(printf '%s' "${TIKTOKEN_URL}" | shasum -a 1 | cut -d' ' -f1)"

if [ "${CONFIGURATION}" != "Release" ] && [ "${OASIS_EMBED_SERVER:-0}" != "1" ]; then
    echo "note: ${CONFIGURATION} build — skipping model embed (dev runs use the machine's caches and the network)"
    # Same reasoning as embed_server.sh: never leave a Release build's payload
    # behind in a Debug .app, or the dev run stops testing the dev path.
    rm -rf "${DEST_RES}/models" "${DEST_RES}/tiktoken"
    exit 0
fi

# --- tiktoken source: wherever this machine's cache actually is -------------
#
# tiktoken looks at TIKTOKEN_CACHE_DIR, then DATA_GYM_CACHE_DIR, then
# $TMPDIR/data-gym-cache. On macOS TMPDIR is a per-user /var/folders path, so
# the default location is not `~/.cache` and is easy to look for in the wrong
# place.
TIKTOKEN_SRC=""
for dir in "${OASIS_TIKTOKEN_CACHE:-}" "${TIKTOKEN_CACHE_DIR:-}" "${DATA_GYM_CACHE_DIR:-}" \
           "${TMPDIR:-/tmp}/data-gym-cache" "/tmp/data-gym-cache" "${HOME}/.cache/data-gym-cache"; do
    [ -n "${dir}" ] && [ -f "${dir}/${TIKTOKEN_FILE}" ] && { TIKTOKEN_SRC="${dir}"; break; }
done

# --- preflight: fail loudly, and say how to fix it --------------------------
missing=0
for m in "${MODELS[@]}"; do
    if [ ! -d "${HF_SRC}/${m}" ]; then
        echo "error: no ${m} in ${HF_SRC}"
        missing=1
    fi
done
if [ -z "${TIKTOKEN_SRC}" ]; then
    echo "error: no tiktoken cl100k_base cache (looked for ${TIKTOKEN_FILE} in TIKTOKEN_CACHE_DIR, DATA_GYM_CACHE_DIR, \$TMPDIR/data-gym-cache, /tmp/data-gym-cache, ~/.cache/data-gym-cache)"
    missing=1
fi
if [ "${missing}" -ne 0 ]; then
    echo "error: populate this machine's caches once, then rebuild — from the repo root:"
    echo "error:   pixi run -e default python -c \"from sentence_transformers import SentenceTransformer, CrossEncoder; SentenceTransformer('all-MiniLM-L6-v2'); CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')\""
    echo "error:   pixi run -e default python -c \"import tiktoken; tiktoken.get_encoding('cl100k_base')\""
    exit 1
fi

mkdir -p "${DEST_RES}/models/hub" "${DEST_RES}/tiktoken"

for m in "${MODELS[@]}"; do
    # -a keeps the relative snapshot→blob symlinks as symlinks; --delete keeps a
    # re-run from leaving a superseded revision behind.
    rsync -a --delete "${HF_SRC}/${m}/" "${DEST_RES}/models/hub/${m}/"
done
rsync -a "${TIKTOKEN_SRC}/${TIKTOKEN_FILE}" "${DEST_RES}/tiktoken/"

echo "note: embedded models → ${DEST_RES}/models ($(du -sh "${DEST_RES}/models" | cut -f1))"
echo "note: embedded tiktoken cl100k_base from ${TIKTOKEN_SRC} → ${DEST_RES}/tiktoken/${TIKTOKEN_FILE}"
