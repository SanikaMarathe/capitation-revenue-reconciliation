"""Tab 1: what a finance analyst sees, laid out like an inbox.

Left: your to-do list (click one). Middle: that one item: what happened, the amount, what approving does.
Right: the month at a glance (expected → paid by the state → reached the bank) and why they differ.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

import streamlit as st
from lib import get_conn, llm_causes, orchestrator, primary_cause, role
from plain import cause, money

import summary
from trace import close_step_lines

from engine.pricing import RateBook
from engine.rate_validation import diff
from governance import approvals
from llm.provider import LLMError
from schemas.enums import Category, DiscrepancyCode, Region
from storage import repo
from workflow import demo
from workflow.close import STEPS, CloseBlocked, run_id_for
from workflow.deterministic_close import DeterministicClose


@dataclass
class Task:
    key: str
    label: str
    show: Callable[[], None]
    plain: str = ""          # the same item as one plain-English sentence, for the summary panel


def render(now) -> None:
    conn = get_conn()
    if st.session_state.pop("done", None):
        st.toast(st.session_state.pop("done_msg", "Done.") + " See Behind the scenes for every step.", icon="✅")
    months = [r[0] for r in conn.execute("SELECT coverage_month FROM close_runs ORDER BY 1").fetchall()]
    if months and st.session_state.get("glance") not in months:
        st.session_state["glance"] = months[-1]

    _month_strip(conn, now, months)
    tasks = _tasks(conn, now, months)
    skipped = st.session_state.setdefault("skipped", set())
    tasks = [t for t in tasks if t.key not in skipped] + [t for t in tasks if t.key in skipped]
    todo, main, side = st.columns([1, 2, 1.3], gap="medium")
    with todo:
        with st.container(key="list-todo"):
            st.markdown(f"<div class='panel-title'>Your to-do · {len(tasks)}</div>", unsafe_allow_html=True)
            keys = [t.key for t in tasks]
            if st.session_state.get("task") not in keys:
                st.session_state["task"] = keys[0] if keys else None
            for t in tasks:
                label = t.label + ("  ·  left open" if t.key in skipped else "")
                if st.button(label, key=f"pick-{t.key}", use_container_width=True,
                             type="primary" if t.key == st.session_state["task"] else "secondary"):
                    st.session_state["task"] = t.key
                    st.rerun()
            if not tasks:
                st.caption("Nothing to do right now.")
    with main:
        current = next((t for t in tasks if t.key == st.session_state.get("task")), None)
        if current:
            current.show()
        else:
            with st.container(key="card-idle"):
                _head("All caught up", "Nothing waiting on you",
                      "New documents appear here when they land in the state inbox. To work on a later month, move the "
                      "<b>business date</b> forward at the top.")
    with side:
        summary.render(conn, now, tasks)
        if months:
            _glance(conn, now, months)


# ---------------------------------------------------------------- to-do list
def _has_rates(conn) -> bool:
    return bool(conn.execute("SELECT 1 FROM rate_versions WHERE status IN ('approved','superseded') LIMIT 1").fetchone())


def _doc_kind(path) -> str:
    return {".pdf": "PDF", ".xlsx": "Excel", ".txt": "email", ".eml": "email"}.get(path.suffix.lower(), "file")


def _signed(d) -> str:
    return ("+" if d >= 0 else "") + money(d)


def _tasks(conn, now, months) -> list[Task]:
    """In the order a finance team works: rates, corrections, the close, cash, then open questions."""
    has_rates = _has_rates(conn)
    drafts, docs, adjs, close, bank, review = [], [], [], [], [], []
    for v in [v for v in repo.rate_versions(conn) if v.status.value == "draft"]:
        start = min((r.effective_from for r in repo.rate_rows(conn) if r.version_id == v.version_id), default=None)
        drafts.append(Task(f"rates-{v.version_id}", "💲  Review rate update" + (f" · from {start:%b %Y}" if start else ""),
                           lambda v=v: _rates_card(conn, now, v),
                           "Approve or reject the rates the AI read from the state's document."))
    for path, read in demo.inbox(conn):
        if not read:
            docs.append(Task(f"doc-{path.name}", f"📄  Review new rate document ({_doc_kind(path)})",
                             lambda p=path: _doc_card(conn, now, p)))
    for adj in repo._rows(conn, "SELECT * FROM adjustments WHERE status='proposed' ORDER BY created_at"):
        ms = sorted({l.coverage_month for l in repo.adjustment_lines(conn, adj["adjustment_id"])})
        span = f"{ms[0]:%b}–{ms[-1]:%b}" if len(ms) > 1 else f"{ms[0]:%b}"
        adjs.append(Task(f"adj-{adj['adjustment_id']}", f"↩️  Review correction to {span} · {_signed(adj['total'])}",
                         lambda a=adj: _correction_card(conn, now, a),
                         f"Approve the correction of <b>{money(abs(adj['total']))}</b> to {span} (months already closed)."))
    m = demo.closable_month(conn, now)
    if m:
        run = conn.execute("SELECT last_completed_step FROM close_runs WHERE run_id=?", [run_id_for(m)]).fetchone()
        ready = bool(run and run[0] == STEPS[-1])
        label = f"✅  Sign off {m:%B}" if ready else f"📅  Run the {m:%B} close" + ("" if has_rates else " · needs rates")
        plain = (f"Sign off {m:%B} to lock its numbers." if ready else
                 f"Run the {m:%B} close: the system checks every member's payment." if has_rates else
                 f"Approve a rate document so {m:%B} can be closed.")
        close.append(Task(f"close-{m:%Y-%m}-{ready}", label, lambda m=m, ready=ready: _close_card(conn, now, m, ready),
                          plain))
    remits = {r.remit_id: r for r in repo.remits(conn)}
    for o in [o for o in DeterministicClose(conn).reconcile_bank(now, record_links=False).outcomes if o.candidates]:
        bank.append(Task(f"bank-{o.remit_id}", f"🏦  Review {remits[o.remit_id].payment_date:%B} bank deposit",
                         lambda o=o: _bank_card(conn, now, o),
                         f"Confirm the {remits[o.remit_id].payment_date:%B} bank deposit: the amount matches the state's "
                         "payment, but it has no reference number."))
    if months:
        mm = months[-1]
        n = _open_groups(conn, run_id_for(mm))
        if n:
            review.append(Task(f"review-{mm:%Y-%m}", f"🔎  Check AI explanations · {mm:%B} ({len(n)})",
                               lambda mm=mm: _review_card(conn, now, mm),
                               f"Optional: check the AI's reasons for {len(n)} groups of differences in {mm:%B}."))
    # Without approved rates nothing can be calculated, so the documents come first.
    if not has_rates:
        return drafts + docs + adjs + close + bank + review
    return drafts + adjs + close + bank + docs + review


def _month_strip(conn, now, months_run) -> None:
    """Where each month stands, the way a finance team tracks its close calendar."""
    all_months = [r[0] for r in conn.execute("SELECT DISTINCT coverage_month FROM remittance_lines ORDER BY 1").fetchall()]
    closed = {r[0] for r in conn.execute("SELECT coverage_month FROM periods WHERE status='closed'").fetchall()}
    correcting = {r[0] for r in conn.execute("SELECT DISTINCT l.coverage_month FROM adjustment_lines l JOIN adjustments a "
                                             "USING (adjustment_id) WHERE a.status='proposed'").fetchall()}
    ready = {r[0] for r in conn.execute("SELECT coverage_month FROM close_runs WHERE last_completed_step=?",
                                        [STEPS[-1]]).fetchall()}
    nxt = demo.closable_month(conn, now)
    has_rates = _has_rates(conn)
    cols = st.columns(len(all_months))
    for col, m in zip(cols, all_months):
        if m in closed:
            status = "↩️ Correction pending" if m in correcting else "✅ Closed"
        elif m in ready:
            status = "📝 Needs sign-off"
        elif m == nxt:
            status = "🟡 Ready to close" if has_rates else "⏳ Needs rates"
        elif nxt and m > nxt and (m.replace(day=28) + timedelta(days=4)).replace(day=1) <= now.date():
            status = f"Waiting for {nxt:%B}"      # the period is over, but months close in order
        else:
            status = "Not due yet"
        with col:
            act = (m not in closed and (m == nxt or m in ready)) or m in correcting
            with st.container(key=f"month-{m:%Y%m}" + ("-act" if act else "")):
                picked = m == st.session_state.get("glance")
                if st.button(f"**{m:%b %Y}**  \n{status}", key=f"m-{m:%Y%m}", use_container_width=True,
                             type="primary" if picked else "secondary", disabled=m not in months_run,
                             help=None if m in months_run else "Run this month's close to see its numbers."):
                    st.session_state["glance"] = m
                    st.rerun()


# ---------------------------------------------------------------- helpers
def _done(msg: str) -> None:
    st.session_state["done"] = True
    st.session_state["done_msg"] = msg
    st.session_state.pop("task", None)
    st.rerun()


def _try(fn, msg: str) -> None:
    try:
        fn()
    except (approvals.ApprovalError, CloseBlocked) as exc:
        st.error(str(exc))
        return
    _done(msg)


def _head(chip: str, title: str, text: str = "") -> None:
    st.markdown(f"<span class='chip'>{chip}</span><div class='card-title'>{title}</div>"
                + (f"<div class='muted'>{text}</div>" if text else ""), unsafe_allow_html=True)


def _rows_table(pairs: list[tuple[str, str]]) -> None:
    body = "".join(f"<tr><td>{html.escape(a)}</td><td class='r'>{html.escape(b)}</td></tr>" for a, b in pairs)
    st.markdown(f"<table class='rows'>{body}</table>", unsafe_allow_html=True)


def _leave_open(key: str) -> None:
    st.session_state.setdefault("skipped", set()).add(key)
    st.session_state.pop("task", None)
    st.rerun()


def _buttons(approve_label: str, key: str):
    a, b, _ = st.columns([1.3, 1, 1.6])
    return (a.button(approve_label, key=f"ap-{key}", type="primary", use_container_width=True),
            b.button("Reject", key=f"rj-{key}", use_container_width=True))


# ---------------------------------------------------------------- item cards (middle panel)
def _doc_card(conn, now, path) -> None:
    kind = {".pdf": "PDF notice", ".xlsx": "Excel rate sheet", ".txt": "email", ".eml": "email"}.get(path.suffix.lower(),
                                                                                                "document")
    with st.container(key=f"card-doc-{path.stem}"):
        _head(f"State inbox · new rate document ({_doc_kind(path)})", "Read this rate document?",
              f"<i>{html.escape(path.name)}</i> · a {kind} from the state is waiting in the inbox. The AI will read "
              "it and pull out the rates. Nothing changes until a person approves them.")
        if st.button("Let the AI read it", key=f"read-{path.name}", type="primary"):
            try:
                with st.spinner("The AI is reading the document…"):
                    orchestrator().ingest_document(path, received=now.date(), version_id=demo.next_version_id(conn),
                                                   at=now)
            except LLMError as exc:
                # Outage, invalid output after one retry, or spend cap. Nothing is saved before the AI answers,
                # so the document stays in the inbox and the books are untouched.
                st.error(f"The AI couldn't read this document ({type(exc).__name__}). Nothing was changed; "
                         "the document is still in the inbox, so try again in a moment.")
                return
            except Exception as exc:  # e.g. a wrong API key: shown plainly, never hidden
                st.error(f"The AI call failed: {type(exc).__name__}: {exc}. Check the API key in .env and the "
                         "provider in config.yaml. Nothing was changed.")
                return
            _done("The AI has read the document.")


def _period(start, end) -> str:
    return f"{start:%B %Y} – {end:%B %Y}" if end else f"from {start:%B %Y}"


def _short(start, end) -> str:
    return f"{start:%b}–{end:%b %Y}" if end else f"from {start:%b %Y}"


def _rates_card(conn, now, v) -> None:
    """What am I approving? Starting rates, or changes to rates already in use, and what stays the same."""
    rows = [r for r in repo.rate_rows(conn) if r.version_id == v.version_id]
    others = [x for x in repo.rate_versions(conn) if x.version_id != v.version_id]
    book = RateBook([r for r in repo.rate_rows(conn) if r.version_id != v.version_id], others)
    cells = diff(rows, book, now)
    doc = conn.execute("SELECT filename FROM source_documents WHERE document_id=?", [v.source_document_id]).fetchone()
    starting = all(c.old_pmpm is None for c in cells)
    changed = [c for c in cells if c.changed and c.old_pmpm is not None]
    same = [c for c in cells if not c.changed]
    periods = {(c.effective_from, c.effective_to) for c in cells}
    shared = len(periods) == 1
    first = min(c.effective_from for c in cells)
    # Cells the document doesn't mention keep the rate already in force (an amendment lists only what changes).
    mentioned = {(c.region, c.category) for c in cells}
    untouched = []
    for region in Region:
        for category in Category:
            if (region.value, category.value) not in mentioned:
                kr = book.lookup(region.value, category.value, first, now)
                if kr:
                    untouched.append((region.value, category.value, kr.row.pmpm))
    past = any(c.effective_from < date(now.year, now.month, 1) for c in cells)

    if starting:
        title = f"Review {len(cells)} starting rates"
        sub_txt = f"Effective {first:%B %Y}" if shared else "Effective dates vary by rate"
        lead = "There are no approved rates yet. These become the first official monthly rates"
    elif changed:
        title = f"Review {len(changed)} rate change{'s' if len(changed) != 1 else ''}"
        sub_txt = (f"Effective {_period(*periods.pop())}" if shared else "Effective dates shown per rate")
        lead = (f"{len(changed)} change · {len(same) + len(untouched)} stay the same" if len(changed) == 1 else
                f"{len(changed)} change · {len(same) + len(untouched)} stay the same")
    else:
        title = "No rate changes"
        sub_txt = f"Effective {first:%B %Y}"
        lead = "Every rate in this document matches the rate already in use"

    with st.container(key=f"card-rates-{v.version_id}"):
        st.markdown(f"<span class='chip'>Needs your decision · rate update</span><div class='card-title'>{title}</div>"
                    f"<div class='eff'>{sub_txt}</div><div class='muted'>{lead}. Read by the AI from "
                    f"<i>{html.escape(doc[0] if doc else 'the document')}</i>; approving makes them official.</div>",
                    unsafe_allow_html=True)
        when = "" if shared else "<th>For</th>"
        if starting:
            head = f"<tr><th>Region</th><th>Coverage category</th>{when}<th class='r'>Monthly rate per member</th></tr>"
            body = "".join(f"<tr><td>{c.region}</td><td>{c.category}</td>"
                           + ("" if shared else f"<td>{_short(c.effective_from, c.effective_to)}</td>")
                           + f"<td class='r'><b>{money(c.new_pmpm)}</b></td></tr>" for c in cells)
        else:
            head = (f"<tr><th>Region</th><th>Coverage category</th>{when}<th class='r'>Current rate</th>"
                    "<th class='r'>New rate</th><th class='r'>Change</th></tr>")
            def line(c, kind):
                pct = f"{c.change_pct:+.1f}%" if c.change_pct is not None else ""
                period = "" if shared else f"<td>{_short(c.effective_from, c.effective_to)}</td>"
                if kind == "chg":
                    return (f"<tr class='chg'><td>{c.region}</td><td>{c.category}</td>{period}"
                            f"<td class='r'>{money(c.old_pmpm)}</td><td class='r'><b>{money(c.new_pmpm)}</b></td>"
                            f"<td class='r'><span class='delta'>{pct}</span></td></tr>")
                return (f"<tr class='same'><td>{c.region}</td><td>{c.category}</td>{period}"
                        f"<td class='r'>{money(c.old_pmpm)}</td><td class='r'>{money(c.new_pmpm)}</td>"
                        f"<td class='r'>no change</td></tr>")
            body = "".join(line(c, "chg") for c in changed) + "".join(line(c, "same") for c in same)
            body += "".join(f"<tr class='same'><td>{r}</td><td>{cat}</td>" + ("" if shared else "<td>·</td>")
                            + f"<td class='r'>{money(pm)}</td><td class='r'>{money(pm)}</td>"
                            "<td class='r'>not in document · no change</td></tr>" for r, cat, pm in untouched)
        st.markdown(f"<div class='ratebox'><table class='rates'>{head}{body}</table></div>", unsafe_allow_html=True)
        if past and not starting:
            st.markdown("<div class='hl'>⚠️ Some of these go back to months already closed, so approving will also "
                        "propose a correction to those months.</div>", unsafe_allow_html=True)
        ok, no = _buttons("Approve rates", v.version_id)
        if ok:
            _try(lambda: orchestrator().approve_rate(v.version_id, role(), now), "Rates approved.")
        if no:
            _try(lambda: approvals.reject_rate_version(conn, v.version_id, role(), "rejected in review"),
                 "Rates rejected.")


def _correction_card(conn, now, adj) -> None:
    by_month: dict = {}
    for l in repo.adjustment_lines(conn, adj["adjustment_id"]):
        by_month[l.coverage_month] = by_month.get(l.coverage_month, 0) + l.delta
    months = sorted(by_month)
    way = "low" if adj["total"] >= 0 else "high"
    title = (f"{months[0]:%B}–{months[-1]:%B} were booked too {way}" if len(months) > 1
             else f"{months[0]:%B} was booked too {way}")
    with st.container(key=f"card-adj-{adj['adjustment_id']}"):
        _head("Needs your decision · prior-period adjustment", title,
              "Those months were booked with the rates known at the time. With what we know now, we should have "
              "booked more. Approving adds the difference to the books.")
        sign = "+" if adj["total"] >= 0 else ""
        st.markdown(f"<div class='hl'><div class='big pos'>{sign}{money(adj['total'])}</div></div>", unsafe_allow_html=True)
        _rows_table([(f"{m:%B %Y}", money(d)) for m, d in sorted(by_month.items())])
        if adj["memo"]:
            with st.expander("AI explanation (every number in it comes from the calculation, not the AI)"):
                st.write(adj["memo"].partition("\n\n")[2])
        ok, no = _buttons("Approve correction", adj["adjustment_id"])
        if ok:
            _try(lambda: approvals.approve_adjustment(conn, adj["adjustment_id"], role(), now),
                 "Correction approved and posted.")
        if no:
            _try(lambda: approvals.reject_adjustment(conn, adj["adjustment_id"], role(), "rejected in review"),
                 "Correction rejected.")


def _bank_card(conn, now, o) -> None:
    deposits = {d.deposit_id: d for d in repo.deposits(conn)}
    r = next(x for x in repo.remits(conn) if x.remit_id == o.remit_id)
    d = deposits[o.candidates[0]]
    window = DeterministicClose(conn).window
    days = (d.deposit_date - r.payment_date).days
    key = f"bank-{o.remit_id}"
    with st.container(key=f"card-bank-{o.remit_id}"):
        _head("Needs your decision · bank reconciliation", f"Is this deposit the state's {r.payment_date:%B} payment?",
              "The system found a likely match but won't confirm it on its own, because one piece of evidence is "
              "missing. Compare the two records.")
        a, b = st.columns(2)
        a.markdown(_record("State payment", "remittance", [("Amount", money(r.total_amount)),
                                                         ("Paid on", f"{r.payment_date:%d %b %Y}"),
                                                         ("Payment reference", r.remit_id)]), unsafe_allow_html=True)
        b.markdown(_record("Bank deposit", "cash received", [("Amount", money(d.amount)),
                                                           ("Arrived on", f"{d.deposit_date:%d %b %Y}"),
                                                           ("Payment reference", d.reference or "none"),
                                                           ("From", d.payer), ("Bank ID", d.deposit_id)]),
                   unsafe_allow_html=True)
        same = d.amount == r.total_amount
        checks = [(same, "Amounts match" if same else f"Amounts differ by {money(abs(d.amount - r.total_amount))}"),
                  (0 <= days <= window, f"Arrived {days} day{'' if days == 1 else 's'} after the payment (within {window} days)"),
                  (d.reference == r.remit_id,
                   "Payment reference matches" if d.reference == r.remit_id else "The bank deposit has no payment reference")]
        st.markdown("".join(f"<div class='check {'ok' if ok else 'no'}'>{'✓' if ok else '✗'} {html.escape(t)}</div>"
                            for ok, t in checks), unsafe_allow_html=True)
        x, y, _ = st.columns([1.1, 1.3, 1.4])
        if x.button("Confirm match", key=f"bk-{o.remit_id}", type="primary", use_container_width=True):
            _try(lambda: approvals.confirm_bank_link(conn, d.deposit_id, o.remit_id, role(), now), "Deposit matched.")
        if y.button("Leave open for now", key=f"lo-{o.remit_id}", use_container_width=True):
            _leave_open(key)
        st.caption("Leaving it open changes nothing: it stays on your to-do list and the month shows the cash as not "
                   "matched yet.")


def _record(title: str, term: str, fields: list) -> str:
    rows = "".join(f"<div class='rf'><span>{html.escape(k)}</span><b>{html.escape(v)}</b></div>" for k, v in fields)
    return f"<div class='rec'><div class='rt'>{title} <span>· {term}</span></div>{rows}</div>"


def _close_card(conn, now, month, ready) -> None:
    rid = run_id_for(month)
    with st.container(key=f"card-close-{rid}"):
        if not ready and not _has_rates(conn):
            _head("Month-end close", f"{month:%B} can't be closed yet",
                  "Approve a rate document first. Without approved rates there is nothing to calculate expected "
                  "revenue from. Rate documents are in your to-do list.")
            st.button(f"Run the {month:%B} close", type="primary", key=f"run-{rid}", disabled=True)
            return
        if not ready:
            _head("Month-end close", f"Time to close {month:%B}",
                  f"The system checks every member's {month:%B} payment against what we expected, checks the bank, "
                  "and explains every difference. You'll see each step as it happens.")
            if st.button(f"Run the {month:%B} close", type="primary", key=f"run-{rid}"):
                _run_close_live(conn, month, now)
            return
        blockers = orchestrator().blockers(month, now)
        _head("Needs your decision · sign-off", f"{month:%B} is checked. Sign it off?",
              f"Signing off locks {month:%B}'s numbers; any later change needs an approved correction. You can check "
              "the AI's explanations first (in your to-do list).")
        for b in blockers:
            st.markdown(f"<div class='hl'>⚠️ Can't sign off yet: {html.escape(b)}</div>", unsafe_allow_html=True)
        if st.button(f"Sign off {month:%B}", type="primary", key=f"so-{rid}", disabled=bool(blockers)):
            _try(lambda: orchestrator().sign_off(month, now, role()), f"{month:%B} is signed off and locked.")


def _run_close_live(conn, month, now) -> None:
    """Show each step as it finishes, the same lines the Behind the scenes trace uses."""
    with st.status(f"Closing {month:%B}…", expanded=True) as status:
        def on_step(step, stats):
            for icon, text in close_step_lines(conn, step, dict(stats, _month=f"{month:%Y-%m}")):
                st.markdown(f"{icon} {text}")
        orchestrator().run_close(month, now, role(), on_step=on_step)
        status.update(label=f"{month:%B} close finished", state="complete", expanded=True)
    st.session_state["glance"] = month
    st.session_state["done"] = True
    st.session_state["done_msg"] = f"The {month:%B} close has run."
    st.session_state.pop("task", None)
    st.button("See the results", type="primary")


def _open_groups(conn, rid) -> dict[str, list]:
    groups: dict[str, list] = {}
    for r in repo._rows(conn, "SELECT * FROM variances WHERE run_id=? AND status IN ('possible_cause','needs_human') "
                              "AND group_id IS NOT NULL", [rid]):
        r["cause"] = primary_cause(r)
        groups.setdefault(r["group_id"], []).append(r)
    return groups


def _review_card(conn, now, month) -> None:
    rid = run_id_for(month)
    groups = _open_groups(conn, rid)
    with st.container(key=f"card-review-{rid}"):
        _head(f"Check the AI's explanations · {month:%B}", f"{len(groups)} findings to review",
              "The AI suggests a reason for each group of differences. Agree, or pick the right reason. Your answer "
              "is saved and reused next time.")
        ordered = sorted(groups.items(), key=lambda kv: -abs(sum(r["delta"] for r in kv[1])))
        codes = [c.value for c in DiscrepancyCode]
        for gid, members in ordered[:4]:
            rep = members[0]
            st.markdown(f"**{cause(rep['cause'])}** · {len(members)} member(s) · "
                        f"{money(sum(r['delta'] for r in members))}")
            st.caption(f"🤖 {rep['explanation'] or 'Found by the rules.'}")
            proposed = (llm_causes(rep) or [rep["possible_cause_rule"]])[0]
            x, y, _ = st.columns([1, 1.6, 1])
            if x.button("Agree", key=f"ok-{rid}-{gid}", type="primary", use_container_width=True, disabled=not proposed):
                approvals.decide_group(conn, rid, gid, decision="accept", final_category=proposed,
                                       reviewer=role(), notes="", at=now)
                st.rerun()
            final = y.selectbox("Different reason", codes, format_func=cause, key=f"fc-{rid}-{gid}", index=None,
                                placeholder="No, the reason is…", label_visibility="collapsed")
            if final and final != proposed:
                approvals.decide_group(conn, rid, gid, decision="override", final_category=final,
                                       reviewer=role(), notes="", at=now)
                st.rerun()
        if len(ordered) > 4:
            st.caption(f"+ {len(ordered) - 4} smaller findings")


# ---------------------------------------------------------------- month at a glance (right panel)
def _glance(conn, now, months) -> None:
    month = st.session_state["glance"]
    rid = run_id_for(month)
    rows = repo._rows(conn, "SELECT * FROM variances WHERE run_id=?", [rid])
    # As they stood when the month was closed, so the reasons below add up to the gap.
    ran_at = conn.execute("SELECT as_of FROM close_runs WHERE run_id=?", [rid]).fetchone()[0]
    dc = DeterministicClose(conn)
    expected = sum((e.pmpm for e in dc.price(month, ran_at).expected), start=0)
    paid = sum((l.amount for l in repo.remittance_lines(conn, month) if l.payment_date <= ran_at.date()), start=0)
    gap = paid - expected
    bank, bank_cls, bank_sub = _bank_status(conn, dc, month, now)

    with st.container(key="card-glance"):
        t, s = st.columns([1.3, 1], vertical_alignment="center")
        t.markdown(f"<div class='card-title'>{month:%B} at a glance</div>", unsafe_allow_html=True)
        s.selectbox("Month", months, key="glance", format_func=lambda m: f"{m:%B %Y}", label_visibility="collapsed")
        gap_txt = "paid exactly right" if gap == 0 else f"{'underpaid' if gap < 0 else 'overpaid'} by {money(abs(gap))}"
        st.markdown(
            f"<div class='kpi'><div class='label'>We expected</div><div class='value'>{money(expected)}</div>"
            f"<div class='sub'>expected revenue · every member × their approved monthly rate</div></div>"
            f"<div class='kpi'><div class='label'>The state paid</div><div class='value'>{money(paid)}</div>"
            f"<div class='sub'>remittance · {gap_txt}</div></div>"
            f"<div class='kpi'><div class='label'>Reached our bank</div><div class='value {bank_cls}'>{bank}</div>"
            f"<div class='sub'>cash received · {bank_sub}</div></div>", unsafe_allow_html=True)
        if rows:
            under = -sum(x["delta"] for x in rows if x["delta"] < 0)
            over = sum(x["delta"] for x in rows if x["delta"] > 0)
            st.markdown(f"<div style='margin-top:0.8rem;font-weight:700'>Why the state "
                        f"{'underpaid' if gap < 0 else 'overpaid'} <span class='muted'>· variances</span></div>",
                        unsafe_allow_html=True)
            agg: dict = {}
            for x in rows:
                a = agg.setdefault(cause(primary_cause(x)), [0, 0])
                a[0] += 1
                a[1] += x["delta"]
            ordered = sorted(agg.items(), key=lambda kv: -abs(kv[1][1]))
            _rows_table([(f"{k} ({n})", f"{'underpaid' if v < 0 else 'overpaid'} {money(abs(v))}")
                         for k, (n, v) in ordered])
            st.markdown(f"<div class='hl'>The state owes us <b>{money(under)}</b> · we may owe back "
                        f"<b>{money(over)}</b> · net <b>{'underpaid' if gap < 0 else 'overpaid'} "
                        f"{money(abs(gap))}</b></div>", unsafe_allow_html=True)


def _bank_status(conn, dc, month, now) -> tuple[str, str, str]:
    batches = [r for r in repo.remits(conn) if (r.payment_date.year, r.payment_date.month) == (month.year, month.month)
               and r.payment_date <= now.date()]
    if not batches:
        return "No payment yet", "", "the state hasn't paid this month yet"
    outcomes = {o.remit_id: o for o in dc.reconcile_bank(now, record_links=False).outcomes}
    outs = [outcomes.get(r.remit_id) for r in batches]
    if any(o is None or o.deposit_id is None for o in outs):
        return "Not matched yet", "warn", "a deposit is waiting for a person to confirm it"
    sent = sum(r.total_amount for r in batches)
    short = sent - sum(o.deposit_amount for o in outs)
    if short <= 0:
        return "All arrived", "good", f"the state sent {money(sent)} and all of it reached the bank"
    return f"{money(short)} short", "warn", f"the state sent {money(sent)}; less reached the bank"
