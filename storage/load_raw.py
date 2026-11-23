"""Loads the raw input files (layer 1) into validated tables (layer 2).

Rate documents are NOT loaded here: rates enter only through extraction + human approval.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

RAW_TABLES = {
    "members": ("members.csv", ["member_id", "first_name", "last_name", "dob"]),
    "enrollment_spans": ("enrollment_834.csv",
                         ["span_id", "member_id", "start_date", "end_date", "region", "category", "source", "received_date"]),
    "remits": ("remittance_820_header.csv", ["remit_id", "payment_date", "total_amount", "line_count"]),
    "remittance_lines": ("remittance_820_lines.csv",
                         ["line_id", "remit_id", "payment_date", "coverage_month", "member_id", "region", "category",
                          "amount", "line_type", "reference"]),
    "bank_deposits": ("bank_deposits.csv", ["deposit_id", "deposit_date", "amount", "payer", "reference"]),
}


def load_raw(conn: duckdb.DuckDBPyConnection, raw_dir: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table, (filename, cols) in RAW_TABLES.items():
        # Read everything as text; DuckDB casts to the declared column types (DECIMAL, DATE) on insert.
        df = pd.read_csv(raw_dir / filename, dtype=str, keep_default_na=False)[cols]
        df = df.replace({"": None})
        if "reference" in df:
            df["reference"] = df["reference"].fillna("")
        conn.register("_incoming", df)
        conn.execute(f"DELETE FROM {table}")
        conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) SELECT {', '.join(cols)} FROM _incoming")
        conn.unregister("_incoming")
        counts[table] = len(df)
    return counts
