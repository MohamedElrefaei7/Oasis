"""Verify retrieval is identical through the HTTP path (Phase 5.2 seam check).

Every eval number this project has produced comes from calling ``hybrid_search``
directly. The service layer wraps that in ``run_search`` behind ``/api/search``,
and nothing has confirmed the served path returns the same ranking. This measures
it once, before a second consumer (the Swift app) is built on the seam.

**Method — hold everything constant except the retrieval call.** This reuses
``run_eval.py``'s machinery wholesale: query loading, the dedicated ``eval/index``,
the qrels, the corpus-relative path mapping (``_relpath`` inside
``_positional_scores``), and ranx scoring (``score``). The ONLY thing that differs
between the two blocks is the retrieval step:

- **Direct (control):** ``hybrid_search(top_n=20)`` + cross-encoder rerank to 10,
  exactly as ``run_eval.run_one_query`` does in hybrid mode.
- **Served (test):** the same index, reached through
  ``GET /api/search?mode=hybrid&limit=10&raw=true`` (in-process ASGI via
  ``TestClient`` — search isn't streamed, so the SSE-buffering problem doesn't
  apply; it still exercises ``run_search`` + the endpoint + serialization).

Both sides feed their ranked path list through the *same* ``_positional_scores``
(order-only, which is what we're comparing) and the *same* ``score``, so a
divergence can only come from the retrieval seam, never the verifier.

Config is ``raw=true`` on both sides — the API default and the canonical best
config. In raw mode ``parsed.semantic_query == q``, so the rerank query is the
same string on both sides too.

Deterministic (fixed models, no sampling, stable cosine over 301 docs), so the
honest expectation is **byte-identical rankings -> identical metrics**. Metrics
equal within FP noise (<=1e-6) passes; anything beyond that STOPS and reports,
with candidate/over-fetch counts surfaced on both sides so a divergence is
diagnosable at a glance.

Usage: ``uv run python eval/verify_served.py [--no-reindex]``
"""

from __future__ import annotations

import argparse
import json
import logging
import secrets
import time
from pathlib import Path

from fastapi.testclient import TestClient

# Reuse the harness — do NOT reimplement any of this, or a mismatch could be the
# verifier's artifact rather than the API's.
from run_eval import (  # type: ignore[import-not-found]
    CANDIDATE_TOP_N,
    FINAL_TOP_N,
    INDEX_DB,
    METRICS,
    QUERIES_PATH,
    RESULTS_DIR,
    Query,
    _positional_scores,
    build_index,
    load_queries,
    score,
)

from oasis.api.app import create_app
from oasis.index.db import open_db
from oasis.index.embeddings import SentenceTransformerEmbedder
from oasis.index.vector import VectorIndex
from oasis.query.parser import ParsedQuery
from oasis.query.reranker import CrossEncoderReranker
from oasis.query.retriever import hybrid_search

logger = logging.getLogger("oasis.verify_served")

OUT_PATH = RESULTS_DIR / "served_verification.json"
# The over-fetch counts, one per side. Direct fixes CANDIDATE_TOP_N=20; run_search
# in hybrid mode uses max(limit*2, 20) = 20 for limit=10. They MUST match, or the
# reranked top-10 can differ; this is the first thing to check on any divergence.
SERVED_CANDIDATE_TOP_N = max(FINAL_TOP_N * 2, 20)
# Within this, direct and served metrics are "equal" (deterministic rankings ->
# byte-identical, so any real gap is far larger than FP noise).
TOLERANCE = 1e-6


