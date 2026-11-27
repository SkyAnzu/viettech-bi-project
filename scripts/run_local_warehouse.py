"""Zero-Cloud Local Kimball Warehouse, Incremental Watermark Engine & Benchmark Runner.

Builds and validates the complete VietTech Electronics retail star schema locally in
SQLite 3 using PURE Python 3.12 standard library (zero third-party dependencies).

Features:
- Deterministic SHA-256 surrogate keys matching BigQuery SQL conventions.
- Full Kimball star schema: dim_date, dim_customer (PDPL 91/2025/QH15 masked),
  dim_product (Type 1), dim_product_scd2 (Type 2 price history), dim_channel,
  dim_payment, fact_orders, fact_order_items (point-in-time SCD2 join), fact_returns.
- Watermarked incremental CDC upserts (ON CONFLICT DO UPDATE) with audit logging.
- Multi-snapshot RFM analytical mart (mart_rfm_snapshot) and 5 BI views.
- 15-check Automated Data Quality & 3-Way Financial Reconciliation engine.
- Empirical benchmark suite (--benchmark) writing JSON and Markdown reports.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import statistics
import sys
import tempfile
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
import uuid

PIPELINE_NAME = "viettech_local_warehouse"
SCD2_EPOCH_START = "2022-12-01 00:00:00"
SCD2_OPEN_END = "9999-12-31 23:59:59"
DEFAULT_SNAPSHOT_DATE = "2025-01-01"


# ---------------------------------------------------------------------------
# Deterministic Keys & PDPL Masking Helpers
# ---------------------------------------------------------------------------


def sha256_key(prefix: str, natural_key: Any) -> str:
    """Deterministic SHA-256 surrogate key matching BigQuery SQL convention."""
    raw = f"{prefix}|{natural_key}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def mask_customer_name(first_name: Optional[str], last_name: Optional[str]) -> str:
    """PDPL 91/2025/QH15 Privacy-by-Design masking: e.g. 'Lâm', 'Vũ' -> 'L*** Vũ'."""
    f_clean = (first_name or "").strip()
    l_clean = (last_name or "").strip()
    if f_clean and l_clean:
        return f"{f_clean[0]}*** {l_clean}"
    if f_clean:
        return f"{f_clean[0]}***"
    if l_clean:
        return f"*** {l_clean}"
    return "***"


def parse_bool_int(val: Any) -> int:
    """Normalize CSV boolean strings ('1', 'true', 'True') to SQLite 0/1."""
    return 1 if str(val or "").strip().lower() in ("1", "true", "yes") else 0


def parse_optional_text(val: Any) -> Optional[str]:
    text = (val or "").strip() if val is not None else ""
    return text if text else None


def date_str_to_key(date_or_ts: Optional[str]) -> Optional[int]:
    """Extract YYYYMMDD integer date key from 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'."""
    if not date_or_ts:
        return None
    s = date_or_ts.strip()
    if len(s) < 10:
        return None
    return int(s[:4] + s[5:7] + s[8:10])


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def read_csv_rows(csv_path: Path) -> List[Dict[str, str]]:
    """Read CSV stripping UTF-8 BOM cleanly."""
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


# ---------------------------------------------------------------------------
# Database Connection & Schema DDL
# ---------------------------------------------------------------------------


def open_connection(db_path: Path) -> sqlite3.Connection:
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


DDL_SCHEMA = """
-- Staging Tables
CREATE TABLE IF NOT EXISTS stg_sales_channels (
    id INTEGER PRIMARY KEY,
    channel_code TEXT NOT NULL,
    channel_name TEXT NOT NULL,
    channel_type TEXT NOT NULL,
    is_active INTEGER NOT NULL,
    opened_date TEXT
);

CREATE TABLE IF NOT EXISTS stg_product_categories (
    id INTEGER PRIMARY KEY,
    category TEXT NOT NULL,
    sub_category TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stg_products (
    id INTEGER PRIMARY KEY,
    product_code TEXT NOT NULL,
    product_name TEXT NOT NULL,
    category_id INTEGER NOT NULL,
    brand TEXT,
    cost_price REAL NOT NULL,
    msrp REAL NOT NULL,
    quantity_in_stock INTEGER NOT NULL,
    is_active INTEGER NOT NULL,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS stg_customers (
    id INTEGER PRIMARY KEY,
    customer_code TEXT NOT NULL,
    first_name TEXT,
    last_name TEXT,
    email TEXT,
    phone TEXT,
    gender TEXT,
    date_of_birth TEXT,
    city TEXT,
    country TEXT,
    segment TEXT,
    registered_date TEXT,
    is_active INTEGER NOT NULL,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS stg_orders (
    id INTEGER PRIMARY KEY,
    order_code TEXT NOT NULL,
    customer_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    order_date TEXT NOT NULL,
    ship_date TEXT,
    status TEXT NOT NULL,
    payment_method TEXT NOT NULL,
    total_amount REAL NOT NULL,
    total_discount REAL NOT NULL,
    net_amount REAL NOT NULL,
    total_cost REAL NOT NULL,
    note TEXT,
    created_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stg_order_details (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price REAL NOT NULL,
    unit_cost REAL NOT NULL,
    discount_rate REAL NOT NULL,
    discount_amount REAL NOT NULL,
    line_total REAL NOT NULL,
    line_cost REAL NOT NULL,
    line_profit REAL NOT NULL,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS stg_order_returns (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    returned_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stg_product_price_history (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL,
    price_type TEXT NOT NULL,
    old_price REAL NOT NULL,
    new_price REAL NOT NULL,
    changed_at TEXT NOT NULL
);

-- Control & Governance Tables
CREATE TABLE IF NOT EXISTS etl_watermarks (
    pipeline_name TEXT NOT NULL,
    table_name TEXT NOT NULL,
    last_watermark_ts TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (pipeline_name, table_name)
);

CREATE TABLE IF NOT EXISTS etl_run_audit (
    run_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    watermark_used TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    orders_processed INTEGER NOT NULL,
    details_processed INTEGER NOT NULL,
    returns_processed INTEGER NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dq_audit_results (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    check_number INTEGER NOT NULL,
    check_name TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    expected_rule TEXT NOT NULL,
    observed_value TEXT NOT NULL,
    failed_count INTEGER NOT NULL,
    details TEXT,
    checked_at TEXT NOT NULL
);

-- Kimball Dimensions
CREATE TABLE IF NOT EXISTS dim_date (
    date_key INTEGER PRIMARY KEY,
    date TEXT,
    year INTEGER,
    quarter INTEGER,
    month INTEGER,
    week_of_year INTEGER,
    day_of_week INTEGER,
    is_weekend INTEGER,
    is_tet INTEGER,
    is_year_end INTEGER,
    label TEXT NOT NULL
);

-- PDPL 91/2025/QH15 Privacy-by-Design: zero raw PII columns in mart
CREATE TABLE IF NOT EXISTS dim_customer (
    customer_key TEXT PRIMARY KEY,
    customer_id INTEGER NOT NULL UNIQUE,
    customer_code TEXT NOT NULL UNIQUE,
    customer_name_masked TEXT NOT NULL,
    gender TEXT,
    city TEXT,
    country TEXT,
    segment TEXT,
    registered_date TEXT,
    is_active INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_product (
    product_key TEXT PRIMARY KEY,
    product_id INTEGER NOT NULL UNIQUE,
    product_code TEXT NOT NULL UNIQUE,
    product_name TEXT NOT NULL,
    category TEXT,
    sub_category TEXT,
    brand TEXT,
    msrp REAL NOT NULL,
    cost_price REAL NOT NULL,
    is_active INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_product_scd2 (
    product_version_key TEXT PRIMARY KEY,
    product_key TEXT NOT NULL REFERENCES dim_product(product_key),
    product_id INTEGER NOT NULL,
    product_code TEXT NOT NULL,
    product_name TEXT NOT NULL,
    category TEXT,
    sub_category TEXT,
    brand TEXT,
    version_number INTEGER NOT NULL,
    msrp REAL NOT NULL,
    cost_price REAL NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT NOT NULL,
    is_current INTEGER NOT NULL,
    change_reason TEXT NOT NULL,
    UNIQUE (product_id, valid_from)
);

CREATE TABLE IF NOT EXISTS dim_channel (
    channel_key TEXT PRIMARY KEY,
    channel_id INTEGER NOT NULL UNIQUE,
    channel_code TEXT NOT NULL UNIQUE,
    channel_name TEXT NOT NULL,
    channel_type TEXT NOT NULL,
    is_online INTEGER NOT NULL,
    is_active INTEGER NOT NULL,
    opened_date TEXT
);

CREATE TABLE IF NOT EXISTS dim_payment (
    payment_key TEXT PRIMARY KEY,
    payment_method TEXT NOT NULL UNIQUE,
    payment_category TEXT NOT NULL
);

-- Kimball Fact Tables
CREATE TABLE IF NOT EXISTS fact_orders (
    order_key TEXT PRIMARY KEY,
    order_id INTEGER NOT NULL UNIQUE,
    order_code TEXT NOT NULL UNIQUE,
    order_date TEXT NOT NULL,
    ship_date TEXT,
    updated_at TEXT NOT NULL,
    order_date_key INTEGER NOT NULL REFERENCES dim_date(date_key),
    ship_date_key INTEGER NOT NULL REFERENCES dim_date(date_key),
    is_missing_online_ship_date INTEGER NOT NULL,
    customer_key TEXT NOT NULL REFERENCES dim_customer(customer_key),
    channel_key TEXT NOT NULL REFERENCES dim_channel(channel_key),
    payment_key TEXT NOT NULL REFERENCES dim_payment(payment_key),
    status TEXT NOT NULL,
    total_amount REAL NOT NULL,
    total_discount REAL NOT NULL,
    net_amount REAL NOT NULL,
    total_cost REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS fact_order_items (
    order_item_key TEXT PRIMARY KEY,
    order_detail_id INTEGER NOT NULL UNIQUE,
    order_id INTEGER NOT NULL,
    order_key TEXT NOT NULL REFERENCES fact_orders(order_key),
    product_key TEXT NOT NULL REFERENCES dim_product(product_key),
    product_version_key TEXT NOT NULL REFERENCES dim_product_scd2(product_version_key),
    order_date_key INTEGER NOT NULL REFERENCES dim_date(date_key),
    quantity INTEGER NOT NULL,
    unit_price REAL NOT NULL,
    unit_cost REAL NOT NULL,
    discount_rate REAL NOT NULL,
    discount_amount REAL NOT NULL,
    line_total REAL NOT NULL,
    line_cost REAL NOT NULL,
    line_profit REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS fact_returns (
    return_key TEXT PRIMARY KEY,
    return_id INTEGER NOT NULL UNIQUE,
    order_id INTEGER NOT NULL UNIQUE,
    order_key TEXT NOT NULL REFERENCES fact_orders(order_key),
    returned_date_key INTEGER NOT NULL REFERENCES dim_date(date_key),
    returned_date TEXT NOT NULL,
    returned_at TEXT NOT NULL,
    return_lead_days REAL NOT NULL,
    is_temporal_anomaly INTEGER NOT NULL,
    reason TEXT NOT NULL
);

-- Analytical Mart: Multi-Snapshot RFM
CREATE TABLE IF NOT EXISTS mart_rfm_snapshot (
    snapshot_date TEXT NOT NULL,
    customer_key TEXT NOT NULL REFERENCES dim_customer(customer_key),
    customer_id INTEGER NOT NULL,
    recency_days INTEGER NOT NULL,
    frequency_orders INTEGER NOT NULL,
    monetary_value REAL NOT NULL,
    r_score INTEGER NOT NULL,
    f_score INTEGER NOT NULL,
    m_score INTEGER NOT NULL,
    rfm_score TEXT NOT NULL,
    rfm_segment TEXT NOT NULL,
    PRIMARY KEY (snapshot_date, customer_key)
);
"""

