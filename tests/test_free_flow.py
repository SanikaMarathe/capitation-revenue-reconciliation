"""The app follows no script: documents wait in the inbox, months close when their data has arrived, and
version numbers follow the order documents are read. These rules come from the data, not a timeline."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from data_gen.generate import RAW, generate
from storage.db import connect
from storage.load_raw import load_raw
from workflow import demo


@pytest.fixture(scope="module")
def conn():
    generate()
    c = connect(":memory:")
    load_raw(c, RAW)
    return c


def test_inbox_lists_every_document_unread_at_the_start(conn):
    names = [p.name for p, read in demo.inbox(conn)]
    assert len(names) == 3 and not any(read for _, read in demo.inbox(conn))


def test_a_month_closes_only_after_it_ends_and_the_state_has_paid(conn):
    assert demo.closable_month(conn, datetime(2026, 1, 20)) is None          # January not over yet
    assert demo.closable_month(conn, datetime(2026, 2, 2)) == date(2026, 1, 1)


def test_months_close_in_order(conn):
    # Even late in the year, the first open month is offered first.
    assert demo.closable_month(conn, datetime(2026, 7, 1)) == date(2026, 1, 1)


def test_versions_are_numbered_in_reading_order(conn):
    assert demo.next_version_id(conn) == "v1"


def test_fresh_start_date_comes_before_any_data(conn):
    first = conn.execute("SELECT min(coverage_month) FROM remittance_lines").fetchone()[0]
    assert demo.data_start(conn).date() < first
