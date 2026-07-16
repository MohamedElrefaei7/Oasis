"""`oasis serve` — process handshake, parent-death watchdog, uvicorn runner.

Kept out of app.py so the FastAPI app stays importable (and testable) without
touching sockets or uvicorn. The CLI command in cli/app.py is a thin wrapper
that imports this lazily, so `oasis search` never pays the fastapi import.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import sys
import threading
import time
from pathlib import Path

import uvicorn

from oasis.api.app import create_app

_log = logging.getLogger(__name__)

_WATCHDOG_POLL_SECONDS = 1.0


def _watchdog() -> None:
    """Exit when the parent dies — getppid() returns 1 once we're re-parented.

    Without this, a crashed/force-quit Swift parent orphans the server holding
    SQLite and LanceDB handles, and the next launch spawns a second server
    against the same DB.
    """
    while True:
        if os.getppid() == 1:
            os._exit(0)
        time.sleep(_WATCHDOG_POLL_SECONDS)


def run_serve(*, port: int | None = None, db: Path | None = None, managed: bool = False) -> None:
    # Bind the socket ourselves so the handshake carries a real port — reading
    # it back off uvicorn after startup is racy (no ordering guarantee the
    # socket exists before we look). Port 0 / omitted → OS-assigned ephemeral.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port or 0))  # loopback only, never 0.0.0.0
    bound_port = sock.getsockname()[1]

    # Fresh per process start; never written to disk, never reused.
    token = secrets.token_urlsafe(32)

    # The handshake: exactly one machine-readable line on stdout, flushed
    # before serving. The Swift parent reads up to the first newline and has
    # everything it needs. Everything after this is human-readable logging.
    sys.stdout.write(json.dumps({"port": bound_port, "token": token, "pid": os.getpid()}) + "\n")
    sys.stdout.flush()

    if managed:
        threading.Thread(target=_watchdog, name="oasis-watchdog", daemon=True).start()

    app = create_app(token=token, db_path=db)
    config = uvicorn.Config(
        app,
        # access_log would put every user search query (GET /api/search?q=…)
        # into the parent's stdout pipe — see CLAUDE.md § Process handshake.
        access_log=False,
        log_level="info",
    )
    uvicorn.Server(config).run(sockets=[sock])
