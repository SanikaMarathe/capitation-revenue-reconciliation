"""Print one example of each seeded discrepancy with the evidence behind it.

    python -m data_gen.samples
Member IDs only; names never printed.
"""

from __future__ import annotations

import csv
from collections import defaultdict

from data_gen.generate import GT, RAW

ORDER = ["MISSING_FROM_REMIT", "RATE_CELL_MISMATCH", "STALE_RATE", "UNEXPECTED_PAYMENT", "DUPLICATE_PAYMENT",
         "RECOUPMENT", "RETRO_ENROLLMENT", "RETRO_RATE_CHANGE"]


def _csv(path):
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main() -> None:
    mm = {(r["member_id"], r["coverage_month"]): r for r in _csv(GT / "member_months.csv")}
    disc = _csv(GT / "discrepancies.csv")
    lines = defaultdict(list)
    for l in _csv(RAW / "remittance_820_lines.csv"):
        lines[(l["member_id"], l["coverage_month"])].append(l)

    def show(row, title):
        key = (row["member_id"], row["coverage_month"])
        m = mm[key]
        print(f"\n=== {title}: {key[0]} {key[1][:7]}  ({row['codes']})")
        print(f"    our cell {m['region']} {m['category']} | booked {m['booked_at_close']} | final expected "
              f"{m['final_expected']} | state net paid {m['state_net_paid']} | variance {m['final_variance']}")
        print(f"    why: {row['notes']}")
        for l in lines.get(key, []):
            print(f"    line {l['line_id']} {l['remit_id']} {l['line_type']:<16} {l['region']} {l['category']:<9} "
                  f"{l['amount']:>9} {('ref ' + l['reference']) if l['reference'] else ''}")

    for code in ORDER:
        row = next(r for r in disc if code in r["codes"].split(";") and r["n_codes"] == "1") \
            if any(code in r["codes"].split(";") and r["n_codes"] == "1" for r in disc) \
            else next(r for r in disc if code in r["codes"].split(";"))
        show(row, code)
    multi = [r for r in disc if len(set(r["codes"].split(";")) - {"RETRO_RATE_CHANGE"}) >= 2]
    print(f"\n--- {len(multi)} multi-cause member-months; two examples:")
    for row in multi[:2]:
        show(row, "MULTI-CAUSE")
    print("\n=== Bank-level")
    for b in _csv(GT / "bank_truth.csv"):
        print(f"    {b['code']:<18} remit {b['remit_id'] or '-':<10} deposit {b['deposit_id']}  {b['note']}")


if __name__ == "__main__":
    main()