def _run_block(paths_by_id: dict[str, list[Path]], scored: list[Query]) -> dict:
    """Score one retrieval block through the SHARED downstream code.

    ``run_d`` uses the same __no_results__ sentinel run_eval uses so an empty
    result set scores a clean 0 rather than crashing ranx.
    """
    qrels_d = {q.id: q.relevant for q in scored}
    run_d: dict[str, dict[str, float]] = {}
    for q in scored:
        s = _positional_scores(paths_by_id[q.id])
        run_d[q.id] = s if s else {"__no_results__": 0.0}
    return score(qrels_d, run_d)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--no-reindex",
        dest="reindex",
        action="store_false",
        help="Reuse the existing eval index instead of rebuilding it fresh.",
    )
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    queries = load_queries(QUERIES_PATH)
    scored = [q for q in queries if not q.expects_empty]
    logger.info("Loaded %d queries (%d scored, %d expected-empty)",
                len(queries), len(scored), len(queries) - len(scored))

    # ---- shared models + index (the process-level _MODEL_CACHE means the app's
    # embedder/reranker are the SAME objects as these, so scores are bit-identical)
    embedder = SentenceTransformerEmbedder()
    lance_path = INDEX_DB.with_name(INDEX_DB.stem + ".lance")
    vector_index = VectorIndex(lance_path, dimension=embedder.dimension)
    build_index(reindex=args.reindex, embedder=embedder, vector_index=vector_index)
    reranker = CrossEncoderReranker()
    conn = open_db(INDEX_DB)

    # ---- DIRECT (control): the exact hybrid path run_eval uses. Only the
    # retrieval seam lives here; scoring/mapping is shared below.
    def direct_paths(q: Query) -> list[Path]:
        parsed = ParsedQuery(semantic_query=q.query)
        candidates = hybrid_search(conn, vector_index, embedder, parsed, top_n=CANDIDATE_TOP_N)
        candidates = reranker.rerank(parsed.semantic_query, candidates, top_n=FINAL_TOP_N)
        return [r.path for r in candidates]

    # ---- SERVED (test): the same index through the real endpoint.
    token = secrets.token_urlsafe(16)
    auth = {"Authorization": f"Bearer {token}"}
    app = create_app(token=token, db_path=INDEX_DB)

    direct_by_id: dict[str, list[Path]] = {}
    served_by_id: dict[str, list[Path]] = {}

    with TestClient(app) as client:
        # The lifespan spawns a background loader (real models); wait for ready.
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if client.get("/api/health").json()["status"] == "ready":
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("server never became ready")

        def served_paths(q: Query) -> list[Path]:
            r = client.get(
                "/api/search",
                params={"q": q.query, "mode": "hybrid", "limit": FINAL_TOP_N, "raw": True},
                headers=auth,
            )
            r.raise_for_status()
            return [Path(x["path"]) for x in r.json()["results"]]

        for q in queries:
            direct_by_id[q.id] = direct_paths(q)
            served_by_id[q.id] = served_paths(q)

    conn.close()

    # ---- exact-ranking comparison (stronger than metric equality: two different
    # orderings could coincidentally score the same, so compare the paths too).
    mismatches = []
    for q in queries:
        d = [str(p) for p in direct_by_id[q.id]]
        s = [str(p) for p in served_by_id[q.id]]
        if d != s:
            mismatches.append({"id": q.id, "query": q.query, "direct": d, "served": s})

    # ---- metrics through the shared downstream code
    direct_metrics = _run_block(direct_by_id, scored)
    served_metrics = _run_block(served_by_id, scored)
    deltas = {m: round(served_metrics[m] - direct_metrics[m], 8) for m in METRICS}
    metrics_ok = all(abs(deltas[m]) <= TOLERANCE for m in METRICS)
    rankings_identical = not mismatches
    passed = metrics_ok and rankings_identical

    report = {
        "passed": passed,
        "rankings_identical": rankings_identical,
        "metrics_within_tolerance": metrics_ok,
        "tolerance": TOLERANCE,
        "num_queries_total": len(queries),
        "num_queries_scored": len(scored),
        "num_queries_ranking_mismatch": len(mismatches),
        "candidate_counts": {
            # Surfaced on both sides: a divergence's most likely cause is these
            # differing. Equal here (20/20 -> rerank to 10) means the reranked
            # top-10 pool is the same on both sides.
            "direct": {"over_fetch": CANDIDATE_TOP_N, "rerank_to": FINAL_TOP_N},
            "served": {"over_fetch": SERVED_CANDIDATE_TOP_N, "rerank_to": FINAL_TOP_N},
            "match": CANDIDATE_TOP_N == SERVED_CANDIDATE_TOP_N,
        },
        "config": {"mode": "hybrid", "limit": FINAL_TOP_N, "raw": True},
        "direct": direct_metrics,
        "served": served_metrics,
        "delta": deltas,
        "mismatches": mismatches[:20],  # first 20 if any; empty on a pass
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, indent=2) + "\n")

    # ---- console report
    print("\n=== Served-vs-direct retrieval verification (raw hybrid, limit=10) ===")
    print(f"{'metric':14s} {'direct':>10s} {'served':>10s} {'delta':>12s}")
    for m in METRICS:
        print(f"{m:14s} {direct_metrics[m]:10.4f} {served_metrics[m]:10.4f} {deltas[m]:12.2e}")
    print(f"\ncandidate over-fetch: direct={CANDIDATE_TOP_N}  served={SERVED_CANDIDATE_TOP_N}  "
          f"(rerank to {FINAL_TOP_N})  match={CANDIDATE_TOP_N == SERVED_CANDIDATE_TOP_N}")
    print(f"exact ranking mismatches: {len(mismatches)}/{len(queries)} queries")
    print(f"\n{'PASS' if passed else 'FAIL'} — wrote {OUT_PATH}")
    if not passed:
        print("\nDIVERGENCE — do not average it away. First mismatches:")
        for mm in mismatches[:5]:
            print(f"  {mm['id']}: {mm['query'][:60]}")
            print(f"    direct: {[Path(p).name for p in mm['direct'][:5]]}")
            print(f"    served: {[Path(p).name for p in mm['served'][:5]]}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