ANALYTICAL_INDEXES: List[Tuple[str, str]] = [
    (
        "idx_fact_orders_status_date_cover",
        "CREATE INDEX IF NOT EXISTS idx_fact_orders_status_date_cover "
        "ON fact_orders(order_date_key, status, net_amount, total_cost, total_discount);",
    ),
    (
        "idx_fact_orders_status_channel_date",
        "CREATE INDEX IF NOT EXISTS idx_fact_orders_status_channel_date "
        "ON fact_orders(status, channel_key, order_date, order_date_key, net_amount);",
    ),
    (
        "idx_fact_orders_customer_status",
        "CREATE INDEX IF NOT EXISTS idx_fact_orders_customer_status "
        "ON fact_orders(customer_key, status, order_date_key, net_amount);",
    ),
    (
        "idx_fact_order_items_order_key",
        "CREATE INDEX IF NOT EXISTS idx_fact_order_items_order_key "
        "ON fact_order_items(order_key, product_version_key, order_date_key, quantity, line_total, line_cost, line_profit);",
    ),
    (
        "idx_fact_order_items_version",
        "CREATE INDEX IF NOT EXISTS idx_fact_order_items_version "
        "ON fact_order_items(product_version_key, order_key, order_date_key, quantity, line_total, line_cost, line_profit);",
    ),
    (
        "idx_fact_returns_order_key",
        "CREATE INDEX IF NOT EXISTS idx_fact_returns_order_key "
        "ON fact_returns(order_key, is_temporal_anomaly, return_lead_days);",
    ),
    (
        "idx_dim_product_scd2_lookup",
        "CREATE INDEX IF NOT EXISTS idx_dim_product_scd2_lookup "
        "ON dim_product_scd2(product_id, valid_from, valid_to);",
    ),
    (
        "idx_dim_channel_type",
        "CREATE INDEX IF NOT EXISTS idx_dim_channel_type "
        "ON dim_channel(channel_type, channel_key, channel_name);",
    ),
    (
        "idx_fact_orders_status_order_key",
        "CREATE INDEX IF NOT EXISTS idx_fact_orders_status_order_key "
        "ON fact_orders(status, order_key);",
    ),
    (
        "idx_dim_customer_key_seg",
        "CREATE INDEX IF NOT EXISTS idx_dim_customer_key_seg "
        "ON dim_customer(customer_key, segment);",
    ),
    (
        "idx_mart_rfm_snapshot_seg",
        "CREATE INDEX IF NOT EXISTS idx_mart_rfm_snapshot_seg "
        "ON mart_rfm_snapshot(snapshot_date, rfm_segment, customer_key, monetary_value, frequency_orders, recency_days);",
    ),
    (
        "idx_dim_date_ym",
        "CREATE INDEX IF NOT EXISTS idx_dim_date_ym "
        "ON dim_date(date_key, year, month);",
    ),
    (
        "idx_stg_orders_updated_at",
        "CREATE INDEX IF NOT EXISTS idx_stg_orders_updated_at "
        "ON stg_orders(updated_at, id);",
    ),
    (
        "idx_stg_order_details_order_id",
        "CREATE INDEX IF NOT EXISTS idx_stg_order_details_order_id "
        "ON stg_order_details(order_id);",
    ),
]

BI_VIEWS_DDL = """
DROP VIEW IF EXISTS vw_monthly_kpi;
CREATE VIEW vw_monthly_kpi AS
SELECT
    dd.year AS year,
    dd.month AS month,
    COUNT(*) AS total_orders,
    SUM(CASE WHEN fo.status = 'Completed' THEN 1 ELSE 0 END) AS total_completed_orders,
    ROUND(SUM(CASE WHEN fo.status = 'Completed' THEN fo.net_amount ELSE 0.0 END), 2) AS completed_revenue,
    ROUND(SUM(CASE WHEN fo.status = 'Completed' THEN fo.total_cost ELSE 0.0 END), 2) AS completed_cost,
    ROUND(SUM(CASE WHEN fo.status = 'Completed' THEN fo.net_amount - fo.total_cost ELSE 0.0 END), 2) AS gross_profit,
    CASE
        WHEN SUM(CASE WHEN fo.status = 'Completed' THEN fo.net_amount ELSE 0.0 END) > 0
        THEN ROUND(
            (SUM(CASE WHEN fo.status = 'Completed' THEN fo.net_amount - fo.total_cost ELSE 0.0 END)
             / SUM(CASE WHEN fo.status = 'Completed' THEN fo.net_amount ELSE 0.0 END)) * 100.0,
            2
        )
        ELSE 0.0
    END AS gross_margin_pct,
    ROUND(AVG(CASE WHEN fo.status = 'Completed' THEN fo.net_amount ELSE NULL END), 2) AS avg_completed_order_value,
    ROUND(SUM(CASE WHEN fo.status = 'Completed' THEN fo.total_discount ELSE 0.0 END), 2) AS completed_discount,
    ROUND(SUM(fo.total_discount), 2) AS total_discount
FROM fact_orders fo
JOIN dim_date dd
  ON fo.order_date_key = dd.date_key
GROUP BY dd.year, dd.month;

DROP VIEW IF EXISTS vw_channel_performance;
CREATE VIEW vw_channel_performance AS
SELECT
    dc.channel_code,
    dc.channel_name,
    dc.channel_type,
    dc.is_online,
    COUNT(*) AS total_orders,
    SUM(CASE WHEN fo.status = 'Completed' THEN 1 ELSE 0 END) AS total_completed_orders,
    ROUND(SUM(CASE WHEN fo.status = 'Completed' THEN fo.net_amount ELSE 0.0 END), 2) AS completed_revenue,
    ROUND(SUM(CASE WHEN fo.status = 'Completed' THEN fo.net_amount - fo.total_cost ELSE 0.0 END), 2) AS gross_profit,
    CASE
        WHEN SUM(CASE WHEN fo.status = 'Completed' THEN fo.net_amount ELSE 0.0 END) > 0
        THEN ROUND(
            (SUM(CASE WHEN fo.status = 'Completed' THEN fo.net_amount - fo.total_cost ELSE 0.0 END)
             / SUM(CASE WHEN fo.status = 'Completed' THEN fo.net_amount ELSE 0.0 END)) * 100.0,
            2
        )
        ELSE 0.0
    END AS gross_margin_pct,
    SUM(fo.is_missing_online_ship_date) AS missing_online_ship_date_orders
FROM fact_orders fo
JOIN dim_channel dc
  ON fo.channel_key = dc.channel_key
JOIN dim_date dd
  ON fo.order_date_key = dd.date_key
GROUP BY dc.channel_code, dc.channel_name, dc.channel_type, dc.is_online;

DROP VIEW IF EXISTS vw_return_metrics;
CREATE VIEW vw_return_metrics AS
SELECT
    dd.year AS year,
    dd.month AS month,
    COUNT(*) AS total_returns,
    ROUND(SUM(fo.net_amount), 2) AS total_return_revenue,
    SUM(fr.is_temporal_anomaly) AS temporal_anomaly_count,
    ROUND(
        AVG(CASE WHEN fr.is_temporal_anomaly = 0 THEN fr.return_lead_days ELSE NULL END),
        2
    ) AS avg_return_lead_days
FROM fact_returns fr
JOIN fact_orders fo
  ON fr.order_key = fo.order_key
JOIN dim_date dd
  ON fo.order_date_key = dd.date_key
GROUP BY dd.year, dd.month;

DROP VIEW IF EXISTS vw_product_margin_history;
CREATE VIEW vw_product_margin_history AS
SELECT
    dp2.product_id,
    dp2.product_code,
    dp2.product_name,
    dp2.category,
    dp2.sub_category,
    dp2.brand,
    dp2.version_number,
    dp2.valid_from,
    dp2.valid_to,
    dp2.is_current,
    dp2.msrp AS version_msrp,
    dp2.cost_price AS version_cost_price,
    dd.year AS order_year,
    COUNT(*) AS completed_line_items,
    SUM(foi.quantity) AS units_sold,
    ROUND(SUM(foi.line_total), 2) AS completed_revenue,
    ROUND(SUM(foi.line_cost), 2) AS completed_cost,
    ROUND(SUM(foi.line_profit), 2) AS gross_profit,
    CASE
        WHEN SUM(foi.line_total) > 0
        THEN ROUND((SUM(foi.line_profit) / SUM(foi.line_total)) * 100.0, 2)
        ELSE 0.0
    END AS gross_margin_pct
FROM fact_order_items foi
JOIN fact_orders fo
  ON foi.order_key = fo.order_key
JOIN dim_product_scd2 dp2
  ON foi.product_version_key = dp2.product_version_key
JOIN dim_date dd
  ON foi.order_date_key = dd.date_key
WHERE fo.status = 'Completed'
GROUP BY
    dp2.product_id,
    dp2.product_code,
    dp2.product_name,
    dp2.category,
    dp2.sub_category,
    dp2.brand,
    dp2.version_number,
    dp2.valid_from,
    dp2.valid_to,
    dp2.is_current,
    dp2.msrp,
    dp2.cost_price,
    dd.year;

DROP VIEW IF EXISTS vw_dq_executive_summary;
CREATE VIEW vw_dq_executive_summary AS
WITH latest_run AS (
    SELECT run_id
    FROM dq_audit_results
    ORDER BY audit_id DESC
    LIMIT 1
)
SELECT
    d.run_id,
    COUNT(*) AS total_checks,
    SUM(CASE WHEN d.severity = 'CRITICAL' THEN 1 ELSE 0 END) AS critical_checks,
    SUM(CASE WHEN d.severity = 'CRITICAL' AND d.status = 'PASS' THEN 1 ELSE 0 END) AS critical_passed,
    SUM(CASE WHEN d.severity = 'CRITICAL' AND d.status != 'PASS' THEN 1 ELSE 0 END) AS critical_failed,
    SUM(CASE WHEN d.severity = 'WARNING' THEN 1 ELSE 0 END) AS warning_checks,
    SUM(CASE WHEN d.severity = 'WARNING' AND d.status = 'WARN' THEN 1 ELSE 0 END) AS warning_flagged,
    CASE
        WHEN SUM(CASE WHEN d.severity = 'CRITICAL' AND d.status != 'PASS' THEN 1 ELSE 0 END) = 0
        THEN 'PASS'
        ELSE 'FAIL'
    END AS overall_gate_status,
    MAX(d.checked_at) AS checked_at
FROM dq_audit_results d
JOIN latest_run lr
  ON d.run_id = lr.run_id
GROUP BY d.run_id;
"""


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL_SCHEMA)
    conn.executescript(BI_VIEWS_DDL)


def create_analytical_indexes(conn: sqlite3.Connection) -> None:
    for _, ddl in ANALYTICAL_INDEXES:
        conn.execute(ddl)
    conn.execute("ANALYZE;")
    conn.commit()


def drop_analytical_indexes(conn: sqlite3.Connection) -> None:
    for idx_name, _ in ANALYTICAL_INDEXES:
        conn.execute(f"DROP INDEX IF EXISTS {idx_name};")
    conn.execute("DELETE FROM sqlite_stat1 WHERE tbl IS NOT NULL;") if table_exists(conn, "sqlite_stat1") else None
    conn.commit()


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Staging Layer Loader
# ---------------------------------------------------------------------------


