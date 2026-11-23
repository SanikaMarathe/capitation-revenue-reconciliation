"""Level 1 reconciliation: expected vs. state-remitted, per member-month.

Full outer join on (member_id, coverage_month). Only lines paid on or before `as_of`
count, so a month can be reconciled as it looked at close or as it looks today.
Each row gets *observed* facts (features); causes are only proposed later by rules/LLM.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from engine.pricing import RateBook
from schemas.enums import LineType
from schemas.ledger import ExpectedRevenue
from schemas.remittance import RemittanceLine

ZERO = Decimal("0.00")


@dataclass
class MemberMonthRecon:
    member_id: str
    coverage_month: date
    expected: Decimal
    remitted: Decimal
    expected_row: ExpectedRevenue | None
    lines: list[RemittanceLine]
    features: dict[str, Any] = field(default_factory=dict)

    @property
    def delta(self) -> Decimal:
        return self.remitted - self.expected

    @property
    def is_variance(self) -> bool:
        f = self.features
        return (self.delta != 0 or f["regular_line_count"] > 1 or f["has_negative_line"]
                or (not f["has_expected"] and f["has_remit"]))

    @property
    def observed_type(self) -> str:
        f = self.features
        if f["has_expected"] and not f["has_remit"]:
            return "no_payment"
        if not f["has_expected"] and f["has_remit"]:
            return "paid_not_expected" if self.delta != 0 else "paid_not_expected_offset"
        if self.delta > 0:
            return "overpaid"
        if self.delta < 0:
            return "underpaid"
        return "offsetting_lines"


def reconcile_members(expected: list[ExpectedRevenue], lines: list[RemittanceLine], book: RateBook,
                      month: date, as_of: datetime, closed_at: datetime | None = None) -> list[MemberMonthRecon]:
    exp = {e.member_id: e for e in expected if e.coverage_month == month}
    paid: dict[str, list[RemittanceLine]] = defaultdict(list)
    all_line_ids = {l.line_id for l in lines}
    for line in lines:
        if line.coverage_month == month and line.payment_date <= as_of.date():
            paid[line.member_id].append(line)

    out = []
    for member_id in sorted(set(exp) | set(paid)):
        e = exp.get(member_id)
        ls = paid.get(member_id, [])
        rec = MemberMonthRecon(
            member_id=member_id, coverage_month=month,
            expected=e.pmpm if e else ZERO,
            remitted=sum((l.amount for l in ls), ZERO),
            expected_row=e, lines=ls,
        )
        rec.features = _features(e, ls, book, month, as_of, all_line_ids)
        # Was this month's rate revised after the month closed? Then the gap may mix a payment
        # problem with the revision (a second cause a single strong rule would hide).
        current = book.lookup(e.region.value, e.category.value, month, as_of) if e else None
        rec.features["rate_revised_after_close"] = bool(closed_at and current and current.approved_at > closed_at)
        out.append(rec)
    return out


def _features(e: ExpectedRevenue | None, ls: list[RemittanceLine], book: RateBook, month: date,
              as_of: datetime, all_line_ids: set[str]) -> dict[str, Any]:
    regular = [l for l in ls if l.line_type == LineType.REGULAR]
    negative = [l for l in ls if l.amount < 0]
    retro = [l for l in ls if l.line_type == LineType.RETRO_ADJUSTMENT]
    f: dict[str, Any] = {
        "has_expected": e is not None,
        "has_remit": bool(regular),
        "regular_line_count": len(regular),
        "regular_amounts": [str(l.amount) for l in regular],
        "duplicate_signature": len(regular) > 1 and len({(l.region, l.category, l.amount) for l in regular}) == 1,
        "has_negative_line": bool(negative),
        "negative_line_types": sorted({l.line_type.value for l in negative}),
        "negative_refs_found": bool(negative) and all(l.reference in all_line_ids for l in negative),
        "retro_line_total": str(sum((l.amount for l in retro), ZERO)),
        "expected_cell": f"{e.region.value} {e.category.value}" if e else None,
        "expected_version": e.rate_version_id if e else None,
        "remit_cells": sorted({f"{l.region.value} {l.category.value}" for l in regular}),
    }
    f["cell_match"] = bool(e and regular and all((l.region, l.category) == (e.region, e.category) for l in regular))

    # Which approved rate (if any) does the first regular payment equal, for the cell the state used?
    f["paid_matches_current"] = False
    f["paid_matches_older_version"] = None
    f["rate_approved_after_payment"] = False
    if regular:
        first = regular[0]
        if e is not None and f["cell_match"]:
            f["paid_matches_current"] = first.amount == e.pmpm
            current = book.lookup(e.region.value, e.category.value, month, as_of)
            # A retroactive version approved after the state paid: the state couldn't have known.
            f["rate_approved_after_payment"] = bool(current and current.approved_at.date() > first.payment_date)
        for kr in book.candidates(first.region.value, first.category.value, month, as_of):
            if kr.row.pmpm == first.amount and (e is None or kr.row.version_id != e.rate_version_id):
                f["paid_matches_older_version"] = kr.row.version_id
    return f
