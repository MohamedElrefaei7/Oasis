"""Generate the README's charts from the committed eval results.

Every number is read out of ``eval/results/`` at run time — nothing here is
typed in by hand. That is the point: a chart with hardcoded figures is a claim
that drifts silently away from the run it names, which is exactly the failure
the eval harness exists to prevent. If a source file is missing the script says
which one and stops, rather than drawing something plausible.

Run it from the repo root:

    pixi run -e dev python eval/plot_readme.py

Writes PNGs into ``docs/img/``. Those are committed; the README embeds them.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  (after the backend is fixed)

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "eval" / "results"
ABLATIONS = RESULTS / "ablations"
OUT_DIR = ROOT / "docs" / "img"

# One palette for all three figures so the README reads as one document.
INK = "#1c1c1e"
MUTED = "#6e6e73"
GRID = "#d8d8dc"
PRIMARY = "#2f6f9f"      # the shipping / after configuration
SECONDARY = "#a8c4d8"    # the comparison / before configuration
WARN = "#b4553f"         # a regression
NEUTRAL = "#b8b8bd"

plt.rcParams.update(
    {
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "axes.edgecolor": GRID,
        "axes.labelcolor": INK,
        "text.color": INK,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.titleweight": "bold",
        "figure.dpi": 160,
    }
)


@dataclass(frozen=True)
class Run:
    """One eval report on disk."""

    path: Path

    def load(self) -> dict:
        if not self.path.exists():
            sys.exit(
                f"missing eval result: {self.path.relative_to(ROOT)}\n"
                "Regenerate it with eval/run_eval.py (see the README's Measured "
                "results section for the flags each row uses)."
            )
        return json.loads(self.path.read_text())

    def overall(self) -> dict[str, float]:
        return self.load()["overall"]

    def per_query(self) -> dict[str, dict[str, float]]:
        return self.load()["per_query"]

    def day(self) -> str:
        return self.load()["timestamp"][:10]


def _style_axes(ax, ymax: float) -> None:
    """Horizontal grid only, and a y-axis that always starts at zero.

    Truncating the y-axis is the standard way to make a small difference look
    decisive. These charts exist to be believed, so the baseline stays at 0.
    """
    ax.set_ylim(0, ymax)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.xaxis.grid(False)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.tick_params(axis="both", length=0)


def _label_bars(ax, bars, fmt="{:.3f}", size=8) -> None:
    for bar in bars:
        h = bar.get_height()
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            h + 0.012,
            fmt.format(h),
            ha="center",
            va="bottom",
            fontsize=size,
            color=MUTED,
        )


def _footer(fig, text: str) -> None:
    fig.text(0.5, 0.012, text, ha="center", fontsize=7.5, color=MUTED)


# ---------------------------------------------------------------- chart 1
def chart_retrieval_modes() -> None:
    """Grouped bars: each retrieval mode across three metrics.

    Reads the four-row matrix measured together on the current stack, so the
    rows are comparable to each other — which is the only thing that makes a
    grouped bar chart honest.
    """
    matrix = ABLATIONS / "matrix-current"
    rows = [
        ("keyword\n(BM25)", Run(matrix / "keyword.json"), NEUTRAL),
        ("semantic\n(vectors)", Run(matrix / "semantic.json"), SECONDARY),
        ("hybrid\n(RRF fusion)", Run(matrix / "hybrid.json"), "#7ba3c2"),
        ("hybrid + rerank\n(shipping)", Run(matrix / "hybrid-ce.json"), PRIMARY),
    ]
    metrics = ["ndcg@10", "mrr", "recall@10"]

    fig, ax = plt.subplots(figsize=(8.2, 4.2))
    n = len(rows)
    width = 0.78 / n
    for i, (label, run, color) in enumerate(rows):
        overall = run.overall()
        xs = [j + (i - (n - 1) / 2) * width for j in range(len(metrics))]
        bars = ax.bar(
            xs, [overall[m] for m in metrics], width * 0.92, label=label, color=color
        )
        _label_bars(ax, bars)

    ax.set_xticks(range(len(metrics)))
    ax.set_xticklabels(metrics)
    ax.set_ylabel("score")
    ax.set_title("Retrieval quality by mode")
    _style_axes(ax, 1.0)
    ax.legend(frameon=False, ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.09),
              fontsize=8.5, handlelength=1.2, columnspacing=1.4)

    day = rows[-1][1].day()
    _footer(
        fig,
        f"80 scored queries over the 300-file labeled corpus · NL parsing off · "
        f"all four rows measured together, {day}",
    )
    fig.tight_layout(rect=(0, 0.10, 1, 1))
    out = OUT_DIR / "retrieval-modes.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out.relative_to(ROOT)}")


# ---------------------------------------------------------------- chart 2
def chart_parsing_reversal() -> None:
    """Raw query vs LLM-parsed query, ndcg@10, per mode.

    The two columns come from the same day's paired runs; the absolute values
    predate filename indexing, which is why the README quotes the *delta* and
    not these levels.
    """
    pairs = [
        ("keyword", Run(ABLATIONS / "keyword.json"), Run(ABLATIONS / "keyword-llm.json")),
        ("semantic", Run(ABLATIONS / "semantic.json"), Run(ABLATIONS / "semantic-llm.json")),
        ("hybrid", Run(ABLATIONS / "hybrid.json"), Run(ABLATIONS / "hybrid-llm.json")),
        ("hybrid + rerank", Run(ABLATIONS / "hybrid-ce.json"), Run(ABLATIONS / "hybrid-ce-llm.json")),
    ]

    fig, ax = plt.subplots(figsize=(8.2, 4.2))
    width = 0.36
    xs = range(len(pairs))
    raw_vals = [p[1].overall()["ndcg@10"] for p in pairs]
    par_vals = [p[2].overall()["ndcg@10"] for p in pairs]

    b1 = ax.bar([x - width / 2 for x in xs], raw_vals, width, label="raw query (shipping)", color=PRIMARY)
    b2 = ax.bar([x + width / 2 for x in xs], par_vals, width, label="LLM-parsed into filters", color=WARN)
    _label_bars(ax, b1)
    _label_bars(ax, b2)

    # The deltas are the finding; put them where the eye already is.
    for x, (raw, par) in enumerate(zip(raw_vals, par_vals, strict=True)):
        delta = par - raw
        ax.text(
            x,
            max(raw, par) + 0.075,
            f"{delta:+.3f}",
            ha="center",
            fontsize=9,
            fontweight="bold",
            color=WARN if delta < 0 else "#3f7d4f",
        )

    ax.set_xticks(list(xs))
    ax.set_xticklabels([p[0] for p in pairs])
    ax.set_ylabel("ndcg@10")
    ax.set_title("What NL query parsing costs")
    _style_axes(ax, 0.72)
    ax.legend(frameon=False, ncol=2, loc="upper center", bbox_to_anchor=(0.5, -0.09),
              fontsize=8.5, handlelength=1.2, columnspacing=1.6)

    day = pairs[-1][1].day()
    _footer(
        fig,
        f"Paired runs, {day} — measured before filename indexing, so the deltas "
        f"are the finding and the levels are historical",
    )
    fig.tight_layout(rect=(0, 0.10, 1, 1))
    out = OUT_DIR / "parsing-reversal.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out.relative_to(ROOT)}")


# ---------------------------------------------------------------- chart 3
def chart_filename_signal() -> None:
    """The filename change: the average, and the distribution behind it.

    Two panels on purpose. The left is the headline the average supports; the
    right is the part an average hides — the change helps and hurts different
    queries, and reporting the net alone would be the misleading half.
    """
    before = Run(ABLATIONS / "baseline-filename.json")
    after = Run(ABLATIONS / "filename-v1.json")
    metrics = ["ndcg@10", "mrr", "recall@10"]

    b_overall, a_overall = before.overall(), after.overall()

    # The win/loss split, recomputed from per-query scores rather than quoted.
    b_pq, a_pq = before.per_query(), after.per_query()
    better = worse = unchanged = 0
    for qid, b in b_pq.items():
        a = a_pq.get(qid)
        if a is None or "ndcg@10" not in b or "ndcg@10" not in a:
            continue
        if a["ndcg@10"] > b["ndcg@10"]:
            better += 1
        elif a["ndcg@10"] < b["ndcg@10"]:
            worse += 1
        else:
            unchanged += 1

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.4, 4.2), gridspec_kw={"width_ratios": [1.5, 1]})

    width = 0.36
    xs = range(len(metrics))
    b1 = ax1.bar([x - width / 2 for x in xs], [b_overall[m] for m in metrics], width,
                 label="filename not indexed", color=SECONDARY)
    b2 = ax1.bar([x + width / 2 for x in xs], [a_overall[m] for m in metrics], width,
                 label="filename as its own signal", color=PRIMARY)
    _label_bars(ax1, b1)
    _label_bars(ax1, b2)
    ax1.set_xticks(list(xs))
    ax1.set_xticklabels(metrics)
    ax1.set_ylabel("score")
    ax1.set_title("Averages")
    _style_axes(ax1, 1.0)
    ax1.legend(frameon=False, ncol=1, loc="upper left", fontsize=8.5, handlelength=1.2)

    counts = [better, worse, unchanged]
    labels = ["better", "worse", "unchanged"]
    bars = ax2.bar(labels, counts, 0.55, color=["#3f7d4f", WARN, NEUTRAL])
    for bar, c in zip(bars, counts, strict=True):
        ax2.text(bar.get_x() + bar.get_width() / 2, c + 0.8, str(c),
                 ha="center", fontsize=10, fontweight="bold", color=MUTED)
    ax2.set_ylabel("queries")
    ax2.set_title(f"Per-query, of {sum(counts)}")
    _style_axes(ax2, max(counts) * 1.28)

    day = after.day()
    _footer(
        fig,
        f"hybrid + cross-encoder, NL parsing off, {day} — the average is +"
        f"{a_overall['ndcg@10'] - b_overall['ndcg@10']:.3f} ndcg@10, and it is not a uniform improvement",
    )
    fig.suptitle("Indexing the filename", fontsize=12, fontweight="bold", y=0.99)
    fig.tight_layout(rect=(0, 0.055, 1, 0.95))
    out = OUT_DIR / "filename-signal.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out.relative_to(ROOT)}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    chart_retrieval_modes()
    chart_parsing_reversal()
    chart_filename_signal()


if __name__ == "__main__":
    main()
