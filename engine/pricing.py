"""Bitemporal pricing: expected revenue for a member-month *as known at* a given time.

    price(member, month, as_of):
        span  = latest-received eligible span with received_date <= as_of
        rate  = for THIS cell, among rows of versions approved at or before as_of
                and covering the month, the one approved last
    No rate -> UNPRICED. Two different spans received the same day -> ENROLLMENT_CONFLICT.
    Never a silent $0. Current status never filters history ('superseded' is display-only).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime

from engine.proration import EligibilityRule, first_of_month
from schemas.enrollment import EnrollmentSpan
from schemas.enums import EngineException, RateVersionStatus
from schemas.ledger import ExpectedRevenue
from schemas.rates import RateRow, RateVersion


@dataclass(frozen=True)
class KnownRate:
    row: RateRow
    approved_at: datetime


@dataclass(frozen=True)
class PricingIssue:
    member_id: str
    coverage_month: date
    code: EngineException
    detail: str


@dataclass
class PricingResult:
    expected: list[ExpectedRevenue] = field(default_factory=list)
    issues: list[PricingIssue] = field(default_factory=list)


def _covers(row: RateRow, month: date) -> bool:
    return row.effective_from <= month and (row.effective_to is None or month <= row.effective_to)


class RateBook:
    """All rate rows from versions that were ever approved, indexed by cell."""

    def __init__(self, rows: Iterable[RateRow], versions: Iterable[RateVersion]) -> None:
        approved_at = {
            v.version_id: v.approved_at
            for v in versions
            if v.approved_at is not None and v.status not in (RateVersionStatus.DRAFT, RateVersionStatus.REJECTED)
        }
        self._by_cell: dict[tuple[str, str], list[KnownRate]] = defaultdict(list)
        for row in rows:
            if row.version_id in approved_at:
                self._by_cell[(row.region.value, row.category.value)].append(KnownRate(row, approved_at[row.version_id]))
        for entries in self._by_cell.values():
            entries.sort(key=lambda k: k.approved_at)

    def lookup(self, region: str, category: str, month: date, as_of: datetime) -> KnownRate | None:
        best = None
        for kr in self._by_cell.get((region, category), []):
            if kr.approved_at <= as_of and _covers(kr.row, month):
                best = kr   # list is sorted by approval time, so the last match wins
        return best

    def candidates_after(self, region: str, category: str, month: date, as_of: datetime) -> list[KnownRate]:
        """Known rate rows for this cell that start after `month` (later prospective changes)."""
        return sorted((kr for kr in self._by_cell.get((region, category), [])
                       if kr.approved_at <= as_of and kr.row.effective_from > month),
                      key=lambda k: k.row.effective_from)

    def candidates(self, region: str, category: str, month: date, as_of: datetime) -> list[KnownRate]:
        """Every known rate that has ever covered this cell-month (used to spot stale-rate payments)."""
        return [kr for kr in self._by_cell.get((region, category), [])
                if kr.approved_at <= as_of and _covers(kr.row, month)]


def resolve_span(spans: list[EnrollmentSpan], month: date, as_of: datetime,
                 rule: EligibilityRule = first_of_month) -> tuple[EnrollmentSpan | None, bool]:
    """Returns (span, conflict)."""
    known = [s for s in spans if s.received_date <= as_of.date() and rule(s, month)]
    if not known:
        return None, False
    latest = max(s.received_date for s in known)
    top = [s for s in known if s.received_date == latest]
    cells = {(s.region, s.category) for s in top}
    if len(cells) > 1:
        return None, True
    return max(top, key=lambda s: s.span_id), False


def price_month(spans_by_member: dict[str, list[EnrollmentSpan]], book: RateBook, month: date,
                as_of: datetime, rule: EligibilityRule = first_of_month) -> PricingResult:
    result = PricingResult()
    for member_id in sorted(spans_by_member):
        span, conflict = resolve_span(spans_by_member[member_id], month, as_of, rule)
        if conflict:
            result.issues.append(PricingIssue(member_id, month, EngineException.ENROLLMENT_CONFLICT,
                                              "two different spans received on the same date"))
            continue
        if span is None:
            continue
        known = book.lookup(span.region.value, span.category.value, month, as_of)
        if known is None:
            result.issues.append(PricingIssue(member_id, month, EngineException.UNPRICED,
                                              f"no approved rate for {span.region.value} {span.category.value} {month:%Y-%m}"))
            continue
        result.expected.append(ExpectedRevenue(
            member_id=member_id, coverage_month=month, region=span.region, category=span.category,
            span_id=span.span_id, rate_version_id=known.row.version_id, source_ref=known.row.source_ref,
            pmpm=known.row.pmpm, as_of=as_of,
        ))
    return result
