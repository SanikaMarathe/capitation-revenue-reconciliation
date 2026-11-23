"""Regression tests for the third design review: pricing gaps, stale adjustments, bank scoping, sign-off integrity."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal as D

import pytest

from engine.bank_match import match_bank
from governance import approvals
from schemas.remittance import BankDeposit, Remit
from storage import repo
from tests.engine_fixtures import APR, BEN, FEB, JAN, MAR, add_span, add_version, ben_world, close_at
from workflow.close import CloseBlocked, CloseOrchestrator
from workflow.deterministic_close import DeterministicClose, PricingIncomplete

V3_DAY = datetime(2026, 5, 22, 9, 0)
V4_DAY = datetime(2026, 6, 1, 9, 0)


def _closed_world():
    conn = ben_world()
    approvals.approve_rate_version(conn, "v1", "controller", datetime(2025, 12, 15, 9))
    approvals.approve_rate_version(conn, "v2", "controller", datetime(2026, 3, 20, 9))
    dc = DeterministicClose(conn)
    for m in (JAN, FEB, MAR, APR):
        dc.close_month(m, close_at(m), "controller")
    return conn, dc


# ---------------------------------------------------------------- P1: pricing gaps
def test_close_refuses_to_book_when_a_member_month_cannot_be_priced():
    conn = ben_world()
    approvals.approve_rate_version(conn, "v1", "controller", datetime(2025, 12, 15, 9))
    add_span(conn, "SP7", "M000007", date(2025, 6, 1), None, "East", "Disabled", date(2025, 6, 1))   # no rate
    with pytest.raises(PricingIncomplete, match="M000007 UNPRICED"):
        DeterministicClose(conn).close_month(JAN, close_at(JAN), "controller")
    assert conn.execute("SELECT count(*) FROM ledger_entries").fetchone()[0] == 0


def test_retro_never_reverses_a_booked_month_just_because_it_became_unpriceable():
    conn, dc = _closed_world()
    # A late, conflicting span for Ben arrives: Jan-Apr now can't be priced (ENROLLMENT_CONFLICT).
    add_span(conn, "SP1b", BEN, date(2025, 6, 1), None, "West", "Child", date(2025, 6, 1))
    lines = dc.retro_lines(V3_DAY)
    assert [l for l in lines if l.member_id == BEN] == []              # no -$400 reversals
    assert {i.member_id for i in dc.last_retro_exceptions} == {BEN}     # surfaced as exceptions instead


def test_sign_off_is_blocked_by_pricing_issues():
    conn = ben_world()
    approvals.approve_rate_version(conn, "v1", "controller", datetime(2025, 12, 15, 9))
    add_span(conn, "SP7", "M000007", date(2025, 6, 1), None, "East", "Disabled", date(2025, 6, 1))
    orch = CloseOrchestrator(conn, client=None)
    orch.run_close(JAN, close_at(JAN), "analyst", investigate=False)
    with pytest.raises(CloseBlocked, match="can't be priced"):
        orch.sign_off(JAN, close_at(JAN), "controller")


# ---------------------------------------------------------------- P1: out-of-order approval
def _two_pending_revisions():
    conn, dc = _closed_world()
    add_version(conn, "v3", "amendment", [("North", "Adult", JAN, None, "420.00")], published=date(2026, 5, 20))
    approvals.approve_rate_version(conn, "v3", "controller", V3_DAY)
    a3 = dc.propose_retro(V3_DAY, "rate_version", "v3")                 # +80, pending
    add_version(conn, "v4", "amendment", [("North", "Adult", JAN, None, "430.00")], published=date(2026, 5, 30))
    approvals.approve_rate_version(conn, "v4", "controller", V4_DAY)
    a4 = dc.propose_retro(V4_DAY, "rate_version", "v4")                 # +40 on top of the pending +80
    return conn, dc, a3, a4


def test_later_adjustment_cannot_post_before_the_one_it_builds_on():
    conn, _, a3, a4 = _two_pending_revisions()
    with pytest.raises(approvals.ApprovalError, match="stale"):
        approvals.approve_adjustment(conn, a4, "controller")
    assert repo.adjustment(conn, a4)["status"] == "stale"
    assert conn.execute("SELECT count(*) FROM ledger_entries WHERE adjustment_id=?", [a4]).fetchone()[0] == 0


def test_in_order_approval_posts_both():
    conn, _, a3, a4 = _two_pending_revisions()
    assert approvals.approve_adjustment(conn, a3, "controller") is True
    assert approvals.approve_adjustment(conn, a4, "controller") is True


def test_rejecting_the_earlier_one_marks_dependents_stale_and_retro_regenerates():
    conn, dc, a3, a4 = _two_pending_revisions()
    approvals.reject_adjustment(conn, a3, "controller", "disputed")
    assert repo.adjustment(conn, a4)["status"] == "stale"
    regen = dc.retro_lines(datetime(2026, 6, 2))
    assert sum((l.delta for l in regen if l.member_id == BEN), D("0")) == D("120.00")   # 400 -> 430 over 4 months
    a5 = dc.propose_retro(datetime(2026, 6, 2), "manual_check", "regenerate")
    assert approvals.approve_adjustment(conn, a5, "controller") is True
    booked = conn.execute("SELECT sum(debit) FROM ledger_entries WHERE source='prior_period_adjustment' "
                          "AND account LIKE '1210%'").fetchone()[0]
    assert booked == D("120.00")


# ---------------------------------------------------------------- P1: bank scoping
def test_historical_bank_view_ignores_links_to_later_deposits():
    remits = [Remit(remit_id="R1", payment_date=date(2026, 3, 25), total_amount=D("100.00"), line_count=1)]
    deposits = [BankDeposit(deposit_id="D1", deposit_date=date(2026, 3, 26), amount=D("100.00"), payer="S", reference="R1")]
    res = match_bank(remits, deposits, {"D9_LATER": "R1"})            # saved link to a deposit not in scope
    assert res.outcomes[0].deposit_id == "D1"                          # no KeyError, links normally


def test_remit_cannot_be_linked_to_two_deposits():
    conn = ben_world()
    approvals.confirm_bank_link(conn, "D1", "R1", "analyst")
    with pytest.raises(approvals.ApprovalError, match="one-to-one"):
        approvals.confirm_bank_link(conn, "D2", "R1", "analyst")


# ---------------------------------------------------------------- P2: sign-off books what was reviewed
def test_sign_off_is_invalidated_when_rates_change_after_review():
    conn = ben_world()
    approvals.approve_rate_version(conn, "v1", "controller", datetime(2025, 12, 15, 9))
    approvals.approve_rate_version(conn, "v2", "controller", datetime(2025, 12, 16, 9))   # no open drafts
    orch = CloseOrchestrator(conn, client=None)
    orch.run_close(JAN, datetime(2026, 2, 10, 17), "analyst", investigate=False)
    # A retroactive amendment is approved between review and sign-off.
    add_version(conn, "v9", "amendment", [("North", "Adult", JAN, None, "410.00")], published=date(2026, 2, 10))
    approvals.approve_rate_version(conn, "v9", "controller", datetime(2026, 2, 10, 17, 30))
    with pytest.raises(CloseBlocked, match="changed since"):
        orch.sign_off(JAN, datetime(2026, 2, 10, 18), "controller")
    assert conn.execute("SELECT last_completed_step FROM close_runs").fetchone()[0] is None   # must re-run
    orch.run_close(JAN, datetime(2026, 2, 10, 18), "analyst", investigate=False)
    total = orch.sign_off(JAN, datetime(2026, 2, 10, 18), "controller")
    assert total == D("610.00")                                        # Ben 410 + Wendy 200: the reviewed result
