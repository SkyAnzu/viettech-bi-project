"""Watermark-driven incremental CDC fact loaders and multi-snapshot RFM mart builder."""

from __future__ import annotations

from datetime import date
import sqlite3
from typing import Any, Dict, Optional, Tuple

from viettech.common.db import table_exists
from viettech.common.utils import utc_now_iso
from viettech.warehouse.schema import PIPELINE_NAME


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
    """Load/upsert fact_orders, fact_order_items, and fact_returns (full or incremental CDC)."""
    effective_watermark = watermark
    if mode == "incremental" and not effective_watermark:
        effective_watermark = get_stored_watermark(conn, "fact_orders")
        if not effective_watermark:
            mode = "full"

    with conn:
        if mode == "full":
            if table_exists(conn, "mart_return_risk_scores"):
                conn.execute("DELETE FROM mart_return_risk_scores;")
            conn.execute("DELETE FROM fact_returns;")
            conn.execute("DELETE FROM fact_order_items;")
            conn.execute("DELETE FROM fact_orders;")
            order_where = "WHERE 1 = 1"
            order_params: Tuple[Any, ...] = ()
        else:
            order_where = "WHERE o.updated_at >= ?"
            order_params = (effective_watermark,)

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
        orders_processed = conn.execute(orders_sql, order_params).rowcount

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
        details_processed = conn.execute(items_sql, order_params).rowcount

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
        returns_processed = conn.execute(returns_sql, return_params).rowcount

        max_order_wm = conn.execute("SELECT MAX(updated_at) FROM fact_orders").fetchone()[0]
        max_return_wm = conn.execute("SELECT MAX(returned_at) FROM fact_returns").fetchone()[0]
        if max_order_wm:
            save_watermark(conn, "fact_orders", str(max_order_wm))
            save_watermark(conn, "fact_order_items", str(max_order_wm))
        if max_return_wm:
            save_watermark(conn, "fact_returns", str(max_return_wm))

    return {
        "effective_mode": mode,
        "watermark_used": effective_watermark,
        "orders_processed": orders_processed,
        "details_processed": details_processed,
        "returns_processed": returns_processed,
        "new_order_watermark": max_order_wm,
        "new_return_watermark": max_return_wm,
    }


def build_rfm_snapshot(conn: sqlite3.Connection, snapshot_date: str) -> int:
    """Compute and upsert mart_rfm_snapshot for a given YYYY-MM-DD snapshot_date."""
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

    return int(
        conn.execute(
            "SELECT COUNT(*) FROM mart_rfm_snapshot WHERE snapshot_date = ?",
            (snapshot_date,),
        ).fetchone()[0]
    )
