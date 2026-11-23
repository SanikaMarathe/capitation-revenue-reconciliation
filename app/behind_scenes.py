"""Tab 2: what the system did, step by step, like an agent narrating its work.

Left: pick an event (newest first) and see its steps as a checklist, each marked code, AI, privacy,
person or waiting. Right: the AI exchange for that event, exactly what was sent and what came back.
Every line is built from what the system recorded (app/trace.py); nothing is scripted.
"""

from __future__ import annotations

import html
import json
import re

import streamlit as st
from lib import get_conn
from trace import AI, CODE, PERSON, PRIVACY, WAIT, build_jobs

from common.config import get_settings
from llm.cache import ResponseCache
from privacy.scanner import PromptScanner
from storage import repo
from storage.phi import load_identifier_values

KIND = {CODE: ("code", "Code", "✓"), AI: ("ai", "AI", "✦"), PRIVACY: ("privacy", "Privacy check", "🔒"),
        PERSON: ("person", "Person", "✓"), WAIT: ("wait", "Waiting for you", "…")}
SHOW = 6


def render(now) -> None:
    conn = get_conn()
    all_jobs = build_jobs(conn)
    jobs = list(reversed(all_jobs))[:SHOW]
    if not jobs:
        st.info("Nothing has happened yet.")
        return
    # A new event becomes the selected one automatically.
    if st.session_state.get("event_count") != len(all_jobs):
        st.session_state["event_count"] = len(all_jobs)
        st.session_state["event"] = 0
    events, steps, ai = st.columns([1, 1.6, 1.4], gap="medium")
    with events:
        with st.container(key="list-events"):
            st.markdown("<div class='panel-title'>What happened · newest first</div>", unsafe_allow_html=True)
            for i, j in enumerate(jobs):
                waiting = any(icon == WAIT for icon, _ in j.lines)
                if st.button(f"{'⏸️' if waiting else '✅'}  {j.ts:%d %b} · {j.title}", key=f"ev-{i}",
                             use_container_width=True, type="primary" if i == st.session_state["event"] else "secondary"):
                    st.session_state["event"] = i
                    st.rerun()
    job = jobs[min(st.session_state["event"], len(jobs) - 1)]
    with steps:
        with st.container(key="card-event"):
            waiting = any(icon == WAIT for icon, _ in job.lines)
            st.markdown(f"<span class='chip'>Step by step · {job.ts:%d %b %Y}</span><div class='card-title'>"
                        f"{html.escape(job.title)}</div><div class='muted'>"
                        + ("Done. Now waiting for a person." if waiting else "Finished.")
                        + "</div>", unsafe_allow_html=True)
            st.markdown(_steps_html(job.lines), unsafe_allow_html=True)
            st.markdown("<div class='muted' style='margin-top:0.4rem'><b style='color:#0f766e'>Code</b> exact and "
                        "repeatable · <b style='color:#6d28d9'>AI</b> reads and explains · "
                        "<b style='color:#0369a1'>Privacy</b> nothing personal leaves · "
                        "<b style='color:#b45309'>Person</b> decides</div>", unsafe_allow_html=True)
    with ai:
        _ai_panel(conn, job)


def _steps_html(lines) -> str:
    out = []
    for icon, text in lines:
        cls, tag, mark = KIND.get(icon, ("code", "Step", "✓"))
        safe = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(text))
        safe = re.sub(r"\*(.+?)\*", r"<i>\1</i>", safe)
        out.append(f"<div class='step'><div class='dot {cls}'>{mark}</div><div class='body'>"
                   f"<div class='tag {cls}'>{tag}</div><div class='txt'>{safe}</div></div></div>")
    return "<div class='steps'>" + "".join(out) + "</div>"


@st.cache_resource
def _scanner(_conn) -> PromptScanner:
    names, dob_by_member, _ids = load_identifier_values(_conn)
    return PromptScanner(names=names, dob_by_member=dob_by_member)


def _pretty(text: str) -> str:
    try:
        return json.dumps(json.loads(text), indent=1)
    except (TypeError, ValueError):
        return text


def _ai_panel(conn, job) -> None:
    keys = [k for k in job.ai_calls if k]
    with st.container(key="card-ai"):
        st.markdown("<span class='chip'>AI exchange</span>", unsafe_allow_html=True)
        if not keys:
            st.markdown("<div class='card-title'>No AI in this step</div><div class='muted'>This was done by code and "
                        "a person only. The AI is used just to read documents and explain differences; it never "
                        "calculates money or approves anything.</div>", unsafe_allow_html=True)
            return
        path = ResponseCache(get_settings().path("llm_cache_dir"))._file(keys[0])
        if not path.exists():
            st.caption("Record not found.")
            return
        rec = json.loads(path.read_text(encoding="utf-8"))
        call = repo._rows(conn, "SELECT * FROM llm_calls WHERE prompt_hash=? ORDER BY ts DESC LIMIT 1", [keys[0]])
        model = call[0]["model"].replace("mock:", "") + (" (mock stand-in)" if call and call[0]["model"].startswith("mock:")
                                                          else "") if call else "AI"
        leaks = _scanner(conn).findings(rec["user"])
        st.markdown(f"<div class='card-title'>What the AI saw and said</div><div class='muted'>Model: <b>{model}</b>"
                    + (" · one example of the calls in this step" if len(job.ai_calls) > 1 else "") + "</div>",
                    unsafe_allow_html=True)
        st.markdown("<div class='hl'>" + ("🔒 Privacy scan passed: no names, no dates of birth, no real member IDs. "
                    "Members appear only as tokens like <code>MBR-…</code>" if not leaks else
                    "❌ " + html.escape(", ".join(leaks))) + "</div>", unsafe_allow_html=True)
        st.markdown("**Sent to the AI**")
        st.code(_pretty(rec["user"]), language="json", height=230)
        st.markdown("**The AI's answer** · checked against a strict format, then fact-checked by code")
        st.code(_pretty(rec["response"]["text"]), language="json", height=230)
