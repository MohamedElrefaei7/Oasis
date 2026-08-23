#!/usr/bin/env bash
# Disposable spike build. Run from the repo root via:  bash spike/build.sh
set -euo pipefail
export PATH="$HOME/.pixi/bin:$PATH"

pixi run -e build pyinstaller --onedir --noconfirm --clean \
  --distpath dist --workpath spike/pybuild --specpath spike \
  --collect-all torch \
  --collect-all sentence_transformers \
  --collect-all transformers \
  --collect-data tokenizers \
  --collect-all lancedb \
  --collect-submodules uvicorn \
  --collect-submodules oasis \
  --collect-submodules tiktoken_ext \
  --hidden-import tiktoken_ext.openai_public \
  spike/serve_entry.py

# Collapse PyInstaller's duplicated 237 MB libtorch_cpu.dylib into a relative
# symlink. Part of the recipe, not a manual afterthought: a re-freeze recreates
# both copies, so the dedup has to run here or the bundle silently regrows.
bash spike/dedupe_torch.sh dist/serve_entry