def load_staging_tables(conn: sqlite3.Connection, raw_dir: Path) -> Dict[str, int]:
    """Load all 8 raw CSV files into stg_* tables cleanly (UTF-8 BOM safe)."""
    counts: Dict[str, int] = {}

    with conn:
        for tbl in (
            "stg_order_returns",
            "stg_order_details",
            "stg_orders",
            "stg_product_price_history",
            "stg_products",
            "stg_product_categories",
            "stg_sales_channels",
            "stg_customers",
        ):
            conn.execute(f"DELETE FROM {tbl};")

        # 1. sales_channels
        rows = read_csv_rows(raw_dir / "sales_channels.csv")
        conn.executemany(
            """
            INSERT INTO stg_sales_channels
                (id, channel_code, channel_name, channel_type, is_active, opened_date)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    int(r["id"]),
                    r["channel_code"].strip(),
                    r["channel_name"].strip(),
                    r["channel_type"].strip(),
                    parse_bool_int(r["is_active"]),
                    parse_optional_text(r.get("opened_date")),
                )
                for r in rows
            ],
        )
        counts["stg_sales_channels"] = len(rows)

        # 2. product_categories
        rows = read_csv_rows(raw_dir / "product_categories.csv")
        conn.executemany(
            """
            INSERT INTO stg_product_categories (id, category, sub_category)
            VALUES (?, ?, ?)
            """,
            [
                (int(r["id"]), r["category"].strip(), r["sub_category"].strip())
                for r in rows
            ],
        )
        counts["stg_product_categories"] = len(rows)

        # 3. products
        rows = read_csv_rows(raw_dir / "products.csv")
        conn.executemany(
            """
            INSERT INTO stg_products
                (id, product_code, product_name, category_id, brand,
                 cost_price, msrp, quantity_in_stock, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    int(r["id"]),
                    r["product_code"].strip(),
                    r["product_name"].strip(),
                    int(r["category_id"]),
                    parse_optional_text(r.get("brand")),
                    float(r["cost_price"]),
                    float(r["msrp"]),
                    int(r["quantity_in_stock"]),
                    parse_bool_int(r["is_active"]),
                    parse_optional_text(r.get("created_at")),
                    parse_optional_text(r.get("updated_at")),
                )
                for r in rows
            ],
        )
        counts["stg_products"] = len(rows)

        # 4. customers
        rows = read_csv_rows(raw_dir / "customers.csv")
        conn.executemany(
            """
            INSERT INTO stg_customers
                (id, customer_code, first_name, last_name, email, phone, gender,
                 date_of_birth, city, country, segment, registered_date, is_active,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    int(r["id"]),
                    r["customer_code"].strip(),
                    parse_optional_text(r.get("first_name")),
                    parse_optional_text(r.get("last_name")),
                    parse_optional_text(r.get("email")),
                    parse_optional_text(r.get("phone")),
                    parse_optional_text(r.get("gender")),
                    parse_optional_text(r.get("date_of_birth")),
                    parse_optional_text(r.get("city")),
                    parse_optional_text(r.get("country")),
                    parse_optional_text(r.get("segment")),
                    parse_optional_text(r.get("registered_date")),
                    parse_bool_int(r["is_active"]),
                    parse_optional_text(r.get("created_at")),
                    parse_optional_text(r.get("updated_at")),
                )
                for r in rows
            ],
        )
        counts["stg_customers"] = len(rows)

        # 5. orders
        rows = read_csv_rows(raw_dir / "orders.csv")
        conn.executemany(
            """
            INSERT INTO stg_orders
                (id, order_code, customer_id, channel_id, order_date, ship_date,
                 status, payment_method, total_amount, total_discount, net_amount,
                 total_cost, note, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    int(r["id"]),
                    r["order_code"].strip(),
                    int(r["customer_id"]),
                    int(r["channel_id"]),
                    r["order_date"].strip(),
                    parse_optional_text(r.get("ship_date")),
                    r["status"].strip(),
                    r["payment_method"].strip(),
                    float(r["total_amount"]),
                    float(r["total_discount"]),
                    float(r["net_amount"]),
                    float(r["total_cost"]),
                    parse_optional_text(r.get("note")),
                    parse_optional_text(r.get("created_at")),
                    r["updated_at"].strip(),
                )
                for r in rows
            ],
        )
        counts["stg_orders"] = len(rows)

        # 6. order_details
        rows = read_csv_rows(raw_dir / "order_details.csv")
        conn.executemany(
            """
            INSERT INTO stg_order_details
                (id, order_id, product_id, quantity, unit_price, unit_cost,
                 discount_rate, discount_amount, line_total, line_cost, line_profit, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    int(r["id"]),
                    int(r["order_id"]),
                    int(r["product_id"]),
                    int(r["quantity"]),
                    float(r["unit_price"]),
                    float(r["unit_cost"]),
                    float(r["discount_rate"]),
                    float(r["discount_amount"]),
                    float(r["line_total"]),
                    float(r["line_cost"]),
                    float(r["line_profit"]),
                    parse_optional_text(r.get("created_at")),
                )
                for r in rows
            ],
        )
        counts["stg_order_details"] = len(rows)

        # 7. order_returns
        rows = read_csv_rows(raw_dir / "order_returns.csv")
        conn.executemany(
            """
            INSERT INTO stg_order_returns (id, order_id, reason, returned_at)
            VALUES (?, ?, ?, ?)
            """,
            [
                (
                    int(r["id"]),
                    int(r["order_id"]),
                    r["reason"].strip(),
                    r["returned_at"].strip(),
                )
                for r in rows
            ],
        )
        counts["stg_order_returns"] = len(rows)

        # 8. product_price_history
        rows = read_csv_rows(raw_dir / "product_price_history.csv")
        conn.executemany(
            """
            INSERT INTO stg_product_price_history
                (id, product_id, price_type, old_price, new_price, changed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    int(r["id"]),
                    int(r["product_id"]),
                    r["price_type"].strip().upper(),
                    float(r["old_price"]),
                    float(r["new_price"]),
                    r["changed_at"].strip(),
                )
                for r in rows
            ],
        )
        counts["stg_product_price_history"] = len(rows)

    return counts


# ---------------------------------------------------------------------------
# Dimension Builders (Including SCD Type 2 Reconstruction)
# ---------------------------------------------------------------------------


def build_dim_date(conn: sqlite3.Connection) -> int:
    """Populate dim_date for 2020-01-01..2027-12-31 plus sentinel keys (-1, -2, -3)."""
    start = date(2020, 1, 1)
    end = date(2027, 12, 31)
    rows: List[Tuple[Any, ...]] = []
    cur = start
    while cur <= end:
        # BigQuery DAYOFWEEK convention: Sunday=1, Monday=2, ..., Saturday=7
        bq_dow = ((cur.weekday() + 1) % 7) + 1
        # BigQuery WEEK(SUNDAY) equivalent: 0-based week of year
        week_of_year = int(cur.strftime("%U"))
        date_iso = cur.isoformat()
        rows.append(
            (
                int(cur.strftime("%Y%m%d")),
                date_iso,
                cur.year,
                (cur.month - 1) // 3 + 1,
                cur.month,
                week_of_year,
                bq_dow,
                1 if bq_dow in (1, 7) else 0,
                1 if cur.month in (1, 2) else 0,
                1 if cur.month in (11, 12) else 0,
                date_iso,
            )
        )
        cur += timedelta(days=1)

    sentinels = [
        (-1, None, None, None, None, None, None, None, None, None, "Unknown"),
        (-2, None, None, None, None, None, None, None, None, None, "Not Shipped"),
        (-3, None, None, None, None, None, None, None, None, None, "Not Applicable"),
    ]
    rows.extend(sentinels)

    with conn:
        conn.executemany(
            """
            INSERT INTO dim_date
                (date_key, date, year, quarter, month, week_of_year, day_of_week,
                 is_weekend, is_tet, is_year_end, label)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date_key) DO UPDATE SET
                date = excluded.date,
                year = excluded.year,
                quarter = excluded.quarter,
                month = excluded.month,
                week_of_year = excluded.week_of_year,
                day_of_week = excluded.day_of_week,
                is_weekend = excluded.is_weekend,
                is_tet = excluded.is_tet,
                is_year_end = excluded.is_year_end,
                label = excluded.label
            """,
            rows,
        )
    return len(rows)


def build_dimensions(conn: sqlite3.Connection) -> Dict[str, int]:
    """Upsert all Kimball dimensions (including PDPL dim_customer and SCD2 products)."""
    counts: Dict[str, int] = {}
    counts["dim_date"] = build_dim_date(conn)

    with conn:
        # dim_customer (PDPL 91/2025/QH15 Privacy-by-Design)
        conn.execute(
            """
            INSERT INTO dim_customer (
                customer_key, customer_id, customer_code, customer_name_masked,
                gender, city, country, segment, registered_date, is_active
            )
            SELECT
                sha256_key('customer', CAST(id AS TEXT)),
                id,
                customer_code,
                mask_customer_name(first_name, last_name),
                gender,
                city,
                country,
                segment,
                registered_date,
                is_active
            FROM stg_customers
            WHERE 1 = 1
            ON CONFLICT(customer_key) DO UPDATE SET
                customer_code = excluded.customer_code,
                customer_name_masked = excluded.customer_name_masked,
                gender = excluded.gender,
                city = excluded.city,
                country = excluded.country,
                segment = excluded.segment,
                registered_date = excluded.registered_date,
                is_active = excluded.is_active
            """
        )
        counts["dim_customer"] = conn.execute("SELECT COUNT(*) FROM dim_customer").fetchone()[0]

        # dim_product (Type 1, LEFT JOIN to categories so 0 dropped products)
        conn.execute(
            """
            INSERT INTO dim_product (
                product_key, product_id, product_code, product_name,
                category, sub_category, brand, msrp, cost_price, is_active
            )
            SELECT
                sha256_key('product', CAST(p.id AS TEXT)),
                p.id,
                p.product_code,
                p.product_name,
                COALESCE(c.category, 'Uncategorized'),
                COALESCE(c.sub_category, 'Uncategorized'),
                p.brand,
                p.msrp,
                p.cost_price,
                p.is_active
            FROM stg_products p
            LEFT JOIN stg_product_categories c
              ON p.category_id = c.id
            WHERE 1 = 1
            ON CONFLICT(product_key) DO UPDATE SET
                product_code = excluded.product_code,
                product_name = excluded.product_name,
                category = excluded.category,
                sub_category = excluded.sub_category,
                brand = excluded.brand,
                msrp = excluded.msrp,
                cost_price = excluded.cost_price,
                is_active = excluded.is_active
            """
        )
        counts["dim_product"] = conn.execute("SELECT COUNT(*) FROM dim_product").fetchone()[0]

        # dim_channel
        conn.execute(
            """
            INSERT INTO dim_channel (
                channel_key, channel_id, channel_code, channel_name,
                channel_type, is_online, is_active, opened_date
            )
            SELECT
                sha256_key('channel', CAST(id AS TEXT)),
                id,
                channel_code,
                channel_name,
                channel_type,
                CASE WHEN channel_type IN ('Web', 'App') THEN 1 ELSE 0 END,
                is_active,
                opened_date
            FROM stg_sales_channels
            WHERE 1 = 1
            ON CONFLICT(channel_key) DO UPDATE SET
                channel_code = excluded.channel_code,
                channel_name = excluded.channel_name,
                channel_type = excluded.channel_type,
                is_online = excluded.is_online,
                is_active = excluded.is_active,
                opened_date = excluded.opened_date
            """
        )
        counts["dim_channel"] = conn.execute("SELECT COUNT(*) FROM dim_channel").fetchone()[0]

        # dim_payment
        conn.execute(
            """
            INSERT INTO dim_payment (payment_key, payment_method, payment_category)
            SELECT
                sha256_key('payment', UPPER(TRIM(payment_method))),
                TRIM(payment_method),
                CASE
                    WHEN TRIM(payment_method) = 'Cash' THEN 'Cash'
                    WHEN TRIM(payment_method) = 'Credit Card' THEN 'Card'
                    ELSE 'Digital'
                END
            FROM (
                SELECT DISTINCT TRIM(payment_method) AS payment_method
                FROM stg_orders
            )
            WHERE 1 = 1
            ON CONFLICT(payment_key) DO UPDATE SET
                payment_method = excluded.payment_method,
                payment_category = excluded.payment_category
            """
        )
        counts["dim_payment"] = conn.execute("SELECT COUNT(*) FROM dim_payment").fetchone()[0]

    # dim_product_scd2 (exact historical price interval reconstruction)
    counts["dim_product_scd2"] = build_dim_product_scd2(conn)
    return counts


