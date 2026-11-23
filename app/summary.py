"""Plain-English summary for the right-hand panel: where things stand, what just happened, what needs you.

Written by code, not the AI: every sentence is built from what the system recorded (audit log, periods,
close runs, adjustments, variances), so it costs nothing and cannot invent a number.
"""

from __future__ import annotations

import html
import json
from datetime import date, timedelta

import streamlit as st
from plain import cause, money

from storage import repo
from workflow import demo
from workflow.close import STEPS, run_id_for

TRIGGERS = {"rate_version.drafted", "rate_version.approved", "rate_version.rejected", "adjustment.approved_and_posted",
            "adjustment.rejected", "bank_link.confirmed", "period.closed", "variance.confirmed", "variance.overridden"}


def render(conn, now, tasks) -> None:
    with st.container(key="card-summary"):
        st.markdown("<span class='chip'>Summary · in plain English</span>", unsafe_allow_html=True)
        _section("Where things stand", _where(conn, now))
        happened = _happened(conn)
        if happened:
            _section("What just happened", "<br>".join(happened))
        _section("What needs you", _needs(tasks))
        result = _latest_result(conn)
        if result:
            _section("Latest month", result)
        st.markdown("<div class='muted' style='font-size:0.8rem'>Written by code from the system's own records, so "
                    "every number matches the calculations.</div>", unsafe_allow_html=True)


def _section(title: str, body: str) -> None:
    st.markdown(f"<div class='sum'><div class='sum-t'>{title}</div><div class='sum-b'>{body}</div></div>",
                unsafe_allow_html=True)


def _month_after(m: date) -> date:
    return (m.replace(day=28) + timedelta(days=4)).replace(day=1)


def _list(names: list[str]) -> str:
    if len(names) <= 2:
        return " and ".join(names)
    return f"{names[0]} to {names[-1]}" if len(names) > 2 else ""


# ---------------------------------------------------------------- where things stand
def _where(conn, now) -> str:
    closed = sorted(r[0] for r in conn.execute("SELECT coverage_month FROM periods WHERE status='closed'").fetchall())
    has_rates = bool(conn.execute("SELECT 1 FROM rate_versions WHERE status IN ('approved','superseded')").fetchone())
    nxt = demo.closable_month(conn, now)
    ready = {r[0] for r in conn.execute("SELECT coverage_month FROM close_runs WHERE last_completed_step=?",
                                        [STEPS[-1]]).fetchall()}
    parts = []
    if not has_rates:
        parts.append("No rates are approved yet, so nothing can be calculated.")
    parts.append(f"{_list([f'{m:%B}' for m in closed])} {'is' if len(closed) == 1 else 'are'} closed."
                 if closed else "No month has been closed yet.")
    if nxt:
        if nxt in ready:
            parts.append(f"{nxt:%B} has been checked and is waiting for sign-off.")
        elif has_rates:
            parts.append(f"{nxt:%B} is ready to close.")
        else:
            parts.append(f"{nxt:%B} could be closed once rates are approved.")
    else:
        months = [r[0] for r in conn.execute("SELECT DISTINCT coverage_month FROM remittance_lines ORDER BY 1").fetchall()]
        upcoming = next((m for m in months if m not in closed), None)
        if upcoming:
            parts.append(f"{upcoming:%B} can be closed from {_month_after(upcoming):%d %B}, once the month is over "
                         "and the state has paid.")
        else:
            parts.append("Every month with data is closed.")
    return " ".join(parts)


# ---------------------------------------------------------------- what just happened
def _groups(conn, n: int = 2) -> list[list[dict]]:
    """The last n actions, each with the follow-on events it caused at the same moment."""
    events = repo._rows(conn, "SELECT * FROM audit_log ORDER BY seq")
    groups: list[list[dict]] = []
    for e in events:
        same = groups and e["ts"] == groups[-1][-1]["ts"]
        follow_on = e["action"] not in TRIGGERS and not e["action"].startswith("close.retro")
        joins_close = e["action"].startswith("close.") and groups and groups[-1][0]["action"].startswith(("close.", "adjustment.proposed"))
        if groups and same and (follow_on or joins_close):
            groups[-1].append(e)
        else:
            groups.append([e])
    # Repeated decisions on one group of members collapse into one action.
    merged: list[list[dict]] = []
    for g in groups:
        if merged and g[0]["action"].startswith("variance.") and merged[-1][0]["action"].startswith("variance.") \
                and g[0]["ts"] == merged[-1][0]["ts"]:
            merged[-1] += g
        else:
            merged.append(g)
    return merged[-n:]


def _p(e) -> dict:
    return json.loads(e["payload"] or "{}")


