"""POST /api/open — open an indexed file in the system default app.

Sync ``def``: subprocess is blocking work and belongs in the threadpool.

Contract: the request must match the exact stored form of an indexed path.
The pipeline normalizes with ``os.path.abspath`` applied once to the index
root — lexical only, no symlink following — so this endpoint normalizes the
same way and does NOT chase symlink aliases. A request that reaches an
indexed file through a different symlink alias is a 404: fail-closed, and a
non-issue for the real client, which echoes stored paths verbatim from
/api/search. Whatever normalization storage uses, lookups must use the
identical one — resolving here would reintroduce the exact mismatch this
design removes.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from fastapi import APIRouter, Request, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from oasis.api.schemas import OpenRequest
from oasis.api.state import AppState, get_conn
from oasis.index.keyword import KeywordIndex

_log = logging.getLogger(__name__)

router = APIRouter()


@router.post("/open", status_code=204)
def open_file(request: Request, body: OpenRequest) -> Response:
    state: AppState = request.app.state.oasis
    assert state.db_path is not None  # ready implies loaded

    # No-op on the absolute paths the client echoes back from /api/search;
    # defensive only if a relative path somehow arrives (it would absolutize
    # against the server's CWD and, in practice, miss the index below).
    p = Path(os.path.abspath(body.path))

    # get_doc_id is the security boundary: only files Oasis actually indexed
    # can be opened, so an arbitrary path (including "../" traversals, which
    # abspath normalizes away) is a 404 rather than a launch.
    doc_id = KeywordIndex(get_conn(state.db_path)).get_doc_id(p)
    if doc_id is None:
        raise StarletteHTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Path is not in the index."},
        )

    if not p.exists():
        # Indexed but gone from disk — distinct from 404 so the app can offer
        # to reindex rather than saying "never heard of it".
        raise StarletteHTTPException(
            status_code=410,
            detail={"code": "gone", "message": "File is indexed but no longer on disk."},
        )

    # List form, never shell=True — the path never reaches a shell.
    subprocess.run(["open", str(p)], check=False)
    return Response(status_code=204)