def build_dim_product_scd2(conn: sqlite3.Connection) -> int:
    """Reconstruct contiguous [valid_from, valid_to) SCD2 intervals for every product.

    products.csv holds the current/final msrp and cost_price, while
    product_price_history.csv holds chronological (price_type, old_price, new_price, changed_at)
    transitions. Starting from the earliest old_price per changed type (or current price
    if unchanged) at 2022-12-01 00:00:00 and stepping forward guarantees:
    - contiguous, non-overlapping [valid_from, valid_to) intervals
    - final is_current = 1 version matches dim_product.msrp and dim_product.cost_price to 0 VND diff.
    """
    products = conn.execute(
        """
        SELECT
            product_key, product_id, product_code, product_name,
            category, sub_category, brand, msrp, cost_price
        FROM dim_product
        ORDER BY product_id
        """
    ).fetchall()

    history_rows = conn.execute(
        """
        SELECT id, product_id, price_type, old_price, new_price, changed_at
        FROM stg_product_price_history
        ORDER BY product_id ASC, changed_at ASC, id ASC
        """
    ).fetchall()

    history_by_product: Dict[int, List[sqlite3.Row]] = {}
    for row in history_rows:
        history_by_product.setdefault(int(row["product_id"]), []).append(row)

    scd2_records: List[Tuple[Any, ...]] = []

    for prod in products:
        pid = int(prod["product_id"])
        events = history_by_product.get(pid, [])

        # Determine initial prices at SCD2_EPOCH_START
        initial_cost = float(prod["cost_price"])
        initial_msrp = float(prod["msrp"])
        for ev in events:
            if ev["price_type"] == "COST":
                initial_cost = float(ev["old_price"])
                break
        for ev in events:
            if ev["price_type"] == "MSRP":
                initial_msrp = float(ev["old_price"])
                break

        # Group events by exact changed_at timestamp in chronological order
        grouped_changes: List[Tuple[str, List[sqlite3.Row]]] = []
        for ev in events:
            ts = ev["changed_at"]
            if grouped_changes and grouped_changes[-1][0] == ts:
                grouped_changes[-1][1].append(ev)
            else:
                grouped_changes.append((ts, [ev]))

        # Build contiguous versions
        versions: List[Dict[str, Any]] = [
            {
                "version_number": 1,
                "msrp": initial_msrp,
                "cost_price": initial_cost,
                "valid_from": SCD2_EPOCH_START,
                "valid_to": SCD2_OPEN_END,
                "is_current": 1,
                "change_reason": "INITIAL",
            }
        ]

        cur_msrp = initial_msrp
        cur_cost = initial_cost

        for ts, ev_list in grouped_changes:
            versions[-1]["valid_to"] = ts
            versions[-1]["is_current"] = 0

            types_changed = set()
            for ev in ev_list:
                if ev["price_type"] == "COST":
                    cur_cost = float(ev["new_price"])
                    types_changed.add("COST")
                elif ev["price_type"] == "MSRP":
                    cur_msrp = float(ev["new_price"])
                    types_changed.add("MSRP")

            if types_changed == {"COST"}:
                reason = "COST_CHANGE"
            elif types_changed == {"MSRP"}:
                reason = "MSRP_CHANGE"
            else:
                reason = "COST_AND_MSRP_CHANGE"

            versions.append(
                {
                    "version_number": len(versions) + 1,
                    "msrp": cur_msrp,
                    "cost_price": cur_cost,
                    "valid_from": ts,
                    "valid_to": SCD2_OPEN_END,
                    "is_current": 1,
                    "change_reason": reason,
                }
            )

        for v in versions:
            version_key = sha256_key("product_scd2", f"{pid}|{v['valid_from']}")
            scd2_records.append(
                (
                    version_key,
                    prod["product_key"],
                    pid,
                    prod["product_code"],
                    prod["product_name"],
                    prod["category"],
                    prod["sub_category"],
                    prod["brand"],
                    v["version_number"],
                    v["msrp"],
                    v["cost_price"],
                    v["valid_from"],
                    v["valid_to"],
                    v["is_current"],
                    v["change_reason"],
                )
            )

    with conn:
        conn.executemany(
            """
            INSERT INTO dim_product_scd2 (
                product_version_key, product_key, product_id, product_code, product_name,
                category, sub_category, brand, version_number, msrp, cost_price,
                valid_from, valid_to, is_current, change_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_version_key) DO UPDATE SET
                product_code = excluded.product_code,
                product_name = excluded.product_name,
                category = excluded.category,
                sub_category = excluded.sub_category,
                brand = excluded.brand,
                version_number = excluded.version_number,
                msrp = excluded.msrp,
                cost_price = excluded.cost_price,
                valid_to = excluded.valid_to,
                is_current = excluded.is_current,
                change_reason = excluded.change_reason
            """,
            scd2_records,
        )

    return len(scd2_records)


# ---------------------------------------------------------------------------
# Fact Loaders (Idempotent Upserts Supporting Full & Watermarked Incremental)
# ---------------------------------------------------------------------------


def get_stored_watermark(conn: sqlite3.Connection, table_name: str) -> Optional[str]:
    row = conn.execute(
        """
        SELECT last_watermark_ts
        FROM etl_watermarks
        WHERE pipeline_name = ? AND table_name = ?
        """,
        (PIPELINE_NAME, table_name),
    ).fetchone()
    return str(row["last_watermark_ts"]) if row else None


def save_watermark(conn: sqlite3.Connection, table_name: str, watermark_ts: str) -> None:
    conn.execute(
        """
        INSERT INTO etl_watermarks (pipeline_name, table_name, last_watermark_ts, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(pipeline_name, table_name) DO UPDATE SET
            last_watermark_ts = excluded.last_watermark_ts,
            updated_at = excluded.updated_at
        """,
        (PIPELINE_NAME, table_name, watermark_ts, utc_now_iso()),
    )


def load_facts(
    conn: sqlite3.Connection,
    mode: str,
    watermark: Optional[str],
) -> Dict[str, Any]:
    """Load/upsert fact_orders, fact_order_items, and fact_returns.

    In 'full' mode: clears facts (for clean full rebuild) and upserts all rows.
    In 'incremental' mode: filters stg_orders by updated_at >= watermark,
    stg_order_details by affected order_ids, and stg_order_returns by
    returned_at >= watermark OR order updated_at >= watermark, then executes
    idempotent ON CONFLICT(...) DO UPDATE upserts.
    """
    effective_watermark = watermark
    if mode == "incremental" and not effective_watermark:
        effective_watermark = get_stored_watermark(conn, "fact_orders")
        if not effective_watermark:
            # No prior watermark recorded -> bootstrap via full load
            mode = "full"

    with conn:
        if mode == "full":
            conn.execute("DELETE FROM fact_returns;")
            conn.execute("DELETE FROM fact_order_items;")
            conn.execute("DELETE FROM fact_orders;")
            order_where = "WHERE 1 = 1"
            order_params: Tuple[Any, ...] = ()
        else:
            order_where = "WHERE o.updated_at >= ?"
            order_params = (effective_watermark,)

        # 1. Upsert fact_orders
        orders_sql = f"""
            INSERT INTO fact_orders (
                order_key, order_id, order_code, order_date, ship_date, updated_at,
                order_date_key, ship_date_key, is_missing_online_ship_date,
                customer_key, channel_key, payment_key,
                status, total_amount, total_discount, net_amount, total_cost
            )
            SELECT
                sha256_key('order', CAST(o.id AS TEXT)) AS order_key,
                o.id AS order_id,
                o.order_code,
                o.order_date,
                o.ship_date,
                o.updated_at,
                CAST(
                    substr(o.order_date, 1, 4)
                    || substr(o.order_date, 6, 2)
                    || substr(o.order_date, 9, 2)
                    AS INTEGER
                ) AS order_date_key,
                CASE
                    WHEN o.ship_date IS NOT NULL AND TRIM(o.ship_date) != ''
                        THEN CAST(
                            substr(o.ship_date, 1, 4)
                            || substr(o.ship_date, 6, 2)
                            || substr(o.ship_date, 9, 2)
                            AS INTEGER
                        )
                    WHEN o.status IN ('Pending', 'Processing') THEN -2
                    WHEN o.status = 'Cancelled' THEN -3
                    WHEN o.status = 'Completed' AND dch.channel_type = 'Physical' THEN -3
                    ELSE -1
                END AS ship_date_key,
                CASE
                    WHEN (o.ship_date IS NULL OR TRIM(o.ship_date) = '')
                         AND o.status = 'Completed'
                         AND dch.channel_type IN ('Web', 'App')
                    THEN 1
                    ELSE 0
                END AS is_missing_online_ship_date,
                dc.customer_key,
                dch.channel_key,
                dp.payment_key,
                o.status,
                o.total_amount,
                o.total_discount,
                o.net_amount,
                o.total_cost
            FROM stg_orders o
            JOIN dim_customer dc
              ON o.customer_id = dc.customer_id
            JOIN dim_channel dch
              ON o.channel_id = dch.channel_id
            JOIN dim_payment dp
              ON sha256_key('payment', UPPER(TRIM(o.payment_method))) = dp.payment_key
            {order_where}
            ON CONFLICT(order_key) DO UPDATE SET
                order_code = excluded.order_code,
                order_date = excluded.order_date,
                ship_date = excluded.ship_date,
                updated_at = excluded.updated_at,
                order_date_key = excluded.order_date_key,
                ship_date_key = excluded.ship_date_key,
                is_missing_online_ship_date = excluded.is_missing_online_ship_date,
                customer_key = excluded.customer_key,
                channel_key = excluded.channel_key,
                payment_key = excluded.payment_key,
                status = excluded.status,
                total_amount = excluded.total_amount,
                total_discount = excluded.total_discount,
                net_amount = excluded.net_amount,
                total_cost = excluded.total_cost
        """
        cur_orders = conn.execute(orders_sql, order_params)
        orders_processed = cur_orders.rowcount

        # 2. Upsert fact_order_items (with point-in-time SCD2 join!)
        items_sql = f"""
            INSERT INTO fact_order_items (
                order_item_key, order_detail_id, order_id, order_key,
                product_key, product_version_key, order_date_key,
                quantity, unit_price, unit_cost, discount_rate,
                discount_amount, line_total, line_cost, line_profit
            )
            SELECT
                sha256_key('order_item', CAST(od.id AS TEXT)) AS order_item_key,
                od.id AS order_detail_id,
                od.order_id,
                fo.order_key,
                dp.product_key,
                dp2.product_version_key,
                fo.order_date_key,
                od.quantity,
                od.unit_price,
                od.unit_cost,
                od.discount_rate,
                od.discount_amount,
                od.line_total,
                od.line_cost,
                od.line_profit
            FROM stg_order_details od
            JOIN stg_orders o
              ON od.order_id = o.id
            JOIN fact_orders fo
              ON od.order_id = fo.order_id
            JOIN dim_product dp
              ON od.product_id = dp.product_id
            JOIN dim_product_scd2 dp2
              ON od.product_id = dp2.product_id
             AND dp2.valid_from <= o.order_date
             AND o.order_date < dp2.valid_to
            {order_where}
            ON CONFLICT(order_item_key) DO UPDATE SET
                order_key = excluded.order_key,
                product_key = excluded.product_key,
                product_version_key = excluded.product_version_key,
                order_date_key = excluded.order_date_key,
                quantity = excluded.quantity,
                unit_price = excluded.unit_price,
                unit_cost = excluded.unit_cost,
                discount_rate = excluded.discount_rate,
                discount_amount = excluded.discount_amount,
                line_total = excluded.line_total,
                line_cost = excluded.line_cost,
                line_profit = excluded.line_profit
        """
        cur_items = conn.execute(items_sql, order_params)
        details_processed = cur_items.rowcount

        # 3. Upsert fact_returns (detecting temporal anomalies where returned_at < order_date)
        if mode == "full":
            return_where = "WHERE 1 = 1"
            return_params: Tuple[Any, ...] = ()
        else:
            return_where = "WHERE r.returned_at >= ? OR o.updated_at >= ?"
            return_params = (effective_watermark, effective_watermark)

        returns_sql = f"""
            INSERT INTO fact_returns (
                return_key, return_id, order_id, order_key,
                returned_date_key, returned_date, returned_at,
                return_lead_days, is_temporal_anomaly, reason
            )
            SELECT
                sha256_key('return', CAST(r.id AS TEXT)) AS return_key,
                r.id AS return_id,
                r.order_id,
                fo.order_key,
                CAST(
                    substr(r.returned_at, 1, 4)
                    || substr(r.returned_at, 6, 2)
                    || substr(r.returned_at, 9, 2)
                    AS INTEGER
                ) AS returned_date_key,
                substr(r.returned_at, 1, 10) AS returned_date,
                r.returned_at,
                ROUND(julianday(r.returned_at) - julianday(o.order_date), 4) AS return_lead_days,
                CASE WHEN r.returned_at < o.order_date THEN 1 ELSE 0 END AS is_temporal_anomaly,
                r.reason
            FROM stg_order_returns r
            JOIN stg_orders o
              ON r.order_id = o.id
            JOIN fact_orders fo
              ON r.order_id = fo.order_id
            {return_where}
            ON CONFLICT(return_key) DO UPDATE SET
                order_key = excluded.order_key,
                returned_date_key = excluded.returned_date_key,
                returned_date = excluded.returned_date,
                returned_at = excluded.returned_at,
                return_lead_days = excluded.return_lead_days,
                is_temporal_anomaly = excluded.is_temporal_anomaly,
                reason = excluded.reason
        """
        cur_returns = conn.execute(returns_sql, return_params)
        returns_processed = cur_returns.rowcount

        # Advance watermarks based on max observed timestamps in facts
        max_order_wm = conn.execute("SELECT MAX(updated_at) FROM fact_orders").fetchone()[0]
        max_return_wm = conn.execute("SELECT MAX(returned_at) FROM fact_returns").fetchone()[0]
        if max_order_wm:
            save_watermark(conn, "fact_orders", max_order_wm)
            save_watermark(conn, "fact_order_items", max_order_wm)
        if max_return_wm:
            save_watermark(conn, "fact_returns", max_return_wm)

    return {
        "effective_mode": mode,
        "watermark_used": effective_watermark,
        "orders_processed": orders_processed,
        "details_processed": details_processed,
        "returns_processed": returns_processed,
        "new_order_watermark": max_order_wm,
        "new_return_watermark": max_return_wm,
    }


# ---------------------------------------------------------------------------
# Analytical Mart: Parameterized Multi-Snapshot RFM
# ---------------------------------------------------------------------------


