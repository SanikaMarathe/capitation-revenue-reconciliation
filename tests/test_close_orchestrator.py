"""Monthly close orchestrator + demo world: gates, restartability, knowledge store, audited reveal."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from agents import tools
from agents.mock_responders import mock_provider
from common.config import get_settings
from data_gen.generate import generate
from governance import approvals
from governance.audit import verify_chain
from llm.factory import build_client
from storage.db import connect
from workflow import demo
from workflow.close import STEPS, CloseBlocked, CloseOrchestrator

MAY = date(2026, 5, 1)


@pytest.fixture(scope="module")
def world():
    generate()
    conn = connect()
    orch = CloseOrchestrator(conn, build_client(get_settings(), conn=conn, provider=mock_provider(), cache_mode="off"))
    demo.reset(conn, orch, "v3 email just arrived (Jan-Apr closed)")
    return conn, orch


def test_v3_scenario_state(world):
    conn, _ = world
    assert [r[0] for r in conn.execute("SELECT coverage_month FROM periods WHERE status='closed' ORDER BY 1").fetchall()] \
        == [date(2026, m, 1) for m in (1, 2, 3, 4)]
    assert conn.execute("SELECT status FROM rate_versions WHERE version_id='v3'").fetchone()[0] == "draft"


def test_sign_off_requires_a_run_and_no_open_gates(world):
    conn, orch = world
    at = datetime(2026, 6, 10, 17)
    with pytest.raises(CloseBlocked, match="run the close"):
        orch.sign_off(MAY, at, demo.CONTROLLER)
    orch.run_close(MAY, at, demo.ANALYST)
    with pytest.raises(CloseBlocked, match="rate version"):          # v3 still a draft
        orch.sign_off(MAY, at, demo.CONTROLLER)


def test_approving_v3_proposes_retro_with_grounded_memo(world):
    conn, orch = world
    adj = orch.approve_rate("v3", demo.CONTROLLER, datetime(2026, 5, 22, 9))
    row = conn.execute("SELECT total, memo_grounded FROM adjustments WHERE adjustment_id=?", [adj]).fetchone()
    assert row[0] > 0 and row[1] is True
    with pytest.raises(CloseBlocked, match="adjustment"):            # adjustment still pending
        orch.sign_off(MAY, datetime(2026, 6, 10, 17), demo.CONTROLLER)
    approvals.approve_adjustment(conn, adj, demo.CONTROLLER, datetime(2026, 5, 22, 10))


def test_rerun_resumes_and_sign_off_closes(world):
    conn, orch = world
    # The May run's retro check also caught late enrollment (spans received May 15): approve every pending item.
    for (adj,) in conn.execute("SELECT adjustment_id FROM adjustments WHERE status='proposed'").fetchall():
        approvals.approve_adjustment(conn, adj, demo.CONTROLLER, datetime(2026, 6, 10, 17, 30))
    again = orch.run_close(MAY, datetime(2026, 6, 10, 17), demo.ANALYST)
    assert again.steps_run == []                                      # all steps already done -> nothing re-run
    # The May close was reviewed BEFORE v3 was approved, and v3 changes May's North Adult rate:
    # sign-off must refuse to book a result nobody reviewed, and require a fresh run.
    with pytest.raises(CloseBlocked, match="changed since"):
        orch.sign_off(MAY, datetime(2026, 6, 10, 18), demo.CONTROLLER)
    rerun = orch.run_close(MAY, datetime(2026, 6, 10, 18), demo.ANALYST)
    assert rerun.steps_run == STEPS
    orch.sign_off(MAY, datetime(2026, 6, 10, 18), demo.CONTROLLER)
    assert conn.execute("SELECT status FROM periods WHERE coverage_month=?", [MAY]).fetchone()[0] == "closed"
    events = {r[0] for r in conn.execute("SELECT action FROM audit_log").fetchall()}
    assert {f"close.{s}" for s in STEPS} <= events and "period.closed" in events


def test_override_feeds_the_knowledge_store(world):
    conn, _ = world
    vid, rule, cell = conn.execute("""SELECT variance_id, possible_cause_rule, json_extract_string(features,'$.expected_cell')
                                      FROM variances WHERE run_id='close-2026-05' AND possible_cause_rule IS NOT NULL
                                      LIMIT 1""").fetchone()
    approvals.decide_variance(conn, vid, decision="override", final_category="RATE_CELL_MISMATCH",
                              reviewer=demo.ANALYST, notes="state confirmed the region change")
    prior = tools.get_prior_explanations(conn, rule, cell)
    assert prior and prior[0]["decision"] == "override" and prior[0]["final_category"] == "RATE_CELL_MISMATCH"


def test_identity_reveal_is_audited_without_the_name(world):
    conn, _ = world
    approvals.log_identity_reveal(conn, "M000001", demo.CONTROLLER, "state inquiry")
    first, last = conn.execute("SELECT first_name, last_name FROM members WHERE member_id='M000001'").fetchone()
    payload = conn.execute("SELECT payload FROM audit_log WHERE action='phi.identity_revealed'").fetchone()[0]
    assert first not in payload and last not in payload


def test_audit_chain_intact_after_whole_demo(world):
    conn, _ = world
    assert verify_chain(conn) == (True, None)
