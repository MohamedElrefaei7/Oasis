"""Inference device selection — one source of truth for every model wrapper.

**CPU is the default, deliberately.** sentence-transformers auto-selects MPS on
Apple Silicon, and that auto-selection is what broke the app: when ``oasis
serve`` is spawned as the child of a GUI process, the cross-encoder's first real
inference aborts the whole server under Metal's validation layer
(``validateComputeFunctionArguments … Read-only bytes … write access enabled``,
SIGABRT), which the app then sees as connection-refused. The identical search
runs fine when ``serve`` is spawned from a shell — even under ``env -i`` — so
the trigger is the Metal device context an MPS subprocess inherits from a GUI
parent, not a missing environment variable.

This has to be a hard CPU pin rather than ``PYTORCH_ENABLE_MPS_FALLBACK``: the
crashing kernel is not one MPS *refuses* (which the fallback would catch) — MPS
accepts it and Metal validation aborts afterwards. The device must never be MPS
in the first place.

Two reasons beyond the crash, which are why this belongs in the engine rather
than in the app that happened to surface it:

* **Determinism.** One compute path for the CLI, the server, and the eval
  harness means the measured matrix describes what every launcher actually runs.
* **Portability.** CPU is the floor that exists on every Mac, across Metal
  driver versions that cannot be tested from here. It is what ships to unknown
  machines.

**CPU is only safe because torch links OpenBLAS, and that is not the default on
PyPI.** Every stock macOS-arm64 wheel links Apple's Accelerate, whose SGEMV path
returns all-NaN cross-encoder logits on realistic batch shapes here (and SIGBUSes
on others) — a *silent* failure, since NaN scores don't raise and sorting on NaN
keys leaves order untouched, so a NaN reranker degrades to "no reranking" with no
error anywhere. The project therefore takes torch from conda-forge via pixi
(`BLAS_INFO=open`); see `pixi.toml` and CONTEXT.md. If torch is ever installed
from PyPI again, this default becomes actively dangerous, and
``tests/test_device.py::test_cpu_cross_encoder_returns_finite_scores`` is the
tripwire that catches it.

This is an interim baseline, not the destination: the Tier-3 Core ML / MLX swap
is what eventually buys the Apple-Silicon acceleration back on a supported path.
The crash is one more measured argument for it.
"""

from __future__ import annotations

import os

#: Opt-in override. Unsupported: MPS is the configuration known to abort a
#: GUI-spawned server, so anyone setting this is doing so at their own risk.
DEVICE_ENV_VAR = "OASIS_DEVICE"

DEFAULT_DEVICE = "cpu"


def resolve_device(device: str | None = None) -> str:
    """Resolve the torch device string: explicit arg > ``OASIS_DEVICE`` > CPU.

    Kept as a plain function (not an ``OasisConfig`` field) so the model
    wrappers, which sit on every import chain, don't pull the settings
    machinery in behind them.
    """
    if device is not None:
        return device
    env_device = os.environ.get(DEVICE_ENV_VAR)
    if env_device:
        return env_device
    return DEFAULT_DEVICE
