"""Analytics mining mart DDL and warehouse SQLite connection helper."""

from __future__ import annotations

from pathlib import Path
import sqlite3

DEFAULT_SEED = 42
DEFAULT_SNAPSHOT_DATE = "2025-01-01"
MODEL_VERSION = "logreg_l2_balanced_v1_pure_stdlib"

MINING_MARTS_DDL = """
CREATE TABLE IF NOT EXISTS mart_market_basket_rules (
    rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
    granularity TEXT NOT NULL,
    antecedent TEXT NOT NULL,
    antecedent_label TEXT NOT NULL,
    consequent TEXT NOT NULL,
    consequent_label TEXT NOT NULL,
    co_occurrences INTEGER NOT NULL,
    antecedent_count INTEGER NOT NULL,
    consequent_count INTEGER NOT NULL,
    total_baskets INTEGER NOT NULL,
    support REAL NOT NULL,
    confidence REAL NOT NULL,
    consequent_support REAL NOT NULL,
    lift REAL NOT NULL,
    is_positive_lift INTEGER NOT NULL,
    computed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mart_mba_gran_lift
    ON mart_market_basket_rules(granularity, lift DESC);

CREATE TABLE IF NOT EXISTS mart_rfm_kmeans_clusters (
    snapshot_date TEXT NOT NULL,
    customer_key TEXT NOT NULL REFERENCES dim_customer(customer_key),
    customer_id INTEGER NOT NULL,
    recency_days INTEGER NOT NULL,
    frequency_orders INTEGER NOT NULL,
    monetary_value REAL NOT NULL,
    r_log_z REAL NOT NULL,
    f_log_z REAL NOT NULL,
    m_log_z REAL NOT NULL,
    rfm_segment TEXT NOT NULL,
    kmeans_k INTEGER NOT NULL,
    cluster_id INTEGER NOT NULL,
    cluster_label TEXT NOT NULL,
    distance_to_centroid REAL NOT NULL,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (snapshot_date, customer_key)
);

CREATE INDEX IF NOT EXISTS idx_mart_rfm_kmeans_cluster
    ON mart_rfm_kmeans_clusters(snapshot_date, cluster_id, rfm_segment);

CREATE TABLE IF NOT EXISTS mart_return_risk_scores (
    order_key TEXT PRIMARY KEY REFERENCES fact_orders(order_key),
    order_id INTEGER NOT NULL UNIQUE,
    order_date TEXT NOT NULL,
    customer_key TEXT NOT NULL REFERENCES dim_customer(customer_key),
    actual_returned INTEGER NOT NULL,
    predicted_risk_score REAL NOT NULL,
    risk_decile INTEGER NOT NULL,
    risk_tier TEXT NOT NULL,
    model_version TEXT NOT NULL,
    scored_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mart_return_risk_decile
    ON mart_return_risk_scores(risk_decile, predicted_risk_score DESC);
"""


def open_warehouse_connection(db_path: Path) -> sqlite3.Connection:
    """Open an existing VietTech SQLite warehouse database for analytics mining."""
    if not db_path.exists():
        raise FileNotFoundError(
            f"Warehouse database not found at {db_path}. "
            "Run `python scripts/run_local_warehouse.py --mode full` first."
        )
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    return conn


def ensure_mining_mart_tables(conn: sqlite3.Connection) -> None:
    """Ensure all 3 analytics mining mart tables and their indexes exist."""
    conn.executescript(MINING_MARTS_DDL)
