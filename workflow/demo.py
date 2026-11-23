"""Demo world: a clock that walks the scenario timeline, plus one-click resets.

The clock matters because the system is bitemporal: an approval made "today" (Nov 2026) would
be later than every close and wreck the story. Every action in the demo uses the demo clock.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from data_gen import spec
from storage.db import connect
from storage.load_raw import load_raw
from workflow.close import CloseOrchestrator

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
# RECON_DB lets a second copy of the app (e.g. for testing) use its own database file.
DB = Path(os.environ.get("RECON_DB", ROOT / "data" / "recon.duckdb"))
DOCS = {"v1": "v1_rate_notice_CY2026.pdf", "v2": "v2_rate_sheet_eff_2026-04-01.xlsx",
        "v3": "v3_rate_amendment_email.txt"}
CONTROLLER = "Controller Alex"
ANALYST = "Analyst Sanika"


@dataclass(frozen=True)
class Event:
    at: datetime
    kind: str          # receive | close
    ref: str           # version id or YYYY-MM
    label: str


def timeline() -> list[Event]:
    ev = [Event(datetime.combine(spec.PUBLISHED[v], time(10)), "receive", v, f"{DOCS[v]} arrives") for v in DOCS]
    ev += [Event(datetime.combine(spec.close_date(m), time(17)), "close", f"{m:%Y-%m}", f"Close {m:%B %Y}")
           for m in spec.MONTHS]
    return sorted(ev, key=lambda e: e.at)


# ---------------------------------------------------------------- clock
def get_clock(conn) -> datetime:
    row = conn.execute("SELECT value FROM app_state WHERE key='clock'").fetchone()
    return datetime.fromisoformat(row[0]) if row else timeline()[0].at


def set_clock(conn, at: datetime) -> None:
    conn.execute("DELETE FROM app_state WHERE key='clock'")
    conn.execute("INSERT INTO app_state VALUES ('clock', ?)", [at.isoformat()])


def next_event(conn) -> Event | None:
    now = get_clock(conn)
    return next((e for e in timeline() if e.at > now), None)


def docs_received(conn) -> list[tuple[str, Path, datetime]]:
    now = get_clock(conn)
    return [(e.ref, RAW / "rate_docs" / DOCS[e.ref], e.at) for e in timeline() if e.kind == "receive" and e.at <= now]


# ---------------------------------------------------------------- resets
def fresh(conn) -> None:
    """Empty plan books on Dec 1 2025: data feeds loaded, no rates, nothing closed."""
    for table in ["rate_versions", "rates", "source_documents", "periods", "close_snapshots", "bank_links",
                  "variances", "variance_decisions", "adjustments", "adjustment_lines", "ledger_entries",
                  "audit_log", "llm_calls", "close_runs", "app_state"]:
        conn.execute(f"DELETE FROM {table}")
    load_raw(conn, RAW)
    set_clock(conn, timeline()[0].at)


def fast_forward(conn, orch: CloseOrchestrator, until: datetime) -> list[str]:
    """Play the timeline with a diligent controller (approves everything) up to `until`."""
    log = []
    for e in timeline():
        if e.at > until:
            break
        set_clock(conn, e.at)
        if e.kind == "receive":
            orch.ingest_document(RAW / "rate_docs" / DOCS[e.ref], received=e.at.date(), version_id=e.ref, at=e.at)
            log.append(f"{e.at:%Y-%m-%d} extracted {e.ref}")
            if e.at < until:   # leave the last arriving document in the inbox for the presenter
                approve_at = datetime.combine(spec.APPROVED[e.ref], time(9))
                adj = orch.approve_rate(e.ref, CONTROLLER, approve_at)
                log.append(f"{approve_at:%Y-%m-%d} approved {e.ref}" + (f", retro {adj}" if adj else ""))
                if adj:
                    from governance.approvals import approve_adjustment
                    approve_adjustment(conn, adj, CONTROLLER, approve_at)
        else:
            month = date.fromisoformat(e.ref + "-01")
            orch.run_close(month, e.at, ANALYST)
            for (adj,) in conn.execute("SELECT adjustment_id FROM adjustments WHERE status='proposed'").fetchall():
                from governance.approvals import approve_adjustment
                approve_adjustment(conn, adj, CONTROLLER, e.at)
            orch.sign_off(month, e.at, CONTROLLER)
            log.append(f"{e.at:%Y-%m-%d} closed {e.ref}")
    set_clock(conn, until)
    return log


SCENARIOS = {
    "Fresh start (1 Dec 2025, nothing approved)": None,
    "v3 email just arrived (Jan-Apr closed)": datetime.combine(spec.PUBLISHED["v3"], time(10)),
    "Half-year complete (all closed)": datetime.combine(spec.close_date(spec.MONTHS[-1]), time(17)),
}


def reset(conn, orch: CloseOrchestrator, scenario: str) -> list[str]:
    fresh(conn)
    until = SCENARIOS[scenario]
    return fast_forward(conn, orch, until) if until else []


def open_db():
    return connect(DB)


# ---------------------------------------------------------------- free flow (no script)
# The app does not follow a timeline. Documents sit in the state inbox until someone asks the AI to read
# them; a month can be closed once its period has ended and the state's payment for it has arrived; the
# business date only moves when the user moves it. Everything below is derived from the data.
INBOX = RAW / "rate_docs"
DOC_TYPES = {".pdf", ".xlsx", ".txt", ".eml"}


def data_start(conn) -> datetime:
    """The day before the first coverage month in the state's files: nothing is knowable yet."""
    first = conn.execute("SELECT min(coverage_month) FROM remittance_lines").fetchone()[0]
    return datetime.combine(first.replace(day=1), time(9)) - timedelta(days=31)


def data_end(conn) -> date:
    """The last date anything happens in the state's or bank's files, plus a month to close the last period."""
    last = conn.execute("SELECT greatest((SELECT max(payment_date) FROM remittance_lines), "
                        "(SELECT max(deposit_date) FROM bank_deposits))").fetchone()[0]
    return last + timedelta(days=45)


def start_fresh(conn) -> None:
    """Empty books: the state's enrollment, payment and bank files are loaded; no rates, nothing closed."""
    fresh(conn)
    set_clock(conn, data_start(conn))


def inbox(conn) -> list[tuple[Path, bool]]:
    """Every document in the state inbox, and whether it has been read."""
    from agents.doc_parse import sha256_of

    seen = {r[0] for r in conn.execute("SELECT sha256 FROM source_documents").fetchall()}
    files = sorted(p for p in INBOX.iterdir() if p.suffix.lower() in DOC_TYPES)
    return [(p, sha256_of(p) in seen) for p in files]


def next_version_id(conn) -> str:
    """Versions are numbered in the order documents are read: v1, v2, ..."""
    n = conn.execute("SELECT count(*) FROM rate_versions").fetchone()[0]
    return f"v{n + 1}"


def closable_month(conn, now: datetime) -> date | None:
    """The earliest open month whose period has ended and for which the state has paid."""
    closed = {r[0] for r in conn.execute("SELECT coverage_month FROM periods WHERE status='closed'").fetchall()}
    months = [r[0] for r in conn.execute("SELECT DISTINCT coverage_month FROM remittance_lines ORDER BY 1").fetchall()]
    for m in months:
        if m in closed:
            continue
        period_over = (m.replace(day=28) + timedelta(days=4)).replace(day=1) <= now.date()
        paid = conn.execute("SELECT 1 FROM remittance_lines WHERE coverage_month=? AND payment_date<=? LIMIT 1",
                            [m, now.date()]).fetchone()
        return m if period_over and paid else None
    return None
