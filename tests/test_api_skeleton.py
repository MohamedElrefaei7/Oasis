"""HTTP API skeleton: lifecycle, health, auth, readiness, error envelope.

The embedder/reranker/vector index are faked so the suite never loads PyTorch;
anything that would need the real models belongs under @pytest.mark.slow.
"""
from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from oasis.api import state as api_state
from oasis.api.app import PROTECTED, create_app
from oasis.api.state import get_conn, invalidate
from oasis.config import OasisConfig
from oasis.index.db import open_db

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

DIM = 8


class FakeEmbedder:
    dimension = DIM

    def embed(self, texts: list[str]) -> np.ndarray:
        rng = np.random.default_rng(0)
        return rng.random((len(texts), DIM), dtype=np.float32)


class FakeReranker:
    def rerank(self, query, results, *, top_n=None):
        return results[:top_n] if top_n is not None else results


class FakeVectorIndex:
    def __init__(self, db_path: Path, dimension: int) -> None:
        self.db_path = db_path
        self.dimension = dimension

    def count(self) -> int:
        return 0


def _patch_models(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the loader at fakes and a throwaway db path; return the db path."""
    db_path = tmp_path / "index.db"
    monkeypatch.setattr("oasis.api.app.load_config", lambda: OasisConfig(db_path=db_path))
    monkeypatch.setattr("oasis.api.app.SentenceTransformerEmbedder", FakeEmbedder)
    monkeypatch.setattr("oasis.api.app.CrossEncoderReranker", FakeReranker)
    monkeypatch.setattr("oasis.api.app.VectorIndex", FakeVectorIndex)
    monkeypatch.setattr("oasis.api.app.ensure_ollama", lambda: None)
    return db_path


class _EchoBody(BaseModel):
    n: int


def _add_test_routes(app: FastAPI) -> None:
    """Trivial protected routes so auth/readiness/422/500 behavior is testable."""

    def ping() -> dict[str, bool]:
        return {"ok": True}

    def echo(payload: _EchoBody) -> dict[str, int]:
        return {"n": payload.n}

    def boom() -> None:
        raise RuntimeError("sensitive internal detail")

    app.add_api_route("/api/_ping", ping, methods=["GET"], dependencies=PROTECTED)
    app.add_api_route("/api/_echo", echo, methods=["POST"], dependencies=PROTECTED)
    app.add_api_route("/api/_boom", boom, methods=["GET"], dependencies=PROTECTED)

    # create_app registers the auth-gated /api catch-all last; routes added
    # after it (like these) would be shadowed. Move it back to the end.
    routes = app.router.routes
    catch_all = next(r for r in routes if getattr(r, "path", "") == "/api/{_rest:path}")
    routes.remove(catch_all)
    routes.append(catch_all)


@pytest.fixture
def ready_client(monkeypatch, tmp_path):
    """A client whose app has finished loading (fakes, so near-instant)."""
    _patch_models(monkeypatch, tmp_path)
    app = create_app(token=TOKEN)
    _add_test_routes(app)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert app.state.oasis.ready.wait(timeout=10), "loader thread never became ready"
        yield client


def _assert_envelope(body: dict) -> None:
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert isinstance(body["error"]["code"], str)
    assert isinstance(body["error"]["message"], str)


# ---------------------------------------------------------------------------
# Lifecycle: loading → ready
# ---------------------------------------------------------------------------


def test_health_loading_then_ready(monkeypatch, tmp_path):
    _patch_models(monkeypatch, tmp_path)
    gate = threading.Event()

    class GatedEmbedder(FakeEmbedder):
        def __init__(self) -> None:
            assert gate.wait(timeout=30)

    monkeypatch.setattr("oasis.api.app.SentenceTransformerEmbedder", GatedEmbedder)
    app = create_app(token=TOKEN)
    _add_test_routes(app)

    with TestClient(app) as client:
        # Loader is blocked on the gate: readiness Event unset, health = loading.
        assert not app.state.oasis.ready.is_set()
        resp = client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "loading"
        assert body["documents"] is None
        assert body["error"] is None

        # Any other route reports 503 in envelope shape while loading.
        resp = client.get("/api/_ping", headers=AUTH)
        assert resp.status_code == 503
        _assert_envelope(resp.json())
        assert resp.json()["error"]["code"] == "loading"

        gate.set()
        assert app.state.oasis.ready.wait(timeout=10)
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"

        # And the protected route now answers.
        resp = client.get("/api/_ping", headers=AUTH)
        assert resp.status_code == 200


def test_health_error_when_loading_fails(monkeypatch, tmp_path):
    _patch_models(monkeypatch, tmp_path)

    class BrokenEmbedder:
        def __init__(self) -> None:
            raise OSError("model cache corrupt")

    monkeypatch.setattr("oasis.api.app.SentenceTransformerEmbedder", BrokenEmbedder)
    app = create_app(token=TOKEN)
    _add_test_routes(app)

    with TestClient(app) as client:
        deadline = threading.Event()
        for _ in range(100):
            if app.state.oasis.status == "error":
                break
            deadline.wait(0.05)
        body = client.get("/api/health").json()
        assert body["status"] == "error"
        assert "model cache corrupt" in body["error"]

        resp = client.get("/api/_ping", headers=AUTH)
        assert resp.status_code == 503
        _assert_envelope(resp.json())


def test_health_reports_document_count(monkeypatch, tmp_path):
    db_path = _patch_models(monkeypatch, tmp_path)
    open_db(db_path).close()  # empty index on disk
    app = create_app(token=TOKEN)
    with TestClient(app) as client:
        assert app.state.oasis.ready.wait(timeout=10)
        body = client.get("/api/health").json()
        assert body["status"] == "ready"
        assert body["documents"] == 0
        assert isinstance(body["version"], str) and body["version"]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_health_requires_no_auth(ready_client):
    resp = ready_client.get("/api/health")  # no Authorization header at all
    assert resp.status_code == 200


def test_missing_token_is_401_envelope(ready_client):
    resp = ready_client.get("/api/_ping")
    assert resp.status_code == 401
    _assert_envelope(resp.json())
    assert resp.json()["error"]["code"] == "unauthorized"


def test_wrong_token_is_401_envelope(ready_client):
    resp = ready_client.get("/api/_ping", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401
    _assert_envelope(resp.json())


def test_correct_token_passes(ready_client):
    resp = ready_client.get("/api/_ping", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_unknown_api_path_401s_without_token(ready_client):
    # A tokenless caller must not be able to distinguish real /api routes from
    # fake ones: unknown paths hit the auth-gated catch-all, not a bare 404.
    resp = ready_client.get("/api/nonsense")
    assert resp.status_code == 401
    _assert_envelope(resp.json())
    resp = ready_client.post("/api/also/not/real")
    assert resp.status_code == 401


def test_unknown_api_path_404s_with_token(ready_client):
    resp = ready_client.get("/api/nonsense", headers=AUTH)
    assert resp.status_code == 404
    _assert_envelope(resp.json())
    assert resp.json()["error"]["code"] == "not_found"


def test_openapi_docs_disabled(ready_client):
    # /openapi.json would enumerate every route to unauthenticated callers.
    assert ready_client.get("/openapi.json").status_code == 404
    assert ready_client.get("/docs").status_code == 404


# ---------------------------------------------------------------------------
# Error envelope
# ---------------------------------------------------------------------------


def test_validation_error_is_422_in_envelope_shape(ready_client):
    # The one error FastAPI generates on its own: its default shape is a LIST
    # under "detail" — assert the envelope keys, not just the status code.
    resp = ready_client.post("/api/_echo", headers=AUTH, json={"n": "not-an-int"})
    assert resp.status_code == 422
    body = resp.json()
    assert "detail" not in body
    _assert_envelope(body)
    assert body["error"]["code"] == "validation_error"
    assert "n" in body["error"]["message"]


def test_unhandled_exception_is_500_envelope_without_traceback(ready_client):
    resp = ready_client.get("/api/_boom", headers=AUTH)
    assert resp.status_code == 500
    _assert_envelope(resp.json())
    text = resp.text
    assert "sensitive internal detail" not in text
    assert "Traceback" not in text
    assert "RuntimeError" not in text


# ---------------------------------------------------------------------------
# Thread-local SQLite
# ---------------------------------------------------------------------------


def test_get_conn_is_thread_local(tmp_path):
    db_path = tmp_path / "index.db"
    results: dict[str, object] = {}
    errors: list[Exception] = []

    def worker(name: str) -> None:
        try:
            conn = get_conn(db_path)
            conn.execute("SELECT count(*) FROM documents").fetchone()
            results[name] = conn
        except Exception as exc:  # noqa: BLE001 — collected and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"cross-thread SQLite error: {errors}"
    assert len(results) == 2
    assert results["t0"] is not results["t1"]


def test_get_conn_reuses_within_thread_and_reopens_after_invalidate(tmp_path):
    db_path = tmp_path / "index.db"
    first = get_conn(db_path)
    assert get_conn(db_path) is first  # cached within the thread

    invalidate()
    second = get_conn(db_path)
    assert second is not first  # stale connection replaced
    second.execute("SELECT 1").fetchone()


def test_state_generation_shared_across_module(tmp_path):
    # invalidate() must affect the module-level generation the helper reads.
    db_path = tmp_path / "index.db"
    conn = get_conn(db_path)
    before = api_state._generation
    invalidate()
    assert api_state._generation == before + 1
    assert get_conn(db_path) is not conn
