"""Staging layer loader reading raw CSVs (or pre-validated clean rows) into SQLite stg_*."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Dict, List, Optional

from viettech.common.utils import parse_bool_int, parse_optional_text, read_csv_rows

STAGING_TRUNCATE_ORDER = (
    "stg_order_returns",
    "stg_order_details",
    "stg_orders",
    "stg_product_price_history",
    "stg_products",
    "stg_product_categories",
    "stg_sales_channels",
    "stg_customers",
)


def _get_table_rows(
    raw_dir: Path,
    table_name: str,
    clean_tables: Optional[Dict[str, List[Dict[str, str]]]],
) -> List[Dict[str, str]]:
    if clean_tables is not None and table_name in clean_tables:
        return clean_tables[table_name]
    return read_csv_rows(raw_dir / f"{table_name}.csv")


def load_staging_tables(
    conn: sqlite3.Connection,
    raw_dir: Path,
    clean_tables: Optional[Dict[str, List[Dict[str, str]]]] = None,
) -> Dict[str, int]:
    """Load all 8 raw CSV files (or pre-validated clean_tables) into stg_* tables cleanly."""
    counts: Dict[str, int] = {}

    with conn:
        for tbl in STAGING_TRUNCATE_ORDER:
            conn.execute(f"DELETE FROM {tbl};")

        rows = _get_table_rows(raw_dir, "sales_channels", clean_tables)
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

        rows = _get_table_rows(raw_dir, "product_categories", clean_tables)
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

        rows = _get_table_rows(raw_dir, "products", clean_tables)
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

        rows = _get_table_rows(raw_dir, "customers", clean_tables)
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

        rows = _get_table_rows(raw_dir, "orders", clean_tables)
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

        rows = _get_table_rows(raw_dir, "order_details", clean_tables)
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

        rows = _get_table_rows(raw_dir, "order_returns", clean_tables)
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

        rows = _get_table_rows(raw_dir, "product_price_history", clean_tables)
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