def _happened(conn) -> list[str]:
    out = []
    for g in reversed(_groups(conn)):
        sentence = _describe(conn, g)
        if sentence:
            out.append(f"<span class='when'>{g[0]['ts']:%d %b}</span> {sentence}")
    return out


def _describe(conn, g: list[dict]) -> str:
    actions = {e["action"]: e for e in g}
    first = g[0]
    adj = actions.get("adjustment.proposed")
    adj_txt = ""
    if adj:
        p = _p(adj)
        ms = sorted({l.coverage_month for l in repo.adjustment_lines(conn, adj["entity_id"])})
        span = _list([f"{m:%B}" for m in ms]) if ms else "earlier months"
        way = "under-booked" if p.get("total", 0) and float(p["total"]) >= 0 else "over-booked"
        adj_txt = (f" Because they reach back into months already closed, the system worked out {span} were {way} by "
                   f"<b>{money(abs(float(p['total'])))}</b> and prepared a correction for you to approve.")
    a = first["action"]
    if a == "rate_version.drafted":
        p = _p(first)
        status = conn.execute("SELECT status FROM rate_versions WHERE version_id=?", [first["entity_id"]]).fetchone()
        tail = "They are waiting for your approval." if status and status[0] == "draft" else ""
        return f"The AI read <i>{html.escape(p.get('document', 'a rate document'))}</i> and found {p.get('cells')} rates. {tail}"
    if a == "rate_version.approved":
        return f"{html.escape(first['actor_id'])} approved new rates. From now on they are used to calculate revenue.{adj_txt}"
    if a == "rate_version.rejected":
        return f"{html.escape(first['actor_id'])} rejected a rate document, so its rates will not be used."
    if a == "adjustment.approved_and_posted":
        return (f"The correction of <b>{money(abs(float(_p(first).get('total', 0))))}</b> was approved and added to the "
                "books, tagged to the months it corrects.")
    if a == "adjustment.rejected":
        return "A correction was rejected, so the closed months stay as they were booked."
    if a == "bank_link.confirmed":
        return "A bank deposit was confirmed as the state's payment, so that money is now accounted for."
    if a == "period.closed":
        m = date.fromisoformat(first["entity_id"] + "-01")
        return f"{m:%B} was signed off and locked at <b>{money(_p(first).get('booked_total'))}</b>."
    if a.startswith("variance."):
        return f"You reviewed the AI's explanation for {len(g)} member(s) and your answer was saved for next time."
    price = actions.get("close.price")
    members = actions.get("close.reconcile_members")
    if price:
        m = date.fromisoformat(price["entity_id"].replace("close-", "") + "-01")
        pp, mm = _p(price), _p(members) if members else {}
        txt = (f"The {m:%B} close ran: {pp.get('member_months', 0):,} members checked, "
               f"<b>{money(pp.get('expected_total'))}</b> expected")
        if members:
            txt += f", and {mm.get('variances', 0)} members were paid differently from what we expected"
        return txt + "." + adj_txt
    if adj:
        return adj_txt.strip()
    return ""


# ---------------------------------------------------------------- what needs you
def _needs(tasks) -> str:
    if not tasks:
        return "Nothing right now. New documents will appear when they arrive, or move the business date forward."
    lines, docs = [], 0
    for t in tasks:
        if t.key.startswith("doc-"):
            docs += 1
            continue
        lines.append(t.plain or t.label)
    if docs:
        lines.append(f"{docs} rate document{'s are' if docs > 1 else ' is'} waiting in the state inbox.")
    return "<ol class='sum-list'>" + "".join(f"<li>{x}</li>" for x in lines) + "</ol>"


# ---------------------------------------------------------------- latest month result
def _latest_result(conn) -> str:
    row = conn.execute("SELECT coverage_month, as_of FROM close_runs ORDER BY coverage_month DESC LIMIT 1").fetchone()
    if not row:
        return ""
    month, ran_at = row
    rows = repo._rows(conn, "SELECT * FROM variances WHERE run_id=?", [run_id_for(month)])
    if not rows:
        return f"In {month:%B}, the state paid every member exactly right."
    gap = sum(r["delta"] for r in rows)
    agg: dict[str, list] = {}
    from lib import primary_cause
    for r in rows:
        a = agg.setdefault(primary_cause(r), [0, 0])
        a[0] += 1
        a[1] += r["delta"]
    top, (n, amt) = max(agg.items(), key=lambda kv: abs(kv[1][1]))
    word = "underpaid" if gap < 0 else "overpaid"
    return (f"In {month:%B}, the state {word} us <b>{money(abs(gap))}</b> overall. The biggest reason: "
            f"{cause(top).lower()} ({n} member{'s' if n != 1 else ''}, {money(abs(amt))}).")
