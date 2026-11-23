"""Groups similar variances and builds one evidence pack per group.

Grouping key = (rule hits, our cell, state cell(s), month, delta, observed type), so 170 identical
stale-rate rows become ONE finding (cost scales with patterns, not rows) while an outlier amount
lands in its own group.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from agents import tools
from schemas.enums import DiscrepancyCode, RecommendedAction, VarianceStatus
from schemas.variance import Variance

FEATURE_KEYS = ["has_expected", "has_remit", "regular_line_count", "duplicate_signature", "has_negative_line",
                "negative_line_types", "negative_refs_found", "cell_match", "paid_matches_current",
                "paid_matches_older_version", "rate_approved_after_payment", "rate_revised_after_close",
                "expected_cell", "remit_cells",
                "expected_version", "retro_line_total"]
MEMBER_CODES = [c.value for c in DiscrepancyCode if not c.value.startswith("BANK_")]


@dataclass
class VarianceGroup:
    group_id: str
    key: tuple
    variances: list[Variance]

    @property
    def representative(self) -> Variance:
        return self.variances[0]

    @property
    def is_complex(self) -> bool:
        """Multi-cause or weak signals -> the stronger model."""
        return self.representative.status == VarianceStatus.NEEDS_HUMAN

    @property
    def total_delta(self) -> Decimal:
        return sum((v.delta for v in self.variances), Decimal("0.00"))


def group_key(v: Variance) -> tuple:
    f = v.features
    hits = tuple(sorted(h["code"] for h in f.get("rule_hits", [])))
    return (hits, f.get("expected_cell"), tuple(f.get("remit_cells", [])), v.coverage_month, v.delta, v.observed_type)


def group_variances(variances: list[Variance]) -> list[VarianceGroup]:
    buckets: dict[tuple, list[Variance]] = defaultdict(list)
    for v in variances:
        buckets[group_key(v)].append(v)
    groups = []
    for i, (key, vs) in enumerate(sorted(buckets.items(), key=lambda kv: (-len(kv[1]), str(kv[0]))), start=1):
        groups.append(VarianceGroup(f"G{i:03d}", key, sorted(vs, key=lambda x: x.member_id or "")))
    return groups


def build_pack(conn, group: VarianceGroup) -> dict[str, Any]:
    """Everything the LLM may see for one group. IDs on every row; no names, no DOB."""
    v = group.representative
    f = v.features
    rate_cells = {c for c in [f.get("expected_cell"), *f.get("remit_cells", [])] if c}
    rate_history = [r for cell in sorted(rate_cells) for r in tools.get_rate_history(conn, *cell.split(" "))]
    return {
        "variance": {
            "group_id": group.group_id,
            "member_id": v.member_id,
            "coverage_month": v.coverage_month,
            "expected": v.expected,
            "remitted": v.remitted,
            "delta": v.delta,
            "observed_type": v.observed_type,
            "members_in_group": len(group.variances),
            "group_total_delta": group.total_delta,
            "other_member_ids": [x.member_id for x in group.variances[1:20]],
        },
        "features": {k: f.get(k) for k in FEATURE_KEYS} | {"rule_hits": f.get("rule_hits", [])},
        "enrollment_history": tools.get_enrollment_history(conn, v.member_id),
        "rate_history": rate_history,
        "remittance_lines": tools.get_remittance_lines(conn, v.member_id, v.coverage_month),
        "prior_explanations": tools.get_prior_explanations(
            conn, v.possible_cause_rule.value if v.possible_cause_rule else None, f.get("expected_cell")),
        "allowed_categories": MEMBER_CODES,
        "allowed_actions": [a.value for a in RecommendedAction],
    }


def pack_ids(pack: dict[str, Any]) -> set[str]:
    ids = {pack["variance"]["group_id"]}
    for section in ("enrollment_history", "rate_history", "remittance_lines", "prior_explanations"):
        ids |= {row["id"] for row in pack[section]}
    return ids


def pack_amounts(pack: dict[str, Any]) -> set[Decimal]:
    """Every dollar figure an explanation may legitimately quote."""
    v = pack["variance"]
    vals = {Decimal(v["expected"]), Decimal(v["remitted"]), Decimal(v["delta"]), Decimal(v["group_total_delta"])}
    for line in pack["remittance_lines"]:
        vals.add(Decimal(line["amount"]))
        vals.add(Decimal(line["amount"]) - Decimal(v["expected"]))
    for r in pack["rate_history"]:
        vals.add(Decimal(r["pmpm"]))
        vals.add(Decimal(r["pmpm"]) - Decimal(v["remitted"]))
    vals.add(Decimal(pack["features"].get("retro_line_total") or "0"))
    return vals | {abs(x) for x in vals}
