from __future__ import annotations

from datetime import date

from schemas.base import Record, classified
from schemas.enums import Category, DataClass, Region

C1 = DataClass.C1_DIRECT_IDENTIFIER
C2 = DataClass.C2_PHI


class Member(Record):
    """The ONLY model holding direct identifiers. Read via storage/phi.py only."""

    member_id: str = classified(C2)
    first_name: str = classified(C1)
    last_name: str = classified(C1)
    dob: date = classified(C1)


class EnrollmentSpan(Record):
    span_id: str = classified(C2)
    member_id: str = classified(C2)
    start_date: date = classified(C2)
    end_date: date | None = classified(C2, default=None)
    region: Region = classified(C2)
    category: Category = classified(C2)
    source: str = classified(C2, default="state_834")
    received_date: date = classified(C2)   # knowledge time for enrollment
