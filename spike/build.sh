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
