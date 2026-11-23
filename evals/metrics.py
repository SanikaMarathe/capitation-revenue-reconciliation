"""Eval metrics. Everything is scored against the generator's ground truth (data/ground_truth)."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from data_gen import spec
from engine.retro import settlement
from storage import repo
from workflow.deterministic_close import DeterministicClose
from workflow.pipeline import END

PAYMENT_CODES = {"MISSING_FROM_REMIT", "RATE_CELL_MISMATCH", "STALE_RATE", "UNEXPECTED_PAYMENT",
                 "DUPLICATE_PAYMENT", "RECOUPMENT"}
UNRESOLVED = "UNRESOLVED"
ZERO = Decimal("0.00")


def _csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def truth_by_member_month(gt: Path) -> dict[tuple[str, date], dict]:
    return {(r["member_id"], date.fromisoformat(r["coverage_month"])): r for r in _csv(gt / "member_months.csv")}


def _codes(row: dict | None) -> set[str]:
    return {c for c in (row or {}).get("codes", "").split(";") if c}


def pct(n: int | Decimal, d: int | Decimal) -> float:
    return round(100 * float(n) / float(d), 1) if d else 0.0


# ---------------------------------------------------------------- extraction
def extraction(conn, gt: Path, report_extractions: dict) -> dict:
    out = {}
    for v in ("v1", "v2", "v3"):
        meta = json.loads((gt / f"rates_{v}.json").read_text(encoding="utf-8"))
        truth = {(r["region"], r["category"]): r for r in meta["rates"]}
        got = {(r.region.value, r.category.value): r for r in repo.rate_rows(conn) if r.version_id == v}
        exact = dates_ok = 0
        for cell, t in truth.items():
            g = got.get(cell)
            if g is None:
                continue
            to = g.effective_to.isoformat() if g.effective_to else None
            date_ok = g.effective_from.isoformat() == t["effective_from"] and to == t["effective_to"]
            dates_ok += date_ok
            exact += date_ok and g.pmpm == Decimal(t["pmpm"])
        ex = report_extractions.get(v, {})
        out[v] = {
            "file": ex.get("file"),
            "format": Path(ex.get("file", "x.txt")).suffix.lstrip(".").replace("txt", "email"),
            "truth_cells": len(truth),
            "cells_exact": exact,
            "cell_accuracy_pct": pct(exact, len(truth)),
            "effective_dates_correct_pct": pct(dates_ok, len(truth)),
            "extra_cells": len(set(got) - set(truth)),
            "kind_correct": ex.get("kind") == meta["kind"],
            "retro_flag_correct": ex.get("is_retroactive") == meta["is_retroactive"],
            "published_date_correct": ex.get("published_date") == meta["published"],
            "validation_errors": len(ex.get("errors", [])),
            "validation_flags": len(ex.get("flags", [])),
        }
    return out


# ---------------------------------------------------------------- detection
def final_variances(conn) -> dict[tuple[str, date], object]:
    dc = DeterministicClose(conn)
    return {(v.member_id, v.coverage_month): v for m in spec.MONTHS for v in dc.reconcile(m, END, "eval")}


def detection(truth: dict, variances: dict) -> dict:
    seeded = {k for k, t in truth.items() if _codes(t) & PAYMENT_CODES}
    flagged = set(variances)
    per_code = {}
    for code in sorted(PAYMENT_CODES):
        keys = {k for k in seeded if code in _codes(truth[k])}
        per_code[code] = {"seeded": len(keys), "detected": len(keys & flagged), "recall_pct": pct(len(keys & flagged), len(keys))}
    false_pos = {k for k in flagged if not _codes(truth.get(k))}
    clean = [k for k, t in truth.items() if not _codes(t)]
    return {
        "seeded_payment_issues": len(seeded),
        "detected": len(seeded & flagged),
        "recall_pct": pct(len(seeded & flagged), len(seeded)),
        "variances_flagged": len(flagged),
        "false_positives": len(false_pos),
        "clean_member_months": len(clean),
        "false_positive_rate_pct": pct(len(false_pos), len(clean)),
        "per_code": per_code,
    }


# ---------------------------------------------------------------- classification
def _rules_only(v) -> list[str]:
    hits = v.features.get("rule_hits", [])
    if len(hits) == 1 and hits[0]["strength"] == "strong":
        return [hits[0]["code"]]
    return [UNRESOLVED]


def classification(conn, truth: dict) -> dict:
    rows = repo._rows(conn, "SELECT * FROM variances WHERE run_id='final'")
    preds: dict[str, list] = {"rules_only": [], "rules_plus_llm": []}
    for r in rows:
        key = (r["member_id"], r["coverage_month"])
        t = _codes(truth.get(key)) - {"RETRO_ENROLLMENT"} or {"NONE"}
        features = json.loads(r["features"])
        hits = features.get("rule_hits", [])
        rules = ([hits[0]["code"]] if len(hits) == 1 and hits[0]["strength"] == "strong" else [UNRESOLVED])
        llm = json.loads(r["possible_cause_llm"] or "[]")
        if rules != [UNRESOLVED]:
            combined = rules
        elif llm and r["grounded"]:
            combined = llm
        else:
            combined = [UNRESOLVED]
        preds["rules_only"].append((t, rules))
        preds["rules_plus_llm"].append((t, combined))

    def score(pairs):
        single = [(t, p) for t, p in pairs if len(t) == 1]
        multi = [(t, p) for t, p in pairs if len(t) > 1]
        exact = sum(set(p) == t for t, p in pairs)
        return {
            "variances": len(pairs),
            "exact_match_pct": pct(exact, len(pairs)),
            "single_cause": {"n": len(single), "correct_pct": pct(sum(set(p) == t for t, p in single), len(single))},
            "multi_cause": {"n": len(multi), "exact_set_pct": pct(sum(set(p) == t for t, p in multi), len(multi)),
                            "primary_in_truth_pct": pct(sum(p[0] in t for t, p in multi), len(multi))},
            "unresolved": sum(p == [UNRESOLVED] for _, p in pairs),
        }

    confusion = Counter()
    for t, p in preds["rules_plus_llm"]:
        if len(t) == 1:
            confusion[(next(iter(t)), p[0])] += 1
    return {
        "rules_only": score(preds["rules_only"]),
        "rules_plus_llm": score(preds["rules_plus_llm"]),
        "confusion_single_cause": [{"truth": a, "predicted": b, "n": n} for (a, b), n in sorted(confusion.items())],
    }


# ---------------------------------------------------------------- groundedness
def groundedness(outcomes) -> dict:
    n = len(outcomes)
    grounded = sum(o.grounded for o in outcomes)
    problems = Counter(p.split(" '")[0].split(" $")[0] for o in outcomes for p in (o.problems or []))
    return {"findings": n, "grounded": grounded, "grounded_pct": pct(grounded, n),
            "errors": sum(o.error is not None for o in outcomes), "problem_types": dict(problems)}


# ---------------------------------------------------------------- retro
def retro(conn, gt: Path, truth: dict) -> dict:
    snaps = {(s.member_id, s.coverage_month): s.pmpm for s in repo.snapshots(conn)}
    adj = defaultdict(lambda: ZERO)
    lines = repo.active_adjustment_lines(conn)
    for l in lines:
        adj[(l.member_id, l.coverage_month)] += l.delta
    exact = sum(snaps.get(k, ZERO) + adj[k] == Decimal(t["final_expected"]) for k, t in truth.items())
    gt_rows = _csv(gt / "retro_deltas.csv")
    gt_total = sum((Decimal(r["retro_delta"]) for r in gt_rows), ZERO)

    dc = DeterministicClose(conn)
    current = {(e.member_id, e.coverage_month): e.pmpm for m in spec.MONTHS for e in dc.price(m, END).expected}
    s = {(x.member_id, x.coverage_month): x for x in settlement(lines, current, repo.remittance_lines(conn))}
    remaining_ok = sum(1 for r in gt_rows
                       if (key := (r["member_id"], date.fromisoformat(r["coverage_month"]))) in s
                       and s[key].remaining_vs_state == Decimal(r["remaining_vs_state"]))
    return {
        "member_months_checked": len(truth),
        "member_months_exact": exact,
        "exact_pct": pct(exact, len(truth)),
        "retro_member_months_truth": len(gt_rows),
        "retro_total_truth": str(gt_total),
        "retro_total_engine": str(sum(adj.values(), ZERO)),
        "settlement_remaining_exact_pct": pct(remaining_ok, len(gt_rows)),
        "adjustments": len({l.adjustment_id for l in lines}),
    }


# ---------------------------------------------------------------- bank
def bank(conn, gt: Path) -> dict:
    res = DeterministicClose(conn).reconcile_bank(END, record_links=False)
    by_remit = {o.remit_id: o for o in res.outcomes}
    checks = []
    for b in _csv(gt / "bank_truth.csv"):
        if b["code"] in ("BANK_TIMING", "BANK_SHORT"):
            ok = b["code"] in by_remit[b["remit_id"]].flags
        elif b["code"] == "NO_REFERENCE":
            o = by_remit[b["remit_id"]]
            ok = o.deposit_id is None and b["deposit_id"] in o.candidates    # left for a human, never auto-linked
        else:   # UNMATCHED_DEPOSIT
            ok = b["deposit_id"] in res.unmatched_deposits
        checks.append({"code": b["code"], "remit": b["remit_id"], "deposit": b["deposit_id"], "correct": ok})
    clean_ok = all(not o.flags for rid, o in by_remit.items()
                   if rid not in {c["remit"] for c in checks})
    return {"checks": checks, "correct": sum(c["correct"] for c in checks), "total": len(checks),
            "clean_remits_unflagged": clean_ok}


# ---------------------------------------------------------------- cost
def cost(report_llm: dict, investigation: dict, seconds: float) -> dict:
    return {
        "provider": report_llm["provider"],
        "calls": report_llm["calls"],
        "cache_hits": report_llm["cache_hits"],
        "cache_hit_pct": pct(report_llm["cache_hits"], report_llm["calls"]),
        "input_tokens": report_llm["input_tokens"],
        "output_tokens": report_llm["output_tokens"],
        "cost_usd": report_llm["cost_usd"],
        "by_task": report_llm["by_task"],
        "models_by_task": report_llm.get("models_by_task", {}),
        "new_spend_usd": report_llm.get("new_spend_usd"),
        "uncached_equivalent_usd": report_llm.get("uncached_equivalent_usd"),
        "cost_mode": report_llm.get("cost_mode", False),
        "wall_seconds": round(seconds, 1),
        "llm_calls_if_one_per_variance": investigation["variances"],
        "llm_calls_grouped_explain_all": investigation["groups"],
        "llm_calls_triage_only": investigation["complex_groups"],
    }
