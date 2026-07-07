"""Plot Oasis eval metrics over time from eval/results/history.jsonl.

Reads every run appended by run_eval.py and draws headline metrics against
run index (labeled by short commit / timestamp). NDCG@10 is the headline line;
precision@5 and MRR are shown for context.

Usage:
    uv run python eval/plot.py                 # -> eval/results/metrics_over_time.png
    uv run python eval/plot.py --metric ndcg@10 mrr
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: write a file, don't open a window
import matplotlib.pyplot as plt  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent
HISTORY_PATH = EVAL_DIR / "results" / "history.jsonl"
OUT_PATH = EVAL_DIR / "results" / "metrics_over_time.png"

DEFAULT_METRICS = ["ndcg@10", "precision@5", "mrr"]


def load_history(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"No history at {path}. Run eval/run_eval.py first.")
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows:
        raise SystemExit(f"{path} is empty. Run eval/run_eval.py first.")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Plot eval metrics over time.")
    ap.add_argument(
        "--metric", nargs="+", default=DEFAULT_METRICS, help="Metrics to plot."
    )
    ap.add_argument("--out", type=Path, default=OUT_PATH, help="Output PNG path.")
    args = ap.parse_args()

    rows = load_history(HISTORY_PATH)
    x = list(range(len(rows)))
    labels = [
        f"{i}\n{r.get('git', {}).get('commit', '?')[:7]}" for i, r in enumerate(rows)
    ]

    fig, ax = plt.subplots(figsize=(max(6, len(rows) * 0.8), 5))
    for metric in args.metric:
        ys = [r["overall"].get(metric) for r in rows]
        ax.plot(x, ys, marker="o", label=metric)

    ax.set_title("Oasis retrieval quality over time")
    ax.set_xlabel("run (index / commit)")
    ax.set_ylabel("score")
    ax.set_ylim(0, 1)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8)
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=120)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
