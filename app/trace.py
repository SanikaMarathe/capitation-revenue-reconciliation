"""Turns what the system recorded into step-by-step 'thinking' traces, one per job.

Nothing is scripted: every line comes from an audit-log event, the rate/adjustment tables, or the
LLM call log (model, time, cache) linked by the call's cache key.

A job starts at a trigger (a document read, an approval, a close, a decision). Follow-on events at
the same moment (e.g. the retro engine running after a rate approval) belong to that job.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field

from plain import cause, money

from common.config import get_settings
from storage import repo

CODE, AI, PERSON, PRIVACY, WAIT = "⚙️", "🤖", "👤", "🔒", "⏸️"
TRIGGERS = {"rate_version.drafted", "rate_version.approved", "rate_version.rejected", "adjustment.approved_and_posted",
            "adjustment.rejected", "bank_link.confirmed", "period.closed", "variance.confirmed", "variance.overridden",
            "phi.identity_revealed"}


@dataclass
class Job:
    title: str
    ts: object
    lines: list[tuple[str, str]] = field(default_factory=list)
    ai_calls: list[str] = field(default_factory=list)      # cache keys of AI calls made in this job
    rate_version: str = ""
    adjustment: str = ""


def _p(e) -> dict:
    return json.loads(e["payload"] or "{}")


def _call(conn, key: str | None) -> dict | None:
    if not key:
        return None
    rows = repo._rows(conn, "SELECT * FROM llm_calls WHERE prompt_hash=? ORDER BY ts DESC LIMIT 1", [key])
    return rows[0] if rows else None


def _model(name: str) -> str:
    mock = name.startswith("mock:")
    base = name.replace("mock:", "").replace("claude-", "Claude ").replace("-5-5", " 5.5").title().replace("Claude", "Claude")
    return f"{base} (mock stand-in)" if mock else base


def _ai_line(conn, key: str | None, what: str) -> str:
    c = _call(conn, key)
    if not c:
        return f"Asked the AI to {what}"
    timing = "answer reused from cache" if c["cache_hit"] else f"answered in {c['latency_ms'] / 1000:.1f}s"
    return f"Asked **{_model(c['model'])}** to {what} ({timing})"


# ---------------------------------------------------------------- per-event lines
def _drafted(conn, e, job: Job) -> None:
    p = _p(e)
    job.title = f"Reading the new rate document: {p.get('document')}"
    job.rate_version = e["entity_id"]
    job.lines.append((CODE, f"Opened **{p.get('document')}** and turned it into text, keeping a reference for every line"))
    job.lines.append((PRIVACY, "Sent the AI only the document text: no member data is ever part of this job"))
    job.lines.append((AI, _ai_line(conn, p.get("prompt_hash"), "pull out every rate, the months it applies to, "
                                                                "and where in the document it found it")))
    rows = [r for r in repo.rate_rows(conn) if r.version_id == e["entity_id"]]
    found = ", ".join(f"{r.region.value} {r.category.value} {money(r.pmpm)}" for r in rows[:3])
    more = f" and {len(rows) - 3} more" if len(rows) > 3 else ""
    job.lines.append((AI, f"The AI returned **{p.get('cells')} rates**: {found}{more}"))
    pct = get_settings().raw.get("thresholds", {}).get("rate_change_flag_pct", 15)
    problems = len(p.get("errors", [])) + len(p.get("flags", []))
    job.lines.append((CODE, "Checked every rate with code: the number really appears where the AI says, the member "
                            f"group exists, the amount is positive, and it moved less than {pct}% → "
                            + ("**all checks passed**" if not problems else f"**{problems} problem(s) flagged**")))
    job.ai_calls.append(p.get("prompt_hash"))


def _rate_approved(conn, e, job: Job) -> None:
    job.title = f"Approving the new rates ({e['entity_id']})"
    job.lines.append((PERSON, f"**{e['actor_id']}** approved the rates. Only now can they be used for pricing"))


def _adjustment_proposed(conn, e, job: Job) -> None:
    p = _p(e)
    job.adjustment = e["entity_id"]
    months = sorted({l.coverage_month for l in repo.adjustment_lines(conn, e["entity_id"])})
    span = f"{months[0]:%B}–{months[-1]:%B}" if len(months) > 1 else f"{months[0]:%B}" if months else ""
    job.lines.append((CODE, f"Re-calculated the months already closed ({span}) with everything known now"))
    job.lines.append((CODE, f"Compared with what was booked: **{p.get('lines')}** member-months change, "
                            f"**{money(p.get('total'))}** in total. Code does this maths, never the AI"))


def _memo(conn, e, job: Job) -> None:
    p = _p(e)
    job.lines.append((AI, _ai_line(conn, p.get("prompt_hash"), "write a short explanation of the correction")))
    job.lines.append((CODE, "Filled in every number in the memo from the calculation and checked the AI typed none "
                            "itself → " + ("**passed**" if p.get("grounded") else "**failed, a person must rewrite it**")))
    if p.get("prompt_hash"):
        job.ai_calls.append(p["prompt_hash"])


def close_step_lines(conn, step: str, stats: dict) -> list[tuple[str, str]]:
    """Lines for one close step. Also used live while a close is running."""
    s = stats
    if step == "retro_check":
        return [(CODE, "Checked the closed months for anything learned since they were closed → "
                       + ("**found a correction**" if s.get("adjustment_proposed") else "nothing new"))]
    if step == "price":
        out = [(CODE, f"Worked out what each of **{s.get('member_months'):,}** members should earn this month, using "
                      f"their enrollment and the approved rates → **{money(s.get('expected_total'))}**")]
        if s.get("exceptions"):
            out.append((WAIT, f"{len(s['exceptions'])} member(s) couldn't be priced; a person must fix the data"))
        return out
    if step == "reconcile_members":
        unsure = s.get("to_investigator", 0)
        return [(CODE, f"Compared every member with the state's payment → **{s.get('variances')}** members differ"),
                (CODE, "Ran the rules on each difference → " + ("a clear reason for **all of them**" if not unsure else
                       f"a clear reason for all but **{unsure}** (mixed causes)"))]
    if step == "reconcile_bank":
        flags = s.get("flags", {})
        mine = {k: v for k, v in flags.items() if k.endswith(s.get("_month", ""))} if s.get("_month") else flags
        if not mine:
            txt = "the full amount arrived" if s.get("auto_linked") else "nothing new to match"
        else:
            txt = "; ".join(", ".join(cause(x) for x in v) for v in mine.values())
        return [(CODE, f"Matched the state's payment to the bank by its reference number → {txt}")]
    if step == "investigate":
        if s.get("skipped"):
            return [(CODE, "AI step skipped")]
        calls = s.get("calls") or []
        out = [(CODE, f"Grouped similar differences → **{s.get('groups')} groups**, so {s.get('groups')} AI calls "
                      "instead of one per member")]
        out.append((PRIVACY, "Before each call: replaced member IDs with tokens, sent only the fields needed, and "
                             "scanned for names and dates of birth → nothing personal sent"))
        models = Counter()
        cached = 0
        for c in calls:
            row = _call(conn, c.get("prompt_hash"))
            if row:
                models[_model(row["model"])] += 1
                cached += bool(row["cache_hit"])
        used = ", ".join(f"{n}× {m}" for m, n in models.items()) or "the AI"
        out.append((AI, f"Asked {used} to explain each group and suggest a next step"
                        + (f" ({cached} answers reused from cache)" if cached else "")))
        failed = s.get("groups", 0) - s.get("grounded", 0)
        out.append((CODE, "Fact-checked every explanation: the records it cites exist and every dollar amount "
                          f"matches the calculation → **{s.get('grounded')} passed**"
                          + (f", {failed} sent to a person" if failed else "")))
        return out
    if step == "ready_for_review":
        return []
    return []


# ---------------------------------------------------------------- jobs
def build_jobs(conn) -> list[Job]:
    events = repo._rows(conn, "SELECT * FROM audit_log ORDER BY seq")
    jobs: list[Job] = []
    for e in events:
        a = e["action"]
        same_moment = bool(jobs) and e["ts"] == jobs[-1].ts
        new = not jobs or a in TRIGGERS or not same_moment
        if a.startswith("close."):
            # A close continues its own job; the retro check at its start may already have opened it.
            new = not (same_moment and (jobs[-1].title.startswith("Closing")
                                        or jobs[-1].title == "Checking closed months for late changes"))
        if a in ("variance.confirmed", "variance.overridden") and same_moment and jobs[-1].title.startswith("Reviewing"):
            new = False
        if new:
            jobs.append(Job(title="", ts=e["ts"]))
        job = jobs[-1]
        if a == "rate_version.drafted":
            _drafted(conn, e, job)
        elif a == "rate_version.approved":
            _rate_approved(conn, e, job)
        elif a == "rate_version.rejected":
            job.title = f"Rejecting rates {e['entity_id']}"
            job.lines.append((PERSON, f"**{e['actor_id']}** rejected the rates: {_p(e).get('reason')}"))
        elif a == "adjustment.proposed":
            job.title = job.title or "Checking closed months for late changes"
            _adjustment_proposed(conn, e, job)
        elif a == "adjustment.memo_drafted":
            _memo(conn, e, job)
        elif a == "adjustment.approved_and_posted":
            job.title = "Approving the correction to past months"
            job.lines.append((PERSON, f"**{e['actor_id']}** approved the correction of {money(_p(e).get('total'))}"))
            job.lines.append((CODE, f"Posted it to the ledger in one step ({_p(e).get('ledger_lines')} entries, "
                                    "each tagged to the month it corrects)"))
        elif a == "adjustment.rejected":
            job.title = "Rejecting a correction"
            job.lines.append((PERSON, f"**{e['actor_id']}** rejected it: {_p(e).get('reason')}"))
        elif a == "bank_link.confirmed":
            job.title = "Matching a bank deposit"
            job.lines.append((PERSON, f"**{e['actor_id']}** confirmed deposit {e['entity_id']} is the state's "
                                      f"payment {_p(e).get('remit_id')}"))
        elif a == "bank_link.auto":
            pass                                     # covered by the close's bank step
        elif a.startswith("close."):
            step = a.split(".", 1)[1]
            month = e["entity_id"].replace("close-", "")
            if not job.title.startswith("Closing"):
                job.title = f"Closing {_month_name(month)}"
            stats = dict(_p(e), _month=month)
            if step == "retro_check" and job.adjustment:
                # The correction it found was logged before this step's own event; show the check first.
                job.lines[:0] = close_step_lines(conn, step, stats)
            else:
                job.lines += close_step_lines(conn, step, stats)
            if step == "investigate":
                job.ai_calls += [c["prompt_hash"] for c in (stats.get("calls") or [])[:1] if c.get("prompt_hash")]
        elif a == "period.closed":
            job.title = f"Signing off {_month_name(e['entity_id'])}"
            job.lines.append((PERSON, f"**{e['actor_id']}** signed off and booked {money(_p(e).get('booked_total'))}. "
                                      "The month is now locked; later changes need an approved correction"))
        elif a in ("variance.confirmed", "variance.overridden"):
            if not job.title:
                job.title = "Reviewing the AI's explanations"
                job.lines.append((PERSON, ""))
            n = int(job.lines[-1][1].split()[0]) + 1 if job.lines[-1][1] else 1
            verb = "agreed with" if a == "variance.confirmed" else "changed"
            job.lines[-1] = (PERSON, f"{n} member(s): **{e['actor_id']}** {verb} the reason "
                                     f"*{cause(_p(e).get('final_category'))}*")
        elif a == "phi.identity_revealed":
            job.title = "Revealing a member's name"
            job.lines.append((PERSON, f"**{e['actor_id']}** revealed a name (reason logged, name not logged)"))
    _add_waiting(conn, jobs)
    return [j for j in jobs if j.title]


def _month_name(ym: str) -> str:
    from datetime import date
    y, m = ym.split("-")
    return f"{date(int(y), int(m), 1):%B %Y}"


def _add_waiting(conn, jobs: list[Job]) -> None:
    """Mark what is still waiting on a person, so a trace ends the way an agent's would: 'over to you'."""
    drafts = {r[0] for r in conn.execute("SELECT version_id FROM rate_versions WHERE status='draft'").fetchall()}
    pending = {r[0] for r in conn.execute("SELECT adjustment_id FROM adjustments WHERE status='proposed'").fetchall()}
    for j in jobs:
        if j.rate_version in drafts:
            j.lines.append((WAIT, "Waiting for a person to approve or reject these rates"))
        if j.adjustment in pending:
            j.lines.append((WAIT, "Waiting for a person to approve the correction"))
    open_runs = conn.execute("SELECT r.coverage_month FROM close_runs r LEFT JOIN periods p USING (coverage_month) "
                             "WHERE r.last_completed_step='ready_for_review' AND coalesce(p.status,'open') <> 'closed'"
                             ).fetchall()
    for (m,) in open_runs:
        j = next((j for j in reversed(jobs) if j.title == f"Closing {m:%B %Y}"), None)
        if j:
            j.lines.append((WAIT, "Done. Waiting for a person to review the findings and sign off"))
