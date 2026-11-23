"""Journal entry amounts. Always computed here, never by an LLM.

Account names are ILLUSTRATIVE (the prompt leaves accounting policy open):
  close:  Dr Capitation Receivable / Cr Capitation Revenue
  retro:  Dr Capitation Receivable / Cr Capitation Revenue - Prior Period Adjustment
          (reversed when the delta is negative), one pair per original period.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

RECEIVABLE = "1210 Capitation Receivable"
REVENUE = "4000 Capitation Revenue"
REVENUE_PPA = "4010 Capitation Revenue - Prior Period Adjustment"
ZERO = Decimal("0.00")


@dataclass(frozen=True)
class JournalLine:
    period: date
    account: str
    debit: Decimal
    credit: Decimal
    memo: str
    posting_key: str


def _pair(period: date, amount: Decimal, revenue_account: str, key_prefix: str, memo: str) -> list[JournalLine]:
    if amount == 0:
        return []
    dr, cr = (RECEIVABLE, revenue_account) if amount > 0 else (revenue_account, RECEIVABLE)
    value = abs(amount)
    return [
        JournalLine(period, dr, value, ZERO, memo, f"{key_prefix}:{period:%Y-%m}:DR"),
        JournalLine(period, cr, ZERO, value, memo, f"{key_prefix}:{period:%Y-%m}:CR"),
    ]


def close_entry(period: date, booked_total: Decimal) -> list[JournalLine]:
    return _pair(period, booked_total, REVENUE, "close", f"Capitation revenue {period:%b %Y}")


def adjustment_entry(adjustment_id: str, deltas: list[tuple[date, Decimal]]) -> list[JournalLine]:
    by_period: dict[date, Decimal] = defaultdict(lambda: ZERO)
    for period, delta in deltas:
        by_period[period] += delta
    out: list[JournalLine] = []
    for period in sorted(by_period):
        out += _pair(period, by_period[period], REVENUE_PPA, adjustment_id,
                     f"Prior period adjustment {adjustment_id} for {period:%b %Y}")
    return out


def is_balanced(lines: list[JournalLine]) -> bool:
    return sum((l.debit for l in lines), ZERO) == sum((l.credit for l in lines), ZERO)
