"""The engine, run over the full synthetic half-year, must agree with the generator's ground truth
to the cent. The engine sees only raw files; the generator's truth came from a separate path."""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal

import pytest

from data_gen import spec
from data_gen.generate import GT, RAW, generate
from engine.retro import settlement
from governance.audit import verify_chain
from storage import repo
from storage.db import connect
from storage.load_raw import load_raw
from workflow import simulate
from workflow.deterministic_close import DeterministicClose

END = datetime(2026, 7, 31, 23, 0)
PAYMENT_CODES = {"MISSING_FROM_REMIT", "RATE_CELL_MISMATCH", "STALE_RATE", "UNEXPECTED_PAYMENT",
                 "DUPLICATE_PAYMENT", "RECOUPMENT"}


def _csv(path):
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def world():
    generate()
    conn = connect()
    load_raw(conn, RAW)
    simulate.insert_ground_truth_rates(conn, GT)
    simulate.run(conn)
    truth = {(r["member_id"], date.fromisoformat(r["coverage_month"])): r for r in _csv(GT / "member_months.csv")}
    dc = DeterministicClose(conn)
    final_variances = {}
    for m in spec.MONTHS:
        for v in dc.reconcile(m, END, run_id="final"):
            final_variances[(v.member_id, m)] = v
    return conn, truth, final_variances


def test_booked_at_close_matches_truth(world):
    conn, truth, _ = world
    snaps = {(s.member_id, s.coverage_month): s.pmpm for s in repo.snapshots(conn)}
    for key, t in truth.items():
        assert snaps.get(key, Decimal("0.00")) == Decimal(t["booked_at_close"]), key


def test_final_expected_matches_truth(world):
    conn, truth, _ = world
    dc = DeterministicClose(conn)
    priced = {(e.member_id, e.coverage_month): e.pmpm for m in spec.MONTHS for e in dc.price(m, END).expected}
    for key, t in truth.items():
        assert priced.get(key, Decimal("0.00")) == Decimal(t["final_expected"]), key


def test_snapshot_plus_adjustments_equals_final(world):
    """Retro deltas are exact at member-month level (incremental across all runs)."""
    conn, truth, _ = world
    snaps = {(s.member_id, s.coverage_month): s.pmpm for s in repo.snapshots(conn)}
    adj = defaultdict(lambda: Decimal("0.00"))
    for line in repo.active_adjustment_lines(conn):
        adj[(line.member_id, line.coverage_month)] += line.delta
    for key, t in truth.items():
        assert snaps.get(key, Decimal("0.00")) + adj[key] == Decimal(t["final_expected"]), key
    gt_total = sum(Decimal(r["retro_delta"]) for r in _csv(GT / "retro_deltas.csv"))
    assert sum(adj.values(), Decimal("0.00")) == gt_total


def test_final_variance_matches_truth_to_the_cent(world):
    _, truth, variances = world
    for key, t in truth.items():
        delta = variances[key].delta if key in variances else Decimal("0.00")
        assert delta == Decimal(t["final_variance"]), key


def test_detection_recall_is_100_percent_with_no_false_positives(world):
    _, truth, variances = world
    seeded = {k for k, t in truth.items() if set(t["codes"].split(";")) & PAYMENT_CODES}
    missed = seeded - set(variances)
    assert not missed, f"missed {len(missed)}: {sorted(missed)[:5]}"
    false_pos = {k for k in variances if not truth.get(k, {}).get("codes")}
    assert not false_pos, f"false positives: {sorted(false_pos)[:5]}"


def test_rule_classifier_on_single_cause_cases(world):
    """Rules alone, on the cases they claim (one strong rule). Full eval comes in Phase 4."""
    _, truth, variances = world
    right = wrong = 0
    confusion = Counter()
    for key, v in variances.items():
        codes = set(truth[key]["codes"].split(";"))
        if v.possible_cause_rule is None or v.status.value != "possible_cause":
            continue
        ok = v.possible_cause_rule.value in codes
        right += ok
        wrong += not ok
        if not ok:
            confusion[(v.possible_cause_rule.value, truth[key]["codes"])] += 1
    accuracy = right / (right + wrong)
    print(f"\nrules-only accuracy on rule-claimed cases: {accuracy:.1%} ({right}/{right + wrong}); errors: {dict(confusion)}")
    assert accuracy >= 0.95


def test_bank_reconciliation_matches_truth(world):
    conn, _, _ = world
    res = DeterministicClose(conn).reconcile_bank(END, record_links=False)
    flags = {o.remit_id: o.flags for o in res.outcomes}
    assert "BANK_TIMING" in flags["R-2026-02"] and "BANK_TIMING" in flags["R-2026-05"]
    assert flags["R-2026-03"] == ["BANK_SHORT"]
    april = next(o for o in res.outcomes if o.remit_id == "R-2026-04")
    assert april.deposit_id is None and april.candidates == ["D0004"]     # no reference -> human decides
    assert "D9001" in res.unmatched_deposits
    short = next(o for o in res.outcomes if o.remit_id == "R-2026-03")
    assert short.delta == Decimal("-1250.00")


def test_april_north_adult_retro_outstanding(world):
    conn, truth, _ = world
    dc = DeterministicClose(conn)
    current = {(e.member_id, e.coverage_month): e.pmpm for m in spec.MONTHS for e in dc.price(m, END).expected}
    s = settlement(repo.active_adjustment_lines(conn), current, repo.remittance_lines(conn))
    april_na = [x for x in s if x.coverage_month == date(2026, 4, 1)
                and truth[(x.member_id, x.coverage_month)]["region"] == "North"
                and truth[(x.member_id, x.coverage_month)]["category"] == "Adult"
                and truth[(x.member_id, x.coverage_month)]["codes"] == "RETRO_RATE_CHANGE"]
    assert april_na and all(x.remaining_vs_state == Decimal("20.00") for x in april_na)


def test_audit_chain_intact_and_ledger_balanced(world):
    conn, _, _ = world
    assert verify_chain(conn) == (True, None)
    dr, cr = conn.execute("SELECT sum(debit), sum(credit) FROM ledger_entries").fetchone()
    assert dr == cr
