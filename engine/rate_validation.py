"""Deterministic checks on an extracted rate notice, and the diff an approver reviews.

errors -> the draft cannot be approved until fixed
flags  -> the approver must look (e.g. a >15% move), but may approve
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from engine.pricing import RateBook
from schemas.enums import VersionKind
from schemas.rates import RateRow

N_CELLS = 16
_NUMBER = re.compile(r"\$?\s?(\d[\d,]*(?:\.\d+)?)")


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    @property
    def approvable(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class CellDiff:
    region: str
    category: str
    effective_from: date
    effective_to: date | None
    old_pmpm: Decimal | None
    old_version: str | None
    new_pmpm: Decimal
    source_ref: str

    @property
    def change_pct(self) -> Decimal | None:
        if not self.old_pmpm:
            return None
        return ((self.new_pmpm - self.old_pmpm) / self.old_pmpm * 100).quantize(Decimal("0.01"))

    @property
    def changed(self) -> bool:
        return self.old_pmpm != self.new_pmpm


def numbers_in(text: str) -> set[Decimal]:
    out = set()
    for m in _NUMBER.findall(text or ""):
        try:
            out.add(Decimal(m.replace(",", "")))
        except InvalidOperation:
            pass
    return out


def month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _prev_month(d: date) -> date:
    return date(d.year - (d.month == 1), 12 if d.month == 1 else d.month - 1, 1)


def validate(rows: list[RateRow], *, kind: VersionKind, is_retroactive: bool, published: date,
             confidence: float, refs: dict[str, str], book: RateBook, as_of: datetime,
             change_flag_pct: Decimal = Decimal("15")) -> ValidationReport:
    rep = ValidationReport()
    seen: set[tuple] = set()
    for r in rows:
        cell = f"{r.region.value} {r.category.value}"
        key = (r.region, r.category, r.effective_from)
        if key in seen:
            rep.errors.append(f"{cell}: duplicate row for {r.effective_from:%Y-%m}")
        seen.add(key)
        if r.pmpm <= 0:
            rep.errors.append(f"{cell}: rate must be positive")
        if r.effective_from.day != 1:
            rep.errors.append(f"{cell}: effective_from {r.effective_from} is not the 1st of a month")
        if r.effective_to is not None and r.effective_to < r.effective_from:
            rep.errors.append(f"{cell}: effective_to before effective_from")
        # Source cross-check: necessary, not sufficient (it proves the number is at the cited spot,
        # not that the spot belongs to this cell; the approver checks that on the diff).
        if r.source_ref not in refs:
            rep.errors.append(f"{cell}: cited source '{r.source_ref}' does not exist in the document")
        elif r.pmpm not in numbers_in(refs[r.source_ref]):
            rep.errors.append(f"{cell}: {r.pmpm} not found at cited source '{r.source_ref}'")

        prior = book.lookup(r.region.value, r.category.value, r.effective_from, as_of)
        if prior is not None and prior.row.pmpm:
            move = abs(r.pmpm - prior.row.pmpm) / prior.row.pmpm * 100
            if move > change_flag_pct:
                rep.flags.append(f"{cell}: {move:.1f}% change vs {prior.row.version_id} "
                                 f"({prior.row.pmpm} -> {r.pmpm}) exceeds {change_flag_pct}%")
        if kind == VersionKind.AMENDMENT and r.effective_to is None:
            for later in book.candidates_after(r.region.value, r.category.value, r.effective_from, as_of):
                before = book.lookup(r.region.value, r.category.value, _prev_month(later.row.effective_from), as_of)
                if before is not None and before.row.pmpm != later.row.pmpm:
                    rep.flags.append(f"{cell}: amendment is open-ended but a later change for this cell starts "
                                     f"{later.row.effective_from:%Y-%m} ({later.row.version_id}); confirm the end date")
                    break

    cells = {(r.region, r.category) for r in rows}
    if kind == VersionKind.FULL and len(cells) != N_CELLS:
        rep.errors.append(f"full rate notice must cover {N_CELLS} cells, found {len(cells)}")
    if kind == VersionKind.AMENDMENT and not rows:
        rep.errors.append("amendment contains no rates")
    if rows:
        starts_before_publication = min(r.effective_from for r in rows) < month_start(published)
        if starts_before_publication != is_retroactive:
            rep.flags.append(f"is_retroactive={is_retroactive} but earliest effective date is "
                             f"{min(r.effective_from for r in rows)} vs published {published}")
    if confidence < 0.8:
        rep.flags.append(f"extractor confidence {confidence:.2f} is low")
    return rep


def diff(rows: list[RateRow], book: RateBook, as_of: datetime) -> list[CellDiff]:
    out = []
    for r in sorted(rows, key=lambda x: (x.region.value, x.category.value, x.effective_from)):
        prior = book.lookup(r.region.value, r.category.value, r.effective_from, as_of)
        out.append(CellDiff(r.region.value, r.category.value, r.effective_from, r.effective_to,
                            prior.row.pmpm if prior else None, prior.row.version_id if prior else None,
                            r.pmpm, r.source_ref))
    return out
