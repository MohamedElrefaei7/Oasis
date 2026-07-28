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
    uv run python eval/run_eval.py --mode keyword  # BM25-only baseline

The four rows of the retrieval comparison table, none of which pollute the
regression history:

    --mode keyword  --no-history --out results/ablations/keyword.json
    --mode semantic --no-history --out results/ablations/semantic.json
    --mode hybrid --no-rerank --no-history --out results/ablations/hybrid.json
    --mode hybrid   --no-history --out results/ablations/hybrid-ce.json
"""

from __future__ import annotations

import argparse
import hashlib
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
from oasis.index.keyword import KeywordIndex
from oasis.index.pipeline import index_directory
from oasis.index.vector import VectorIndex
from oasis.llm.manager import ensure_ollama
from oasis.llm.ollama import DEFAULT_MODEL as OLLAMA_MODEL
from oasis.query.parser import _SYSTEM_PROMPT, ParsedQuery, parse_query
from oasis.query.reranker import CrossEncoderReranker
from oasis.query.retriever import (
    build_fts_query,
    build_kw_filters,
    build_vec_where,
    hybrid_search,
)

logger = logging.getLogger("oasis.eval")

# --------------------------------------------------------------------------- paths
EVAL_DIR = Path(__file__).resolve().parent
CORPUS_DIR = EVAL_DIR / "corpus"
QUERIES_PATH = EVAL_DIR / "queries.yaml"
INDEX_DB = EVAL_DIR / "index" / "index.db"
RESULTS_DIR = EVAL_DIR / "results"
LATEST_PATH = RESULTS_DIR / "latest.json"
HISTORY_PATH = RESULTS_DIR / "history.jsonl"
PARSE_CACHE_PATH = RESULTS_DIR / "parse_cache.json"

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


def _positional_scores(paths: list[Path]) -> dict[str, float]:
    """Score an already-ranked list by position, best first.

    ranx ranks documents by score *descending*, but the two arms disagree on
    sign: FTS5's bm25 ``rank`` is negative (more negative = better, hence
    ``ORDER BY rank`` ascending) while vector ``_distance`` is positive (lower
    = better).  Feeding either raw to ranx would silently invert the ranking
    and make that mode look far worse than it is.

    Both arms already return correctly-ordered lists, and every metric here is
    rank-based (absolute scores never matter, only order), so scoring on
    position is both sign-agnostic and honest.
    """
    out: dict[str, float] = {}
    for i, p in enumerate(paths):
        key = _relpath(p)
        if key not in out:  # keep the best rank if a path somehow repeats
            out[key] = float(len(paths) - i)
    return out


class ParseCache:
    """Disk cache of LLM query parses, keyed by (query, today, model, prompt).

    Two reasons, and the second matters more than the speed:

    1. A parse costs ~35s on a 3b model, and it depends only on the query text
       and *today* — never on the retrieval mode.  Running the four-mode
       comparison re-parses the same 83 queries four times for identical
       results: ~3.5 hours instead of ~50 minutes.

    2. **It makes the comparison honest.**  Without a cache each row samples
       the LLM independently, so row-to-row deltas mix the retrieval strategy
       (what we're measuring) with LLM sampling variance (noise).  Sharing one
       set of parses makes retrieval the only variable across rows.

    The key includes the system prompt hash, so editing the prompt invalidates
    the cache automatically rather than silently serving stale parses.

    **Failures are cached too, as an explicit null.**  llama3.2:3b fails to
    produce a valid ParsedQuery for roughly a quarter of the query set, and it
    fails *non-deterministically* — a retry often succeeds.  If failures were
    left uncached, every run would re-attempt them and a different subset would
    succeed each time, so each row of the comparison would run against a
    different parse set.  Worse, the cache would warm monotonically across a
    four-run sweep: the last row (hybrid+CE) would get the most successful
    parses and the first (keyword) the fewest, biasing the table in favour of
    whichever mode happened to run last.  Freezing failures makes all rows
    share one input set.  For a comparison, determinism beats optimism — and a
    user only gets one attempt per query anyway, so the frozen rate is the
    honest one.  Use --retry-failed-parses to re-attempt them.
    """

    #: distinguishes "known failure" from "not in cache" (both are falsy)
    FAILED = object()

    def __init__(self, path: Path, *, enabled: bool = True, retry_failed: bool = False) -> None:
        self.path = path
        self.enabled = enabled
        self.retry_failed = retry_failed
        self._data: dict[str, dict | None] = {}
        self.hits = 0
        self.misses = 0
        self.cached_failures = 0
        if enabled and path.exists():
            try:
                self._data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                logger.warning("Parse cache unreadable; starting empty.")

    @staticmethod
    def key(query: str, today: date, model: str, prompt: str) -> str:
        blob = "\x00".join([query, today.isoformat(), model, prompt])
        return hashlib.sha256(blob.encode()).hexdigest()[:20]

    def get(self, k: str):
        """Return a ParsedQuery, FAILED (known bad), or None (not cached)."""
        if not self.enabled or k not in self._data:
            return None
        raw = self._data[k]
        if raw is None:
            if self.retry_failed:
                return None
            self.hits += 1
            self.cached_failures += 1
            return self.FAILED
        try:
            hit = ParsedQuery.model_validate(raw)
        except Exception:
            return None  # schema changed under the cache — treat as a miss
        self.hits += 1
        return hit

    def put(self, k: str, parsed: ParsedQuery | None) -> None:
        """Record a parse, or None to freeze a failure."""
        self.misses += 1
        if self.enabled:
            self._data[k] = parsed.model_dump(mode="json") if parsed is not None else None

    def save(self) -> None:
        if not self.enabled:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, sort_keys=True) + "\n")


def _parse(q: Query, llm, today: date, cache: ParseCache) -> tuple[ParsedQuery, bool]:
    """Parse the NL query exactly like the CLI (LLM if available, else raw).

    Returns (parsed, llm_actually_parsed).  The second element exists because
    ``ensure_ollama()`` returning a provider does **not** mean the provider
    works — it only checks that the server answers HTTP and that the model is
    listed, neither of which catches a broken inference backend.  Every parse
    can fail while the harness happily reports ``llm_used: true``, producing a
    report that claims to measure NL parsing while measuring raw queries.
    """
    if llm is None:
        return ParsedQuery(semantic_query=q.query), False

    k = ParseCache.key(q.query, today, OLLAMA_MODEL, _SYSTEM_PROMPT)
    cached = cache.get(k)
    if cached is ParseCache.FAILED:
        # Frozen failure: this query is known not to parse with this model.
        return ParsedQuery(semantic_query=q.query), False
    if cached is not None:
        return cached, True

    try:
        parsed = parse_query(q.query, llm, today=today)
    except Exception as exc:
        logger.warning("Parse failed for %s (%s); falling back to raw", q.id, type(exc).__name__)
        cache.put(k, None)  # freeze it so every row sees the same failure
        return ParsedQuery(semantic_query=q.query), False

    cache.put(k, parsed)
    return parsed, True


def run_one_query(
    q: Query,
    *,
    mode: str,
    conn: sqlite3.Connection,
    vector_index: VectorIndex,
    embedder: SentenceTransformerEmbedder,
    reranker: CrossEncoderReranker | None,
    llm,
    today: date,
    cache: ParseCache,
) -> tuple[dict[str, float], bool]:
    """Return ({relpath: score}, llm_parsed) — results empty on a search error.

    Each mode mirrors the corresponding ``oasis search --mode`` branch in
    ``cli/app.py``, so the numbers describe the shipping system rather than a
    parallel implementation that only exists in the harness.
    """
    parsed, llm_parsed = _parse(q, llm, today, cache)

    # ------------------------------------------------------------- keyword (BM25)
    if mode == "keyword":
        # No second arm to fall back to, so a malformed FTS5 expression is
        # genuinely empty here — same as the CLI, which exits 1.
        try:
            kw = KeywordIndex(conn).search(
                build_fts_query(parsed), limit=FINAL_TOP_N, **build_kw_filters(parsed)
            )
        except sqlite3.OperationalError as exc:
            logger.warning("Search error for %s (%s) — scoring as empty", q.id, exc)
            return {}, llm_parsed
        return _positional_scores([r.path for r in kw]), llm_parsed

    # ---------------------------------------------------------- semantic (vector)
    if mode == "semantic":
        # Never parses the query as an expression, so FTS5 syntax can't bite.
        # Over-fetch, then dedupe to the best chunk per doc — exactly as the CLI.
        qv = embedder.embed([parsed.semantic_query])[0]
        raw = vector_index.search(qv, limit=FINAL_TOP_N * 3, where=build_vec_where(parsed))
        best: dict[str, object] = {}
        for r in raw:
            if r.path not in best or r.score < best[r.path].score:  # type: ignore[union-attr]
                best[r.path] = r
        deduped = sorted(best.values(), key=lambda r: r.score)[:FINAL_TOP_N]  # type: ignore[attr-defined]
        return _positional_scores([Path(r.path) for r in deduped]), llm_parsed  # type: ignore[attr-defined]

    # --------------------------------------------------------------------- hybrid
    # The arms fail independently, so an FTS5 syntax error degrades to
    # semantic-only rather than losing the query.  This still catches the case
    # where *both* arms fail, which hybrid_search re-raises.
    try:
        candidates = hybrid_search(
            conn, vector_index, embedder, parsed, top_n=CANDIDATE_TOP_N
        )
    except sqlite3.OperationalError as exc:
        logger.warning("Search error for %s (%s) — scoring as empty", q.id, exc)
        return {}, llm_parsed

    if reranker is not None and candidates:
        candidates = reranker.rerank(parsed.semantic_query, candidates, top_n=FINAL_TOP_N)
    else:
        candidates = candidates[:FINAL_TOP_N]

    # RRF scores and CE logits are both higher-is-better and already sorted.
    return {_relpath(r.path): float(r.score) for r in candidates}, llm_parsed


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
        "--mode",
        choices=["keyword", "semantic", "hybrid"],
        default="hybrid",
        help="Retrieval strategy, mirroring `oasis search --mode` (default: hybrid).",
    )
    ap.add_argument(
        "--rerank",
        dest="rerank",
        action="store_true",
        default=None,
        help="Force the cross-encoder reranker on (default: on for hybrid, off otherwise).",
    )
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
        "--no-history",
        dest="history",
        action="store_false",
        help="Don't append to history.jsonl. Use for ablation runs — the history "
             "is a regression time series and mixing modes into it is meaningless.",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=LATEST_PATH,
        help=f"Where to write the full report (default: {LATEST_PATH.name}).",
    )
    ap.add_argument(
        "--no-parse-cache",
        dest="parse_cache",
        action="store_false",
        help="Ignore the on-disk parse cache and re-run every LLM parse.",
    )
    ap.add_argument(
        "--retry-failed-parses",
        action="store_true",
        help="Re-attempt parses previously frozen as failures (they are "
             "non-deterministic, so this changes the parse set — don't use it "
             "between rows of a comparison).",
    )
    ap.add_argument(
        "--today",
        type=date.fromisoformat,
        default=DEFAULT_TODAY,
        help="Reference date for relative-date queries (default: 2026-07-07).",
    )
    args = ap.parse_args()

    # The CLI only reranks in hybrid mode; default to matching it so `--mode
    # keyword` measures what `oasis search --mode keyword` actually does rather
    # than an ablation the product never runs.
    if args.rerank and args.mode != "hybrid":
        logger.warning("--rerank only applies to --mode hybrid; ignoring.")
    rerank_enabled = (
        args.rerank if args.rerank is not None else True
    ) and args.mode == "hybrid"

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

    reranker = CrossEncoderReranker() if rerank_enabled else None
    llm = ensure_ollama() if args.parse else None
    if args.parse and llm is None:
        logger.warning(
            "Ollama unavailable — running with raw queries "
            "(file-type/date/folder filters will not be applied)."
        )
    logger.info(
        "Config: mode=%s rerank=%s parse=%s llm_used=%s",
        args.mode, rerank_enabled, args.parse, llm is not None,
    )

    # ------------------------------------------------------------------- run queries
    cache = ParseCache(PARSE_CACHE_PATH, enabled=args.parse_cache,
                       retry_failed=args.retry_failed_parses)
    conn = open_db(INDEX_DB)
    run_by_id: dict[str, dict[str, float]] = {}
    parse_ok = 0
    try:
        for q in queries:
            results, llm_parsed = run_one_query(
                q,
                mode=args.mode,
                conn=conn,
                vector_index=vector_index,
                embedder=embedder,
                reranker=reranker,
                llm=llm,
                today=args.today,
                cache=cache,
            )
            run_by_id[q.id] = results
            parse_ok += int(llm_parsed)
            logger.info("%s  (%d results)  %s", q.id, len(results), q.query[:60])
    finally:
        conn.close()
        cache.save()

    if llm is not None:
        logger.info("Parse cache: %d hits (%d frozen failures), %d misses (%s)",
                    cache.hits, cache.cached_failures, cache.misses, PARSE_CACHE_PATH.name)

    parse_failed = len(queries) - parse_ok
    if llm is not None and parse_ok == 0:
        logger.error(
            "LLM parsing produced NOTHING: all %d parses failed, so this run measured "
            "RAW queries with no file-type/date/folder filters. ensure_ollama() only "
            "checks that the server answers and the model is listed — it does not "
            "verify that inference works. Reporting llm_used=false.",
            len(queries),
        )
    elif llm is not None and parse_failed:
        logger.warning("%d/%d parses fell back to raw.", parse_failed, len(queries))

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
        "mode": args.mode,
        "rerank": rerank_enabled,
        "parse": args.parse,
        # "did the LLM actually contribute to these numbers", not "was a
        # provider object constructed" — those came apart the first time a
        # broken Ollama backend answered health checks and 500'd every
        # inference, and the report claimed llm_used: true regardless.
        "llm_used": parse_ok > 0,
        "llm_provider_available": llm is not None,
        "llm_parse_ok": parse_ok,
        "llm_parse_failed": parse_failed,
        "llm_model": OLLAMA_MODEL,
        "parse_cache_hits": cache.hits,
        "parse_cache_frozen_failures": cache.cached_failures,
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
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    logger.info("Wrote %s", args.out)

    if args.history:
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
    else:
        logger.info("Skipped history append (--no-history).")

    # --------------------------------------------------------------- console summary
    label = args.mode + ("+ce" if rerank_enabled else "")
    llm_state = f"on ({parse_ok}/{len(queries)} parsed)" if parse_ok else "off"
    print(f"\n=== Oasis eval — {label} (llm={llm_state}) ===")
    for m in METRICS:
        print(f"  {m:14s} {overall[m]:.4f}")
    print(f"\nScored {len(scored)} queries ({len(empty)} expected-empty reported separately).")
    print(f"Full report: {args.out}")


if __name__ == "__main__":
    main()
