from __future__ import annotations

from datetime import date, datetime

from schemas.base import LLMOutput, Money, Record, classified
from schemas.enums import AdjustmentStatus, Category, DataClass, PeriodStatus, Region

C2 = DataClass.C2_PHI
C3 = DataClass.C3_FINANCIAL


class Period(Record):
    coverage_month: date = classified(C3)
    status: PeriodStatus = classified(C3)
    closed_at: datetime | None = classified(C3, default=None)
    closed_by: str | None = classified(C3, default=None)


class ExpectedRevenue(Record):
    """One priced member-month, with full provenance."""

    member_id: str = classified(C2)
    coverage_month: date = classified(C2)
    region: Region = classified(C2)
    category: Category = classified(C2)
    span_id: str = classified(C2)
    rate_version_id: str = classified(C3)
    source_ref: str = classified(C3)
    pmpm: Money = classified(C3)
    as_of: datetime = classified(C3)


class CloseSnapshot(Record):
    """Immutable copy of what was booked when a month closed."""

    coverage_month: date = classified(C2)
    member_id: str = classified(C2)
    region: Region = classified(C2)
    category: Category = classified(C2)
    span_id: str = classified(C2)
    rate_version_id: str = classified(C3)
    pmpm: Money = classified(C3)
    snapshot_at: datetime = classified(C3)


class Adjustment(Record):
    adjustment_id: str = classified(C3)
    trigger: str = classified(C3)          # rate_version | retro_enrollment
    trigger_ref: str = classified(C3)
    status: AdjustmentStatus = classified(C3)
    total: Money = classified(C3)
    proposed_by: str = classified(C3)
    memo: str = classified(C3, default="")
    memo_grounded: bool | None = classified(C3, default=None)
    created_at: datetime = classified(C3)


class AdjustmentLine(Record):
    adjustment_id: str = classified(C3)
    member_id: str = classified(C2)
    coverage_month: date = classified(C2)
    region: Region = classified(C2)
    category: Category = classified(C2)
    previously_booked: Money = classified(C3)
    new_expected: Money = classified(C3)
    old_version_id: str = classified(C3)
    new_version_id: str = classified(C3)
    delta: Money = classified(C3)


class LedgerEntry(Record):
    entry_id: str = classified(C3)
    period: date = classified(C3)
    account: str = classified(C3)
    debit: Money = classified(C3)
    credit: Money = classified(C3)
    memo: str = classified(C3)
    source: str = classified(C3)            # close | prior_period_adjustment
    adjustment_id: str | None = classified(C3, default=None)
    posting_key: str = classified(C3)       # unique -> idempotent posting
    approved_by: str = classified(C3)
    approved_at: datetime = classified(C3)


# ---------- LLM output ----------

class MemoDraft(LLMOutput):
    """Memo prose with {placeholders}; code fills the numbers."""

    title: str
    body: str
