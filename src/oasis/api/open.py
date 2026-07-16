"""POST /api/open — open an indexed file in the system default app.

Sync ``def``: subprocess is blocking work and belongs in the threadpool.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from fastapi import APIRouter, Request, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from oasis.api.schemas import OpenRequest
from oasis.api.state import AppState, get_conn
from oasis.index.keyword import KeywordIndex

_log = logging.getLogger(__name__)

router = APIRouter()


def _lookup_doc_id(idx: KeywordIndex, requested: Path, resolved: Path) -> int | None:
    """Find *requested* in the index, tolerating symlink differences.

    Path normalization has to match how the pipeline stores paths, and the
    pipeline stores whatever the walker yielded — ``root`` joined with each
    relative sub-path, absolute but **not** symlink-resolved. So the stored
    form depends on how the user invoked ``oasis index``:

    - ``oasis index ~/Documents`` stores ``/Users/you/Documents/…`` (no
      symlinks in that path on macOS — ``/Users`` is a *firmlink*, which
      ``resolve()`` leaves alone — so stored == resolved here by luck, not
      design).
    - ``oasis index /tmp/notes`` stores ``/tmp/notes/…`` while ``resolve()``
      yields ``/private/tmp/notes/…`` — ``/tmp`` really is a symlink.

    So resolving only the request side misses every index built through a
    symlinked root, and matching only the raw form misses every request made
    through a symlink. Try both forms of the *same* request: whichever matches,
    both name the same file, and the caller opens the resolved one.
    """
    for candidate in dict.fromkeys((requested, resolved)):  # dedupe, keep order
        doc_id = idx.get_doc_id(candidate)
        if doc_id is not None:
            return doc_id
    return None


@router.post("/open", status_code=204)
def open_file(request: Request, body: OpenRequest) -> Response:
    state: AppState = request.app.state.oasis
    assert state.db_path is not None  # ready implies loaded

    requested = Path(body.path)
    if not requested.is_absolute():
        # resolve() on a relative path is relative to the server's CWD, which
        # the client knows nothing about — it could match one file and open a
        # different one. The client always has absolute paths from /api/search.
        raise StarletteHTTPException(
            status_code=400,
            detail={"code": "bad_request", "message": "path must be absolute."},
        )

    # Normalizes away ".." and symlinks. Done before the lookup so a traversal
    # path can't smuggle itself past get_doc_id.
    resolved = requested.resolve()

    # get_doc_id is the security boundary: only files Oasis actually indexed
    # can be opened, so an arbitrary path is a 404 rather than a launch.
    doc_id = _lookup_doc_id(KeywordIndex(get_conn(state.db_path)), requested, resolved)
    if doc_id is None:
        raise StarletteHTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Path is not in the index."},
        )

    if not resolved.exists():
        # Indexed but gone from disk — distinct from 404 so the app can offer
        # to reindex rather than saying "never heard of it".
        raise StarletteHTTPException(
            status_code=410,
            detail={"code": "gone", "message": "File is indexed but no longer on disk."},
        )

    # List form, never shell=True — the path never reaches a shell.
    subprocess.run(["open", str(resolved)], check=False)
    return Response(status_code=204)
