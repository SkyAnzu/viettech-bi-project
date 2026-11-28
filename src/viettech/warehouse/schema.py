"""SQLite Kimball Star Schema DDL, semantic BI views, and analytical indexes."""

from __future__ import annotations

import sqlite3
from typing import List, Tuple

from viettech.common.db import table_exists

PIPELINE_NAME = "viettech_local_warehouse"
SCD2_EPOCH_START = "2022-12-01 00:00:00"
SCD2_OPEN_END = "9999-12-31 23:59:59"
DEFAULT_SNAPSHOT_DATE = "2025-01-01"

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
    """Create all staging, governance, dimension, fact, RFM mart tables and BI views."""
    conn.executescript(DDL_SCHEMA)
    conn.executescript(BI_VIEWS_DDL)


def create_analytical_indexes(conn: sqlite3.Connection) -> None:
    """Create all analytical covering/lookup indexes and refresh SQLite planner statistics."""
    for _, ddl in ANALYTICAL_INDEXES:
        conn.execute(ddl)
    conn.execute("ANALYZE;")
    conn.commit()


def drop_analytical_indexes(conn: sqlite3.Connection) -> None:
    """Drop all analytical indexes and clear sqlite_stat1 for unindexed benchmarking."""
    for idx_name, _ in ANALYTICAL_INDEXES:
        conn.execute(f"DROP INDEX IF EXISTS {idx_name};")
    if table_exists(conn, "sqlite_stat1"):
        conn.execute("DELETE FROM sqlite_stat1 WHERE tbl IS NOT NULL;")
    conn.commit()
