"""Full eval suite against the generator's ground truth.

    python tasks.py evals                     # replay only (offline, needs a previous recording)
    python -m evals.run_evals                 # record (or reuse cache), then replay and compare
    python -m evals.run_evals --provider anthropic

Writes evals/results.md, evals/results.json and evals/charts/*.png.
Reproducibility: the same seed + cached LLM responses must give identical metrics on replay.
"""

from __future__ import annotations

import argparse
import json
import time
from decimal import Decimal
from pathlib import Path

from common.config import get_settings
from data_gen.generate import GT, RAW
from evals import adversarial, charts, metrics, privacy_eval
from llm.factory import build_gateway
from workflow import pipeline

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "evals"
TIMING_KEYS = {"wall_seconds", "cache_hits", "cache_hit_pct", "input_tokens", "output_tokens", "cost_usd",
               "new_spend_usd"}   # spend bookkeeping differs by design between a recording run and its replay


def evaluate(provider: str, cache_mode: str, cost_mode: bool = False, max_spend=None) -> dict:
    start = time.perf_counter()
    conn, report = pipeline.run(":memory:", provider=provider, cache_mode=cache_mode,
                                cost_mode=cost_mode, max_spend_usd=max_spend)
    seconds = time.perf_counter() - start
    truth = metrics.truth_by_member_month(GT)
    variances = metrics.final_variances(conn)
    settings = get_settings()
    return {
        "provider": report.llm["provider"],
        "cache_mode": cache_mode,
        "extraction": metrics.extraction(conn, GT, report.extractions),
        "detection": metrics.detection(truth, variances),
        "classification": metrics.classification(conn, truth),
        "groundedness": metrics.groundedness(report.outcomes),
        "memos": {"total": len(report.memos), "grounded": sum(m["grounded"] for m in report.memos)},
        "retro": metrics.retro(conn, GT, truth),
        "bank": metrics.bank(conn, GT),
        "adversarial": adversarial.run(conn, report.outcomes, pipeline.DOCS, RAW / "rate_docs"),
        "privacy": privacy_eval.run(conn, settings, build_gateway(settings, conn)),
        "cost": metrics.cost(report.llm, report.investigation, seconds),
    }


