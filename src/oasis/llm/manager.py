"""Ollama lifecycle helpers.

Provides ``ensure_ollama()``, which checks whether a local Ollama server is
running, starts it if the binary is installed, and returns an ``OllamaProvider``
when both the server and the requested model are ready.  Returns ``None``
silently so callers can fall back gracefully when Ollama is unavailable.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import time
import urllib.error
import urllib.request

from oasis.llm.ollama import DEFAULT_MODEL, OllamaProvider

_log = logging.getLogger(__name__)

_OLLAMA_URL = "http://localhost:11434/"
_STARTUP_TIMEOUT = 5.0   # seconds to wait for the server to become ready
_POLL_INTERVAL = 0.25    # seconds between readiness checks


def _server_running() -> bool:
    try:
        urllib.request.urlopen(_OLLAMA_URL, timeout=1)
        return True
    except Exception:
        return False


def _model_available(model: str) -> bool:
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return model in result.stdout
    except Exception:
        return False


def _start_server() -> bool:
    """Spawn ``ollama serve`` in the background; wait up to *_STARTUP_TIMEOUT* seconds."""
    if shutil.which("ollama") is None:
        return False
    subprocess.Popen(  # noqa: S603
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + _STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if _server_running():
            return True
        time.sleep(_POLL_INTERVAL)
    return False


def ensure_ollama(model: str = DEFAULT_MODEL) -> OllamaProvider | None:
    """Return a ready ``OllamaProvider``, or ``None`` if Ollama is unavailable.

    Steps:
    1. If the server isn't running, try to start it (requires ``ollama`` on PATH).
    2. If the server still isn't up after *_STARTUP_TIMEOUT* seconds, return None.
    3. If the requested model hasn't been pulled, return None.
    """
    if not _server_running():
        _log.debug("Ollama server not running — attempting to start")
        if not _start_server():
            _log.debug("Could not start Ollama (binary missing or timed out)")
            return None

    if not _model_available(model):
        _log.debug("Model %r not found — run: ollama pull %s", model, model)
        return None

    return OllamaProvider(model=model)
