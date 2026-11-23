"""Every state change that touches rates or the books goes through here.

Each function changes state AND writes its audit event in ONE transaction:
no audit event -> no state change.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime
from decimal import Decimal

from engine import journal
from engine.retro import RetroLine
from governance.audit import append_event
from schemas.enums import ActorType, AdjustmentStatus, PeriodStatus, RateVersionStatus, VersionKind
from schemas.ledger import CloseSnapshot, ExpectedRevenue
from storage import repo
from storage.db import transaction


class ApprovalError(Exception):
    pass


# ---------------------------------------------------------------- rate versions
def approve_rate_version(conn, version_id: str, approver: str, at: datetime | None = None) -> None:
    at = at or datetime.now()
    with transaction(conn):
        row = conn.execute("SELECT status, kind FROM rate_versions WHERE version_id = ?", [version_id]).fetchone()
        if row is None:
            raise ApprovalError(f"unknown rate version {version_id}")
        if row[0] != RateVersionStatus.DRAFT.value:
            raise ApprovalError(f"{version_id} is {row[0]}, only drafts can be approved")
        doc = conn.execute("SELECT d.extraction_status FROM rate_versions v JOIN source_documents d "
                           "ON d.document_id = v.source_document_id WHERE v.version_id = ?", [version_id]).fetchone()
        if doc and doc[0] == "needs_fix":
            raise ApprovalError(f"{version_id} has validation errors; fix the draft before approving")
        conn.execute("UPDATE rate_versions SET status=?, approved_by=?, approved_at=? WHERE version_id=?",
                     [RateVersionStatus.APPROVED.value, approver, at, version_id])
        if row[1] == VersionKind.FULL.value:
            # Display label only: an older full table now has a newer one. History lookups ignore status.
            conn.execute("UPDATE rate_versions SET status=? WHERE status=? AND kind=? AND version_id<>? AND approved_at<?",
                         [RateVersionStatus.SUPERSEDED.value, RateVersionStatus.APPROVED.value,
                          VersionKind.FULL.value, version_id, at])
        append_event(conn, actor_type=ActorType.HUMAN, actor_id=approver, action="rate_version.approved",
                     entity="rate_version", entity_id=version_id, ts=at)


def reject_rate_version(conn, version_id: str, reviewer: str, reason: str) -> None:
    with transaction(conn):
        conn.execute("UPDATE rate_versions SET status=?, notes=? WHERE version_id=? AND status=?",
                     [RateVersionStatus.REJECTED.value, reason, version_id, RateVersionStatus.DRAFT.value])
        append_event(conn, actor_type=ActorType.HUMAN, actor_id=reviewer, action="rate_version.rejected",
                     entity="rate_version", entity_id=version_id, payload={"reason": reason})


# ---------------------------------------------------------------- period close
def close_period(conn, month: date, expected: list[ExpectedRevenue], actor: str, at: datetime) -> Decimal:
    """Freeze what is booked for the month and post the close entry."""
    with transaction(conn):
        status = conn.execute("SELECT status FROM periods WHERE coverage_month=?", [month]).fetchone()
        if status and status[0] == PeriodStatus.CLOSED.value:
            raise ApprovalError(f"{month:%Y-%m} is already closed")
        snaps = [CloseSnapshot(coverage_month=e.coverage_month, member_id=e.member_id, region=e.region,
                               category=e.category, span_id=e.span_id, rate_version_id=e.rate_version_id,
                               pmpm=e.pmpm, snapshot_at=at) for e in expected if e.coverage_month == month]
        repo.insert_snapshots(conn, snaps)
        total = repo.sum_decimal(s.pmpm for s in snaps)
        conn.execute("DELETE FROM periods WHERE coverage_month=?", [month])
        conn.execute("INSERT INTO periods VALUES (?,?,?,?)", [month, PeriodStatus.CLOSED.value, at, actor])
        _post(conn, journal.close_entry(month, total), source="close", adjustment_id=None, approver=actor, at=at)
        append_event(conn, actor_type=ActorType.HUMAN, actor_id=actor, action="period.closed", entity="period",
                     entity_id=f"{month:%Y-%m}", payload={"member_months": len(snaps), "booked_total": total}, ts=at)
    return total


# ---------------------------------------------------------------- adjustments
def propose_adjustment(conn, *, trigger: str, trigger_ref: str, lines: list[RetroLine], proposer: str,
                       at: datetime, memo: str = "") -> str | None:
    lines = [l for l in lines if l.delta != 0]
    if not lines:
        return None
    # Deterministic ID (same trigger at the same time -> same ID) so offline replay reproduces memo prompts.
    adj_id = "ADJ-" + hashlib.sha256(f"{trigger}|{trigger_ref}|{at.isoformat()}".encode()).hexdigest()[:8]
    total = repo.sum_decimal(l.delta for l in lines)
    with transaction(conn):
        conn.execute("INSERT INTO adjustments VALUES (?,?,?,?,?,?,?,?,?)",
                     [adj_id, trigger, trigger_ref, AdjustmentStatus.PROPOSED.value, total, proposer, memo, None, at])
        conn.executemany("INSERT INTO adjustment_lines VALUES (?,?,?,?,?,?,?,?,?,?)",
                         [[adj_id, l.member_id, l.coverage_month, l.region.value, l.category.value,
                           l.previously_booked, l.new_expected, l.old_version_id, l.new_version_id, l.delta]
                          for l in lines])
        append_event(conn, actor_type=ActorType.SYSTEM, actor_id=proposer, action="adjustment.proposed",
                     entity="adjustment", entity_id=adj_id,
                     payload={"trigger": trigger, "trigger_ref": trigger_ref, "lines": len(lines), "total": total}, ts=at)
    return adj_id


def approve_adjustment(conn, adjustment_id: str, approver: str, at: datetime | None = None) -> bool:
    """Approve AND post in one transaction. Returns False if already posted (idempotent retry)."""
    at = at or datetime.now()
    adj = repo.adjustment(conn, adjustment_id)
    if adj is None:
        raise ApprovalError(f"unknown adjustment {adjustment_id}")
    if adj["status"] == AdjustmentStatus.POSTED.value:
        return False
    if adj["status"] != AdjustmentStatus.PROPOSED.value:
        raise ApprovalError(f"{adjustment_id} is {adj['status']}")
    if approver == adj["proposed_by"]:
        raise ApprovalError("maker-checker: the proposer cannot approve their own adjustment")
    stale = _baseline_mismatches(conn, adjustment_id)
    if stale:
        # The proposal assumed an earlier adjustment would post (it was pending when this was computed).
        # Posting now would book the wrong amount, so mark it stale; the next retro run regenerates it.
        with transaction(conn):
            conn.execute("UPDATE adjustments SET status=? WHERE adjustment_id=?", [AdjustmentStatus.STALE.value, adjustment_id])
            append_event(conn, actor_type=ActorType.SYSTEM, actor_id="approvals", action="adjustment.stale",
                         entity="adjustment", entity_id=adjustment_id, ts=at,
                         payload={"member_months_mismatched": stale,
                                  "reason": "baseline differs from what is actually posted"})
        raise ApprovalError(f"{adjustment_id} is stale: it was computed on top of an adjustment that is not posted "
                            f"({stale} member-month(s) differ). Approve or reject earlier adjustments first, then "
                            "re-run the retro engine.")
    lines = repo.adjustment_lines(conn, adjustment_id)
    entries = journal.adjustment_entry(adjustment_id, [(l.coverage_month, l.delta) for l in lines])
    assert journal.is_balanced(entries)
    with transaction(conn):
        _post(conn, entries, source="prior_period_adjustment", adjustment_id=adjustment_id, approver=approver, at=at)
        conn.execute("UPDATE adjustments SET status=? WHERE adjustment_id=?", [AdjustmentStatus.POSTED.value, adjustment_id])
        append_event(conn, actor_type=ActorType.HUMAN, actor_id=approver, action="adjustment.approved_and_posted",
                     entity="adjustment", entity_id=adjustment_id,
                     payload={"total": adj["total"], "ledger_lines": len(entries)}, ts=at)
    return True


def reject_adjustment(conn, adjustment_id: str, reviewer: str, reason: str) -> None:
    with transaction(conn):
        conn.execute("UPDATE adjustments SET status=? WHERE adjustment_id=? AND status=?",
                     [AdjustmentStatus.REJECTED.value, adjustment_id, AdjustmentStatus.PROPOSED.value])
        append_event(conn, actor_type=ActorType.HUMAN, actor_id=reviewer, action="adjustment.rejected",
                     entity="adjustment", entity_id=adjustment_id, payload={"reason": reason})
        # Later pending proposals computed on top of this one are now built on a false baseline.
        dependents = [r[0] for r in conn.execute("""
            SELECT DISTINCT a.adjustment_id FROM adjustments a JOIN adjustment_lines l USING (adjustment_id)
            WHERE a.status = ? AND a.created_at >= (SELECT created_at FROM adjustments WHERE adjustment_id = ?)
              AND a.adjustment_id <> ?
              AND (l.member_id, l.coverage_month) IN (SELECT member_id, coverage_month FROM adjustment_lines
                                                      WHERE adjustment_id = ?)""",
            [AdjustmentStatus.PROPOSED.value, adjustment_id, adjustment_id, adjustment_id]).fetchall()]
        for dep in dependents:
            conn.execute("UPDATE adjustments SET status=? WHERE adjustment_id=?", [AdjustmentStatus.STALE.value, dep])
            append_event(conn, actor_type=ActorType.SYSTEM, actor_id="approvals", action="adjustment.stale",
                         entity="adjustment", entity_id=dep, payload={"reason": f"depended on rejected {adjustment_id}"})


def _baseline_mismatches(conn, adjustment_id: str) -> int:
    """Lines whose previously_booked != close snapshot + adjustments actually POSTED for that member-month."""
    return conn.execute("""
        SELECT count(*) FROM adjustment_lines l
        LEFT JOIN close_snapshots s ON s.member_id = l.member_id AND s.coverage_month = l.coverage_month
        WHERE l.adjustment_id = ?
          AND l.previously_booked <> coalesce(s.pmpm, 0) + coalesce((
                SELECT sum(l2.delta) FROM adjustment_lines l2 JOIN adjustments a2 USING (adjustment_id)
                WHERE a2.status = ? AND l2.member_id = l.member_id AND l2.coverage_month = l.coverage_month), 0)""",
        [adjustment_id, AdjustmentStatus.POSTED.value]).fetchone()[0]


# ---------------------------------------------------------------- variance review (feeds the knowledge store)
def decide_variance(conn, variance_id: str, *, decision: str, final_category: str, reviewer: str, notes: str,
                    at: datetime | None = None) -> None:
    """Accept the proposed cause or override it. Stored decisions are reused as context for
    similar future variances (agents.tools.get_prior_explanations)."""
    if decision not in ("accept", "override"):
        raise ApprovalError("decision must be accept or override")
    at = at or datetime.now()
    status = "confirmed" if decision == "accept" else "overridden"
    with transaction(conn):
        row = conn.execute("SELECT group_id FROM variances WHERE variance_id=?", [variance_id]).fetchone()
        if row is None:
            raise ApprovalError(f"unknown variance {variance_id}")
        conn.execute("UPDATE variances SET status=?, confirmed_cause=? WHERE variance_id=?",
                     [status, final_category, variance_id])
        conn.execute("INSERT INTO variance_decisions VALUES (?,?,?,?,?,?,?,?)",
                     [uuid.uuid4().hex, variance_id, row[0], decision, final_category, reviewer, notes, at])
        append_event(conn, actor_type=ActorType.HUMAN, actor_id=reviewer, action=f"variance.{status}",
                     entity="variance", entity_id=variance_id,
                     payload={"final_category": final_category, "notes": notes}, ts=at)


def decide_group(conn, run_id: str, group_id: str, **kw) -> int:
    ids = [r[0] for r in conn.execute("SELECT variance_id FROM variances WHERE run_id=? AND group_id=?",
                                      [run_id, group_id]).fetchall()]
    for vid in ids:
        decide_variance(conn, vid, **kw)
    return len(ids)


def log_identity_reveal(conn, member_id: str, viewer: str, reason: str) -> None:
    """Revealing a member's name in the UI is an audited event (the name itself is never logged)."""
    append_event(conn, actor_type=ActorType.HUMAN, actor_id=viewer, action="phi.identity_revealed",
                 entity="member", entity_id=member_id, payload={"reason": reason})