def build_rfm_snapshot(conn: sqlite3.Connection, snapshot_date: str) -> int:
    """Compute and upsert mart_rfm_snapshot for a given YYYY-MM-DD snapshot_date.

    Filters Completed orders with order_date <= snapshot_date (no future leakage),
    computes NTILE(5) scores matching BigQuery SQL, and preserves prior snapshots.
    """
    # Validate ISO date format
    date.fromisoformat(snapshot_date)

    with conn:
        conn.execute(
            "DELETE FROM mart_rfm_snapshot WHERE snapshot_date = ?",
            (snapshot_date,),
        )
        conn.execute(
            """
            WITH rfm_base AS (
                SELECT
                    :snapshot_date AS snapshot_date,
                    fo.customer_key,
                    CAST(
                        julianday(:snapshot_date) - julianday(MAX(substr(fo.order_date, 1, 10)))
                        AS INTEGER
                    ) AS recency_days,
                    COUNT(*) AS frequency_orders,
                    ROUND(SUM(fo.net_amount), 2) AS monetary_value
                FROM fact_orders fo
                WHERE fo.status = 'Completed'
                  AND substr(fo.order_date, 1, 10) <= :snapshot_date
                GROUP BY fo.customer_key
            ),
            scored AS (
                SELECT
                    snapshot_date,
                    customer_key,
                    recency_days,
                    frequency_orders,
                    monetary_value,
                    NTILE(5) OVER (ORDER BY recency_days DESC, customer_key ASC) AS r_score,
                    NTILE(5) OVER (ORDER BY frequency_orders ASC, customer_key ASC) AS f_score,
                    NTILE(5) OVER (ORDER BY monetary_value ASC, customer_key ASC) AS m_score
                FROM rfm_base
            )
            INSERT INTO mart_rfm_snapshot (
                snapshot_date, customer_key, customer_id,
                recency_days, frequency_orders, monetary_value,
                r_score, f_score, m_score, rfm_score, rfm_segment
            )
            SELECT
                s.snapshot_date,
                s.customer_key,
                dc.customer_id,
                s.recency_days,
                s.frequency_orders,
                s.monetary_value,
                s.r_score,
                s.f_score,
                s.m_score,
                CAST(s.r_score AS TEXT) || CAST(s.f_score AS TEXT) || CAST(s.m_score AS TEXT) AS rfm_score,
                CASE
                    WHEN s.r_score >= 4 AND s.f_score >= 4 AND s.m_score >= 4 THEN 'Champions'
                    WHEN s.r_score >= 3 AND s.f_score >= 3 THEN 'Loyal'
                    WHEN s.r_score <= 2 AND s.f_score >= 3 THEN 'At-risk'
                    WHEN s.r_score <= 2 AND s.f_score <= 2 THEN 'Churned'
                    ELSE 'Regular'
                END AS rfm_segment
            FROM scored s
            JOIN dim_customer dc
              ON s.customer_key = dc.customer_key
            """,
            {"snapshot_date": snapshot_date},
        )

    return conn.execute(
        "SELECT COUNT(*) FROM mart_rfm_snapshot WHERE snapshot_date = ?",
        (snapshot_date,),
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# 15-Check Data Quality & 3-Way Financial Reconciliation Engine
# ---------------------------------------------------------------------------


def run_data_quality_audit(conn: sqlite3.Connection, run_id: str) -> List[Dict[str, Any]]:
    """Run all 15 automated DQ checks (11 CRITICAL + 4 WARNING) and persist into dq_audit_results."""
    checked_at = utc_now_iso()
    results: List[Dict[str, Any]] = []

    def record(
        num: int,
        name: str,
        severity: str,
        expected_rule: str,
        observed_value: str,
        failed_count: int,
        details: str = "",
    ) -> None:
        if severity == "CRITICAL":
            status = "PASS" if failed_count == 0 else "FAIL"
        else:
            status = "PASS" if failed_count == 0 else "WARN"
        results.append(
            {
                "run_id": run_id,
                "check_number": num,
                "check_name": name,
                "severity": severity,
                "status": status,
                "expected_rule": expected_rule,
                "observed_value": observed_value,
                "failed_count": int(failed_count),
                "details": details,
                "checked_at": checked_at,
            }
        )

    # 1. PK uniqueness & NOT NULL across all dims & facts
    pk_tables = [
        ("dim_date", "date_key"),
        ("dim_customer", "customer_key"),
        ("dim_product", "product_key"),
        ("dim_product_scd2", "product_version_key"),
        ("dim_channel", "channel_key"),
        ("dim_payment", "payment_key"),
        ("fact_orders", "order_key"),
        ("fact_order_items", "order_item_key"),
        ("fact_returns", "return_key"),
    ]
    pk_violations = 0
    pk_counts: List[str] = []
    for tbl, col in pk_tables:
        total, distinct_cnt, null_cnt = conn.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT {col}), SUM(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) FROM {tbl}"
        ).fetchone()
        pk_violations += (total - distinct_cnt) + (null_cnt or 0)
        pk_counts.append(f"{tbl}={total}")
    record(
        1,
        "pk_uniqueness_and_not_null",
        "CRITICAL",
        "0 NULL or duplicate PKs across 9 star-schema tables",
        f"violations={pk_violations} ({', '.join(pk_counts)})",
        pk_violations,
    )

    # 2. Zero orphan foreign keys across all facts
    fk_checks = [
        ("fact_orders.customer_key", "SELECT COUNT(*) FROM fact_orders f LEFT JOIN dim_customer d ON f.customer_key = d.customer_key WHERE d.customer_key IS NULL"),
        ("fact_orders.channel_key", "SELECT COUNT(*) FROM fact_orders f LEFT JOIN dim_channel d ON f.channel_key = d.channel_key WHERE d.channel_key IS NULL"),
        ("fact_orders.payment_key", "SELECT COUNT(*) FROM fact_orders f LEFT JOIN dim_payment d ON f.payment_key = d.payment_key WHERE d.payment_key IS NULL"),
        ("fact_orders.order_date_key", "SELECT COUNT(*) FROM fact_orders f LEFT JOIN dim_date d ON f.order_date_key = d.date_key WHERE d.date_key IS NULL"),
        ("fact_orders.ship_date_key", "SELECT COUNT(*) FROM fact_orders f LEFT JOIN dim_date d ON f.ship_date_key = d.date_key WHERE d.date_key IS NULL"),
        ("fact_order_items.order_key", "SELECT COUNT(*) FROM fact_order_items f LEFT JOIN fact_orders d ON f.order_key = d.order_key WHERE d.order_key IS NULL"),
        ("fact_order_items.product_key", "SELECT COUNT(*) FROM fact_order_items f LEFT JOIN dim_product d ON f.product_key = d.product_key WHERE d.product_key IS NULL"),
        ("fact_order_items.product_version_key", "SELECT COUNT(*) FROM fact_order_items f LEFT JOIN dim_product_scd2 d ON f.product_version_key = d.product_version_key WHERE d.product_version_key IS NULL"),
        ("fact_order_items.order_date_key", "SELECT COUNT(*) FROM fact_order_items f LEFT JOIN dim_date d ON f.order_date_key = d.date_key WHERE d.date_key IS NULL"),
        ("fact_returns.order_key", "SELECT COUNT(*) FROM fact_returns f LEFT JOIN fact_orders d ON f.order_key = d.order_key WHERE d.order_key IS NULL"),
        ("fact_returns.returned_date_key", "SELECT COUNT(*) FROM fact_returns f LEFT JOIN dim_date d ON f.returned_date_key = d.date_key WHERE d.date_key IS NULL"),
    ]
    orphan_total = 0
    orphan_details: List[str] = []
    for label, sql in fk_checks:
        cnt = conn.execute(sql).fetchone()[0]
        if cnt:
            orphan_total += cnt
            orphan_details.append(f"{label}={cnt}")
    record(
        2,
        "zero_orphan_foreign_keys",
        "CRITICAL",
        "0 orphan FK references across fact_orders, fact_order_items, fact_returns",
        f"orphans={orphan_total}",
        orphan_total,
        "; ".join(orphan_details) if orphan_details else "All 11 FK paths verified 100% intact",
    )

    # 3. 3-Way Financial Reconciliation: stg_orders vs fact_orders vs fact_order_items
    stg_rev, stg_cost = conn.execute(
        "SELECT COALESCE(SUM(net_amount), 0), COALESCE(SUM(total_cost), 0) FROM stg_orders WHERE status = 'Completed'"
    ).fetchone()
    fo_rev, fo_cost = conn.execute(
        "SELECT COALESCE(SUM(net_amount), 0), COALESCE(SUM(total_cost), 0) FROM fact_orders WHERE status = 'Completed'"
    ).fetchone()
    foi_rev, foi_cost = conn.execute(
        """
        SELECT COALESCE(SUM(foi.line_total), 0), COALESCE(SUM(foi.line_cost), 0)
        FROM fact_order_items foi
        JOIN fact_orders fo ON foi.order_key = fo.order_key
        WHERE fo.status = 'Completed'
        """
    ).fetchone()
    rev_diff_1 = abs(stg_rev - fo_rev)
    rev_diff_2 = abs(fo_rev - foi_rev)
    cost_diff_1 = abs(stg_cost - fo_cost)
    cost_diff_2 = abs(fo_cost - foi_cost)
    max_diff = max(rev_diff_1, rev_diff_2, cost_diff_1, cost_diff_2)
    recon_fail = 0 if max_diff < 0.01 else 1
    record(
        3,
        "three_way_financial_reconciliation",
        "CRITICAL",
        "ABS(stg_orders - fact_orders) < 0.01 AND ABS(fact_orders - fact_order_items) < 0.01",
        f"completed_rev={fo_rev:,.0f} VND, completed_cost={fo_cost:,.0f} VND, max_diff={max_diff:.4f} VND",
        recon_fail,
        f"stg_rev={stg_rev:,.2f} | fact_orders_rev={fo_rev:,.2f} | fact_items_rev={foi_rev:,.2f}",
    )

    # 4. vw_monthly_kpi total completed revenue reconciles 100% to fact_orders
    vw_rev, vw_profit = conn.execute(
        "SELECT COALESCE(SUM(completed_revenue), 0), COALESCE(SUM(gross_profit), 0) FROM vw_monthly_kpi"
    ).fetchone()
    fo_profit = fo_rev - fo_cost
    vw_diff = max(abs(vw_rev - fo_rev), abs(vw_profit - fo_profit))
    record(
        4,
        "vw_monthly_kpi_reconciliation",
        "CRITICAL",
        "vw_monthly_kpi revenue & gross_profit match fact_orders (diff < 0.01 VND)",
        f"vw_rev={vw_rev:,.0f} VND, vw_profit={vw_profit:,.0f} VND, diff={vw_diff:.4f} VND",
        0 if vw_diff < 0.01 else 1,
    )

    # 5. fact_orders arithmetic invariant: net_amount = total_amount - total_discount
    ord_arith_fail = conn.execute(
        "SELECT COUNT(*) FROM fact_orders WHERE ABS(net_amount - (total_amount - total_discount)) >= 0.01"
    ).fetchone()[0]
    record(
        5,
        "fact_orders_arithmetic_invariant",
        "CRITICAL",
        "net_amount = total_amount - total_discount (tol < 0.01 VND)",
        f"mismatched_rows={ord_arith_fail}",
        ord_arith_fail,
    )

    # 6. fact_order_items arithmetic invariant: line_profit = line_total - line_cost
    item_arith_fail = conn.execute(
        "SELECT COUNT(*) FROM fact_order_items WHERE ABS(line_profit - (line_total - line_cost)) >= 0.01"
    ).fetchone()[0]
    record(
        6,
        "fact_order_items_arithmetic_invariant",
        "CRITICAL",
        "line_profit = line_total - line_cost (tol < 0.01 VND)",
        f"mismatched_rows={item_arith_fail}",
        item_arith_fail,
    )

    # 7. dim_product_scd2 interval continuity, zero overlaps & current price match
    bad_bounds = conn.execute(
        "SELECT COUNT(*) FROM dim_product_scd2 WHERE valid_from >= valid_to"
    ).fetchone()[0]
    overlaps_or_gaps = conn.execute(
        """
        WITH ordered AS (
            SELECT
                product_id,
                valid_from,
                valid_to,
                LEAD(valid_from) OVER (PARTITION BY product_id ORDER BY valid_from) AS next_valid_from
            FROM dim_product_scd2
        )
        SELECT COUNT(*)
        FROM ordered
        WHERE next_valid_from IS NOT NULL AND valid_to != next_valid_from
        """
    ).fetchone()[0]
    bad_current_counts = conn.execute(
        """
        SELECT COUNT(*)
        FROM (
            SELECT product_id, SUM(is_current) AS curr_cnt
            FROM dim_product_scd2
            GROUP BY product_id
            HAVING curr_cnt != 1
        )
        """
    ).fetchone()[0]
    price_mismatches = conn.execute(
        """
        SELECT COUNT(*)
        FROM dim_product dp
        LEFT JOIN dim_product_scd2 s
          ON dp.product_id = s.product_id AND s.is_current = 1
        WHERE s.product_version_key IS NULL
           OR ABS(dp.msrp - s.msrp) >= 0.01
           OR ABS(dp.cost_price - s.cost_price) >= 0.01
        """
    ).fetchone()[0]
    scd2_total_versions = conn.execute("SELECT COUNT(*) FROM dim_product_scd2").fetchone()[0]
    scd2_fail = bad_bounds + overlaps_or_gaps + bad_current_counts + price_mismatches
    record(
        7,
        "dim_product_scd2_integrity",
        "CRITICAL",
        "Contiguous non-overlapping SCD2 intervals and is_current=1 matches dim_product 100%",
        f"versions={scd2_total_versions}, violations={scd2_fail}",
        scd2_fail,
        f"bad_bounds={bad_bounds}, gaps_or_overlaps={overlaps_or_gaps}, bad_current={bad_current_counts}, price_mismatch={price_mismatches}",
    )

    # 8. Every fact_order_items row maps to exactly 1 valid point-in-time SCD2 version
    scd2_unmapped = conn.execute(
        """
        SELECT COUNT(*)
        FROM fact_order_items foi
        JOIN fact_orders fo ON foi.order_key = fo.order_key
        LEFT JOIN dim_product_scd2 dp2
          ON foi.product_version_key = dp2.product_version_key
         AND dp2.valid_from <= fo.order_date
         AND fo.order_date < dp2.valid_to
        WHERE dp2.product_version_key IS NULL
        """
    ).fetchone()[0]
    total_items = conn.execute("SELECT COUNT(*) FROM fact_order_items").fetchone()[0]
    record(
        8,
        "fact_order_items_scd2_coverage",
        "CRITICAL",
        "100% of fact_order_items map to valid point-in-time dim_product_scd2 version",
        f"mapped={total_items - scd2_unmapped}/{total_items}, unmapped={scd2_unmapped}",
        scd2_unmapped,
    )

    # 9. dim_customer PDPL privacy check
    forbidden_cols = {"email", "phone", "date_of_birth", "full_name", "first_name", "last_name"}
    actual_cols = {row["name"].lower() for row in conn.execute("PRAGMA table_info(dim_customer)").fetchall()}
    exposed_pii_cols = sorted(forbidden_cols.intersection(actual_cols))
    unmasked_emails = conn.execute(
        "SELECT COUNT(*) FROM dim_customer WHERE customer_name_masked LIKE '%@%'"
    ).fetchone()[0]
    pdpl_fail = len(exposed_pii_cols) + unmasked_emails
    record(
        9,
        "dim_customer_pdpl_privacy",
        "CRITICAL",
        "Zero PII columns (email, phone, date_of_birth, raw name) exposed in dim_customer",
        f"exposed_cols={exposed_pii_cols}, email_leaks={unmasked_emails}",
        pdpl_fail,
        f"Approved mart columns: {sorted(actual_cols)}",
    )

    # 10. mart_rfm_snapshot score bounds & composite PK uniqueness
    rfm_bounds_fail = conn.execute(
        """
        SELECT COUNT(*)
        FROM mart_rfm_snapshot
        WHERE r_score NOT BETWEEN 1 AND 5
           OR f_score NOT BETWEEN 1 AND 5
           OR m_score NOT BETWEEN 1 AND 5
           OR recency_days < 0
        """
    ).fetchone()[0]
    rfm_dups = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT snapshot_date, customer_key, COUNT(*) AS c
            FROM mart_rfm_snapshot
            GROUP BY snapshot_date, customer_key
            HAVING c > 1
        )
        """
    ).fetchone()[0]
    rfm_rows = conn.execute("SELECT COUNT(*) FROM mart_rfm_snapshot").fetchone()[0]
    record(
        10,
        "mart_rfm_snapshot_bounds_and_uniqueness",
        "CRITICAL",
        "r/f/m scores in 1..5, recency_days >= 0, 0 duplicate (snapshot_date, customer_key)",
        f"rows={rfm_rows}, bounds_fail={rfm_bounds_fail}, duplicates={rfm_dups}",
        rfm_bounds_fail + rfm_dups,
    )

    # 11. Return order status integrity
    ret_status_fail = conn.execute(
        """
        SELECT COUNT(*)
        FROM fact_returns fr
        JOIN fact_orders fo ON fr.order_key = fo.order_key
        WHERE fo.status != 'Returned'
        """
    ).fetchone()[0]
    record(
        11,
        "return_order_status_integrity",
        "CRITICAL",
        "Every fact_returns row links to a fact_orders row with status = 'Returned'",
        f"mismatched_status_rows={ret_status_fail}",
        ret_status_fail,
    )

    # 12. WARNING: Temporal return anomalies (returned_at < order_date)
    temporal_rows = conn.execute(
        """
        SELECT fr.return_id, fr.order_id, fo.order_date, fr.returned_at
        FROM fact_returns fr
        JOIN fact_orders fo ON fr.order_key = fo.order_key
        WHERE fr.is_temporal_anomaly = 1
        ORDER BY fr.return_id
        """
    ).fetchall()
    anom_ids = [r["return_id"] for r in temporal_rows]
    record(
        12,
        "temporal_return_anomalies",
        "WARNING",
        "Monitor returns with returned_at < order_date and isolate from avg_return_lead_days",
        f"flagged_rows={len(temporal_rows)} (return_ids={anom_ids})",
        len(temporal_rows),
        "Isolated via is_temporal_anomaly = 1 so vw_return_metrics.avg_return_lead_days remains unskewed",
    )

    # 13. WARNING: Missing online fulfillment ship_date (Web/App Completed orders)
    missing_online_ship = conn.execute(
        "SELECT COUNT(*) FROM fact_orders WHERE is_missing_online_ship_date = 1"
    ).fetchone()[0]
    phys_pickup = conn.execute(
        """
        SELECT COUNT(*)
        FROM fact_orders fo
        JOIN dim_channel dc ON fo.channel_key = dc.channel_key
        WHERE fo.status = 'Completed' AND fo.ship_date IS NULL AND dc.channel_type = 'Physical'
        """
    ).fetchone()[0]
    record(
        13,
        "missing_online_fulfillment_ship_date",
        "WARNING",
        "Monitor Web/App Completed orders missing ship_date (mapped to sentinel -1; Physical pickup mapped to -3)",
        f"online_missing={missing_online_ship}, physical_pickup_sentinels={phys_pickup}",
        missing_online_ship,
        "Distinguishes genuine online logistics gaps (-1) from valid in-store Physical pickup (-3)",
    )

    # 14. WARNING: Missing Physical Shipped ship_date
    missing_phys_shipped = conn.execute(
        """
        SELECT COUNT(*)
        FROM fact_orders fo
        JOIN dim_channel dc ON fo.channel_key = dc.channel_key
        WHERE fo.status = 'Shipped'
          AND dc.channel_type = 'Physical'
          AND (fo.ship_date IS NULL OR TRIM(fo.ship_date) = '')
        """
    ).fetchone()[0]
    record(
        14,
        "missing_physical_shipped_ship_date",
        "WARNING",
        "Monitor Physical channel orders with status='Shipped' but NULL ship_date",
        f"flagged_rows={missing_phys_shipped}",
        missing_phys_shipped,
        "Mapped to ship_date_key = -1 (Unknown) for fulfillment ops follow-up",
    )

    # 15. WARNING: Staging natural key uniqueness (orders.order_code, customers.customer_code, products.product_code)
    dup_ord = conn.execute("SELECT COUNT(*) - COUNT(DISTINCT order_code) FROM stg_orders").fetchone()[0]
    dup_cust = conn.execute("SELECT COUNT(*) - COUNT(DISTINCT customer_code) FROM stg_customers").fetchone()[0]
    dup_prod = conn.execute("SELECT COUNT(*) - COUNT(DISTINCT product_code) FROM stg_products").fetchone()[0]
    dup_total = dup_ord + dup_cust + dup_prod
    record(
        15,
        "staging_natural_key_uniqueness",
        "WARNING",
        "0 duplicate business codes across stg_orders, stg_customers, stg_products",
        f"duplicates={dup_total} (orders={dup_ord}, customers={dup_cust}, products={dup_prod})",
        dup_total,
    )

    with conn:
        conn.executemany(
            """
            INSERT INTO dq_audit_results (
                run_id, check_number, check_name, severity, status,
                expected_rule, observed_value, failed_count, details, checked_at
            )
            VALUES (
                :run_id, :check_number, :check_name, :severity, :status,
                :expected_rule, :observed_value, :failed_count, :details, :checked_at
            )
            """,
            results,
        )

    return results


# ---------------------------------------------------------------------------
# End-to-End Pipeline Orchestrator
# ---------------------------------------------------------------------------


def run_pipeline(
    raw_dir: Path,
    db_path: Path,
    mode: str = "full",
    watermark: Optional[str] = None,
    snapshot_date: str = DEFAULT_SNAPSHOT_DATE,
    quiet: bool = False,
) -> Dict[str, Any]:
    """Execute the full or incremental local warehouse pipeline end-to-end."""
    run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    started_iso = utc_now_iso()
    t0 = time.perf_counter()

    conn = open_connection(db_path)
    try:
        create_schema(conn)

        t_stg = time.perf_counter()
        stg_counts = load_staging_tables(conn, raw_dir)
        stg_ms = (time.perf_counter() - t_stg) * 1000.0

        t_dim = time.perf_counter()
        dim_counts = build_dimensions(conn)
        dim_ms = (time.perf_counter() - t_dim) * 1000.0

        t_fact = time.perf_counter()
        fact_meta = load_facts(conn, mode=mode, watermark=watermark)
        fact_ms = (time.perf_counter() - t_fact) * 1000.0

        t_mart = time.perf_counter()
        rfm_count = build_rfm_snapshot(conn, snapshot_date=snapshot_date)
        create_analytical_indexes(conn)
        mart_ms = (time.perf_counter() - t_mart) * 1000.0

        t_dq = time.perf_counter()
        dq_results = run_data_quality_audit(conn, run_id=run_id)
        dq_ms = (time.perf_counter() - t_dq) * 1000.0

        total_ms = (time.perf_counter() - t0) * 1000.0
        finished_iso = utc_now_iso()

        critical_failed = sum(1 for r in dq_results if r["severity"] == "CRITICAL" and r["status"] != "PASS")
        warning_flagged = sum(1 for r in dq_results if r["severity"] == "WARNING" and r["status"] == "WARN")
        run_status = "SUCCESS" if critical_failed == 0 else "DQ_CRITICAL_FAILURE"

        with conn:
            conn.execute(
                """
                INSERT INTO etl_run_audit (
                    run_id, mode, watermark_used, started_at, finished_at, duration_ms,
                    orders_processed, details_processed, returns_processed, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    fact_meta["effective_mode"],
                    fact_meta["watermark_used"],
                    started_iso,
                    finished_iso,
                    round(total_ms, 2),
                    fact_meta["orders_processed"],
                    fact_meta["details_processed"],
                    fact_meta["returns_processed"],
                    run_status,
                ),
            )

        summary = collect_warehouse_summary(conn, snapshot_date)
        result = {
            "run_id": run_id,
            "mode": fact_meta["effective_mode"],
            "watermark_used": fact_meta["watermark_used"],
            "snapshot_date": snapshot_date,
            "status": run_status,
            "timings_ms": {
                "staging_load": round(stg_ms, 2),
                "dimensions_and_scd2": round(dim_ms, 2),
                "facts_upsert": round(fact_ms, 2),
                "rfm_and_indexes": round(mart_ms, 2),
                "dq_audit": round(dq_ms, 2),
                "total": round(total_ms, 2),
            },
            "staging_counts": stg_counts,
            "dimension_counts": dim_counts,
            "fact_processed": {
                "orders": fact_meta["orders_processed"],
                "order_items": fact_meta["details_processed"],
                "returns": fact_meta["returns_processed"],
            },
            "rfm_rows": rfm_count,
            "dq_summary": {
                "total_checks": len(dq_results),
                "critical_passed": sum(1 for r in dq_results if r["severity"] == "CRITICAL" and r["status"] == "PASS"),
                "critical_failed": critical_failed,
                "warning_flagged": warning_flagged,
            },
            "dq_results": dq_results,
            "warehouse_summary": summary,
        }

        if not quiet:
            print_run_summary(result)

        return result
    finally:
        conn.close()


