"""Hand-written edge cases (DECISIONS.md D17-D23). Expected values are typed by hand, not computed."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal as D

import pytest

from engine import journal, rules
from engine.bank_match import match_bank
from engine.pricing import RateBook, price_month
from engine.proration import first_of_month
from engine.retro import settlement
from governance import approvals
from governance.audit import append_event, verify_chain
from schemas.enrollment import EnrollmentSpan
from schemas.enums import ActorType, DiscrepancyCode, RuleStrength
from schemas.remittance import BankDeposit, Remit, RemittanceLine
from storage import repo
from tests.engine_fixtures import APR, BEN, FEB, JAN, JUN, MAR, MAY, WENDY, add_span, add_version, ben_world, close_at
from workflow.deterministic_close import DeterministicClose

V3_DAY = datetime(2026, 5, 22, 9, 0)
V4_DAY = datetime(2026, 6, 1, 9, 0)


def _setup_closed_jan_to_apr():
    conn = ben_world()
    approvals.approve_rate_version(conn, "v1", "controller", datetime(2025, 12, 15, 9))
    approvals.approve_rate_version(conn, "v2", "controller", datetime(2026, 3, 20, 9))
    dc = DeterministicClose(conn)
    for m in (JAN, FEB, MAR, APR):
        dc.close_month(m, close_at(m), "controller")
    add_version(conn, "v3", "amendment", [("North", "Adult", JAN, None, "420.00")], published=date(2026, 5, 20))
    approvals.approve_rate_version(conn, "v3", "controller", V3_DAY)
    return conn, dc


def _ben_total(lines):
    return sum((l.delta for l in lines if l.member_id == BEN), D("0"))


# ---------------------------------------------------------------- retro
def test_retro_after_amendment_is_80_for_ben():
    conn, dc = _setup_closed_jan_to_apr()
    lines = dc.retro_lines(V3_DAY)
    assert _ben_total(lines) == D("80.00")                     # 4 closed months x $20
    assert {l.member_id for l in lines} == {BEN}               # Wendy untouched


def test_second_revision_books_only_the_increment():
    conn, dc = _setup_closed_jan_to_apr()
    adj = dc.propose_retro(V3_DAY, "rate_version", "v3")
    approvals.approve_adjustment(conn, adj, "controller")
    add_version(conn, "v4", "amendment", [("North", "Adult", JAN, None, "430.00")], published=date(2026, 5, 30))
    approvals.approve_rate_version(conn, "v4", "controller", V4_DAY)
    assert _ben_total(dc.retro_lines(V4_DAY)) == D("40.00")    # not 120


def test_pending_first_adjustment_still_counts_as_booked():
    conn, dc = _setup_closed_jan_to_apr()
    dc.propose_retro(V3_DAY, "rate_version", "v3")             # proposed, NOT approved
    add_version(conn, "v4", "amendment", [("North", "Adult", JAN, None, "430.00")], published=date(2026, 5, 30))
    approvals.approve_rate_version(conn, "v4", "controller", V4_DAY)
    assert _ben_total(dc.retro_lines(V4_DAY)) == D("40.00")


def test_rejected_adjustment_frees_the_member_months():
    conn, dc = _setup_closed_jan_to_apr()
    adj = dc.propose_retro(V3_DAY, "rate_version", "v3")
    approvals.reject_adjustment(conn, adj, "controller", "wrong")
    assert _ben_total(dc.retro_lines(V3_DAY)) == D("80.00")


def test_retro_enrollment_adds_full_pmpm_for_closed_months():
    conn = ben_world()
    approvals.approve_rate_version(conn, "v1", "controller", datetime(2025, 12, 15, 9))
    # New member's February coverage arrives on Apr 15, after Feb and Mar closed.
    add_span(conn, "SP9", "M000009", date(2026, 1, 20), None, "North", "Adult", date(2026, 4, 15))
    dc = DeterministicClose(conn)
    for m in (JAN, FEB, MAR):
        dc.close_month(m, close_at(m), "controller")
    lines = [l for l in dc.retro_lines(datetime(2026, 4, 16)) if l.member_id == "M000009"]
    assert [(l.coverage_month, l.previously_booked, l.delta, l.old_version_id) for l in lines] == [
        (FEB, D("0.00"), D("400.00"), "none"),
        (MAR, D("0.00"), D("400.00"), "none"),
    ]


# ---------------------------------------------------------------- pricing
def _price_all(conn, as_of):
    dc = DeterministicClose(conn)
    return {(e.member_id, e.coverage_month): e for m in (JAN, FEB, MAR, APR, MAY, JUN)
            for e in dc.price(m, as_of).expected}


def test_cell_level_precedence():
    conn, _ = _setup_closed_jan_to_apr()
    p = _price_all(conn, V3_DAY)
    assert [p[(WENDY, m)].pmpm for m in (JAN, FEB, MAR, APR, MAY, JUN)] == [D("200.00")] * 3 + [D("250.00")] * 3
    assert all(p[(BEN, m)].pmpm == D("420.00") for m in (JAN, FEB, MAR, APR, MAY, JUN))


def test_historical_view_before_amendment():
    conn, _ = _setup_closed_jan_to_apr()
    p = _price_all(conn, datetime(2026, 5, 21))
    assert all(p[(BEN, m)].pmpm == D("400.00") for m in (JAN, FEB, MAR, APR))


def test_historical_lookup_after_supersession():
    conn, _ = _setup_closed_jan_to_apr()
    status = conn.execute("SELECT status FROM rate_versions WHERE version_id='v1'").fetchone()[0]
    assert status == "superseded"                              # label only...
    feb = DeterministicClose(conn).price(FEB, close_at(FEB)).expected
    ben = next(e for e in feb if e.member_id == BEN)
    assert (ben.pmpm, ben.rate_version_id) == (D("400.00"), "v1")   # ...history still finds it


def test_unpriced_is_an_exception_not_zero():
    conn = ben_world()
    approvals.approve_rate_version(conn, "v1", "controller", datetime(2025, 12, 15, 9))
    add_span(conn, "SP7", "M000007", date(2025, 6, 1), None, "East", "Disabled", date(2025, 6, 1))
    result = DeterministicClose(conn).price(JAN, close_at(JAN))
    assert "M000007" not in {e.member_id for e in result.expected}
    assert [(i.member_id, i.code.value) for i in result.issues] == [("M000007", "UNPRICED")]


def test_enrollment_conflict_is_an_exception():
    conn = ben_world()
    approvals.approve_rate_version(conn, "v1", "controller", datetime(2025, 12, 15, 9))
    add_span(conn, "SP8a", "M000008", date(2025, 6, 1), None, "North", "Adult", date(2025, 6, 1))
    add_span(conn, "SP8b", "M000008", date(2025, 6, 1), None, "West", "Child", date(2025, 6, 1))
    result = DeterministicClose(conn).price(JAN, close_at(JAN))
    assert [(i.member_id, i.code.value) for i in result.issues] == [("M000008", "ENROLLMENT_CONFLICT")]


def test_first_of_month_proration():
    span = EnrollmentSpan(span_id="S", member_id="M1", start_date=date(2026, 2, 14), end_date=date(2026, 4, 17),
                          region="North", category="Adult", received_date=date(2026, 2, 1))
    assert [first_of_month(span, m) for m in (FEB, MAR, APR, MAY)] == [False, True, True, False]


# ---------------------------------------------------------------- governance
def test_approve_twice_posts_once():
    conn, dc = _setup_closed_jan_to_apr()
    adj = dc.propose_retro(V3_DAY, "rate_version", "v3")
    assert approvals.approve_adjustment(conn, adj, "controller") is True
    assert approvals.approve_adjustment(conn, adj, "controller") is False
    n = conn.execute("SELECT count(*) FROM ledger_entries WHERE adjustment_id=?", [adj]).fetchone()[0]
    assert n == 8                                              # 4 periods x (Dr + Cr)


def test_maker_cannot_approve_own_adjustment():
    conn, dc = _setup_closed_jan_to_apr()
    adj = dc.propose_retro(V3_DAY, "rate_version", "v3", proposer="analyst")
    with pytest.raises(approvals.ApprovalError, match="maker-checker"):
        approvals.approve_adjustment(conn, adj, "analyst")


def test_closed_period_cannot_be_closed_again():
    conn, dc = _setup_closed_jan_to_apr()
    with pytest.raises(approvals.ApprovalError):
        dc.close_month(JAN, close_at(JAN), "controller")


def test_journal_balances_and_reverses_negative_deltas():
    entries = journal.adjustment_entry("ADJ1", [(JAN, D("20.00")), (FEB, D("-15.00"))])
    assert journal.is_balanced(entries)
    jan_dr = next(e for e in entries if e.period == JAN and e.debit > 0)
    feb_dr = next(e for e in entries if e.period == FEB and e.debit > 0)
    assert jan_dr.account == journal.RECEIVABLE and feb_dr.account == journal.REVENUE_PPA


def test_audit_chain_detects_tampering():
    conn = ben_world()
    for i in range(3):
        append_event(conn, actor_type=ActorType.SYSTEM, actor_id="t", action="a", entity="e", entity_id=str(i))
    assert verify_chain(conn) == (True, None)
    conn.execute("UPDATE audit_log SET actor_id='someone-else' WHERE seq=2")
    assert verify_chain(conn) == (False, 2)


def test_audit_refuses_identity_fields():
    conn = ben_world()
    with pytest.raises(ValueError):
        append_event(conn, actor_type=ActorType.SYSTEM, actor_id="t", action="a", entity="e", entity_id="1",
                     payload={"member": {"first_name": "x"}})


# ---------------------------------------------------------------- settlement
def _line(i, month, amount, line_type="regular", paid=None):
    return RemittanceLine(line_id=f"L{i}", remit_id="R", payment_date=paid or date(month.year, month.month, 25),
                          coverage_month=month, member_id=BEN, region="North", category="Adult",
                          amount=D(amount), line_type=line_type)


def test_partial_state_settlement_leaves_60_outstanding():
    conn, dc = _setup_closed_jan_to_apr()
    adj = dc.propose_retro(V3_DAY, "rate_version", "v3")
    adj_lines = repo.adjustment_lines(conn, adj)
    lines = [_line(i, m, "400.00") for i, m in enumerate((JAN, FEB, MAR, APR))]
    lines.append(_line(9, JAN, "20.00", "retro_adjustment", paid=date(2026, 6, 25)))   # state pays $20 of $80
    s = settlement(adj_lines, {(BEN, m): D("420.00") for m in (JAN, FEB, MAR, APR)}, lines)
    assert sum((x.owed_by_adjustments for x in s), D("0")) == D("80.00")
    assert sum((x.state_retro_paid for x in s), D("0")) == D("20.00")
    assert sum((x.remaining_vs_state for x in s), D("0")) == D("60.00")


# ---------------------------------------------------------------- bank
def _remit(rid, total, day):
    return Remit(remit_id=rid, payment_date=day, total_amount=D(total), line_count=1)


def _dep(did, amount, day, ref=""):
    return BankDeposit(deposit_id=did, deposit_date=day, amount=D(amount), payer="STATE", reference=ref)


def test_ambiguous_amount_match_is_never_auto_linked():
    res = match_bank([_remit("R1", "1000.00", date(2026, 3, 25))],
                     [_dep("D1", "1000.00", date(2026, 3, 26)), _dep("D2", "1000.00", date(2026, 3, 27))], {})
    o = res.outcomes[0]
    assert o.deposit_id is None and o.flags == ["UNMATCHED"] and o.candidates == ["D1", "D2"]
    assert res.new_links == []


def test_unique_reference_links_and_deposit_is_used_once():
    res = match_bank([_remit("R1", "1000.00", date(2026, 3, 25)), _remit("R2", "1000.00", date(2026, 3, 25))],
                     [_dep("D1", "1000.00", date(2026, 3, 26), ref="R1")], {})
    assert res.new_links == [("D1", "R1")]
    assert res.outcomes[1].deposit_id is None


def test_late_and_short_keeps_both_flags():
    res = match_bank([_remit("R1", "1000.00", date(2026, 3, 25))],
                     [_dep("D1", "980.00", date(2026, 4, 2), ref="R1")], {})
    assert res.outcomes[0].flags == ["BANK_TIMING", "BANK_SHORT"]
    assert res.outcomes[0].delta == D("-20.00")


# ---------------------------------------------------------------- rules
BASE = {"has_expected": True, "has_remit": True, "regular_line_count": 1, "duplicate_signature": False,
        "has_negative_line": False, "negative_line_types": [], "negative_refs_found": False, "cell_match": True,
        "paid_matches_current": True, "paid_matches_older_version": None, "rate_approved_after_payment": False}


def test_single_strong_rule_needs_no_llm():
    hits = rules.classify({**BASE, "has_remit": False, "regular_line_count": 0, "cell_match": False,
                           "paid_matches_current": False})
    answer, needs_llm = rules.route(hits)
    assert (answer.code, answer.strength, needs_llm) == (DiscrepancyCode.MISSING_FROM_REMIT, RuleStrength.STRONG, False)


def test_two_causes_go_to_investigator():
    hits = rules.classify({**BASE, "regular_line_count": 2, "duplicate_signature": True, "paid_matches_current": False,
                           "paid_matches_older_version": "v1"})
    assert {h.code for h in hits} == {DiscrepancyCode.DUPLICATE_PAYMENT, DiscrepancyCode.STALE_RATE}
    assert rules.route(hits)[1] is True


def test_unknown_amount_is_weak_and_goes_to_investigator():
    hits = rules.classify({**BASE, "paid_matches_current": False})
    assert [(h.code, h.strength) for h in hits] == [(DiscrepancyCode.STALE_RATE, RuleStrength.WEAK)]
    assert rules.route(hits)[1] is True
