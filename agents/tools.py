"""Read-only tools for the investigator. None of them can return names or DOB:
they read only enrollment_spans, rates, remittance_lines, bank_deposits and variance_decisions.
Every row returned carries an ID so the LLM can cite it as evidence.
"""

from __future__ import annotations

from datetime import date

from storage.repo import _rows


def get_enrollment_history(conn, member_id: str) -> list[dict]:
    return [{"id": r["span_id"], "start_date": r["start_date"], "end_date": r["end_date"], "region": r["region"],
             "category": r["category"], "received_date": r["received_date"]}
            for r in _rows(conn, "SELECT * FROM enrollment_spans WHERE member_id=? ORDER BY start_date", [member_id])]


def get_rate_history(conn, region: str, category: str) -> list[dict]:
    rows = _rows(conn, """SELECT r.*, v.approved_at, v.status FROM rates r JOIN rate_versions v USING (version_id)
                          WHERE r.region=? AND r.category=? AND v.approved_at IS NOT NULL
                          ORDER BY v.approved_at""", [region, category])
    return [{"id": f"RATE:{r['version_id']}:{region}-{category}:{r['effective_from']:%Y-%m}",
             "version": r["version_id"], "effective_from": r["effective_from"], "effective_to": r["effective_to"],
             "pmpm": r["pmpm"], "approved_at": r["approved_at"]} for r in rows]


def get_remittance_lines(conn, member_id: str, month: date) -> list[dict]:
    return [{"id": r["line_id"], "remit_id": r["remit_id"], "payment_date": r["payment_date"],
             "region": r["region"], "category": r["category"], "amount": r["amount"],
             "line_type": r["line_type"], "reference": r["reference"]}
            for r in _rows(conn, "SELECT * FROM remittance_lines WHERE member_id=? AND coverage_month=? ORDER BY line_id",
                           [member_id, month])]


def get_bank_deposits(conn, start: date, end: date) -> list[dict]:
    return [{"id": r["deposit_id"], "deposit_date": r["deposit_date"], "amount": r["amount"],
             "reference": r["reference"]}
            for r in _rows(conn, "SELECT * FROM bank_deposits WHERE deposit_date BETWEEN ? AND ? ORDER BY 1", [start, end])]


def get_prior_explanations(conn, category: str | None, cell: str | None, limit: int = 3) -> list[dict]:
    """Knowledge store: past human decisions on similar variances (overrides first)."""
    rows = _rows(conn, """
        SELECT d.decision_id, d.decision, d.final_category, d.notes, v.observed_type, v.explanation
        FROM variance_decisions d JOIN variances v ON v.variance_id = d.variance_id
        WHERE (? IS NULL OR v.possible_cause_rule = ?) AND (? IS NULL OR json_extract_string(v.features, '$.expected_cell') = ?)
        ORDER BY CASE d.decision WHEN 'override' THEN 0 ELSE 1 END, d.ts DESC LIMIT ?""",
        [category, category, cell, cell, limit])
    return [{"id": r["decision_id"], "decision": r["decision"], "final_category": r["final_category"],
             "notes": r["notes"], "observed_type": r["observed_type"], "explanation": r["explanation"]} for r in rows]