def _core(results: dict) -> dict:
    """Everything except timing/cache bookkeeping, for the replay comparison."""
    out = json.loads(json.dumps(results, default=str))
    for k in TIMING_KEYS:
        out["cost"].pop(k, None)
    out.pop("cache_mode", None)
    out.pop("privacy", None)        # record counts grow as the egress log accumulates
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["mock", "anthropic"], default=None)
    ap.add_argument("--replay", action="store_true", help="offline only: every LLM answer must come from cache")
    ap.add_argument("--cost-mode", action="store_true",
                    help="complex investigations and memos on Haiku unless an Opus answer is already cached")
    ap.add_argument("--max-spend", type=Decimal, default=None, help="hard cap on NEW spend in USD for this run")
    args = ap.parse_args()
    provider = args.provider or get_settings().llm["provider"]

    if args.replay:
        results = evaluate(provider, "replay", args.cost_mode)
        reproducible = None
    else:
        recorded = evaluate(provider, "read_write", args.cost_mode, args.max_spend)
        results = evaluate(provider, "replay", args.cost_mode)
        reproducible = _core(recorded) == _core(results)
        results["recorded_run_cost"] = recorded["cost"]
    results["replay_reproducible"] = reproducible

    (OUT / "results.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    chart_files = charts.render_all(OUT / "charts", results)
    (OUT / "results.md").write_text(render_markdown(results, chart_files), encoding="utf-8")
    print(f"wrote {OUT / 'results.md'}  (replay reproducible: {reproducible})")


# ---------------------------------------------------------------- markdown
def render_markdown(r: dict, chart_files: list[str]) -> str:
    mock = r["provider"] == "mock"
    ex, det, cls, gr, rt, bk, adv, pv, cost = (r[k] for k in
        ("extraction", "detection", "classification", "groundedness", "retro", "bank", "adversarial", "privacy", "cost"))
    lines = ["# Evaluation results", ""]
    if mock:
        lines += ["> **MOCK LLM RUN.** AI-side numbers (extraction, classification, groundedness) come from a",
                  "> deterministic stand-in (heuristic parser / rule echo). They prove the plumbing, NOT model quality.",
                  "> Deterministic numbers (detection, retro, bank, guardrails, privacy) are real. Re-run with",
                  "> `--provider anthropic` for model results.", ""]
    lines += [f"Provider: `{r['provider']}` · ground truth: `data/ground_truth` (seed {json.loads((GT / 'summary.json').read_text())['seed']})"
              f" · replay reproducible: **{r['replay_reproducible']}**", ""]

    lines += ["## Headline", "", "| Area | Result |", "|---|---|",
              f"| Rate extraction (3 formats) | {sum(d['cells_exact'] for d in ex.values())}/{sum(d['truth_cells'] for d in ex.values())} cells exact |",
              f"| Detection of seeded payment issues | {det['recall_pct']}% recall, {det['false_positives']} false positives |",
              f"| Classification, rules only | {cls['rules_only']['exact_match_pct']}% exact ({cls['rules_only']['unresolved']} unresolved) |",
              f"| Classification, rules + LLM | {cls['rules_plus_llm']['exact_match_pct']}% exact |",
              f"| Findings passing evidence + amount checks | {gr['grounded_pct']}% ({gr['grounded']}/{gr['findings']}) · memos {r['memos']['grounded']}/{r['memos']['total']} (checks passed, not a guarantee of correctness) |",
              f"| Retro deltas vs. truth | {rt['exact_pct']}% of member-months exact · total {rt['retro_total_engine']} (truth {rt['retro_total_truth']}) |",
              f"| Bank reconciliation | {bk['correct']}/{bk['total']} seeded cases correct |",
              f"| Guardrails (injected errors) | {adv['designed_catch_rate_pct']}% caught of the cases they're designed for |",
              f"| Privacy | {pv['total_leaks']} leaks in {pv['total_records']} records · {pv['injection_test']['blocked_pct']}% of injected PHI blocked |",
              f"| LLM calls | {cost['llm_calls_if_one_per_variance']} per-variance → {cost['llm_calls_grouped_explain_all']} grouped → {cost['llm_calls_triage_only']} triage-only |",
              f"| LLM cost | uncached run ≈ ${cost.get('uncached_equivalent_usd') or '0'} (from token counts) · this run's new spend ${cost.get('new_spend_usd') or '0'} (replay from cache) |",
              ""]

    lines += ["## 1. Rate extraction", "", "![](charts/extraction_accuracy.png)", "",
              "| Version | Format | Cells exact | Dates correct | Extra cells | Kind | Retro flag | Published date | Validation errors / flags |",
              "|---|---|---|---|---|---|---|---|---|"]
    for v, d in ex.items():
        lines.append(f"| {v} | {d['format']} | {d['cells_exact']}/{d['truth_cells']} ({d['cell_accuracy_pct']}%) | "
                     f"{d['effective_dates_correct_pct']}% | {d['extra_cells']} | {'✓' if d['kind_correct'] else '✗'} | "
                     f"{'✓' if d['retro_flag_correct'] else '✗'} | {'✓' if d['published_date_correct'] else '✗'} | "
                     f"{d['validation_errors']} / {d['validation_flags']} |")
    lines += ["", "## 2. Detection (expected vs. state-remitted, member-month)", "",
              f"{det['detected']}/{det['seeded_payment_issues']} seeded payment issues flagged "
              f"({det['recall_pct']}%); {det['false_positives']} false positives across "
              f"{det['clean_member_months']} clean member-months.", "",
              "| Code | Seeded | Detected | Recall |", "|---|---|---|---|"]
    for code, d in det["per_code"].items():
        lines.append(f"| {code} | {d['seeded']} | {d['detected']} | {d['recall_pct']}% |")
    ro, rl = cls["rules_only"], cls["rules_plus_llm"]
    lines += ["", "## 3. Classification: rules only vs. rules + LLM", "", "![](charts/classification.png)", "",
              "| | Rules only | Rules + LLM |", "|---|---|---|",
              f"| All variances (n={ro['variances']}), exact cause set | {ro['exact_match_pct']}% | {rl['exact_match_pct']}% |",
              f"| Single-cause (n={ro['single_cause']['n']}) | {ro['single_cause']['correct_pct']}% | {rl['single_cause']['correct_pct']}% |",
              f"| Multi-cause (n={ro['multi_cause']['n']}), exact set | {ro['multi_cause']['exact_set_pct']}% | {rl['multi_cause']['exact_set_pct']}% |",
              f"| Left unresolved for a human | {ro['unresolved']} | {rl['unresolved']} |",
              "", "Rules settle single-cause cases at no cost; ambiguous and multi-cause cases go to the LLM.", "",
              "![](charts/confusion_matrix.png)", "",
              "## 4. Groundedness", "",
              f"{gr['grounded']}/{gr['findings']} findings passed every implemented check (evidence IDs exist, every $ "
              f"matches a computed value, cause consistent with facts). Passing the checks is not proof the explanation "
              f"is correct (see section 7 for what the checks can't catch). Failing findings go to a human. Problem types: "
              f"{gr['problem_types'] or 'none'}. Memos: {r['memos']['grounded']}/{r['memos']['total']} passed "
              "(numbers injected by code).", "",
              "## 5. Retro (prior-period) accuracy", "",
              f"- Close snapshot + adjustments equals final expected for **{rt['member_months_exact']}/{rt['member_months_checked']}** member-months.",
              f"- Total retro: engine **{rt['retro_total_engine']}**, truth **{rt['retro_total_truth']}** "
              f"({rt['retro_member_months_truth']} member-months, {rt['adjustments']} adjustments).",
              f"- Remaining-to-collect from the state matches truth for {rt['settlement_remaining_exact_pct']}% of retro member-months.", "",
              "## 6. Bank reconciliation", "", "| Seeded case | Remit | Deposit | Correct |", "|---|---|---|---|"]
    for c in bk["checks"]:
        lines.append(f"| {c['code']} | {c['remit'] or '-'} | {c['deposit']} | {'✓' if c['correct'] else '✗'} |")
    lines += [f"", f"Clean remittances left unflagged: {bk['clean_remits_unflagged']}.", "",
              "## 7. Guardrails (adversarial)", "", "![](charts/guardrails.png)", "",
              "| Injected into | Injection | Caught |", "|---|---|---|"]
    for where in ("extraction", "findings"):
        for name, d in adv[where].items():
            tag = "" if d["expected_to_catch"] else " *(not designed to catch: the approver's diff review is the control)*"
            lines.append(f"| {where} | {name}{tag} | {d['caught']}/{d['n']} |")
    lines += ["", "## 8. Privacy", "", "| Source scanned | Records | Leaks |", "|---|---|---|"]
    for name, d in pv["scanned"].items():
        lines.append(f"| {name} | {d['records']} | {d['leaks']} {d['leak_types'] or ''} |")
    it = pv["injection_test"]
    lines += ["", f"Injection test: {it['blocked']}/{it['payloads']} payloads carrying a name, DOB or forbidden field were blocked.", "",
              "## 9. Cost, latency, caching", "", "![](charts/llm_calls.png)", "",
              "| | Recorded run | Replay run |", "|---|---|---|"]
    rec = r.get("recorded_run_cost", {})
    for k in ("calls", "cache_hit_pct", "uncached_equivalent_usd", "new_spend_usd", "wall_seconds"):
        lines.append(f"| {k} | {rec.get(k, '-')} | {cost.get(k, '-')} |")
    lines += ["", f"Calls by task: {cost['by_task']}. Models by task: {cost.get('models_by_task', {})}.", "",
              "**How to read cost:** `uncached_equivalent_usd` is what this exact run would cost with an empty cache, "
              "priced from every response's token counts. `new_spend_usd` is what this run actually paid; a replay from "
              "cache pays $0. The cumulative spend across all live calls made for this project was about $0.35.", ""]
    if mock:
        lines += ["Mock calls are reported at $0; token counts are estimates (characters / 4).", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
