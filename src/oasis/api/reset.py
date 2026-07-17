"""POST /api/reset — delete the index and stand up a fresh empty one.

The deletion is the easy 20%; the commit is the **swap**. Four commits have
relied on ``VectorIndex`` being one shared handle that every search reads and
the index job writes through — that sharing is *why* search-during-index
returns fresh rows. Reset destroys and replaces that handle while searches may
be mid-flight against it, and this endpoint is written around that inversion:

- Reset takes the same ``job_lock`` as ``/api/index`` and ``409``s if a job is
  running — a reset concurrent with indexing would drop the handle the job
  writes through. The whole reset runs under the lock so no job can start
  mid-swap.
- An in-flight search holding the OLD handle degrades cleanly when its files
  vanish (the hybrid vector arm catches it) and subsequent searches bind the
  new empty handle. Never a 500, never a torn read. The swap mechanics and the
  crash-safe deletion order live in ``AppState.reset_index``.

Sync ``def``: filesystem + SQLite deletion is blocking work and belongs in the
threadpool (CLAUDE.md § Concurrency). Auth + readiness come from
``protected_router``.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from oasis.api.schemas import ResetRequest
from oasis.api.state import AppState

router = APIRouter()


@router.post("/reset", status_code=204)
def reset(request: Request, body: ResetRequest) -> Response:
    state: AppState = request.app.state.oasis
    assert state.db_path is not None  # ready implies loaded

    if not body.confirm:
        # No interactive prompt over HTTP (the CLI's typer.confirm), so the
        # confirmation must be explicit — guards a bare/empty request from
        # nuking the index.
        raise StarletteHTTPException(
            status_code=400,
            detail={"code": "bad_request", "message": "Reset requires an explicit confirm: true."},
        )

    # Reset and index are mutually exclusive under the SAME job_lock, for the
    # same reason a second /api/index is refused: reset drops the VectorIndex
    # handle the job writes through. Hold the lock across the WHOLE reset so no
    # job can start mid-swap (raising inside the with-block still releases it).
    with state.job_lock:
        job = state.index_job
        if job is not None and job.status == "running":
            raise StarletteHTTPException(
                status_code=409,
                detail={
                    "code": "conflict",
                    "message": f"An index job is running (job_id={job.id}); cancel it before reset.",
                },
            )
        if not state.db_path.exists():
            # Nothing to reset — a real not-found, mirroring /api/status and the
            # CLI's "no index at <path>".
            raise StarletteHTTPException(
                status_code=404,
                detail={"code": "not_found", "message": "No index exists at the configured path."},
            )
        state.reset_index()

    return Response(status_code=204)
