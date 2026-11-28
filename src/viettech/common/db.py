"""SQLite connection management, introspection, and query-plan inspection."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import List

from viettech.common.utils import mask_customer_name, sha256_key


def open_connection(db_path: Path) -> sqlite3.Connection:
    """Open a configured SQLite warehouse connection with custom UDFs registered."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA cache_size = -32000;")
    conn.create_function("sha256_key", 2, sha256_key)
    conn.create_function("mask_customer_name", 2, mask_customer_name)
    return conn


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """Return True if a SQLite table with the exact name exists."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def explain_query_plan(conn: sqlite3.Connection, sql: str) -> List[str]:
    """Return SQLite EXPLAIN QUERY PLAN detail lines for a SQL query."""
    rows = conn.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall()
    return [str(r["detail"]) for r in rows]
