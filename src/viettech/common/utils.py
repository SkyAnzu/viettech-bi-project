"""Deterministic key hashing, PDPL name masking, CSV/BOM, and date utilities."""

from __future__ import annotations

import csv
from datetime import date, datetime, timezone
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

UTF8_BOM = b"\xef\xbb\xbf"


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
    """Normalize CSV boolean strings ('1', 'true', 'True', 'yes') to SQLite 0/1."""
    if isinstance(val, bool):
        return 1 if val else 0
    return 1 if str(val or "").strip().lower() in ("1", "true", "yes") else 0


def parse_optional_text(val: Any) -> Optional[str]:
    """Strip text and convert empty/blank values to None."""
    text = str(val).strip() if val is not None else ""
    return text if text else None


def date_str_to_key(date_or_ts: Optional[str]) -> Optional[int]:
    """Extract YYYYMMDD integer date key from 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'."""
    if not date_or_ts:
        return None
    s = date_or_ts.strip()
    if len(s) < 10:
        return None
    date_part = s[:10]
    try:
        parsed = date.fromisoformat(date_part)
    except ValueError:
        return None
    return parsed.year * 10000 + parsed.month * 100 + parsed.day


def utc_now_iso() -> str:
    """Return current UTC timestamp formatted as 'YYYY-MM-DD HH:MM:SS'."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def utc_now_iso_z() -> str:
    """Return current UTC timestamp formatted as ISO-8601 'YYYY-MM-DDTHH:MM:SZ'."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_csv_rows(csv_path: Path) -> List[Dict[str, str]]:
    """Read CSV file into row dicts, stripping UTF-8 BOM cleanly."""
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def strip_utf8_bom_bytes(raw_bytes: bytes) -> Tuple[bytes, bool]:
    """Strip UTF-8 BOM if present and return (clean_bytes, had_bom)."""
    if raw_bytes.startswith(UTF8_BOM):
        return raw_bytes[len(UTF8_BOM) :], True
    return raw_bytes, False
