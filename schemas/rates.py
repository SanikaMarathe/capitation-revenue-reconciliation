from __future__ import annotations

from datetime import date, datetime

from pydantic import Field

from schemas.base import LLMOutput, Money, Record, classified
from schemas.enums import Category, DataClass, DocFormat, RateVersionStatus, Region, VersionKind

C4 = DataClass.C4_PUBLIC


class SourceDocument(Record):
    document_id: str = classified(C4)
    filename: str = classified(C4)
    format: DocFormat = classified(C4)
    received_date: date = classified(C4)
    sha256: str = classified(C4)
    extraction_status: str = classified(C4, default="pending")


class RateVersion(Record):
    version_id: str = classified(C4)
    kind: VersionKind = classified(C4)
    published_date: date = classified(C4)
    source_document_id: str = classified(C4)
    status: RateVersionStatus = classified(C4)
    approved_by: str | None = classified(C4, default=None)
    approved_at: datetime | None = classified(C4, default=None)   # knowledge time
    notes: str = classified(C4, default="")


class RateRow(Record):
    """One cell, one effective interval. effective_to is the last month covered (inclusive)."""

    version_id: str = classified(C4)
    region: Region = classified(C4)
    category: Category = classified(C4)
    effective_from: date = classified(C4)   # always the 1st of a month
    effective_to: date | None = classified(C4, default=None)   # None = open-ended
    pmpm: Money = classified(C4)
    source_ref: str = classified(C4)


# ---------- LLM output (validated strictly) ----------

class ExtractedRate(LLMOutput):
    region: Region
    category: Category
    effective_from: date
    effective_to: date | None = None
    # A string, not a number: avoids float rounding inside JSON. Converted to Decimal by code.
    pmpm: str = Field(pattern=r"^\d+\.\d{2}$")
    source_ref: str


class RateNotice(LLMOutput):
    kind: VersionKind
    is_retroactive: bool
    published_date: date
    rates: list[ExtractedRate]
    notes: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