def collect_warehouse_summary(conn: sqlite3.Connection, snapshot_date: str) -> Dict[str, Any]:
    """Gather verified financial totals, table counts, SCD2 stats, and RFM breakdown."""
    kpi = conn.execute(
        """
        SELECT
            COUNT(*) AS total_orders,
            SUM(CASE WHEN status = 'Completed' THEN 1 ELSE 0 END) AS completed_orders,
            ROUND(SUM(CASE WHEN status = 'Completed' THEN net_amount ELSE 0 END), 2) AS completed_revenue,
            ROUND(SUM(CASE WHEN status = 'Completed' THEN total_cost ELSE 0 END), 2) AS completed_cost,
            ROUND(SUM(CASE WHEN status = 'Completed' THEN net_amount - total_cost ELSE 0 END), 2) AS gross_profit,
            ROUND(SUM(CASE WHEN status = 'Completed' THEN total_discount ELSE 0 END), 2) AS completed_discount
        FROM fact_orders
        """
    ).fetchone()

    ret = conn.execute(
        """
        SELECT
            COUNT(*) AS total_returns,
            ROUND(SUM(fo.net_amount), 2) AS return_revenue,
            SUM(fr.is_temporal_anomaly) AS temporal_anomalies,
            ROUND(AVG(CASE WHEN fr.is_temporal_anomaly = 0 THEN fr.return_lead_days END), 2) AS clean_avg_return_lead_days
        FROM fact_returns fr
        JOIN fact_orders fo ON fr.order_key = fo.order_key
        """
    ).fetchone()

    rfm_rows = conn.execute(
        """
        SELECT
            rfm_segment,
            COUNT(*) AS customers,
            ROUND(AVG(recency_days), 1) AS avg_recency_days,
            ROUND(AVG(frequency_orders), 2) AS avg_frequency,
            ROUND(SUM(monetary_value), 2) AS total_monetary
        FROM mart_rfm_snapshot
        WHERE snapshot_date = ?
        GROUP BY rfm_segment
        ORDER BY total_monetary DESC
        """,
        (snapshot_date,),
    ).fetchall()

    scd2_stats = conn.execute(
        """
        SELECT
            COUNT(*) AS total_versions,
            COUNT(DISTINCT product_id) AS distinct_products,
            SUM(CASE WHEN version_number > 1 THEN 1 ELSE 0 END) AS price_change_versions
        FROM dim_product_scd2
        """
    ).fetchone()

    comp_rev = float(kpi["completed_revenue"] or 0.0)
    gross_profit = float(kpi["gross_profit"] or 0.0)
    margin_pct = round((gross_profit / comp_rev) * 100.0, 2) if comp_rev > 0 else 0.0

    return {
        "total_orders": int(kpi["total_orders"]),
        "completed_orders": int(kpi["completed_orders"]),
        "completed_revenue_vnd": comp_rev,
        "completed_cost_vnd": float(kpi["completed_cost"] or 0.0),
        "gross_profit_vnd": gross_profit,
        "gross_margin_pct": margin_pct,
        "completed_discount_vnd": float(kpi["completed_discount"] or 0.0),
        "total_order_items": int(conn.execute("SELECT COUNT(*) FROM fact_order_items").fetchone()[0]),
        "total_returns": int(ret["total_returns"]),
        "return_revenue_vnd": float(ret["return_revenue"] or 0.0),
        "temporal_return_anomalies": int(ret["temporal_anomalies"] or 0),
        "clean_avg_return_lead_days": float(ret["clean_avg_return_lead_days"] or 0.0),
        "scd2": {
            "total_versions": int(scd2_stats["total_versions"]),
            "distinct_products": int(scd2_stats["distinct_products"]),
            "price_change_versions": int(scd2_stats["price_change_versions"]),
        },
        "rfm_segments": [dict(r) for r in rfm_rows],
    }


