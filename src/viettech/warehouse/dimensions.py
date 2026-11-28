"""Kimball dimension builders including PDPL dim_customer and SCD Type 2 dim_product_scd2."""

from __future__ import annotations

from datetime import date, timedelta
import sqlite3
from typing import Any, Dict, List, Tuple

from viettech.common.utils import sha256_key
from viettech.warehouse.schema import SCD2_EPOCH_START, SCD2_OPEN_END


def build_dim_date(conn: sqlite3.Connection) -> int:
    """Populate dim_date for 2020-01-01..2027-12-31 plus sentinel keys (-1, -2, -3)."""
    start = date(2020, 1, 1)
    end = date(2027, 12, 31)
    rows: List[Tuple[Any, ...]] = []
    cur = start
    while cur <= end:
        bq_dow = ((cur.weekday() + 1) % 7) + 1
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
        counts["dim_customer"] = int(conn.execute("SELECT COUNT(*) FROM dim_customer").fetchone()[0])

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
        counts["dim_product"] = int(conn.execute("SELECT COUNT(*) FROM dim_product").fetchone()[0])

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
        counts["dim_channel"] = int(conn.execute("SELECT COUNT(*) FROM dim_channel").fetchone()[0])

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
        counts["dim_payment"] = int(conn.execute("SELECT COUNT(*) FROM dim_payment").fetchone()[0])

    counts["dim_product_scd2"] = build_dim_product_scd2(conn)
    return counts


def build_dim_product_scd2(conn: sqlite3.Connection) -> int:
    """Reconstruct contiguous [valid_from, valid_to) SCD2 intervals for every product."""
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

        grouped_changes: List[Tuple[str, List[sqlite3.Row]]] = []
        for ev in events:
            ts = str(ev["changed_at"])
            if grouped_changes and grouped_changes[-1][0] == ts:
                grouped_changes[-1][1].append(ev)
            else:
                grouped_changes.append((ts, [ev]))

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
