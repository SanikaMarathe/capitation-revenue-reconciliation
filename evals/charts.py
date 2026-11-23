"""Static PNG charts for the deck (light mode, validated default palette).

Palette slots (validated with the dataviz validator: CVD + normal-vision separation pass;
aqua is below 3:1 contrast, so every bar carries a printed value label).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

plt.rcParams["text.parse_math"] = False      # "$400" is money, not a math formula

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
BLUE, ORANGE = "#2a78d6", "#eb6834"
SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]


def _style(ax, title: str, subtitle: str = "") -> None:
    ax.set_facecolor(SURFACE)
    ax.figure.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
    ax.tick_params(colors=INK_2, labelsize=9)
    ax.grid(axis="x" if ax.get_xlim() else "y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    # Title and subtitle sit at the figure's left edge, so long axis labels can't push them off-canvas.
    fig = ax.figure
    fig.text(0.015, 0.975, title, color=INK, fontsize=12, fontweight="bold", va="top")
    if subtitle:
        fig.text(0.015, 0.975 - 0.32 / fig.get_figheight(), subtitle, color=INK_2, fontsize=9, va="top")


def _finish(fig, path: Path) -> None:
    top = 1 - 0.75 / fig.get_figheight()
    fig.tight_layout(rect=(0, 0, 1, top))
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def _hbars(path: Path, labels: list[str], values: list[float], title: str, subtitle: str, fmt: str,
           colors: list[str] | None = None, xmax: float | None = None) -> None:
    fig, ax = plt.subplots(figsize=(7.5, 0.55 * len(labels) + 1.4), dpi=160)
    y = range(len(labels))[::-1]
    bars = ax.barh(list(y), values, height=0.6, color=colors or BLUE, edgecolor=SURFACE, linewidth=2)
    ax.set_yticks(list(y), labels)
    top = xmax or max(values) * 1.15
    ax.set_xlim(0, top)
    for b, v in zip(bars, values):
        ax.text(b.get_width() + top * 0.01, b.get_y() + b.get_height() / 2, fmt.format(v),
                va="center", color=INK, fontsize=9)
    _style(ax, title, subtitle)
    ax.grid(axis="y", visible=False)
    _finish(fig, path)


def extraction(path: Path, ex: dict, note: str) -> None:
    labels = [f"{v} · {d['format'].upper()}" for v, d in ex.items()]
    _hbars(path, labels, [d["cell_accuracy_pct"] for d in ex.values()],
           "Rate extraction: cell-level exact match", note, "{:.0f}%", xmax=115)


def classification(path: Path, cls: dict, note: str) -> None:
    groups = ["Single-cause", "Multi-cause", "All variances"]
    r = cls["rules_only"]
    l = cls["rules_plus_llm"]
    a = [r["single_cause"]["correct_pct"], r["multi_cause"]["exact_set_pct"], r["exact_match_pct"]]
    b = [l["single_cause"]["correct_pct"], l["multi_cause"]["exact_set_pct"], l["exact_match_pct"]]
    fig, ax = plt.subplots(figsize=(7.5, 3.6), dpi=160)
    x = range(len(groups))
    w = 0.36
    ba = ax.bar([i - w / 2 for i in x], a, w, color=BLUE, edgecolor=SURFACE, linewidth=2, label="Rules only")
    bb = ax.bar([i + w / 2 for i in x], b, w, color=ORANGE, edgecolor=SURFACE, linewidth=2, label="Rules + LLM")
    for bars in (ba, bb):
        for bar in bars:
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1.5, f"{bar.get_height():.0f}%",
                    ha="center", color=INK, fontsize=9)
    ax.set_xticks(list(x), [f"{g}\n(n={n})" for g, n in zip(groups, [r["single_cause"]["n"], r["multi_cause"]["n"], r["variances"]])])
    ax.set_ylim(0, 115)
    ax.set_ylabel("Exact cause match", color=INK_2, fontsize=9)
    _style(ax, "Cause classification: rules only vs. rules + LLM", note)
    ax.grid(axis="x", visible=False)
    leg = ax.legend(frameon=False, loc="upper right", fontsize=9, ncol=2, bbox_to_anchor=(1, 1.12))
    for t in leg.get_texts():
        t.set_color(INK_2)
    _finish(fig, path)


def confusion(path: Path, cls: dict, note: str) -> None:
    rows = cls["confusion_single_cause"]
    truth = sorted({r["truth"] for r in rows})
    pred = sorted({r["predicted"] for r in rows})
    grid = [[0] * len(pred) for _ in truth]
    for r in rows:
        grid[truth.index(r["truth"])][pred.index(r["predicted"])] = r["n"]
    top = max(max(row) for row in grid) or 1
    fig, ax = plt.subplots(figsize=(8, 0.5 * len(truth) + 2.2), dpi=160)
    for i, row in enumerate(grid):
        for j, n in enumerate(row):
            if n == 0:
                color = SURFACE
            else:
                color = SEQ[min(len(SEQ) - 1, 1 + int((len(SEQ) - 2) * n / top))]
            ax.add_patch(plt.Rectangle((j, len(truth) - 1 - i), 1, 1, facecolor=color, edgecolor=GRID, linewidth=1))
            if n:
                dark = SEQ.index(color) >= 3
                ax.text(j + 0.5, len(truth) - 0.5 - i, str(n), ha="center", va="center", fontsize=9,
                        color="#ffffff" if dark else INK)
    ax.set_xlim(0, len(pred))
    ax.set_ylim(0, len(truth))
    ax.set_xticks([j + 0.5 for j in range(len(pred))], [p.replace("_", " ").title() for p in pred], rotation=30, ha="right")
    ax.set_yticks([len(truth) - 0.5 - i for i in range(len(truth))], [t.replace("_", " ").title() for t in truth])
    ax.set_xlabel("Predicted (rules + LLM)", color=INK_2, fontsize=9)
    ax.set_ylabel("Ground truth", color=INK_2, fontsize=9)
    _style(ax, "Confusion matrix, single-cause variances", note)
    ax.grid(False)
    _finish(fig, path)


def llm_calls(path: Path, cost: dict, note: str) -> None:
    _hbars(path, ["One LLM call per variance", "Grouped, explain every pattern", "Triage only (rules first)"],
           [cost["llm_calls_if_one_per_variance"], cost["llm_calls_grouped_explain_all"], cost["llm_calls_triage_only"]],
           "Investigation LLM calls for the half-year", note, "{:.0f} calls")


def guardrails(path: Path, adv: dict, note: str) -> None:
    items = list(adv["extraction"].items()) + list(adv["findings"].items())
    labels = [k for k, _ in items]
    values = [100 * d["caught"] / d["n"] for _, d in items]
    colors = [BLUE if d["expected_to_catch"] else MUTED for _, d in items]
    labels = [l if d["expected_to_catch"] else f"{l} (human diff review)" for l, (_, d) in zip(labels, items)]
    _hbars(path, labels, values, "Guardrails: injected errors caught by deterministic checks", note,
           "{:.0f}%", colors=colors, xmax=115)


def render_all(out: Path, results: dict) -> list[str]:
    out.mkdir(parents=True, exist_ok=True)
    note = "MOCK LLM - plumbing check, not model quality" if results["provider"] == "mock" else f"Live model ({results['provider']})"
    files = {
        "extraction_accuracy.png": lambda p: extraction(p, results["extraction"], note),
        "classification.png": lambda p: classification(p, results["classification"], note),
        "confusion_matrix.png": lambda p: confusion(p, results["classification"], note),
        "llm_calls.png": lambda p: llm_calls(p, results["cost"], "Same 462 variances; deterministic counts"),
        "guardrails.png": lambda p: guardrails(p, results["adversarial"], "Gray: a case the code is not designed to catch"),
    }
    for name, fn in files.items():
        fn(out / name)
    return list(files)