def print_run_summary(result: Dict[str, Any]) -> None:
    ws = result["warehouse_summary"]
    dq = result["dq_summary"]
    print(f"\n=== VietTech Local Warehouse Run ({result['run_id']}) ===")
    print(
        f"Mode: {result['mode']} | Watermark: {result['watermark_used'] or 'NONE (Full)'} "
        f"| Snapshot: {result['snapshot_date']} | Status: {result['status']}"
    )
    print(
        f"Processed: orders={result['fact_processed']['orders']:,}, "
        f"items={result['fact_processed']['order_items']:,}, "
        f"returns={result['fact_processed']['returns']:,} "
        f"in {result['timings_ms']['total']:.2f} ms"
    )
    print(
        f"Verified Financials: Completed Orders={ws['completed_orders']:,} | "
        f"Revenue={ws['completed_revenue_vnd']:,.0f} VND | "
        f"Cost={ws['completed_cost_vnd']:,.0f} VND | "
        f"Gross Profit={ws['gross_profit_vnd']:,.0f} VND ({ws['gross_margin_pct']:.2f}%)"
    )
    print(
        f"Returns: {ws['total_returns']:,} rows ({ws['return_revenue_vnd']:,.0f} VND) | "
        f"SCD2 Versions: {ws['scd2']['total_versions']} across {ws['scd2']['distinct_products']} products"
    )
    print(
        f"DQ Gate: {dq['critical_passed']}/{dq['critical_passed'] + dq['critical_failed']} CRITICAL PASSED, "
        f"{dq['warning_flagged']} WARNINGS monitored"
    )


# ---------------------------------------------------------------------------
# Empirical Benchmark Suite (--benchmark)
# ---------------------------------------------------------------------------


BENCHMARK_QUERIES: List[Dict[str, str]] = [
    {
        "id": "Q1_monthly_kpi",
        "title": "Q1: 24-Month Executive KPI Rollup (`vw_monthly_kpi`)",
        "sql": "SELECT * FROM vw_monthly_kpi ORDER BY year, month;",
    },
    {
        "id": "Q2_filtered_channel_orders",
        "title": "Q2: Filtered Completed Web Orders by Date (2024)",
        "sql": (
            "SELECT fo.order_date_key, dc.channel_name, COUNT(*) AS orders, "
            "ROUND(SUM(fo.net_amount), 2) AS revenue "
            "FROM fact_orders fo "
            "JOIN dim_channel dc ON fo.channel_key = dc.channel_key "
            "WHERE fo.status = 'Completed' "
            "  AND dc.channel_type = 'Web' "
            "  AND fo.order_date BETWEEN '2024-01-01 00:00:00' AND '2024-12-31 23:59:59' "
            "GROUP BY fo.order_date_key, dc.channel_name "
            "ORDER BY fo.order_date_key;"
        ),
    },
    {
        "id": "Q3_scd2_product_margin_history",
        "title": "Q3: Historical Product Margin by SCD2 Version (`vw_product_margin_history`)",
        "sql": "SELECT * FROM vw_product_margin_history ORDER BY product_id, version_number, order_year;",
    },
    {
        "id": "Q4_rfm_segment_aggregation",
        "title": "Q4: RFM Segment x Customer Tier Aggregation (`mart_rfm_snapshot`)",
        "sql": (
            "SELECT r.rfm_segment, c.segment AS customer_segment, COUNT(*) AS customers, "
            "ROUND(AVG(r.recency_days), 2) AS avg_recency, "
            "ROUND(AVG(r.frequency_orders), 2) AS avg_frequency, "
            "ROUND(SUM(r.monetary_value), 2) AS total_monetary "
            "FROM mart_rfm_snapshot r "
            "JOIN dim_customer c ON r.customer_key = c.customer_key "
            "WHERE r.snapshot_date = '2025-01-01' "
            "GROUP BY r.rfm_segment, c.segment "
            "ORDER BY total_monetary DESC;"
        ),
    },
]


def percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return ordered[f]
    return ordered[f] + (ordered[c] - ordered[f]) * (k - f)


def explain_query_plan(conn: sqlite3.Connection, sql: str) -> List[str]:
    rows = conn.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall()
    return [str(r["detail"]) for r in rows]


def time_query(conn: sqlite3.Connection, sql: str, runs: int = 50, warmup: int = 5) -> Dict[str, Any]:
    for _ in range(warmup):
        conn.execute(sql).fetchall()

    samples_ms: List[float] = []
    row_count = 0
    for _ in range(runs):
        t0 = time.perf_counter()
        rows = conn.execute(sql).fetchall()
        samples_ms.append((time.perf_counter() - t0) * 1000.0)
        row_count = len(rows)

    return {
        "runs": runs,
        "row_count": row_count,
        "p50_ms": round(percentile(samples_ms, 50), 3),
        "p95_ms": round(percentile(samples_ms, 95), 3),
        "mean_ms": round(statistics.mean(samples_ms), 3),
        "min_ms": round(min(samples_ms), 3),
        "plan": explain_query_plan(conn, sql),
    }


