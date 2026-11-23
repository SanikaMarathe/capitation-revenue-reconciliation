"""Monthly Close Orchestrator.

    run_close(month)  : retro check -> price -> reconcile (member) -> reconcile (bank) -> investigate
                        -> ready for review. Each step is logged to the audit log and recorded in
                        close_runs, so a failed run resumes from the next step (steps are idempotent).
    sign_off(month)   : the human gate. Refuses while a rate draft or adjustment is undecided, then
                        freezes the snapshot, posts the close entry and closes the period.

Plain sequential Python: a 6-step process doesn't need a workflow framework
(production maps to Durable Functions).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from agents import investigator, memo_writer, rate_extractor
from engine.retro import settlement, summarise
from governance import approvals
from governance.audit import append_event
from llm.client import LLMClient
from schemas.enums import ActorType, AdjustmentStatus, VarianceStatus
from storage import repo
from storage.db import transaction
from workflow.deterministic_close import DeterministicClose

STEPS = ["retro_check", "price", "reconcile_members", "reconcile_bank", "investigate", "ready_for_review"]


class CloseBlocked(Exception):
    pass


@dataclass
class CloseRunResult:
    month: date
    run_id: str
    steps_run: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def run_id_for(month: date) -> str:
    return f"close-{month:%Y-%m}"


def pricing_fingerprint(res) -> str:
    """Hash of exactly what would be booked (and what couldn't be priced)."""
    rows = sorted((e.member_id, str(e.coverage_month), e.region.value, e.category.value, e.span_id,
                   e.rate_version_id, str(e.pmpm)) for e in res.expected)
    issues = sorted((i.member_id, i.code.value) for i in res.issues)
    return hashlib.sha256(json.dumps([rows, issues]).encode("utf-8")).hexdigest()


class CloseOrchestrator:
    def __init__(self, conn, client: LLMClient | None) -> None:
        self.conn = conn
        self.client = client
        self.dc = DeterministicClose(conn)

    # ---------------------------------------------------------------- documents
    def ingest_document(self, path, *, received: date, version_id: str, at: datetime):
        return rate_extractor.extract(self.conn, self.client, path, received=received, version_id=version_id, as_of=at)

    def approve_rate(self, version_id: str, approver: str, at: datetime) -> str | None:
        """Approval of a rate version triggers the retro engine over closed months."""
        approvals.approve_rate_version(self.conn, version_id, approver, at)
        return self.propose_retro(at, "rate_version", version_id)

    # ---------------------------------------------------------------- retro
    def propose_retro(self, at: datetime, trigger: str, trigger_ref: str) -> str | None:
        adj = self.dc.propose_retro(at, trigger, trigger_ref)
        if adj and self.client is not None:
            self.write_memo(adj, at)
        return adj

    def write_memo(self, adjustment_id: str, at: datetime) -> None:
        adj = repo.adjustment(self.conn, adjustment_id)
        lines = repo.adjustment_lines(self.conn, adjustment_id)
        months = sorted({l.coverage_month for l in lines})
        current = {(e.member_id, e.coverage_month): e.pmpm for m in months for e in self.dc.price(m, at).expected}
        remits = [l for l in repo.remittance_lines(self.conn) if l.payment_date <= at.date()]
        remaining = sum((s.remaining_vs_state for s in settlement(lines, current, remits)), Decimal("0.00"))
        memo = memo_writer.write_memo(self.client, adjustment_id=adjustment_id, trigger=adj["trigger"],
                                      trigger_ref=adj["trigger_ref"], summary=summarise(lines),
                                      remaining_vs_state=remaining,
                                      late_enrollment_member_months=sum(l.old_version_id == "none" for l in lines))
        self.conn.execute("UPDATE adjustments SET memo=?, memo_grounded=? WHERE adjustment_id=?",
                          [memo.title + "\n\n" + memo.body, memo.grounded, adjustment_id])
        append_event(self.conn, actor_type=ActorType.AGENT, actor_id="memo_writer", action="adjustment.memo_drafted",
                     entity="adjustment", entity_id=adjustment_id, ts=at,
                     payload={"grounded": memo.grounded, "problems": memo.problems, "cache_hit": memo.cache_hit,
                              "prompt_hash": memo.prompt_hash})

    # ---------------------------------------------------------------- close
    def run_close(self, month: date, at: datetime, actor: str, investigate: bool = True,
                  on_step=None) -> CloseRunResult:
        """on_step(step, stats): optional callback after each step, so a UI can show progress live."""
        rid = run_id_for(month)
        row = self.conn.execute("SELECT last_completed_step FROM close_runs WHERE run_id=?", [rid]).fetchone()
        if row is None:
            self.conn.execute("INSERT INTO close_runs (run_id, coverage_month, as_of, last_completed_step, started_at, "
                              "finished_at) VALUES (?,?,?,?,?,?)", [rid, month, at, None, at, None])
            done = -1
        else:
            done = STEPS.index(row[0]) if row[0] in STEPS else -1
        result = CloseRunResult(month, rid)
        for i, step in enumerate(STEPS):
            if i <= done:
                continue
            stats = getattr(self, f"_step_{step}")(month, at, rid, investigate)
            result.steps_run.append(step)
            result.stats[step] = stats
            with transaction(self.conn):
                self.conn.execute("UPDATE close_runs SET last_completed_step=? WHERE run_id=?", [step, rid])
                append_event(self.conn, actor_type=ActorType.SYSTEM, actor_id=actor, action=f"close.{step}",
                             entity="close_run", entity_id=rid, payload=stats, ts=at)
            if on_step is not None:
                on_step(step, stats)
        return result

    def _step_retro_check(self, month, at, rid, _):
        adj = self.propose_retro(at, "retro_enrollment", f"before close {month:%Y-%m}")
        return {"adjustment_proposed": adj}

    def _step_price(self, month, at, rid, _):
        res = self.dc.price(month, at)
        fp = pricing_fingerprint(res)
        self.conn.execute("UPDATE close_runs SET priced_fingerprint=? WHERE run_id=?", [fp, rid])
        return {"member_months": len(res.expected), "expected_total": sum((e.pmpm for e in res.expected), Decimal("0.00")),
                "exceptions": [f"{i.member_id}:{i.code.value}" for i in res.issues], "fingerprint": fp[:16]}

    def _step_reconcile_members(self, month, at, rid, _):
        self.conn.execute("DELETE FROM variances WHERE run_id=?", [rid])
        variances = self.dc.reconcile(month, at, rid, save=True)
        return {"variances": len(variances),
                "to_investigator": sum(v.status == VarianceStatus.NEEDS_HUMAN for v in variances)}

    def _step_reconcile_bank(self, month, at, rid, _):
        res = self.dc.reconcile_bank(at)
        return {"auto_linked": len(res.new_links),
                "flags": {o.remit_id: o.flags for o in res.outcomes if o.flags},
                "unmatched_deposits": res.unmatched_deposits}

    def _step_investigate(self, month, at, rid, investigate):
        if not investigate or self.client is None:
            return {"skipped": True}
        variances = [v for v in self.dc.reconcile(month, at, rid)]
        updated, outcomes = investigator.investigate(self.conn, self.client, variances)
        self.conn.execute("DELETE FROM variances WHERE run_id=?", [rid])
        repo.insert_variances(self.conn, updated)
        return {"groups": len(outcomes), "grounded": sum(o.grounded for o in outcomes),
                "complex": sum(o.task == "investigate_complex" for o in outcomes),
                # One entry per AI call, so every explanation traces to its exact (pseudonymised) request.
                "calls": [{"group_id": o.group.group_id, "task": o.task, "prompt_hash": o.prompt_hash,
                           "cache_hit": o.cache_hit, "grounded": o.grounded, "problems": o.problems,
                           "error": o.error} for o in outcomes]}

    def _step_ready_for_review(self, month, at, rid, _):
        return {"open_items": self.blockers(month, at)}

    # ---------------------------------------------------------------- sign-off gate
    def blockers(self, month: date, at: datetime | None = None) -> list[str]:
        out = []
        if at is not None:
            issues = self.dc.price(month, at).issues
            if issues:
                out.append(f"{len(issues)} member-month(s) can't be priced ("
                           + ", ".join(f"{i.member_id} {i.code.value}" for i in issues[:3])
                           + "); fix the rate or enrollment data first")
        drafts = self.conn.execute("SELECT version_id FROM rate_versions WHERE status='draft'").fetchall()
        if drafts:
            out.append(f"rate version(s) awaiting approval: {', '.join(d[0] for d in drafts)}")
        pending = self.conn.execute("SELECT adjustment_id FROM adjustments WHERE status=?",
                                    [AdjustmentStatus.PROPOSED.value]).fetchall()
        if pending:
            out.append(f"adjustment(s) awaiting approval: {', '.join(p[0] for p in pending)}")
        return out

    def sign_off(self, month: date, at: datetime, approver: str):
        rid = run_id_for(month)
        row = self.conn.execute("SELECT last_completed_step FROM close_runs WHERE run_id=?", [rid]).fetchone()
        if row is None or row[0] != STEPS[-1]:
            raise CloseBlocked("run the close first")
        blocking = self.blockers(month, at)
        if blocking:
            raise CloseBlocked("; ".join(blocking))
        # Book exactly what was reviewed: if pricing inputs changed since the close ran, review is void.
        stored = self.conn.execute("SELECT priced_fingerprint FROM close_runs WHERE run_id=?", [rid]).fetchone()[0]
        if stored != pricing_fingerprint(self.dc.price(month, at)):
            with transaction(self.conn):
                self.conn.execute("UPDATE close_runs SET last_completed_step=NULL, priced_fingerprint=NULL "
                                  "WHERE run_id=?", [rid])
                append_event(self.conn, actor_type=ActorType.SYSTEM, actor_id=approver, action="close.invalidated",
                             entity="close_run", entity_id=rid, ts=at,
                             payload={"reason": "pricing inputs changed since review"})
            raise CloseBlocked("rates or enrollment changed since this close was reviewed; run the close again")
        total = self.dc.close_month(month, at, approver)
        self.conn.execute("UPDATE close_runs SET finished_at=? WHERE run_id=?", [at, rid])
        return total
