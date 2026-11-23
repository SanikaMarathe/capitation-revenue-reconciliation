"""Shared UI plumbing: one DB connection, the LLM client, the demo clock, the role switcher."""

from __future__ import annotations

import json
import sys
from datetime import datetime, time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from agents.mock_responders import mock_provider  # noqa: E402
from common.config import get_settings  # noqa: E402
from llm.factory import build_client  # noqa: E402
from privacy.masking import mask_name  # noqa: E402
from storage.phi import get_member_identity  # noqa: E402
from workflow import demo  # noqa: E402
from workflow.close import CloseOrchestrator  # noqa: E402

ROLES = [demo.ANALYST, demo.CONTROLLER]


@st.cache_resource
def get_conn():
    try:
        conn = demo.open_db()
    except Exception as exc:                 # the database file can only be open in one app at a time
        if "being used by another process" not in str(exc):
            raise
        st.error("The app is already open somewhere else (another browser tab or terminal on a different port). "
                 "Close that copy, then refresh this page.")
        st.stop()
    if conn.execute("SELECT count(*) FROM members").fetchone()[0] == 0:
        demo.start_fresh(conn)
    return conn


@st.cache_resource
def get_client(_conn):
    settings = get_settings()
    provider = mock_provider() if settings.llm["provider"] == "mock" else None
    return build_client(settings, conn=_conn, provider=provider)


def orchestrator() -> CloseOrchestrator:
    conn = get_conn()
    return CloseOrchestrator(conn, get_client(conn))


def clock() -> datetime:
    """The business date the user has set, at the current time of day, so actions on the same day stay in order."""
    return datetime.combine(demo.get_clock(get_conn()).date(), datetime.now().time().replace(microsecond=0))


def role() -> str:
    # One signed-in user for the demo. Approvals are still recorded with this name in the audit log, and
    # code still blocks anyone from approving an adjustment they proposed (the proposer is the retro engine).
    return demo.CONTROLLER


def is_controller() -> bool:
    return True


def money(d) -> str:
    d = Decimal(str(d or 0))
    return f"-${abs(d):,.2f}" if d < 0 else f"${d:,.2f}"


def llm_causes(row: dict) -> list[str]:
    return json.loads(row.get("possible_cause_llm") or "[]")


def primary_cause(row: dict) -> str:
    """Human-confirmed cause first, then a rule answer, then a grounded LLM answer."""
    if row.get("confirmed_cause"):
        return row["confirmed_cause"]
    hits = json.loads(row.get("features") or "{}").get("rule_hits", [])
    if len(hits) == 1 and hits[0]["strength"] == "strong":
        return hits[0]["code"]
    causes = llm_causes(row)
    return " + ".join(causes) if causes and row.get("grounded") else "unresolved"


def masked(member_id: str | None) -> str:
    if not member_id:
        return ""
    m = get_member_identity(get_conn(), member_id)
    return mask_name(m.first_name, m.last_name) if m else ""


CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"], .stMarkdown, button, input { font-family: 'Inter', sans-serif; }
[data-testid='stSidebar'], [data-testid='stSidebarCollapsedControl'] { display: none; }
.block-container { padding: 1rem 2rem 2rem 2rem; max-width: 100% !important; }
header[data-testid="stHeader"] { height: 0; background: transparent; }
[data-testid="stToolbar"] { display: none; }
/* app bar */
.st-key-appbar { background: #0f3d36; border-radius: 16px; padding: 0.9rem 1.4rem; margin-bottom: 1rem; }
.st-key-appbar .brand { color: #ffffff; font-size: 1.45rem; font-weight: 800; letter-spacing: -0.01em; line-height: 1.1; }
.st-key-appbar .tagline { color: #a7d3cb; font-size: 0.82rem; }
.st-key-appbar .pill { background: #14524a; border-color: #2a6b62; color: #fff; }
.st-key-appbar [data-testid="stSegmentedControl"] button { font-size: 1rem; font-weight: 600; padding: 0.45rem 1.1rem; }
.st-key-appbar button[data-variant="segmented_control"] { background: #14524a !important; border-color: #2a6b62 !important; }
.st-key-appbar button[data-variant="segmented_control"] * { color: #d7ece8 !important; }
.st-key-appbar button[data-variant="segmented_control"][aria-checked="true"] { background: #ffffff !important;
    border-color: #ffffff !important; }
.st-key-appbar button[data-variant="segmented_control"][aria-checked="true"] * { color: #0f3d36 !important; font-weight: 700; }
.st-key-appbar .stButton button { border-radius: 10px; }
.st-key-appbar label p { color: #a7d3cb !important; font-size: 0.78rem; }
.st-key-appbar [data-testid='stDateInput'] input { font-weight: 600; }
.st-key-appbar .stButton button[kind="secondary"] { background: transparent; color: #fff; border-color: #2a6b62; }
/* list panels (to-do, events): rows as full-width left-aligned buttons */
[class*="st-key-list"] .stButton button { justify-content: flex-start; text-align: left; padding: 0.75rem 0.9rem;
                                          border-radius: 12px; min-height: 3.2rem; }
[class*="st-key-list"] .stButton button p { font-size: 0.95rem; }
.panel-title { font-size: 0.8rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #5b6b67;
               margin: 0.2rem 0 0.6rem 0.2rem; }
h1.app-title { font-size: 2.3rem; font-weight: 800; color: #0d2b26; margin: 0; letter-spacing: -0.02em; }
.app-sub { color: #5b6b67; font-size: 0.95rem; margin-top: 0.2rem; }
.pill { display: inline-block; border: 1px solid #d5dfdb; background: #fff; border-radius: 10px;
        padding: 0.45rem 0.9rem; font-weight: 600; color: #0d2b26; }
/* tabs as pills */
.stTabs [data-baseweb="tab-list"] { gap: 0.4rem; border-bottom: 1px solid #dfe6e3; }
.stTabs [data-baseweb="tab"] { padding: 0.55rem 1.2rem; border-radius: 10px 10px 0 0; font-weight: 600; font-size: 1rem; }
.stTabs [aria-selected="true"] { background: #e3f2ef; color: #0f766e; }
/* cards: any container whose key starts with "card" */
[class*="st-key-card"] { background: #ffffff; border: 1px solid #e1e8e5; border-radius: 16px; padding: 1.3rem 1.4rem;
                         box-shadow: 0 1px 2px rgba(16,40,35,0.04); }
.chip { display: inline-block; font-size: 0.78rem; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase;
        color: #0f766e; background: #e3f2ef; border-radius: 999px; padding: 0.2rem 0.65rem; margin-bottom: 0.5rem; }
.big { font-size: 2.6rem; font-weight: 800; color: #0d2b26; line-height: 1.1; letter-spacing: -0.02em; }
.big.pos { color: #0f766e; }
.hl { background: #e3f2ef; border-radius: 12px; padding: 0.6rem 1rem; margin: 0.4rem 0 0.6rem 0; }
.muted { color: #5b6b67; font-size: 0.92rem; }
.card-title { font-size: 1.45rem; font-weight: 800; color: #0d2b26; margin-bottom: 0.4rem; }
.kpi { padding: 0.7rem 0; border-bottom: 1px solid #e6ece9; }
.kpi:last-child { border-bottom: none; }
.kpi .label { color: #5b6b67; font-size: 0.95rem; }
.kpi .value { font-size: 2.2rem; font-weight: 800; color: #0d2b26; letter-spacing: -0.02em; }
.kpi .value.good { color: #0f766e; } .kpi .value.warn { color: #b45309; }
.kpi .sub { color: #5b6b67; font-size: 0.85rem; }
.rows { width: 100%; border-collapse: collapse; font-size: 0.98rem; }
.rows td { padding: 0.55rem 0.2rem; border-bottom: 1px solid #edf1ef; }
.rows td.r { text-align: right; font-weight: 600; }
.rows tr:last-child td { border-bottom: none; }
.rows, .rows tr, .rows td { border-left: none !important; border-right: none !important; border-top: none !important; }
.rows td { border-bottom: 1px solid #edf1ef !important; }
.rows tr:last-child td { border-bottom: none !important; }
/* step timeline */
.steps { position: relative; margin: 0.4rem 0 0 0.2rem; }
.step { display: flex; gap: 0.9rem; position: relative; padding-bottom: 0.9rem; }
.step:not(:last-child)::before { content: ""; position: absolute; left: 15px; top: 32px; bottom: 0; width: 2px;
                                 background: #cfe3de; }
.dot { flex: 0 0 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
       color: #fff; font-weight: 800; font-size: 0.95rem; }
.dot.code { background: #0f766e; } .dot.ai { background: #6d28d9; } .dot.privacy { background: #0369a1; }
.dot.person { background: #b45309; } .dot.wait { background: #9ca3af; }
.step .body { background: #f7faf9; border: 1px solid #e6ece9; border-radius: 12px; padding: 0.55rem 0.85rem; flex: 1; }
.step .tag { font-size: 0.72rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; }
.tag.code { color: #0f766e; } .tag.ai { color: #6d28d9; } .tag.privacy { color: #0369a1; }
.tag.person { color: #b45309; } .tag.wait { color: #6b7280; }
.step .txt { font-size: 0.95rem; color: #12211e; }
/* side-by-side records (bank reconciliation) */
.rec { background: #f7faf9; border: 1px solid #e1e8e5; border-radius: 12px; padding: 0.8rem 1rem; margin: 0.6rem 0; }
.rec .rt { font-weight: 800; color: #0d2b26; margin-bottom: 0.4rem; }
.rec .rt span { font-weight: 500; color: #5b6b67; font-size: 0.85rem; }
.rec .rf { display: flex; justify-content: space-between; gap: 1rem; padding: 0.25rem 0; border-bottom: 1px solid #edf1ef;
           font-size: 0.93rem; }
.rec .rf:last-child { border-bottom: none; } .rec .rf span { color: #5b6b67; }
.check { font-size: 0.97rem; padding: 0.2rem 0; } .check.ok { color: #0f766e; } .check.no { color: #b45309; font-weight: 600; }
/* summary panel */
.sum { padding: 0.6rem 0; border-bottom: 1px solid #edf1ef; }
.sum:last-of-type { border-bottom: none; }
.sum-t { font-size: 0.78rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: #0f766e;
         margin-bottom: 0.25rem; }
.sum-b { font-size: 0.98rem; line-height: 1.5; color: #12211e; }
.sum-b .when { display: inline-block; font-size: 0.75rem; font-weight: 700; color: #5b6b67; background: #eef3f1;
               border-radius: 6px; padding: 0 0.35rem; margin-right: 0.3rem; }
.sum-list { margin: 0.1rem 0 0 1.1rem; padding: 0; } .sum-list li { margin-bottom: 0.25rem; }
/* rate review table */
.eff { font-weight: 700; color: #0f766e; margin: -0.2rem 0 0.3rem 0; }
.ratebox { max-height: 390px; overflow-y: auto; border: 1px solid #e6ece9; border-radius: 12px; margin: 0.7rem 0; }
table.rates { width: 100%; border-collapse: collapse; font-size: 0.93rem; }
table.rates th { position: sticky; top: 0; background: #f2f6f4; text-align: left; font-weight: 600; color: #5b6b67;
                 padding: 0.5rem 0.7rem; border: none !important; }
table.rates td { padding: 0.42rem 0.7rem; border: none !important; border-bottom: 1px solid #edf1ef !important; }
table.rates .r { text-align: right; }
table.rates tr.chg td { background: #fff7ed; }
table.rates tr.same td { color: #8a9893; }
.delta { background: #fde7cf; color: #9a4a06; font-weight: 700; border-radius: 999px; padding: 0.1rem 0.5rem; }
/* month strip */
[class*="st-key-month-"][class*="-act"] .stButton button { border: 2px solid #b45309 !important; background: #fff7ed !important; }
[class*="st-key-month-"][class*="-act"] .stButton button p { color: #9a4a06 !important; font-weight: 700; }
[class*="st-key-month-"] .stButton button:disabled p { color: #9aa5a1; }
[class*="st-key-month-"] .stButton button { min-height: 3.6rem; border-radius: 12px; }
[class*="st-key-month-"] .stButton button p { font-size: 0.88rem; line-height: 1.3; }
</style>
"""


def page(title: str, icon: str = "") -> None:
    st.set_page_config(page_title=title, layout="wide", initial_sidebar_state="collapsed")
    st.markdown(CSS, unsafe_allow_html=True)


def top_bar() -> str:
    """App bar: name, the two views, the business date (you move it, forward only), Start fresh."""
    conn = get_conn()
    today = demo.get_clock(conn).date()
    with st.container(key="appbar"):
        b, nav, d, r = st.columns([2.4, 2.2, 1.6, 1.1], vertical_alignment="center")
        b.markdown("<div class='brand'>Capitation Revenue Check</div><div class='tagline'>Were we paid right by the "
                   "state? Code checks, AI explains, you decide.</div>", unsafe_allow_html=True)
        view = nav.segmented_control("View", ["Analyst view", "Behind the scenes"], default="Analyst view",
                                     key="nav", label_visibility="collapsed") or "Analyst view"
        picked = d.date_input("Business date", value=today, min_value=today, max_value=demo.data_end(conn),
                              format="MM/DD/YYYY", key=f"bizdate-{today}",
                              help="The date the books are being worked on. It only moves forward: the system "
                                   "remembers what was known on each date.")
        if picked != today:
            demo.set_clock(conn, datetime.combine(picked, time(9)))
            st.session_state.pop("task", None)
            st.rerun()
        if r.button("Start fresh", use_container_width=True,
                    help="Clear the books: keeps the state's enrollment, payment and bank files, removes all rates, "
                         "closes and approvals."):
            demo.start_fresh(conn)
            st.session_state.clear()
            st.rerun()
    return view