def run_benchmark_suite(
    raw_dir: Path,
    benchmark_dir: Path,
    snapshot_date: str = DEFAULT_SNAPSHOT_DATE,
    quiet: bool = False,
) -> Dict[str, Any]:
    """Run Benchmark A (Full vs Incremental) and Benchmark B (Unindexed vs Indexed) in isolation."""
    benchmark_dir.mkdir(parents=True, exist_ok=True)
    generated_at = utc_now_iso()

    with tempfile.TemporaryDirectory(prefix="viettech_bench_") as tmpdir:
        bench_db = Path(tmpdir) / "benchmark_mart.db"
        conn = open_connection(bench_db)
        try:
            create_schema(conn)
            load_staging_tables(conn, raw_dir)
            build_dimensions(conn)

            # Create staging indexes so incremental lookup benchmarks realistic CDC scans
            conn.execute("CREATE INDEX IF NOT EXISTS idx_stg_orders_updated_at ON stg_orders(updated_at, id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_stg_order_details_order_id ON stg_order_details(order_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dim_product_scd2_lookup ON dim_product_scd2(product_id, valid_from, valid_to);")
            conn.commit()

            # Benchmark A: Full Refresh vs 30-day Incremental vs 1-day Incremental (fact upsert cost)
            def measure_fact_load(mode: str, wm: Optional[str], iterations: int = 5) -> Dict[str, Any]:
                timings: List[float] = []
                last_meta: Dict[str, Any] = {}
                # Warmup once
                last_meta = load_facts(conn, mode=mode, watermark=wm)
                for _ in range(iterations):
                    t0 = time.perf_counter()
                    last_meta = load_facts(conn, mode=mode, watermark=wm)
                    timings.append((time.perf_counter() - t0) * 1000.0)
                return {
                    "mode": mode,
                    "watermark": wm,
                    "iterations": iterations,
                    "orders_processed": last_meta["orders_processed"],
                    "details_processed": last_meta["details_processed"],
                    "returns_processed": last_meta["returns_processed"],
                    "total_fact_rows_processed": (
                        last_meta["orders_processed"]
                        + last_meta["details_processed"]
                        + last_meta["returns_processed"]
                    ),
                    "p50_ms": round(percentile(timings, 50), 2),
                    "p95_ms": round(percentile(timings, 95), 2),
                    "mean_ms": round(statistics.mean(timings), 2),
                }

            full_bench = measure_fact_load("full", None, iterations=5)
            # Ensure full baseline is populated before measuring incremental runs
            load_facts(conn, mode="full", watermark=None)
            inc_30d_bench = measure_fact_load("incremental", "2024-12-02 00:00:00", iterations=5)
            inc_1d_bench = measure_fact_load("incremental", "2024-12-31 00:00:00", iterations=5)

            # Also compute order_date-only counts for transparent comparison in report
            ord_date_30d = conn.execute(
                "SELECT COUNT(*) FROM stg_orders WHERE order_date >= '2024-12-02 00:00:00'"
            ).fetchone()[0]
            ord_date_1d = conn.execute(
                "SELECT COUNT(*) FROM stg_orders WHERE order_date >= '2024-12-31 00:00:00'"
            ).fetchone()[0]

            full_orders = full_bench["orders_processed"]
            full_total_rows = full_bench["total_fact_rows_processed"]
            full_p50 = full_bench["p50_ms"]

            for b, created_cnt in ((inc_30d_bench, ord_date_30d), (inc_1d_bench, ord_date_1d)):
                b["orders_created_in_window"] = int(created_cnt)
                b["late_updated_orders_captured"] = int(b["orders_processed"] - created_cnt)
                b["order_row_reduction_pct"] = round((1.0 - b["orders_processed"] / full_orders) * 100.0, 2)
                b["total_fact_row_reduction_pct"] = round(
                    (1.0 - b["total_fact_rows_processed"] / full_total_rows) * 100.0, 2
                )
                b["speedup_x"] = round(full_p50 / b["p50_ms"], 2) if b["p50_ms"] > 0 else 0.0

            # Final clean state for Benchmark B + DQ Scorecard
            load_facts(conn, mode="full", watermark=None)
            build_rfm_snapshot(conn, snapshot_date=snapshot_date)
            dq_run_id = f"bench_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            dq_results = run_data_quality_audit(conn, run_id=dq_run_id)
            warehouse_summary = collect_warehouse_summary(conn, snapshot_date=snapshot_date)

            # Benchmark B: Unindexed vs Indexed Analytical Queries (50 runs each)
            drop_analytical_indexes(conn)
            unindexed_results: Dict[str, Dict[str, Any]] = {}
            for q in BENCHMARK_QUERIES:
                unindexed_results[q["id"]] = time_query(conn, q["sql"], runs=50, warmup=5)

            create_analytical_indexes(conn)
            indexed_results: Dict[str, Dict[str, Any]] = {}
            for q in BENCHMARK_QUERIES:
                indexed_results[q["id"]] = time_query(conn, q["sql"], runs=50, warmup=5)

            query_comparisons: List[Dict[str, Any]] = []
            for q in BENCHMARK_QUERIES:
                u = unindexed_results[q["id"]]
                i = indexed_results[q["id"]]
                speedup_p50 = round(u["p50_ms"] / i["p50_ms"], 2) if i["p50_ms"] > 0 else 0.0
                speedup_p95 = round(u["p95_ms"] / i["p95_ms"], 2) if i["p95_ms"] > 0 else 0.0
                query_comparisons.append(
                    {
                        "id": q["id"],
                        "title": q["title"],
                        "row_count": i["row_count"],
                        "unindexed": u,
                        "indexed": i,
                        "speedup_p50_x": speedup_p50,
                        "speedup_p95_x": speedup_p95,
                    }
                )
        finally:
            conn.close()

    report_payload = {
        "metadata": {
            "dataset_disclosure": "SYNTHETIC DATASET (VietTech Electronics retail simulation, 2023-01-06 to 2024-12-31)",
            "generated_at_utc": generated_at,
            "python_version": platform.python_version(),
            "sqlite_version": sqlite3.sqlite_version,
            "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "cpu_count": os.cpu_count(),
            "snapshot_date": snapshot_date,
        },
        "warehouse_summary": warehouse_summary,
        "benchmark_a_incremental_watermark": {
            "full_refresh": full_bench,
            "incremental_30d_2024_12_02": inc_30d_bench,
            "incremental_1d_2024_12_31": inc_1d_bench,
        },
        "benchmark_b_query_indexing": query_comparisons,
        "dq_scorecard": dq_results,
    }

    json_path = benchmark_dir / "benchmark_results.json"
    json_path.write_text(json.dumps(report_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    md_path = benchmark_dir / "benchmark_report.md"
    md_path.write_text(render_benchmark_markdown(report_payload), encoding="utf-8")

    if not quiet:
        print(f"\nBenchmark reports written to:\n  - {json_path}\n  - {md_path}")

    return report_payload


def render_benchmark_markdown(payload: Dict[str, Any]) -> str:
    meta = payload["metadata"]
    ws = payload["warehouse_summary"]
    ba = payload["benchmark_a_incremental_watermark"]
    full_b = ba["full_refresh"]
    inc30 = ba["incremental_30d_2024_12_02"]
    inc1 = ba["incremental_1d_2024_12_31"]
    qb = payload["benchmark_b_query_indexing"]
    dq = payload["dq_scorecard"]

    crit_pass = sum(1 for r in dq if r["severity"] == "CRITICAL" and r["status"] == "PASS")
    crit_total = sum(1 for r in dq if r["severity"] == "CRITICAL")
    warn_flagged = sum(1 for r in dq if r["severity"] == "WARNING" and r["status"] == "WARN")

    lines: List[str] = [
        "# VietTech BI Local Warehouse — Empirical Benchmark & Data Quality Report",
        "",
        "> **[SYNTHETIC DATASET DISCLOSURE]** All metrics below are measured empirically on the local",
        "> VietTech Electronics synthetic retail dataset (`21,435` orders, `39,548` order items, `1,288` returns,",
        "> `5,000` customers, `47` products, `24` price history events spanning `2023-01-06` to `2024-12-31`).",
        "> Results reflect local SQLite execution on this workstation and are **not** production BigQuery traffic.",
        "",
        "## 1. Test Environment & Dataset Ground Truth",
        "",
        "| Parameter | Verified Value |",
        "| --- | --- |",
        f"| Generated At (UTC) | `{meta['generated_at_utc']}` |",
        f"| Runtime | Python `{meta['python_version']}` / SQLite `{meta['sqlite_version']}` (pure stdlib, 0 external deps) |",
        f"| Platform | `{meta['os']}` ({meta['cpu_count']} logical CPUs) |",
        f"| RFM Snapshot Date | `{meta['snapshot_date']}` |",
        f"| Total / Completed Orders | `{ws['total_orders']:,}` / `{ws['completed_orders']:,}` |",
        f"| Completed Revenue | `{ws['completed_revenue_vnd']:,.0f}` VND |",
        f"| Completed Cost | `{ws['completed_cost_vnd']:,.0f}` VND |",
        f"| Gross Profit / Margin | `{ws['gross_profit_vnd']:,.0f}` VND (`{ws['gross_margin_pct']:.2f}%`) |",
        f"| Completed Discount | `{ws['completed_discount_vnd']:,.0f}` VND |",
        f"| Returns | `{ws['total_returns']:,}` rows totaling `{ws['return_revenue_vnd']:,.0f}` VND (`{ws['temporal_return_anomalies']}` temporal anomalies isolated; clean avg lead time `{ws['clean_avg_return_lead_days']:.2f}` days) |",
        f"| SCD Type 2 Product Versions | `{ws['scd2']['total_versions']}` total versions across `{ws['scd2']['distinct_products']}` products (`{ws['scd2']['price_change_versions']}` historical price transitions) |",
        "",
        "---",
        "",
        "## 2. Benchmark A — Full Refresh vs. Incremental Watermark CDC (`updated_at`)",
        "",
        "Incremental runs filter `stg_orders` on `updated_at >= watermark` (true CDC so late status transitions",
        "from prior days are never silently dropped) and execute idempotent `ON CONFLICT(...) DO UPDATE` upserts.",
        "",
        "| Scenario | Watermark | Orders Processed | Items Processed | Returns Processed | Order Row Reduction | Total Fact Row Reduction | p50 Time (ms) | p95 Time (ms) | Speedup |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        (
            f"| Full Refresh | `NONE` | `{full_b['orders_processed']:,}` | `{full_b['details_processed']:,}` | "
            f"`{full_b['returns_processed']:,}` | `0.00%` | `0.00%` | `{full_b['p50_ms']:.2f}` | `{full_b['p95_ms']:.2f}` | `1.00x` |"
        ),
        (
            f"| 30-Day Incremental | `{inc30['watermark']}` | `{inc30['orders_processed']:,}` | `{inc30['details_processed']:,}` | "
            f"`{inc30['returns_processed']:,}` | **`{inc30['order_row_reduction_pct']:.2f}%`** | **`{inc30['total_fact_row_reduction_pct']:.2f}%`** | "
            f"`{inc30['p50_ms']:.2f}` | `{inc30['p95_ms']:.2f}` | **`{inc30['speedup_x']:.2f}x`** |"
        ),
        (
            f"| 1-Day Incremental | `{inc1['watermark']}` | `{inc1['orders_processed']:,}` | `{inc1['details_processed']:,}` | "
            f"`{inc1['returns_processed']:,}` | **`{inc1['order_row_reduction_pct']:.2f}%`** | **`{inc1['total_fact_row_reduction_pct']:.2f}%`** | "
            f"`{inc1['p50_ms']:.2f}` | `{inc1['p95_ms']:.2f}` | **`{inc1['speedup_x']:.2f}x`** |"
        ),
        "",
        "**CDC Late-Arriving Update Insight:**",
        f"- In the 30-day window (`>= 2024-12-02`), `{inc30['orders_created_in_window']:,}` orders had `order_date >= watermark` (`{(1 - inc30['orders_created_in_window']/full_b['orders_processed'])*100:.2f}%` reduction), plus **`{inc30['late_updated_orders_captured']}`** earlier orders whose `updated_at` crossed the watermark (status updates / returns). Watermarking on `updated_at` captures 100% of late status mutations.",
        f"- In the 1-day window (`>= 2024-12-31`), `{inc1['orders_created_in_window']:,}` orders were created on Dec 31 plus **`{inc1['late_updated_orders_captured']}`** late-updated orders from Dec 28–30.",
        "",
        "---",
        "",
        "## 3. Benchmark B — Unindexed vs. Indexed Analytical Queries (50 Measured Runs Each)",
        "",
        "| Query | Rows | Unindexed p50 (ms) | Indexed p50 (ms) | p50 Speedup | Unindexed p95 (ms) | Indexed p95 (ms) | p95 Speedup |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for q in qb:
        lines.append(
            f"| {q['title']} | `{q['row_count']}` | `{q['unindexed']['p50_ms']:.3f}` | "
            f"`{q['indexed']['p50_ms']:.3f}` | **`{q['speedup_p50_x']:.2f}x`** | "
            f"`{q['unindexed']['p95_ms']:.3f}` | `{q['indexed']['p95_ms']:.3f}` | **`{q['speedup_p95_x']:.2f}x`** |"
        )

    lines.extend(["", "### `EXPLAIN QUERY PLAN` Comparison", ""])
    for q in qb:
        lines.append(f"#### {q['title']}")
        lines.append("- **Before (Unindexed):**")
        for step in q["unindexed"]["plan"]:
            lines.append(f"  - `{step}`")
        lines.append("- **After (Indexed + `ANALYZE`):**")
        for step in q["indexed"]["plan"]:
            lines.append(f"  - `{step}`")
        lines.append("")

    lines.extend(
        [
            "---",
            "",
            f"## 4. RFM Customer Segmentation Snapshot (`{meta['snapshot_date']}`)",
            "",
            "| RFM Segment | Customers | Avg Recency (Days) | Avg Frequency (Orders) | Total Monetary Value (VND) |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for seg in ws["rfm_segments"]:
        lines.append(
            f"| `{seg['rfm_segment']}` | `{seg['customers']:,}` | `{seg['avg_recency_days']:.1f}` | "
            f"`{seg['avg_frequency']:.2f}` | `{seg['total_monetary']:,.0f}` |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            f"## 5. Automated 15-Check Data Quality & Reconciliation Scorecard (`{crit_pass}/{crit_total}` CRITICAL PASS, `{warn_flagged}` WARNINGS Monitored)",
            "",
            "| # | Check Name | Severity | Status | Observed Result | Failed / Flagged |",
            "| ---: | --- | --- | --- | --- | ---: |",
        ]
    )
    for r in dq:
        lines.append(
            f"| {r['check_number']} | `{r['check_name']}` | `{r['severity']}` | **`{r['status']}`** | "
            f"{r['observed_value']} | `{r['failed_count']}` |"
        )

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="VietTech Zero-Cloud Local Kimball Warehouse & Incremental Watermark Engine"
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=project_root / "data" / "raw",
        help="Path to raw CSV directory (default: data/raw)",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=project_root / "data" / "warehouse" / "viettech_mart.db",
        help="Output SQLite warehouse path (default: data/warehouse/viettech_mart.db)",
    )
    parser.add_argument(
        "--mode",
        choices=["full", "incremental"],
        default="full",
        help="Pipeline load mode: full or incremental (default: full)",
    )
    parser.add_argument(
        "--watermark",
        type=str,
        default=None,
        help="Optional ISO timestamp watermark (e.g. '2024-12-02 00:00:00'); auto-reads etl_watermarks if omitted",
    )
    parser.add_argument(
        "--snapshot-date",
        type=str,
        default=DEFAULT_SNAPSHOT_DATE,
        help="RFM snapshot date in YYYY-MM-DD (default: 2025-01-01)",
    )
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Run full empirical benchmark suite and write benchmarks/benchmark_results.json + benchmark_report.md",
    )
    parser.add_argument(
        "--benchmark-dir",
        type=Path,
        default=project_root / "benchmarks",
        help="Output directory for benchmark reports (default: benchmarks/)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress console summary output",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    result = run_pipeline(
        raw_dir=args.raw_dir,
        db_path=args.db_path,
        mode=args.mode,
        watermark=args.watermark,
        snapshot_date=args.snapshot_date,
        quiet=args.quiet,
    )

    if result["dq_summary"]["critical_failed"] > 0:
        if not args.quiet:
            print("ERROR: Critical Data Quality gate failed!", file=sys.stderr)
        return 2

    if args.benchmark:
        run_benchmark_suite(
            raw_dir=args.raw_dir,
            benchmark_dir=args.benchmark_dir,
            snapshot_date=args.snapshot_date,
            quiet=args.quiet,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
