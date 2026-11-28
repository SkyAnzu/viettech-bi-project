"""Shared pure-stdlib utilities and SQLite database helpers for VietTech BI."""

from __future__ import annotations

from viettech.common.db import explain_query_plan, open_connection, table_exists
from viettech.common.utils import (
    UTF8_BOM,
    date_str_to_key,
    mask_customer_name,
    parse_bool_int,
    parse_optional_text,
    read_csv_rows,
    sha256_key,
    strip_utf8_bom_bytes,
    utc_now_iso,
    utc_now_iso_z,
)

__all__ = [
    "UTF8_BOM",
    "date_str_to_key",
    "explain_query_plan",
    "mask_customer_name",
    "open_connection",
    "parse_bool_int",
    "parse_optional_text",
    "read_csv_rows",
    "sha256_key",
    "strip_utf8_bom_bytes",
    "table_exists",
    "utc_now_iso",
    "utc_now_iso_z",
]
