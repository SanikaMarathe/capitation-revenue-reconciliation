"""DuckDB connection and transaction helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

SCHEMA_FILE = Path(__file__).with_name("schema.sql")


def connect(db_path: str | Path = ":memory:") -> duckdb.DuckDBPyConnection:
    """Open a database and make sure all tables exist."""
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(db_path))
    init_schema(conn)
    return conn


def init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))


@contextmanager
def transaction(conn: duckdb.DuckDBPyConnection) -> Iterator[duckdb.DuckDBPyConnection]:
    """All-or-nothing block. Used so a state change and its audit event commit together."""
    conn.execute("BEGIN TRANSACTION")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
