"""Oasis retrieval evaluation harness.

Runs every labeled query in ``eval/queries.yaml`` through Oasis' real search
path (``oasis.query.retriever.hybrid_search``, optionally followed by the
cross-encoder reranker — exactly what the CLI does in ``--mode hybrid``) and
scores the ranked results against the graded relevance judgments using ranx.

Metrics (computed by ranx):
    precision@5, precision@10  — fraction of the top k that is relevant
    recall@10                  — fraction of all relevant docs found in top 10
    mrr                        — mean reciprocal rank of the first relevant hit
    ndcg@10                    — graded, order-sensitive headline metric

Outputs:
    eval/results/latest.json   — full report (overall + per-tag + per-query)
    eval/results/history.jsonl — one line appended per run (for regression plots)

Usage:
    uv run python eval/run_eval.py                 # build index if needed, run
    uv run python eval/run_eval.py --reindex       # force a fresh index
    uv run python eval/run_eval.py --no-rerank     # score raw fusion only
    uv run python eval/run_eval.py --no-parse      # skip NL parsing (raw query)
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import yaml
from ranx import Qrels, Run, evaluate

from oasis.index.db import open_db
from oasis.index.embeddings import SentenceTransformerEmbedder
from oasis.index.pipeline import index_directory
from oasis.index.vector import VectorIndex
from oasis.llm.manager import ensure_ollama
from oasis.query.parser import ParsedQuery, parse_query
from oasis.query.reranker import CrossEncoderReranker
from oasis.query.retriever import hybrid_search

logger = logging.getLogger("oasis.eval")

# --------------------------------------------------------------------------- paths
EVAL_DIR = Path(__file__).resolve().parent
CORPUS_DIR = EVAL_DIR / "corpus"
QUERIES_PATH = EVAL_DIR / "queries.yaml"
INDEX_DB = EVAL_DIR / "index" / "index.db"
RESULTS_DIR = EVAL_DIR / "results"
LATEST_PATH = RESULTS_DIR / "latest.json"
HISTORY_PATH = RESULTS_DIR / "history.jsonl"

# The graded metrics ranx computes for us. Order = order in the report.
METRICS = ["precision@5", "precision@10", "recall@10", "mrr", "ndcg@10"]

# The corpus mtimes are spread deterministically over 2019-01..2026-06, so
# relative-date queries ("last year", "recent") assume "today" is mid-2026.
DEFAULT_TODAY = date(2026, 7, 7)

# How many candidates hybrid_search fetches before (optional) reranking. Mirrors
# the CLI, which over-fetches so the reranker has a real pool to reorder.
CANDIDATE_TOP_N = 20
FINAL_TOP_N = 10


# --------------------------------------------------------------------------- models
@dataclass
class Query:
    id: str
    query: str
    relevant: dict[str, int]  # relpath -> grade (>= 1)
    tags: list[str]
    notes: str | None = None

    @property
    def expects_empty(self) -> bool:
        """True for adversarial queries with no ground truth (relevant: [])."""
        return not self.relevant


def load_queries(path: Path) -> list[Query]:
    raw = yaml.safe_load(path.read_text())
    queries: list[Query] = []
    for item in raw:
        relevant = {
            rel["path"]: int(rel["grade"]) for rel in (item.get("relevant") or [])
        }
        queries.append(
            Query(
                id=item["id"],
                query=item["query"],
                relevant=relevant,
                tags=list(item.get("tags") or []),
                notes=item.get("notes"),
            )
        )
    return queries


# ----------------------------------------------------------------------- indexing
def build_index(
    *,
    reindex: bool,
    embedder: SentenceTransformerEmbedder,
    vector_index: VectorIndex,
) -> None:
    """Index the eval corpus into a dedicated DB, skipping unchanged files."""
    INDEX_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = open_db(INDEX_DB)
    try:
        stats = index_directory(
            conn,
            CORPUS_DIR,
            force=reindex,
            vector_index=vector_index,
            embedder=embedder,
        )
    finally:
        conn.close()
    logger.info(
        "Index ready: %d indexed, %d skipped, %d failed, %d chunks",
        stats["indexed"],
        stats["skipped"],
        stats["failed"],
        stats["chunks"],
    )


# ------------------------------------------------------------------------ searching
def _relpath(abs_path: Path) -> str:
    """Convert an absolute result path to a corpus-relative POSIX key.

    Qrels are keyed by paths like ``pdfs/manual-espresso-machine-em500.pdf``;
    hybrid_search returns absolute paths, so we make them relative to CORPUS_DIR
    to line the two up.
    """
    try:
        return abs_path.resolve().relative_to(CORPUS_DIR).as_posix()
    except ValueError:
        return abs_path.as_posix()


def run_one_query(
    q: Query,
    *,
    conn: sqlite3.Connection,
    vector_index: VectorIndex,
    embedder: SentenceTransformerEmbedder,
    reranker: CrossEncoderReranker | None,
    llm,
    today: date,
) -> dict[str, float]:
    """Return {relpath: score} for a single query (empty on any search error)."""
    # 1. Parse the NL query exactly like the CLI (LLM if available, else raw).
    if llm is not None:
        try:
            parsed = parse_query(q.query, llm, today=today)
        except Exception:
            logger.warning("Parse failed for %s; falling back to raw", q.id)
            parsed = ParsedQuery(semantic_query=q.query)
    else:
        parsed = ParsedQuery(semantic_query=q.query)

    # 2. Fuse FTS5 + vector. Adversarial FTS syntax (q079/q080) can raise
    #    OperationalError — treat that as "no results" rather than crashing,
    #    which is the behavior the CLI degrades to.
    try:
        candidates = hybrid_search(
            conn, vector_index, embedder, parsed, top_n=CANDIDATE_TOP_N
        )
    except sqlite3.OperationalError as exc:
        logger.warning("Search error for %s (%s) — scoring as empty", q.id, exc)
        return {}

    # 3. Optional cross-encoder rerank (the CLI's hybrid default).
    if reranker is not None and candidates:
        candidates = reranker.rerank(parsed.semantic_query, candidates, top_n=FINAL_TOP_N)
    else:
        candidates = candidates[:FINAL_TOP_N]

    return {_relpath(r.path): float(r.score) for r in candidates}


# -------------------------------------------------------------------------- scoring
def _slice(qrels_d: dict, run_d: dict, ids: set[str]) -> tuple[dict, dict]:
    return (
        {qid: qrels_d[qid] for qid in ids},
        {qid: run_d[qid] for qid in ids},
    )


def score(qrels_d: dict, run_d: dict) -> dict[str, float]:
    """Evaluate a qrels/run pair, returning {metric: value} rounded to 4 dp."""
    if not qrels_d:
        return {m: 0.0 for m in METRICS}
    results = evaluate(Qrels(qrels_d), Run(run_d), METRICS)
    # ranx returns a bare float when given a single metric; normalize to dict.
    if isinstance(results, (int, float)):
        results = {METRICS[0]: float(results)}
    return {m: round(float(results[m]), 4) for m in METRICS}


def per_query_scores(qrels_d: dict, run_d: dict) -> dict[str, dict[str, float]]:
    """Per-query metric values (uses ranx return_mean=False)."""
    if not qrels_d:
        return {}
    ids = list(qrels_d)
    out: dict[str, dict[str, float]] = {qid: {} for qid in ids}
    for m in METRICS:
        arr = evaluate(Qrels(qrels_d), Run(run_d), m, return_mean=False)
        for qid, val in zip(ids, arr, strict=False):
            out[qid][m] = round(float(val), 4)
    return out


# ----------------------------------------------------------------------------- git
def _git_commit() -> dict[str, str | bool]:
    def _run(args: list[str]) -> str | None:
        try:
            out = subprocess.run(
                ["git", *args], capture_output=True, text=True, cwd=EVAL_DIR
            )
            return out.stdout.strip() if out.returncode == 0 else None
        except Exception:
            return None

    commit = _run(["rev-parse", "HEAD"]) or "unknown"
    status = _run(["status", "--porcelain"])
    return {"commit": commit, "dirty": bool(status)}


# ----------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description="Run the Oasis retrieval eval.")
    ap.add_argument("--reindex", action="store_true", help="Force a fresh index.")
    ap.add_argument(
        "--no-rerank",
        dest="rerank",
        action="store_false",
        help="Score raw RRF fusion without the cross-encoder reranker.",
    )
    ap.add_argument(
        "--no-parse",
        dest="parse",
        action="store_false",
        help="Skip LLM NL parsing; search with the raw query string.",
    )
    ap.add_argument(
        "--today",
        type=date.fromisoformat,
        default=DEFAULT_TODAY,
        help="Reference date for relative-date queries (default: 2026-07-07).",
    )
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    queries = load_queries(QUERIES_PATH)
    logger.info("Loaded %d queries from %s", len(queries), QUERIES_PATH.name)

    # ------------------------------------------------------------ load shared models
    embedder = SentenceTransformerEmbedder()
    lance_path = INDEX_DB.with_name(INDEX_DB.stem + ".lance")
    vector_index = VectorIndex(lance_path, dimension=embedder.dimension)
    build_index(reindex=args.reindex, embedder=embedder, vector_index=vector_index)

    reranker = CrossEncoderReranker() if args.rerank else None
    llm = ensure_ollama() if args.parse else None
    if args.parse and llm is None:
        logger.warning(
            "Ollama unavailable — running with raw queries "
            "(file-type/date/folder filters will not be applied)."
        )

    # ------------------------------------------------------------------- run queries
    conn = open_db(INDEX_DB)
    run_by_id: dict[str, dict[str, float]] = {}
    try:
        for q in queries:
            results = run_one_query(
                q,
                conn=conn,
                vector_index=vector_index,
                embedder=embedder,
                reranker=reranker,
                llm=llm,
                today=args.today,
            )
            run_by_id[q.id] = results
            logger.info("%s  (%d results)  %s", q.id, len(results), q.query[:60])
    finally:
        conn.close()

    # --------------------------------------------------------- build qrels / run sets
    # Scored queries have ground truth; expected-empty ones are reported apart.
    scored = [q for q in queries if not q.expects_empty]
    empty = [q for q in queries if q.expects_empty]

    qrels_d: dict[str, dict[str, int]] = {q.id: q.relevant for q in scored}
    run_d: dict[str, dict[str, float]] = {}
    for q in scored:
        res = run_by_id[q.id]
        # ranx needs every qrels query present in the run; use a sentinel
        # (grade-0, never in qrels) so empty result sets score a clean 0.
        run_d[q.id] = res if res else {"__no_results__": 0.0}

    overall = score(qrels_d, run_d)
    logger.info("Overall: %s", overall)

    # ------------------------------------------------------------ per-tag breakdown
    all_tags = sorted({t for q in scored for t in q.tags})
    by_tag: dict[str, dict] = {}
    for tag in all_tags:
        ids = {q.id for q in scored if tag in q.tags}
        sub_qrels, sub_run = _slice(qrels_d, run_d, ids)
        by_tag[tag] = {"n": len(ids), **score(sub_qrels, sub_run)}

    # ---------------------------------------------------- expected-empty diagnostics
    expected_empty = {
        q.id: {
            "query": q.query,
            "num_results": len(run_by_id[q.id]),
            "top_paths": list(
                dict(
                    sorted(run_by_id[q.id].items(), key=lambda kv: kv[1], reverse=True)
                )
            )[:3],
        }
        for q in empty
    }

    # ------------------------------------------------------------------ assemble report
    git = _git_commit()
    timestamp = datetime.now(UTC).isoformat()
    config = {
        "rerank": args.rerank,
        "parse": args.parse,
        "llm_used": llm is not None,
        "today": args.today.isoformat(),
        "candidate_top_n": CANDIDATE_TOP_N,
        "final_top_n": FINAL_TOP_N,
    }
    report = {
        "timestamp": timestamp,
        "git": git,
        "config": config,
        "corpus_files": sum(1 for _ in CORPUS_DIR.rglob("*") if _.is_file()),
        "num_queries_total": len(queries),
        "num_queries_scored": len(scored),
        "num_queries_expected_empty": len(empty),
        "overall": overall,
        "by_tag": by_tag,
        "expected_empty": expected_empty,
        "per_query": per_query_scores(qrels_d, run_d),
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_PATH.write_text(json.dumps(report, indent=2) + "\n")
    logger.info("Wrote %s", LATEST_PATH)

    history_row = {
        "timestamp": timestamp,
        "git": git,
        "config": config,
        "num_queries_scored": len(scored),
        "overall": overall,
    }
    with HISTORY_PATH.open("a") as f:
        f.write(json.dumps(history_row) + "\n")
    logger.info("Appended run to %s", HISTORY_PATH)

    # --------------------------------------------------------------- console summary
    print("\n=== Oasis eval — overall ===")
    for m in METRICS:
        print(f"  {m:14s} {overall[m]:.4f}")
    print(f"\nScored {len(scored)} queries ({len(empty)} expected-empty reported separately).")
    print(f"Full report: {LATEST_PATH}")


if __name__ == "__main__":
    main()