# ---------------------------------------------------------------- bank links (human confirmation of a candidate)
def confirm_bank_link(conn, deposit_id: str, remit_id: str, reviewer: str, at: datetime | None = None) -> None:
    at = at or datetime.now()
    with transaction(conn):
        if conn.execute("SELECT 1 FROM bank_links WHERE deposit_id=?", [deposit_id]).fetchone():
            raise ApprovalError(f"deposit {deposit_id} is already linked")
        if conn.execute("SELECT 1 FROM bank_links WHERE remit_id=?", [remit_id]).fetchone():
            raise ApprovalError(f"remit {remit_id} is already linked to a deposit (matching is one-to-one)")
        conn.execute("INSERT INTO bank_links VALUES (?,?,?,?)", [deposit_id, remit_id, reviewer, at])
        append_event(conn, actor_type=ActorType.HUMAN, actor_id=reviewer, action="bank_link.confirmed",
                     entity="bank_deposit", entity_id=deposit_id, payload={"remit_id": remit_id}, ts=at)


def record_system_links(conn, links: list[tuple[str, str]], at: datetime) -> None:
    with transaction(conn):
        for deposit_id, remit_id in links:
            conn.execute("INSERT INTO bank_links VALUES (?,?,?,?)", [deposit_id, remit_id, "system", at])
            append_event(conn, actor_type=ActorType.SYSTEM, actor_id="bank_match", action="bank_link.auto",
                         entity="bank_deposit", entity_id=deposit_id, payload={"remit_id": remit_id}, ts=at)


def _post(conn, entries: list[journal.JournalLine], *, source: str, adjustment_id: str | None, approver: str,
          at: datetime) -> None:
    conn.executemany(
        "INSERT INTO ledger_entries VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [[uuid.uuid4().hex, e.period, e.account, e.debit, e.credit, e.memo, source, adjustment_id, e.posting_key,
          approver, at] for e in entries])
