"""The ONLY module allowed to read direct identifiers (names, DOB).

engine/ and agents/ are forbidden from importing this module (enforced by
tests/test_import_boundaries.py). The UI uses it only for masked display and
for an audited "reveal" action.
"""

from __future__ import annotations

import duckdb

from schemas.enrollment import Member


def get_member_identity(conn: duckdb.DuckDBPyConnection, member_id: str) -> Member | None:
    row = conn.execute(
        "SELECT member_id, first_name, last_name, dob FROM members WHERE member_id = ?",
        [member_id],
    ).fetchone()
    if row is None:
        return None
    return Member(member_id=row[0], first_name=row[1], last_name=row[2], dob=row[3])


def load_identifier_values(conn: duckdb.DuckDBPyConnection) -> tuple[set[str], dict[str, str], set[str]]:
    """Known names, DOB per member, and member IDs, used to arm the privacy scanner."""
    rows = conn.execute("SELECT member_id, first_name, last_name, dob FROM members").fetchall()
    names = {r[1] for r in rows} | {r[2] for r in rows}
    dob_by_member = {r[0]: r[3].isoformat() for r in rows}
    ids = {r[0] for r in rows}
    return names, dob_by_member, ids
