"""Rule pre-classifier: proposes a POSSIBLE cause from observed features.

Each rule needs corroborating fields to be 'strong'. A cause stays 'possible' until a
human confirms it. Routing: exactly one strong rule -> rule answer, no LLM call.
Anything else (no rule, only weak rules, several rules) -> the investigator agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from schemas.enums import DiscrepancyCode as C
from schemas.enums import RuleStrength as S


@dataclass(frozen=True)
class RuleHit:
    code: C
    strength: S
    reason: str


def classify(f: dict[str, Any]) -> list[RuleHit]:
    hits: list[RuleHit] = []
    if f["has_expected"] and not f["has_remit"] and not f["has_negative_line"]:
        hits.append(RuleHit(C.MISSING_FROM_REMIT, S.STRONG, "expected, no payment lines"))
    if not f["has_expected"] and f["has_remit"]:
        hits.append(RuleHit(C.UNEXPECTED_PAYMENT, S.STRONG, "paid, but no coverage on record for the month"))
    if f["regular_line_count"] >= 2:
        if f["duplicate_signature"]:
            hits.append(RuleHit(C.DUPLICATE_PAYMENT, S.STRONG, "2+ regular lines, same cell and amount"))
        else:
            hits.append(RuleHit(C.DUPLICATE_PAYMENT, S.WEAK, "2+ regular lines with different cells/amounts"))
    if f["has_negative_line"]:
        if "recoupment" in f["negative_line_types"] and f["negative_refs_found"]:
            hits.append(RuleHit(C.RECOUPMENT, S.STRONG, "negative recoupment line referencing a prior payment"))
        else:
            hits.append(RuleHit(C.RECOUPMENT, S.WEAK, "negative line without a matching reference"))
    if f["has_expected"] and f["has_remit"] and not f["cell_match"]:
        hits.append(RuleHit(C.RATE_CELL_MISMATCH, S.STRONG, "state paid a different region/category"))
    if f["cell_match"] and not f["paid_matches_current"]:
        if f["paid_matches_older_version"] and f["rate_approved_after_payment"]:
            hits.append(RuleHit(C.RETRO_RATE_CHANGE, S.STRONG,
                                "paid at the pre-amendment rate; retro difference not yet settled"))
        elif f["paid_matches_older_version"]:
            hits.append(RuleHit(C.STALE_RATE, S.STRONG,
                                f"paid amount equals the {f['paid_matches_older_version']} rate for this cell"))
        else:
            hits.append(RuleHit(C.STALE_RATE, S.WEAK, "paid amount matches no known rate for this cell"))
    if f.get("rate_revised_after_close") and hits and not any(h.code == C.RETRO_RATE_CHANGE for h in hits):
        hits.append(RuleHit(C.RETRO_RATE_CHANGE, S.WEAK,
                            "this month's rate was revised after the month closed; part of the gap may be the revision"))
    return hits


def route(hits: list[RuleHit]) -> tuple[RuleHit | None, bool]:
    """Returns (rule answer, needs_investigator)."""
    if len(hits) == 1 and hits[0].strength == S.STRONG:
        return hits[0], False
    return (hits[0] if hits else None), True
