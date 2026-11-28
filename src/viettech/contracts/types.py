"""Contract loading and strict typed field parsers for raw CSV validation."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "raw"
DEFAULT_CONTRACT_PATH = PROJECT_ROOT / "config" / "data_contracts.json"

INT64_MIN = -(1 << 63)
INT64_MAX = (1 << 63) - 1


def load_contract(contract_path: Optional[Path | str] = None) -> Dict[str, Any]:
    """Load and structurally verify config/data_contracts.json."""
    path = Path(contract_path) if contract_path is not None else DEFAULT_CONTRACT_PATH
    if not path.is_absolute() and not path.exists():
        candidate = PROJECT_ROOT / path
        if candidate.exists():
            path = candidate
    if not path.exists():
        raise FileNotFoundError(f"Data contract file not found: {path}")
    data: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8-sig"))
    if "contract_version" not in data or "tables" not in data:
        raise ValueError(f"Invalid data contract schema in {path}")
    return data


def _parse_int(value: Any) -> Tuple[Optional[int], Optional[str]]:
    if isinstance(value, bool) or not isinstance(value, str):
        return None, f"Invalid integer literal (non-string/bool): {value!r}"
    stripped = value.strip()
    if not stripped:
        return None, "Empty integer value"
    digits = stripped[1:] if stripped[0] in ("+", "-") else stripped
    if not digits or not digits.isascii() or not digits.isdigit():
        return None, f"Invalid integer literal: {value!r}"
    parsed = int(stripped)
    if parsed < INT64_MIN or parsed > INT64_MAX:
        return None, f"Integer overflow (outside signed 64-bit range): {value!r}"
    return parsed, None


def _parse_decimal(value: str) -> Tuple[Optional[Decimal], Optional[str]]:
    stripped = value.strip()
    if not stripped:
        return None, "Empty decimal value"
    try:
        dec = Decimal(stripped)
    except InvalidOperation:
        return None, f"Invalid decimal literal: {value!r}"
    if not dec.is_finite():
        return None, f"Non-finite decimal value: {value!r}"
    return dec, None


def _parse_date(value: str) -> Tuple[Optional[datetime], Optional[str]]:
    stripped = value.strip()
    if len(stripped) != 10:
        return None, f"Expected YYYY-MM-DD date, got {value!r}"
    try:
        dt = datetime.strptime(stripped, "%Y-%m-%d")
        return dt, None
    except ValueError:
        return None, f"Invalid calendar date: {value!r}"


def _parse_timestamp(value: str) -> Tuple[Optional[datetime], Optional[str]]:
    stripped = value.strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S.%f",
    ):
        try:
            return datetime.strptime(stripped, fmt), None
        except ValueError:
            continue
    return None, f"Invalid timestamp literal (expected YYYY-MM-DD HH:MM:SS): {value!r}"


def _parse_bool_int(value: str) -> Tuple[Optional[int], Optional[str]]:
    stripped = value.strip()
    if stripped in ("0", "1"):
        return int(stripped), None
    return None, f"Expected bool_int ('0' or '1'), got {value!r}"


def _parse_typed_value(col_type: str, raw_value: str) -> Tuple[Any, Optional[str], Optional[str]]:
    """Parse a non-empty raw string according to contract type.

    Returns (parsed_value, error_code, error_detail).
    """
    if col_type == "int":
        int_val, err = _parse_int(raw_value)
        return int_val, ("INVALID_INT" if err else None), err
    if col_type == "decimal":
        dec_val, err = _parse_decimal(raw_value)
        return dec_val, ("INVALID_DECIMAL" if err else None), err
    if col_type == "date":
        date_val, err = _parse_date(raw_value)
        return date_val, ("INVALID_DATE" if err else None), err
    if col_type == "timestamp":
        ts_val, err = _parse_timestamp(raw_value)
        return ts_val, ("INVALID_TIMESTAMP" if err else None), err
    if col_type == "bool_int":
        bool_val, err = _parse_bool_int(raw_value)
        return bool_val, ("INVALID_BOOL_INT" if err else None), err
    if col_type == "string":
        return raw_value.strip(), None, None
    return None, "UNSUPPORTED_CONTRACT_TYPE", f"Unsupported column type {col_type!r}"


def _build_natural_key(row: Dict[str, Any], pk_cols: Sequence[str], row_number: int) -> str:
    parts = [str(row.get(col, "") or "").strip() for col in pk_cols]
    if any(parts):
        return "|".join(parts)
    return f"ROW#{row_number}"
