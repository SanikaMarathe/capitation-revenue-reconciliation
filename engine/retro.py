"""Retro engine: reprice CLOSED months with today's knowledge and propose the increment.

    previously_booked = close snapshot (0 if the member-month was unknown at close)
                        + every non-rejected adjustment already raised (proposed or posted)
    delta             = new_expected - previously_booked

So a second revision books only its increment (+$80 then +$40, never +$120), even while the
first adjustment still awaits approval. The snapshot itself is never modified.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from schemas.enums import Category, LineType, Region
from schemas.ledger import AdjustmentLine, CloseSnapshot, ExpectedRevenue
from schemas.remittance import RemittanceLine

ZERO = Decimal("0.00")


@dataclass(frozen=True)
class RetroLine:
    member_id: str
    coverage_month: date
    region: Region
    category: Category
    previously_booked: Decimal
    new_expected: Decimal
    old_version_id: str
    new_version_id: str

    @property
    def delta(self) -> Decimal:
        return self.new_expected - self.previously_booked


def compute_retro(closed_months: list[date], snapshots: list[CloseSnapshot], prior_lines: list[AdjustmentLine],
                  repriced: list[ExpectedRevenue],
                  unpriceable: set[tuple[str, date]] | frozenset = frozenset()) -> list[RetroLine]:
    """`unpriceable`: member-months that could not be priced now (UNPRICED / ENROLLMENT_CONFLICT).
    They are skipped, never treated as $0: a missing price is a data problem, not evidence of ineligibility."""
    closed = set(closed_months)
    snap = {(s.member_id, s.coverage_month): s for s in snapshots if s.coverage_month in closed}
    prior_total: dict[tuple[str, date], Decimal] = defaultdict(lambda: ZERO)
    prior_last: dict[tuple[str, date], AdjustmentLine] = {}
    for line in prior_lines:
        key = (line.member_id, line.coverage_month)
        if line.coverage_month in closed:
            prior_total[key] += line.delta
            prior_last[key] = line
    new = {(e.member_id, e.coverage_month): e for e in repriced if e.coverage_month in closed}

    out: list[RetroLine] = []
    for key in sorted(set(snap) | set(prior_total) | set(new)):
        if key in unpriceable:
            continue
        s, e = snap.get(key), new.get(key)
        booked = (s.pmpm if s else ZERO) + prior_total.get(key, ZERO)
        new_expected = e.pmpm if e else ZERO
        if new_expected == booked:
            continue
        old_version = (prior_last[key].new_version_id if key in prior_last
                       else s.rate_version_id if s else "none")
        cell_src = e or s or prior_last[key]
        out.append(RetroLine(
            member_id=key[0], coverage_month=key[1], region=cell_src.region, category=cell_src.category,
            previously_booked=booked, new_expected=new_expected, old_version_id=old_version,
            new_version_id=e.rate_version_id if e else "none",
        ))
    return out


@dataclass(frozen=True)
class CellMonthSummary:
    coverage_month: date
    region: str
    category: str
    member_months: int
    delta: Decimal


def summarise(lines: list[RetroLine] | list[AdjustmentLine]) -> list[CellMonthSummary]:
    agg: dict[tuple[date, str, str], list] = defaultdict(lambda: [0, ZERO])
    for l in lines:
        k = (l.coverage_month, l.region.value, l.category.value)
        agg[k][0] += 1
        agg[k][1] += l.delta
    return [CellMonthSummary(m, r, c, n, d) for (m, r, c), (n, d) in sorted(agg.items())]


@dataclass(frozen=True)
class Settlement:
    member_id: str
    coverage_month: date
    owed_by_adjustments: Decimal       # total retro change we booked
    state_retro_paid: Decimal          # retro_adjustment lines from the state
    remaining_vs_state: Decimal        # current expected - everything the state paid for the month


def settlement(adjustment_lines: list[AdjustmentLine], current_expected: dict[tuple[str, date], Decimal],
               lines: list[RemittanceLine]) -> list[Settlement]:
    """Per member-month; each remittance line counts once, so nothing double-counts across revisions."""
    owed: dict[tuple[str, date], Decimal] = defaultdict(lambda: ZERO)
    for a in adjustment_lines:
        owed[(a.member_id, a.coverage_month)] += a.delta
    retro_paid: dict[tuple[str, date], Decimal] = defaultdict(lambda: ZERO)
    net_paid: dict[tuple[str, date], Decimal] = defaultdict(lambda: ZERO)
    for l in lines:
        k = (l.member_id, l.coverage_month)
        if k in owed:
            net_paid[k] += l.amount
            if l.line_type == LineType.RETRO_ADJUSTMENT:
                retro_paid[k] += l.amount
    return [Settlement(k[0], k[1], owed[k], retro_paid[k], current_expected.get(k, ZERO) - net_paid[k])
            for k in sorted(owed)]
