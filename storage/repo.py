"""Typed reads and writes. The engine works on these Pydantic records, never on raw SQL rows."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from decimal import Decimal

import duckdb

from schemas.enrollment import EnrollmentSpan
from schemas.enums import AdjustmentStatus
from schemas.ledger import AdjustmentLine, CloseSnapshot
from schemas.rates import RateRow, RateVersion
from schemas.remittance import BankDeposit, Remit, RemittanceLine
from schemas.variance import Variance


def _rows(conn, sql: str, params: list | None = None) -> list[dict]:
    cur = conn.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# ---------------- reads
def spans(conn) -> list[EnrollmentSpan]:
    return [EnrollmentSpan(**r) for r in _rows(conn, "SELECT * FROM enrollment_spans ORDER BY span_id")]


def spans_by_member(conn) -> dict[str, list[EnrollmentSpan]]:
    out: dict[str, list[EnrollmentSpan]] = defaultdict(list)
    for s in spans(conn):
        out[s.member_id].append(s)
    return out


def rate_versions(conn) -> list[RateVersion]:
    return [RateVersion(**r) for r in _rows(conn, "SELECT * FROM rate_versions ORDER BY version_id")]


def rate_rows(conn) -> list[RateRow]:
    return [RateRow(**r) for r in _rows(conn, "SELECT * FROM rates ORDER BY version_id, region, category, effective_from")]


def remittance_lines(conn, coverage_month: date | None = None) -> list[RemittanceLine]:
    if coverage_month is None:
        return [RemittanceLine(**r) for r in _rows(conn, "SELECT * FROM remittance_lines ORDER BY line_id")]
    return [RemittanceLine(**r) for r in _rows(
        conn, "SELECT * FROM remittance_lines WHERE coverage_month = ? ORDER BY line_id", [coverage_month])]


def remits(conn) -> list[Remit]:
    return [Remit(**r) for r in _rows(conn, "SELECT * FROM remits ORDER BY remit_id")]


def deposits(conn) -> list[BankDeposit]:
    return [BankDeposit(**r) for r in _rows(conn, "SELECT * FROM bank_deposits ORDER BY deposit_id")]


def bank_links(conn) -> dict[str, str]:
    return {r["deposit_id"]: r["remit_id"] for r in _rows(conn, "SELECT deposit_id, remit_id FROM bank_links")}


def closed_months(conn) -> list[date]:
    return [r["coverage_month"] for r in _rows(conn, "SELECT coverage_month FROM periods WHERE status='closed' ORDER BY 1")]


def snapshots(conn, months: list[date] | None = None) -> list[CloseSnapshot]:
    rows = _rows(conn, "SELECT * FROM close_snapshots ORDER BY coverage_month, member_id")
    out = [CloseSnapshot(**r) for r in rows]
    return [s for s in out if months is None or s.coverage_month in months]


def active_adjustment_lines(conn) -> list[AdjustmentLine]:
    """Lines of every live adjustment (proposed or posted): the retro baseline. Rejected and stale are excluded."""
    rows = _rows(conn, """
        SELECT l.* FROM adjustment_lines l JOIN adjustments a USING (adjustment_id)
        WHERE a.status NOT IN (?, ?) ORDER BY a.created_at, l.member_id, l.coverage_month""",
        [AdjustmentStatus.REJECTED.value, AdjustmentStatus.STALE.value])
    return [AdjustmentLine(**r) for r in rows]


def adjustment(conn, adjustment_id: str) -> dict | None:
    rows = _rows(conn, "SELECT * FROM adjustments WHERE adjustment_id = ?", [adjustment_id])
    return rows[0] if rows else None


def adjustment_lines(conn, adjustment_id: str) -> list[AdjustmentLine]:
    return [AdjustmentLine(**r) for r in _rows(
        conn, "SELECT * FROM adjustment_lines WHERE adjustment_id = ? ORDER BY coverage_month, member_id", [adjustment_id])]


# ---------------- writes
def insert_rate_version(conn, version: RateVersion, rows: list[RateRow]) -> None:
    conn.execute("INSERT INTO rate_versions VALUES (?,?,?,?,?,?,?,?)",
                 [version.version_id, version.kind.value, version.published_date, version.source_document_id,
                  version.status.value, version.approved_by, version.approved_at, version.notes])
    conn.executemany("INSERT INTO rates VALUES (?,?,?,?,?,?,?)",
                     [[r.version_id, r.region.value, r.category.value, r.effective_from, r.effective_to, r.pmpm,
                       r.source_ref] for r in rows])


def insert_snapshots(conn, snaps: list[CloseSnapshot]) -> None:
    conn.executemany("INSERT INTO close_snapshots VALUES (?,?,?,?,?,?,?,?)",
                     [[s.coverage_month, s.member_id, s.region.value, s.category.value, s.span_id, s.rate_version_id,
                       s.pmpm, s.snapshot_at] for s in snaps])


def insert_variances(conn, variances: list[Variance]) -> None:
    def j(v):
        return json.dumps(v, default=str)

    conn.executemany(
        "INSERT INTO variances VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [[v.variance_id, v.run_id, v.coverage_month, v.member_id, v.level.value, v.expected, v.remitted, v.banked,
          v.delta, j(v.features), v.observed_type,
          v.possible_cause_rule.value if v.possible_cause_rule else None,
          v.rule_strength.value if v.rule_strength else None,
          j([c.value for c in v.possible_cause_llm]), v.llm_confidence,
          v.confirmed_cause.value if v.confirmed_cause else None, v.explanation, j(v.evidence_refs),
          v.recommended_action.value if v.recommended_action else None, v.grounded, v.group_id, v.status.value]
         for v in variances])


def sum_decimal(values) -> Decimal:
    return sum(values, Decimal("0.00"))
