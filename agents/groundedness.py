"""Groundedness: turns 'trust the AI' into 'verify the AI'.

A finding is grounded only if:
 (a) every evidence ID it cites exists in the evidence pack
 (b) every amount it cites, and every $ figure in its prose, equals a computed value
 (c) each cause it proposes is consistent with the observed facts
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from agents.evidence import pack_amounts, pack_ids
from schemas.enums import DiscrepancyCode as C
from schemas.variance import Finding

DOLLAR = re.compile(r"-?\$\s?-?\d[\d,]*(?:\.\d{1,2})?")


def dollars_in(text: str) -> list[Decimal]:
    return [Decimal(m.replace("$", "").replace(",", "").replace(" ", "")) for m in DOLLAR.findall(text)]


# What each cause requires to be true in the observed facts.
CONSISTENT = {
    C.MISSING_FROM_REMIT: lambda f: f["has_expected"] and not f["has_remit"],
    C.UNEXPECTED_PAYMENT: lambda f: f["has_remit"] and not f["has_expected"],
    C.DUPLICATE_PAYMENT: lambda f: f["regular_line_count"] >= 2,
    C.RECOUPMENT: lambda f: f["has_negative_line"],
    C.RATE_CELL_MISMATCH: lambda f: f["has_expected"] and f["has_remit"] and not f["cell_match"],
    C.STALE_RATE: lambda f: f["cell_match"] and not f["paid_matches_current"],
    C.RETRO_RATE_CHANGE: lambda f: (bool(f["rate_approved_after_payment"]) or bool(f.get("rate_revised_after_close"))
                                    or Decimal(f.get("retro_line_total") or "0") != 0),
    C.RETRO_ENROLLMENT: lambda f: True,      # established from enrollment history; not refutable from features
}


@dataclass
class GroundednessResult:
    grounded: bool
    problems: list[str] = field(default_factory=list)


def check(finding: Finding, pack: dict[str, Any]) -> GroundednessResult:
    problems: list[str] = []
    ids = pack_ids(pack)
    for ref in finding.evidence_refs:
        if ref not in ids:
            problems.append(f"cites unknown evidence '{ref}'")
    if not finding.evidence_refs:
        problems.append("cites no evidence")
    amounts = pack_amounts(pack)
    for cited in finding.amounts_cited:
        if Decimal(cited.amount) not in amounts:
            problems.append(f"amount {cited.label}={cited.amount} matches no computed value")
    for value in dollars_in(finding.explanation):
        if value not in amounts:
            problems.append(f"explanation quotes ${value} which matches no computed value")
    for cause in finding.possible_causes:
        name = getattr(cause, "value", cause)
        rule = CONSISTENT.get(C(name)) if name in C._value2member_map_ else None
        if rule is None:
            problems.append(f"{name} is not a member-level cause")
        elif not rule(pack["features"]):
            problems.append(f"{name} contradicts the observed facts")
    return GroundednessResult(not problems, problems)
