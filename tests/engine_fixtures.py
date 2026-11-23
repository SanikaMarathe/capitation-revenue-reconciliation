"""Tiny hand-built worlds for the edge-case tests. Every expected value in the tests is typed by hand."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal as D

from schemas.enrollment import EnrollmentSpan
from schemas.enums import RateVersionStatus, VersionKind
from schemas.rates import RateRow, RateVersion
from storage import repo
from storage.db import connect

JAN, FEB, MAR, APR, MAY, JUN = (date(2026, m, 1) for m in range(1, 7))
BEN, WENDY = "M000002", "M000003"


def close_at(month: date) -> datetime:
    return datetime(month.year + month.month // 12, month.month % 12 + 1, 10, 17, 0)


def add_span(conn, span_id, member, start, end, region, category, received):
    conn.execute("INSERT INTO enrollment_spans VALUES (?,?,?,?,?,?,?,?)",
                 [span_id, member, start, end, region, category, "state_834", received])


def add_version(conn, version_id, kind, rows, published=date(2025, 12, 1)):
    v = RateVersion(version_id=version_id, kind=VersionKind(kind), published_date=published,
                    source_document_id=f"DOC-{version_id}", status=RateVersionStatus.DRAFT)
    repo.insert_rate_version(conn, v, [
        RateRow(version_id=version_id, region=r, category=c, effective_from=f, effective_to=t, pmpm=D(p),
                source_ref=f"{version_id}:{r}-{c}")
        for (r, c, f, t, p) in rows])


def ben_world():
    """Ben: North Adult all year. Wendy: West Child all year.
    v1 (Jan):  North Adult 400, West Child 200
    v2 (Apr):  full table, West Child 250, North Adult unchanged 400
    """
    conn = connect()
    add_span(conn, "SP1", BEN, date(2025, 6, 1), None, "North", "Adult", date(2025, 6, 1))
    add_span(conn, "SP2", WENDY, date(2025, 6, 1), None, "West", "Child", date(2025, 6, 1))
    add_version(conn, "v1", "full", [("North", "Adult", JAN, None, "400.00"), ("West", "Child", JAN, None, "200.00")])
    add_version(conn, "v2", "full", [("North", "Adult", APR, None, "400.00"), ("West", "Child", APR, None, "250.00")],
                published=date(2026, 3, 15))
    return conn
