"""Proration strategies: does a span earn the PMPM for a coverage month?"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from schemas.enrollment import EnrollmentSpan

EligibilityRule = Callable[[EnrollmentSpan, date], bool]


def first_of_month(span: EnrollmentSpan, month: date) -> bool:
    """Full PMPM iff the span covers day 1 of the month.

    Mid-month joins start next month; mid-month terms keep the month.
    """
    return span.start_date <= month and (span.end_date is None or span.end_date >= month)


RULES: dict[str, EligibilityRule] = {"first_of_month": first_of_month}
