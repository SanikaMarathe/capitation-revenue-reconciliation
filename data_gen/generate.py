"""Generate the synthetic world: raw input files, rate documents and ground truth.

    python tasks.py data          (or: python -m data_gen.generate)

Deterministic: same seed -> byte-identical CSV/JSON output.
"""

from __future__ import annotations

import csv
import json
import random
from collections import Counter
from decimal import Decimal
from pathlib import Path

from data_gen import docs, rates, spec
from data_gen.ground_truth import MemberMonthTruth, build
from data_gen.members import build_population
from data_gen.remittance import build_state_side

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
GT = ROOT / "data" / "ground_truth"

MEMBER_CODES = ["MISSING_FROM_REMIT", "RATE_CELL_MISMATCH", "STALE_RATE", "UNEXPECTED_PAYMENT",
                "DUPLICATE_PAYMENT", "RECOUPMENT", "RETRO_ENROLLMENT", "RETRO_RATE_CHANGE"]


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if v is None else v) for k, v in row.items()})


def _json_default(v):
    if isinstance(v, Decimal):
        return str(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    raise TypeError(type(v))


def generate(seed: int = spec.SEED) -> dict:
    rng = random.Random(seed)
    pop = build_population(rng)
    st = build_state_side(rng, pop)
    truth = build(pop, st)

    # ---------------- raw inputs (what the plan receives)
    _write_csv(RAW / "members.csv", [vars(m) for m in pop.members])          # C1: names/DOB, stays local
    _write_csv(RAW / "enrollment_834.csv", [vars(s) for s in pop.spans])
    _write_csv(RAW / "remittance_820_header.csv", st.remits)
    _write_csv(RAW / "remittance_820_lines.csv", [vars(l) for l in st.lines])
    _write_csv(RAW / "bank_deposits.csv", st.deposits)
    doc_files = docs.write_all(RAW / "rate_docs")

    # ---------------- ground truth (what the system should conclude)
    for v, rows in rates.version_rows().items():
        (GT / f"rates_{v}.json").parent.mkdir(parents=True, exist_ok=True)
        meta = {"version": v, "published": spec.PUBLISHED[v], "approved": spec.APPROVED[v],
                "source_file": doc_files[v].name, "kind": "amendment" if v == "v3" else "full",
                "is_retroactive": v == "v3", "rates": rows}
        (GT / f"rates_{v}.json").write_text(json.dumps(meta, indent=1, default=_json_default), encoding="utf-8")

    mm_rows = [_mm_row(t) for t in truth]
    _write_csv(GT / "member_months.csv", mm_rows)
    _write_csv(GT / "discrepancies.csv", [
        {"member_id": t.member_id, "coverage_month": t.coverage_month, "codes": ";".join(t.codes),
         "n_codes": len(t.codes), "notes": " | ".join(t.notes)}
        for t in truth if t.codes
    ])
    _write_csv(GT / "retro_deltas.csv", [
        {"member_id": t.member_id, "coverage_month": t.coverage_month, "region": t.region, "category": t.category,
         "booked_at_close": t.booked_at_close, "final_expected": t.final_expected, "retro_delta": t.retro_delta,
         "causes": ";".join(c for c in t.codes if c.startswith("RETRO_")), "state_retro_paid": t.state_retro_paid,
         "state_net_paid": t.state_net_paid, "remaining_vs_state": t.final_expected - t.state_net_paid}
        for t in truth if t.retro_delta != 0
    ])
    _write_csv(GT / "bank_truth.csv", st.bank_labels)

    summary = _summarise(pop, st, truth)
    _self_check(st, truth, summary)
    (GT / "summary.json").write_text(json.dumps(summary, indent=1, default=_json_default), encoding="utf-8")
    return summary


def _mm_row(t: MemberMonthTruth) -> dict:
    return {"member_id": t.member_id, "coverage_month": t.coverage_month, "enrolled": t.enrolled,
            "region": t.region, "category": t.category, "booked_at_close": t.booked_at_close,
            "final_expected": t.final_expected, "state_regular_paid": t.state_regular_paid,
            "state_retro_paid": t.state_retro_paid, "state_recouped": t.state_recouped,
            "state_net_paid": t.state_net_paid, "final_variance": t.final_variance, "codes": ";".join(t.codes)}


def _summarise(pop, st, truth: list[MemberMonthTruth]) -> dict:
    code_counts = Counter(c for t in truth for c in t.codes)
    enrolled = [t for t in truth if t.enrolled]
    payment_issue = [t for t in truth if set(t.codes) - {"RETRO_RATE_CHANGE", "RETRO_ENROLLMENT"}]
    multi = [t for t in truth if len(set(t.codes) - {"RETRO_RATE_CHANGE"}) >= 2]
    by_month = {}
    for month in spec.MONTHS:
        rows = [t for t in truth if t.coverage_month == month]
        remit = next(r for r in st.remits if r["payment_date"].month == month.month)
        deposit = next(d for d in st.deposits if d["deposit_id"] == f"D{month.month:04d}")
        by_month[f"{month:%Y-%m}"] = {
            "member_months": sum(t.enrolled for t in rows),
            "booked_at_close": sum((t.booked_at_close for t in rows), Decimal("0")),
            "final_expected": sum((t.final_expected for t in rows), Decimal("0")),
            "state_net_paid_for_month": sum((t.state_net_paid for t in rows), Decimal("0")),
            "remit_total_paid_in_month": remit["total_amount"],
            "bank_deposit": deposit["amount"],
        }
    retro = [t for t in truth if t.retro_delta != 0]
    return {
        "seed": spec.SEED,
        "members": len(pop.members),
        "enrollment_spans": len(pop.spans),
        "enrolled_member_months": len(enrolled),
        "remittance_lines": len(st.lines),
        "bank_deposits": len(st.deposits),
        "code_counts": dict(sorted(code_counts.items())),
        "bank_codes": dict(Counter(b["code"] for b in st.bank_labels)),
        "payment_issue_rate_pct": round(100 * len(payment_issue) / len(enrolled), 2),
        "multi_cause_member_months": len(multi),
        "retro_member_months": len(retro),
        "retro_delta_total": sum((t.retro_delta for t in retro), Decimal("0")),
        "retro_state_paid_total": sum((t.state_retro_paid for t in retro), Decimal("0")),
        "by_month": by_month,
    }


def _self_check(st, truth: list[MemberMonthTruth], summary: dict) -> None:
    # remit headers tie to their lines
    for remit in st.remits:
        lines = [l for l in st.lines if l.remit_id == remit["remit_id"]]
        assert sum((l.amount for l in lines), Decimal("0")) == remit["total_amount"], remit["remit_id"]
        assert len(lines) == remit["line_count"]
    # enough examples of every member-level code to evaluate
    for code in MEMBER_CODES:
        assert summary["code_counts"].get(code, 0) >= 5, f"too few {code}"
    for code in ("BANK_TIMING", "BANK_SHORT"):
        assert summary["bank_codes"].get(code, 0) >= 1, f"missing {code}"
    assert summary["multi_cause_member_months"] >= 5, "need overlapping-cause cases"
    # names must not collide with domain words (privacy scanner false positives)
    domain = set(spec.REGIONS) | set(spec.CATEGORIES) | {"January", "February", "March", "April", "May", "June"}
    assert not (set(spec.FIRST_NAMES) | set(spec.LAST_NAMES)) & domain


def main() -> None:
    s = generate()
    print(json.dumps(s, indent=1, default=_json_default))
    print(f"\nWrote raw inputs to {RAW} and ground truth to {GT}")


if __name__ == "__main__":
    main()
