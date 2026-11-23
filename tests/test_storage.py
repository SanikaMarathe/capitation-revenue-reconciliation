from __future__ import annotations

from datetime import date

import pytest

from storage.db import connect, transaction
from storage.phi import get_member_identity, load_identifier_values


def test_schema_creates_all_tables():
    conn = connect()
    tables = {r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    for t in ["members", "enrollment_spans", "rate_versions", "rates", "remits", "remittance_lines",
              "bank_deposits", "periods", "close_snapshots", "bank_links", "variances", "adjustments",
              "adjustment_lines", "ledger_entries", "audit_log", "llm_calls", "close_runs"]:
        assert t in tables


def test_money_columns_are_decimal():
    conn = connect()
    dtype = conn.execute(
        "SELECT data_type FROM information_schema.columns WHERE table_name='rates' AND column_name='pmpm'"
    ).fetchone()[0]
    assert dtype.startswith("DECIMAL")


def test_transaction_rolls_back_on_error():
    conn = connect()
    with pytest.raises(RuntimeError):
        with transaction(conn):
            conn.execute("INSERT INTO periods VALUES ('2026-01-01', 'open', NULL, NULL)")
            raise RuntimeError("boom")
    assert conn.execute("SELECT count(*) FROM periods").fetchone()[0] == 0


def test_posting_key_is_unique():
    conn = connect()
    row = ["E1", "2026-01-01", "Capitation Receivable", "20.00", "0.00", "m", "prior_period_adjustment",
           "ADJ1", "ADJ1:2026-01:DR", "ctrl", "2026-05-21 10:00:00"]
    conn.execute("INSERT INTO ledger_entries VALUES (?,?,?,?,?,?,?,?,?,?,?)", row)
    with pytest.raises(Exception):
        conn.execute("INSERT INTO ledger_entries VALUES (?,?,?,?,?,?,?,?,?,?,?)", ["E2", *row[1:]])


def test_audit_append_after_delete_and_reopen_on_file_db(tmp_path):
    """Regression: on a persisted file, after deleting and reinserting audit rows, appending must still
    find the true last event (ORDER BY ... DESC LIMIT 1 returned the second-highest row in DuckDB 2.0.1)."""
    from governance.audit import append_event, verify_chain
    from schemas.enums import ActorType
    path = tmp_path / "t.duckdb"
    conn = connect(path)
    for i in range(45):
        append_event(conn, actor_type=ActorType.SYSTEM, actor_id="t", action="a", entity="e", entity_id=str(i))
    conn.execute("DELETE FROM audit_log")
    for i in range(40):
        append_event(conn, actor_type=ActorType.SYSTEM, actor_id="t", action="a", entity="e", entity_id=str(i))
    conn.close()
    conn = connect(path)
    for i in range(3):
        append_event(conn, actor_type=ActorType.SYSTEM, actor_id="t", action="b", entity="e", entity_id=str(i))
    assert conn.execute("SELECT max(seq), count(*) FROM audit_log").fetchone() == (43, 43)
    assert verify_chain(conn) == (True, None)


def test_phi_module_reads_identity():
    conn = connect()
    conn.execute("INSERT INTO members VALUES ('M000001', 'Asha', 'Ramirez', '1990-04-12')")
    member = get_member_identity(conn, "M000001")
    assert member is not None and member.dob == date(1990, 4, 12)
    names, dobs, ids = load_identifier_values(conn)
    assert names == {"Asha", "Ramirez"} and dobs == {"M000001": "1990-04-12"} and ids == {"M000001"}
