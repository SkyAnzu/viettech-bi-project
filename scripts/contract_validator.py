"""Enterprise Pre-Ingestion Data Contracts, Quarantine Dead-Letter Validator,
Volume-Drift Detector & Lineage Manifest Generator for VietTech Retail BI.

Pure Python 3.12 standard library (zero external packages).

Provides both:
- CLI:
    python scripts/contract_validator.py \
        --raw-dir data/raw \
        --contract config/data_contracts.json \
        --emit-lineage benchmarks/lineage_manifest.json \
        [--db-path data/warehouse/viettech_mart.db]
- Importable Python API:
    load_contract(...)
    validate_table_csv(...)
    validate_raw_directory(...)
    persist_quarantine_records(...)
    build_lineage_manifest(...)
    export_lineage_manifest(...)
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import io
import json
import logging
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
import uuid

LOGGER = logging.getLogger("viettech.contract_validator")

UTF8_BOM = b"\xef\xbb\xbf"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "raw"
DEFAULT_CONTRACT_PATH = PROJECT_ROOT / "config" / "data_contracts.json"

QUARANTINE_DDL = """
CREATE TABLE IF NOT EXISTS dq_quarantine_records (
    quarantine_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    quarantined_at TEXT NOT NULL,
    table_name TEXT NOT NULL,
    row_number INTEGER NOT NULL,
    natural_key TEXT NOT NULL,
    severity TEXT NOT NULL,
    error_code TEXT NOT NULL,
    error_detail TEXT NOT NULL,
    raw_payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dq_quarantine_run_table
    ON dq_quarantine_records(run_id, table_name, severity, error_code);
"""


# ---------------------------------------------------------------------------
# Helpers & Parsers
# ---------------------------------------------------------------------------


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def strip_utf8_bom_bytes(raw_bytes: bytes) -> Tuple[bytes, bool]:
    """Strip UTF-8 BOM if present and return (clean_bytes, had_bom)."""
    if raw_bytes.startswith(UTF8_BOM):
        return raw_bytes[len(UTF8_BOM) :], True
    return raw_bytes, False


def load_contract(contract_path: Optional[Path | str] = None) -> Dict[str, Any]:
    """Load and structurally verify config/data_contracts.json."""
    path = Path(contract_path) if contract_path is not None else DEFAULT_CONTRACT_PATH
    if not path.is_absolute() and not path.exists():
        candidate = PROJECT_ROOT / path
        if candidate.exists():
            path = candidate
    if not path.exists():
        raise FileNotFoundError(f"Data contract file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if "contract_version" not in data or "tables" not in data:
        raise ValueError(f"Invalid data contract schema in {path}")
    return data


def _parse_int(value: str) -> Tuple[Optional[int], Optional[str]]:
    stripped = value.strip()
    if not stripped:
        return None, "Empty integer value"
    # Disallow leading/trailing decimal points or floats like '1.0'
    if stripped[0] in ("+", "-"):
        digits = stripped[1:]
    else:
        digits = stripped
    if not digits.isdigit():
        return None, f"Invalid integer literal: {value!r}"
    return int(stripped), None


def _parse_decimal(value: str) -> Tuple[Optional[Decimal], Optional[str]]:
    stripped = value.strip()
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
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%f"):
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
        val, err = _parse_int(raw_value)
        return val, ("INVALID_INT" if err else None), err
    if col_type == "decimal":
        val, err = _parse_decimal(raw_value)
        return val, ("INVALID_DECIMAL" if err else None), err
    if col_type == "date":
        val, err = _parse_date(raw_value)
        return val, ("INVALID_DATE" if err else None), err
    if col_type == "timestamp":
        val, err = _parse_timestamp(raw_value)
        return val, ("INVALID_TIMESTAMP" if err else None), err
    if col_type == "bool_int":
        val, err = _parse_bool_int(raw_value)
        return val, ("INVALID_BOOL_INT" if err else None), err
    if col_type == "string":
        return raw_value.strip(), None, None
    return None, "UNSUPPORTED_CONTRACT_TYPE", f"Unsupported column type {col_type!r}"


def _build_natural_key(row: Dict[str, Any], pk_cols: Sequence[str], row_number: int) -> str:
    parts = [str(row.get(col, "") or "").strip() for col in pk_cols]
    if any(parts):
        return "|".join(parts)
    return f"ROW#{row_number}"


# ---------------------------------------------------------------------------
# Single-Table Pre-Ingestion Contract Validation & Quarantine Routing
# ---------------------------------------------------------------------------


def validate_table_csv(
    csv_path: Path | str,
    table_name: str,
    contract: Dict[str, Any],
    delimiter: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate a single raw CSV file against its contract definition.

    Performs:
    - UTF-8 BOM stripping
    - Schema drift checks (missing required columns, unexpected extra columns, column order)
    - Per-row type, non-null, min/max numeric bounds, enum membership, and PK/unique key checks
    - Clean vs. quarantined row separation (dead-letter routing without crashing clean rows)
    - Nullable column max_null_ratio checks
    - Volume-drift check against expected_row_count_range
    """
    t0 = time.perf_counter()
    path = Path(csv_path)
    tables_spec = contract.get("tables", {})
    if table_name not in tables_spec:
        raise KeyError(f"Table '{table_name}' is not defined in data contract.")

    table_spec = tables_spec[table_name]
    csv_delim = delimiter or contract.get("sla", {}).get("delimiter", ",")
    col_specs: List[Dict[str, Any]] = table_spec["columns"]
    expected_columns: List[str] = [c["name"] for c in col_specs]
    expected_set: Set[str] = set(expected_columns)
    pk_cols: List[str] = list(table_spec.get("primary_key", ["id"]))
    unique_keys: List[List[str]] = [list(uk) for uk in table_spec.get("unique_keys", [pk_cols])]
    strict_order: bool = bool(table_spec.get("strict_column_order", True))

    schema_errors: List[Dict[str, Any]] = []
    schema_warnings: List[Dict[str, Any]] = []
    null_ratio_alerts: List[Dict[str, Any]] = []
    quarantined_rows: List[Dict[str, Any]] = []
    clean_rows: List[Dict[str, str]] = []
    clean_row_numbers: List[int] = []
    column_null_counts: Dict[str, int] = {col: 0 for col in expected_columns}

    if not path.exists():
        schema_errors.append(
            {
                "table_name": table_name,
                "severity": "CRITICAL",
                "error_code": "MISSING_SOURCE_FILE",
                "detail": f"Expected raw CSV file does not exist: {path}",
            }
        )
        return {
            "table_name": table_name,
            "file_path": str(path),
            "status": "FAIL",
            "had_bom": False,
            "header": [],
            "total_rows": 0,
            "clean_row_count": 0,
            "quarantined_row_count": 0,
            "quarantine_event_count": 0,
            "schema_errors": schema_errors,
            "schema_warnings": schema_warnings,
            "volume_check": {
                "status": "ALERT",
                "severity": "CRITICAL",
                "error_code": "MISSING_SOURCE_FILE",
                "observed_rows": 0,
                "expected_range": table_spec.get("expected_row_count_range"),
                "baseline_row_count": table_spec.get("baseline_row_count"),
            },
            "null_ratio_alerts": null_ratio_alerts,
            "column_null_counts": column_null_counts,
            "clean_rows": clean_rows,
            "clean_row_numbers": clean_row_numbers,
            "quarantined_rows": quarantined_rows,
            "duration_ms": round((time.perf_counter() - t0) * 1000.0, 3),
        }

    raw_bytes = path.read_bytes()
    clean_bytes, had_bom = strip_utf8_bom_bytes(raw_bytes)
    text = clean_bytes.decode("utf-8")
    stream = io.StringIO(text, newline="")
    reader = csv.DictReader(stream, delimiter=csv_delim)
    header: List[str] = list(reader.fieldnames or [])

    if not header:
        schema_errors.append(
            {
                "table_name": table_name,
                "severity": "CRITICAL",
                "error_code": "SCHEMA_EMPTY_FILE",
                "detail": f"CSV file {path.name} is empty or missing a header row.",
            }
        )
    else:
        header_set = set(header)
        missing_cols = [c for c in expected_columns if c not in header_set]
        extra_cols = [c for c in header if c not in expected_set]

        if missing_cols:
            schema_errors.append(
                {
                    "table_name": table_name,
                    "severity": "CRITICAL",
                    "error_code": "SCHEMA_MISSING_COLUMN",
                    "missing_columns": missing_cols,
                    "detail": f"Missing required columns in {table_name}: {missing_cols}",
                }
            )
        if extra_cols:
            schema_errors.append(
                {
                    "table_name": table_name,
                    "severity": "CRITICAL",
                    "error_code": "SCHEMA_EXTRA_COLUMN",
                    "extra_columns": extra_cols,
                    "detail": f"Unexpected extra columns in {table_name}: {extra_cols}",
                }
            )
        if strict_order and not missing_cols and not extra_cols and header != expected_columns:
            schema_warnings.append(
                {
                    "table_name": table_name,
                    "severity": "WARNING",
                    "error_code": "SCHEMA_COLUMN_ORDER_DRIFT",
                    "expected_order": expected_columns,
                    "observed_order": header,
                    "detail": f"Column order in {table_name} differs from contract specification.",
                }
            )

    total_rows = 0
    quarantined_row_numbers: Set[int] = set()

    # Only run row-level validation when no critical schema errors prevent field lookup
    if not schema_errors:
        seen_unique_sets: List[Set[Tuple[str, ...]]] = [set() for _ in unique_keys]

        for row_number, raw_row in enumerate(reader, start=1):
            total_rows += 1
            row_violations: List[Tuple[str, str]] = []
            natural_key = _build_natural_key(raw_row, pk_cols, row_number)

            # Check ragged CSV rows (too many or too few columns)
            if None in raw_row:
                row_violations.append(
                    (
                        "ROW_COLUMN_COUNT_MISMATCH",
                        f"Row has extra unkeyed CSV fields: {raw_row[None]!r}",
                    )
                )
            elif any(raw_row.get(col) is None for col in expected_columns):
                missing_vals = [col for col in expected_columns if raw_row.get(col) is None]
                row_violations.append(
                    (
                        "ROW_COLUMN_COUNT_MISMATCH",
                        f"Row has fewer fields than header; missing values for: {missing_vals}",
                    )
                )

            if not row_violations:
                for spec in col_specs:
                    col_name = spec["name"]
                    col_type = spec["type"]
                    nullable = bool(spec.get("nullable", False))
                    raw_val = (raw_row.get(col_name) or "").strip()

                    if raw_val == "":
                        column_null_counts[col_name] += 1
                        if not nullable:
                            row_violations.append(
                                (
                                    "NULL_VIOLATION",
                                    f"Column '{col_name}' is non-nullable but received empty value.",
                                )
                            )
                        continue

                    parsed_val, err_code, err_detail = _parse_typed_value(col_type, raw_val)
                    if err_code is not None:
                        row_violations.append((err_code, f"Column '{col_name}': {err_detail}"))
                        continue

                    allowed = spec.get("allowed_values")
                    if allowed is not None and raw_val not in allowed:
                        row_violations.append(
                            (
                                "INVALID_ENUM_VALUE",
                                f"Column '{col_name}' value {raw_val!r} not in allowed enum {allowed}.",
                            )
                        )

                    if col_type in ("int", "decimal") and parsed_val is not None:
                        min_v = spec.get("min_value")
                        if min_v is not None:
                            min_cmp = Decimal(str(min_v)) if col_type == "decimal" else int(min_v)
                            if parsed_val < min_cmp:
                                row_violations.append(
                                    (
                                        "MIN_VALUE_VIOLATION",
                                        f"Column '{col_name}' value {parsed_val} < contract min_value {min_v}.",
                                    )
                                )
                        max_v = spec.get("max_value")
                        if max_v is not None:
                            max_cmp = Decimal(str(max_v)) if col_type == "decimal" else int(max_v)
                            if parsed_val > max_cmp:
                                row_violations.append(
                                    (
                                        "MAX_VALUE_VIOLATION",
                                        f"Column '{col_name}' value {parsed_val} > contract max_value {max_v}.",
                                    )
                                )

                # Unique & Primary Key checks
                for idx, uk_cols in enumerate(unique_keys):
                    key_tuple = tuple((raw_row.get(c) or "").strip() for c in uk_cols)
                    if all(part != "" for part in key_tuple):
                        if key_tuple in seen_unique_sets[idx]:
                            is_pk = uk_cols == pk_cols
                            code = "DUPLICATE_PRIMARY_KEY" if is_pk else "DUPLICATE_UNIQUE_KEY"
                            row_violations.append(
                                (
                                    code,
                                    f"Duplicate {'primary' if is_pk else 'unique'} key {uk_cols}={list(key_tuple)}.",
                                )
                            )
                        else:
                            seen_unique_sets[idx].add(key_tuple)

            if row_violations:
                quarantined_row_numbers.add(row_number)
                safe_payload = {str(k): v for k, v in raw_row.items()}
                payload_json = json.dumps(safe_payload, ensure_ascii=False)
                for err_code, err_detail in row_violations:
                    quarantined_rows.append(
                        {
                            "table_name": table_name,
                            "row_number": row_number,
                            "natural_key": natural_key,
                            "severity": "CRITICAL",
                            "error_code": err_code,
                            "error_detail": err_detail,
                            "raw_payload_json": payload_json,
                        }
                    )
            else:
                clean_rows.append({col: (raw_row.get(col) or "").strip() for col in expected_columns})
                clean_row_numbers.append(row_number)

        # Evaluate max_null_ratio for nullable columns
        if total_rows > 0:
            for spec in col_specs:
                col_name = spec["name"]
                if not spec.get("nullable", False):
                    continue
                max_ratio = spec.get("max_null_ratio")
                if max_ratio is None:
                    continue
                observed_ratio = column_null_counts[col_name] / total_rows
                if observed_ratio > float(max_ratio):
                    null_ratio_alerts.append(
                        {
                            "table_name": table_name,
                            "column_name": col_name,
                            "severity": "WARNING",
                            "error_code": "NULL_RATIO_DRIFT",
                            "observed_null_ratio": round(observed_ratio, 6),
                            "max_null_ratio": float(max_ratio),
                            "null_count": column_null_counts[col_name],
                            "total_rows": total_rows,
                            "detail": (
                                f"Column '{table_name}.{col_name}' null ratio "
                                f"{observed_ratio:.4f} exceeds contract limit {float(max_ratio):.4f}."
                            ),
                        }
                    )

    # Volume-drift detection
    expected_range = table_spec.get("expected_row_count_range")
    baseline_rows = table_spec.get("baseline_row_count")
    volume_check: Dict[str, Any] = {
        "status": "PASS",
        "severity": "INFO",
        "error_code": None,
        "observed_rows": total_rows,
        "expected_range": expected_range,
        "baseline_row_count": baseline_rows,
        "drift_from_baseline_pct": (
            round(((total_rows - baseline_rows) / baseline_rows) * 100.0, 2)
            if baseline_rows
            else 0.0
        ),
        "detail": f"Observed {total_rows} rows within expected range {expected_range}.",
    }
    if expected_range and len(expected_range) == 2:
        min_rows, max_rows = int(expected_range[0]), int(expected_range[1])
        if total_rows < min_rows:
            volume_check.update(
                {
                    "status": "ALERT",
                    "severity": "WARNING",
                    "error_code": "VOLUME_DRIFT_BELOW_FLOOR",
                    "direction": "BELOW_FLOOR",
                    "detail": (
                        f"Volume drift in '{table_name}': observed {total_rows} rows "
                        f"< contract floor {min_rows}."
                    ),
                }
            )
        elif total_rows > max_rows:
            volume_check.update(
                {
                    "status": "ALERT",
                    "severity": "WARNING",
                    "error_code": "VOLUME_DRIFT_ABOVE_CEILING",
                    "direction": "ABOVE_CEILING",
                    "detail": (
                        f"Volume drift in '{table_name}': observed {total_rows} rows "
                        f"> contract ceiling {max_rows}."
                    ),
                }
            )

    has_critical = bool(schema_errors) or bool(quarantined_rows)
    has_warnings = bool(schema_warnings) or bool(null_ratio_alerts) or volume_check["status"] == "ALERT"
    if has_critical:
        status = "FAIL"
    elif has_warnings:
        status = "WARN"
    else:
        status = "PASS"

    return {
        "table_name": table_name,
        "file_path": str(path),
        "status": status,
        "had_bom": had_bom,
        "header": header,
        "total_rows": total_rows,
        "clean_row_count": len(clean_rows),
        "quarantined_row_count": len(quarantined_row_numbers),
        "quarantine_event_count": len(quarantined_rows),
        "schema_errors": schema_errors,
        "schema_warnings": schema_warnings,
        "volume_check": volume_check,
        "null_ratio_alerts": null_ratio_alerts,
        "column_null_counts": column_null_counts,
        "clean_rows": clean_rows,
        "clean_row_numbers": clean_row_numbers,
        "quarantined_rows": quarantined_rows,
        "duration_ms": round((time.perf_counter() - t0) * 1000.0, 3),
    }


# ---------------------------------------------------------------------------
# Cross-Table Pre-Ingestion Temporal Checks & SQLite Quarantine Persistence
# ---------------------------------------------------------------------------


def _run_cross_table_temporal_checks(
    table_results: Dict[str, Dict[str, Any]],
    contract: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Evaluate cross-table temporal rules (e.g., order_returns.returned_at < orders.order_date).

    Returns WARNING quarantine candidate records without failing critical schema checks.
    """
    candidates: List[Dict[str, Any]] = []
    for chk in contract.get("cross_table_checks", []):
        left_table = chk["left_table"]
        right_table = chk["right_table"]
        if left_table not in table_results or right_table not in table_results:
            continue
        left_res = table_results[left_table]
        right_res = table_results[right_table]
        if left_res["schema_errors"] or right_res["schema_errors"]:
            continue

        left_key_col = chk["left_join_key"]
        left_ts_col = chk["left_timestamp_col"]
        right_key_col = chk["right_join_key"]
        right_ts_col = chk["right_timestamp_col"]
        severity = chk.get("severity", "WARNING")
        error_code = chk.get("error_code", "TEMPORAL_RETURN_BEFORE_ORDER")
        left_pk_cols = contract["tables"][left_table].get("primary_key", ["id"])

        right_index: Dict[str, Tuple[datetime, str, Dict[str, str]]] = {}
        for row in right_res["clean_rows"]:
            join_val = row.get(right_key_col, "")
            ts_str = row.get(right_ts_col, "")
            parsed_ts, err = _parse_timestamp(ts_str)
            if join_val and parsed_ts is not None and err is None:
                right_index[join_val] = (parsed_ts, ts_str, row)

        left_clean_rows = left_res["clean_rows"]
        left_clean_row_numbers = left_res.get("clean_row_numbers")
        for pos, row in enumerate(left_clean_rows):
            orig_row_number = (
                left_clean_row_numbers[pos]
                if left_clean_row_numbers is not None and pos < len(left_clean_row_numbers)
                else pos + 1
            )
            join_val = row.get(left_key_col, "")
            left_ts_str = row.get(left_ts_col, "")
            left_ts, err = _parse_timestamp(left_ts_str)
            if err is not None or left_ts is None or join_val not in right_index:
                continue
            right_ts, right_ts_str, _ = right_index[join_val]
            if left_ts < right_ts:
                delta_seconds = (left_ts - right_ts).total_seconds()
                delta_days = round(delta_seconds / 86400.0, 2)
                nat_key = _build_natural_key(row, left_pk_cols, orig_row_number)
                candidates.append(
                    {
                        "check_id": chk["check_id"],
                        "table_name": left_table,
                        "row_number": orig_row_number,
                        "natural_key": nat_key,
                        "order_id": join_val,
                        "order_date": right_ts_str,
                        "returned_at": left_ts_str,
                        "lead_time_days": delta_days,
                        "severity": severity,
                        "error_code": error_code,
                        "error_detail": (
                            f"{left_table}.{left_ts_col} ({left_ts_str}) < "
                            f"{right_table}.{right_ts_col} ({right_ts_str}) "
                            f"for {left_key_col}={join_val} (delta={delta_days} days)"
                        ),
                        "raw_payload_json": json.dumps(row, ensure_ascii=False),
                    }
                )
    return candidates


def persist_quarantine_records(
    db_path: Path | str,
    records: Sequence[Dict[str, Any]],
    run_id: Optional[str] = None,
) -> int:
    """Persist quarantined dead-letter records into SQLite `dq_quarantine_records`."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    effective_run_id = run_id or str(uuid.uuid4())
    now_iso = utc_now_iso()

    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(QUARANTINE_DDL)
        with conn:
            conn.executemany(
                """
                INSERT INTO dq_quarantine_records (
                    run_id, quarantined_at, table_name, row_number,
                    natural_key, severity, error_code, error_detail, raw_payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        effective_run_id,
                        now_iso,
                        str(rec["table_name"]),
                        int(rec["row_number"]),
                        str(rec["natural_key"]),
                        str(rec.get("severity", "CRITICAL")),
                        str(rec["error_code"]),
                        str(rec["error_detail"]),
                        str(rec["raw_payload_json"]),
                    )
                    for rec in records
                ],
            )
    finally:
        conn.close()
    return len(records)


def validate_raw_directory(
    raw_dir: Path | str = DEFAULT_RAW_DIR,
    contract_path: Optional[Path | str] = DEFAULT_CONTRACT_PATH,
    db_path: Optional[Path | str] = None,
    tables: Optional[Sequence[str]] = None,
    delimiter: Optional[str] = None,
    include_clean_rows: bool = False,
) -> Dict[str, Any]:
    """Validate all raw CSV tables in a directory against the versioned data contract.

    Args:
        raw_dir: Directory containing the raw CSV files.
        contract_path: Path to config/data_contracts.json.
        db_path: Optional SQLite path to persist quarantined records into dq_quarantine_records.
        tables: Optional subset of table names (defaults to all tables in contract).
        delimiter: Optional CSV delimiter override.
        include_clean_rows: If True, retain parsed clean_rows lists in returned per-table dicts.
    """
    t0 = time.perf_counter()
    run_id = str(uuid.uuid4())
    raw_path = Path(raw_dir)
    if not raw_path.is_absolute() and not raw_path.exists():
        candidate = PROJECT_ROOT / raw_path
        if candidate.exists():
            raw_path = candidate

    contract = load_contract(contract_path)
    target_tables = list(tables) if tables is not None else list(contract["tables"].keys())

    table_results_full: Dict[str, Dict[str, Any]] = {}
    all_critical_quarantine: List[Dict[str, Any]] = []
    volume_alerts: List[Dict[str, Any]] = []
    null_ratio_alerts: List[Dict[str, Any]] = []
    schema_errors: List[Dict[str, Any]] = []

    for table_name in target_tables:
        file_name = contract["tables"][table_name]["file_name"]
        csv_path = raw_path / file_name
        res = validate_table_csv(csv_path, table_name, contract, delimiter=delimiter)
        table_results_full[table_name] = res
        schema_errors.extend(res["schema_errors"])
        all_critical_quarantine.extend(res["quarantined_rows"])
        if res["volume_check"]["status"] == "ALERT":
            volume_alerts.append({"table_name": table_name, **res["volume_check"]})
        null_ratio_alerts.extend(res["null_ratio_alerts"])

    temporal_warnings = _run_cross_table_temporal_checks(table_results_full, contract)

    persisted_quarantine_count = 0
    combined_quarantine_records = all_critical_quarantine + temporal_warnings
    if db_path is not None:
        persisted_quarantine_count = persist_quarantine_records(
            db_path=db_path,
            records=combined_quarantine_records,
            run_id=run_id,
        )

    total_rows = sum(r["total_rows"] for r in table_results_full.values())
    total_clean_rows = sum(r["clean_row_count"] for r in table_results_full.values())
    total_quarantined_rows = sum(r["quarantined_row_count"] for r in table_results_full.values())
    critical_error_count = len(schema_errors) + len(all_critical_quarantine)
    warning_count = (
        len(temporal_warnings)
        + len(volume_alerts)
        + len(null_ratio_alerts)
        + sum(len(r["schema_warnings"]) for r in table_results_full.values())
    )

    overall_status = "FAIL" if critical_error_count > 0 else ("PASS_WITH_WARNINGS" if warning_count > 0 else "PASS")

    # Strip large clean_rows payload unless caller explicitly asked for it
    tables_output: Dict[str, Dict[str, Any]] = {}
    for tname, res in table_results_full.items():
        if include_clean_rows:
            tables_output[tname] = res
        else:
            trimmed = dict(res)
            trimmed.pop("clean_rows", None)
            trimmed.pop("clean_row_numbers", None)
            tables_output[tname] = trimmed

    return {
        "run_id": run_id,
        "checked_at": utc_now_iso(),
        "contract_version": contract["contract_version"],
        "raw_dir": str(raw_path),
        "overall_status": overall_status,
        "critical_gate_passed": critical_error_count == 0,
        "summary": {
            "tables_checked": len(target_tables),
            "total_rows": total_rows,
            "total_clean_rows": total_clean_rows,
            "total_quarantined_rows": total_quarantined_rows,
            "critical_schema_errors": len(schema_errors),
            "critical_row_quarantine_events": len(all_critical_quarantine),
            "critical_error_count": critical_error_count,
            "temporal_warning_count": len(temporal_warnings),
            "volume_drift_alert_count": len(volume_alerts),
            "null_ratio_alert_count": len(null_ratio_alerts),
            "warning_count": warning_count,
            "persisted_quarantine_records": persisted_quarantine_count,
            "duration_ms": round((time.perf_counter() - t0) * 1000.0, 3),
        },
        "schema_errors": schema_errors,
        "volume_alerts": volume_alerts,
        "null_ratio_alerts": null_ratio_alerts,
        "quarantined_records": all_critical_quarantine,
        "temporal_warnings": temporal_warnings,
        "tables": tables_output,
    }


# ---------------------------------------------------------------------------
# End-to-End Lineage Manifest DAG Generator
# ---------------------------------------------------------------------------


def build_lineage_manifest(
    contract: Dict[str, Any],
    validation_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the complete machine-readable data lineage DAG for VietTech Retail BI.

    Topology:
      8 raw CSVs
        -> 8 staging tables
        -> 6 dimensions (incl. dim_product_scd2 & PDPL-compliant dim_customer)
        -> 3 facts (fact_orders, fact_order_items, fact_returns)
        -> mart_rfm_snapshot
        -> 5 BI views + 3 analytics mining marts
        -> 4 Power BI dashboard pages
    """
    compliance = contract.get("compliance", {})
    prohibited_pii: List[str] = list(compliance.get("mart_prohibited_pii_columns", []))
    dim_customer_cols: List[str] = list(compliance.get("dim_customer_allowed_columns", []))
    tables_spec: Dict[str, Any] = contract["tables"]

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []

    def _add_node(
        node_id: str,
        name: str,
        layer: str,
        asset_type: str,
        zone: str,
        pii_classification: str,
        columns: Optional[List[str]] = None,
        pii_restricted_columns: Optional[List[str]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        node: Dict[str, Any] = {
            "id": node_id,
            "name": name,
            "layer": layer,
            "asset_type": asset_type,
            "trust_zone": zone,
            "pii_classification": pii_classification,
            "pii_restricted_columns": pii_restricted_columns or [],
        }
        if columns is not None:
            node["columns"] = columns
        if extra:
            node.update(extra)
        nodes.append(node)

    def _add_edge(
        source: str,
        target: str,
        transformation_type: str,
        pii_tags: List[str],
        description: str,
        pdpl_boundary_crossing: bool = False,
    ) -> None:
        edges.append(
            {
                "edge_id": f"{source}->{target}",
                "source": source,
                "target": target,
                "transformation_type": transformation_type,
                "pii_tags": pii_tags,
                "pdpl_boundary_crossing": pdpl_boundary_crossing,
                "description": description,
            }
        )

    # 1. 8 Raw CSV nodes + 2. 8 Staging table nodes + Raw -> Staging edges
    for table_name, spec in tables_spec.items():
        cols = [c["name"] for c in spec["columns"]]
        pii_cols = [c["name"] for c in spec["columns"] if c.get("pii_classification") == "PII_RESTRICTED"]
        col_pii_map = {c["name"]: c.get("pii_classification", "NONE") for c in spec["columns"]}
        observed_rows = (
            validation_summary["tables"][table_name]["total_rows"]
            if validation_summary and table_name in validation_summary.get("tables", {})
            else spec.get("baseline_row_count")
        )

        raw_id = f"raw.{table_name}"
        stg_name = spec["staging_table"]
        stg_id = f"stg.{stg_name}"

        _add_node(
            node_id=raw_id,
            name=spec["file_name"],
            layer="raw_csv",
            asset_type="csv_snapshot",
            zone="Zone A - Restricted Ingestion",
            pii_classification=spec.get("pii_classification", "NONE"),
            columns=cols,
            pii_restricted_columns=pii_cols,
            extra={
                "source_path": f"data/raw/{spec['file_name']}",
                "owner": spec.get("owner"),
                "row_count": observed_rows,
                "column_pii_tags": col_pii_map,
            },
        )
        _add_node(
            node_id=stg_id,
            name=stg_name,
            layer="staging",
            asset_type="staging_table",
            zone="Zone A - Restricted Staging",
            pii_classification=spec.get("pii_classification", "NONE"),
            columns=cols,
            pii_restricted_columns=pii_cols,
            extra={
                "bigquery_dataset": "retailbi_stg",
                "sqlite_table": stg_name,
                "column_pii_tags": col_pii_map,
            },
        )

        edge_pii_tags = ["PII_RESTRICTED"] if pii_cols else ["PII_FREE"]
        _add_edge(
            source=raw_id,
            target=stg_id,
            transformation_type="csv_bom_strip_and_contract_gate",
            pii_tags=edge_pii_tags,
            description=(
                f"UTF-8 BOM strip, pre-ingestion data contract check, and dead-letter quarantine routing "
                f"from data/raw/{spec['file_name']} into {stg_name}."
            ),
        )

    # 3. 6 Dimension nodes (incl. dim_product_scd2 & PDPL-clean dim_customer)
    _add_node(
        node_id="mart.dim_date",
        name="dim_date",
        layer="dimension",
        asset_type="dimension_table",
        zone="Zone B - Governed Analytics Mart",
        pii_classification="NONE",
        columns=[
            "date_key",
            "date",
            "year",
            "quarter",
            "month",
            "week_of_year",
            "day_of_week",
            "is_weekend",
            "is_tet",
            "is_year_end",
        ],
        pii_restricted_columns=[],
        extra={
            "grain": "1 row per calendar day (2020-2027) + sentinel keys (-1, -2, -3)",
            "seasonality_flags": ["is_tet", "is_year_end"],
        },
    )
    _add_node(
        node_id="mart.dim_customer",
        name="dim_customer",
        layer="dimension",
        asset_type="dimension_table",
        zone="Zone B - Governed Analytics Mart",
        pii_classification="PSEUDONYMIZED",
        columns=dim_customer_cols,
        pii_restricted_columns=[],
        extra={
            "surrogate_key": "SHA-256('customer|' || id)",
            "pdpl_compliance": "91/2025/QH15",
            "prohibited_pii_columns_removed": prohibited_pii,
            "pii_vault_target": "retailbi_pii_vault.customer_pii_vault",
        },
    )
    _add_node(
        node_id="mart.dim_product",
        name="dim_product",
        layer="dimension",
        asset_type="dimension_table",
        zone="Zone B - Governed Analytics Mart",
        pii_classification="NONE",
        columns=[
            "product_key",
            "product_id",
            "product_code",
            "product_name",
            "category",
            "sub_category",
            "brand",
            "msrp",
            "cost_price",
            "is_active",
        ],
        pii_restricted_columns=[],
        extra={"scd_type": "Type 1 (current catalog state)", "surrogate_key": "SHA-256('product|' || id)"},
    )
    _add_node(
        node_id="mart.dim_product_scd2",
        name="dim_product_scd2",
        layer="dimension",
        asset_type="dimension_table_scd2",
        zone="Zone B - Governed Analytics Mart",
        pii_classification="INTERNAL",
        columns=[
            "product_version_key",
            "product_key",
            "product_id",
            "product_code",
            "product_name",
            "category",
            "sub_category",
            "brand",
            "msrp",
            "cost_price",
            "valid_from",
            "valid_to",
            "is_current",
        ],
        pii_restricted_columns=[],
        extra={
            "scd_type": "Type 2 (half-open [valid_from, valid_to) intervals)",
            "baseline_versions": 71,
            "surrogate_key": "SHA-256('product_scd2|' || product_id || '|' || valid_from)",
        },
    )
    _add_node(
        node_id="mart.dim_channel",
        name="dim_channel",
        layer="dimension",
        asset_type="dimension_table",
        zone="Zone B - Governed Analytics Mart",
        pii_classification="NONE",
        columns=[
            "channel_key",
            "channel_id",
            "channel_code",
            "channel_name",
            "channel_type",
            "is_online",
            "is_active",
            "opened_date",
        ],
        pii_restricted_columns=[],
        extra={"surrogate_key": "SHA-256('channel|' || id)"},
    )
    _add_node(
        node_id="mart.dim_payment",
        name="dim_payment",
        layer="dimension",
        asset_type="dimension_table",
        zone="Zone B - Governed Analytics Mart",
        pii_classification="NONE",
        columns=["payment_key", "payment_method", "payment_category"],
        pii_restricted_columns=[],
        extra={"surrogate_key": "SHA-256('payment|' || payment_method)"},
    )

    # Staging -> Dimension edges
    _add_edge(
        source="stg.stg_customers",
        target="mart.dim_customer",
        transformation_type="pdpl_pii_strip_and_sha256_pseudonymization",
        pii_tags=["PII_STRIPPED_PDPL_91_2025_QH15", "PSEUDONYMIZED"],
        pdpl_boundary_crossing=True,
        description=(
            "Removes first_name, last_name, email, phone, date_of_birth at the PDPL 91/2025/QH15 "
            "trust boundary, generates deterministic SHA-256 customer_key, and masks display name."
        ),
    )
    _add_edge(
        source="stg.stg_products",
        target="mart.dim_product",
        transformation_type="scd1_catalog_join",
        pii_tags=["PII_FREE"],
        description="Joins stg_products with category taxonomy into SCD Type 1 dim_product.",
    )
    _add_edge(
        source="stg.stg_product_categories",
        target="mart.dim_product",
        transformation_type="category_hierarchy_enrichment",
        pii_tags=["PII_FREE"],
        description="Denormalizes category and sub_category onto dim_product.",
    )
    _add_edge(
        source="stg.stg_products",
        target="mart.dim_product_scd2",
        transformation_type="scd2_price_history_timeline",
        pii_tags=["PII_FREE"],
        description="Provides base catalog attributes and initial pricing for dim_product_scd2.",
    )
    _add_edge(
        source="stg.stg_product_categories",
        target="mart.dim_product_scd2",
        transformation_type="category_hierarchy_enrichment",
        pii_tags=["PII_FREE"],
        description="Denormalizes category and sub_category onto every SCD Type 2 version.",
    )
    _add_edge(
        source="stg.stg_product_price_history",
        target="mart.dim_product_scd2",
        transformation_type="scd2_price_history_timeline",
        pii_tags=["PII_FREE"],
        description="Applies 24 MSRP/COST price transitions across 14 SKUs to build 71 contiguous SCD2 intervals.",
    )
    _add_edge(
        source="stg.stg_sales_channels",
        target="mart.dim_channel",
        transformation_type="sha256_dimension_upsert",
        pii_tags=["PII_FREE"],
        description="Derives channel_key and is_online flag (Web/App vs Physical stores).",
    )
    _add_edge(
        source="stg.stg_orders",
        target="mart.dim_payment",
        transformation_type="distinct_payment_extraction",
        pii_tags=["PII_FREE"],
        description="Extracts conformed payment_method dimension with Cash/Card/Digital category.",
    )
    _add_edge(
        source="stg.stg_orders",
        target="mart.dim_date",
        transformation_type="calendar_date_coverage_validation",
        pii_tags=["PII_FREE"],
        description="Ensures generated calendar dimension covers all order_date/ship_date values + sentinels (-1/-2/-3).",
    )

    # 4. 3 Fact nodes
    _add_node(
        node_id="mart.fact_orders",
        name="fact_orders",
        layer="fact",
        asset_type="fact_table",
        zone="Zone B - Governed Analytics Mart",
        pii_classification="INTERNAL",
        columns=[
            "order_key",
            "order_id",
            "order_code",
            "order_date",
            "updated_at",
            "order_date_key",
            "ship_date_key",
            "is_missing_online_ship_date",
            "customer_key",
            "channel_key",
            "payment_key",
            "status",
            "total_amount",
            "total_discount",
            "net_amount",
            "total_cost",
        ],
        pii_restricted_columns=[],
        extra={"partition_by": "order_date", "cluster_by": ["status", "channel_key", "customer_key"]},
    )
    _add_node(
        node_id="mart.fact_order_items",
        name="fact_order_items",
        layer="fact",
        asset_type="fact_table",
        zone="Zone B - Governed Analytics Mart",
        pii_classification="INTERNAL",
        columns=[
            "order_item_key",
            "order_detail_id",
            "order_id",
            "order_key",
            "product_key",
            "product_version_key",
            "order_date_key",
            "quantity",
            "unit_price",
            "unit_cost",
            "discount_rate",
            "discount_amount",
            "line_total",
            "line_cost",
            "line_profit",
        ],
        pii_restricted_columns=[],
        extra={"partition_by": "order_date", "cluster_by": ["product_key", "order_key"]},
    )
    _add_node(
        node_id="mart.fact_returns",
        name="fact_returns",
        layer="fact",
        asset_type="fact_table",
        zone="Zone B - Governed Analytics Mart",
        pii_classification="INTERNAL",
        columns=[
            "return_key",
            "return_id",
            "order_id",
            "order_key",
            "returned_date_key",
            "returned_date",
            "returned_at",
            "return_lead_days",
            "is_temporal_anomaly",
            "reason",
        ],
        pii_restricted_columns=[],
        extra={"partition_by": "returned_date", "cluster_by": ["is_temporal_anomaly", "reason"]},
    )

    # Staging & Dimensions -> Facts edges
    _add_edge(
        source="stg.stg_orders",
        target="mart.fact_orders",
        transformation_type="watermark_incremental_merge",
        pii_tags=["PII_FREE"],
        description="Watermark-driven incremental MERGE on updated_at with ship_date sentinel mapping (-1/-2/-3).",
    )
    for dim_src in ("mart.dim_date", "mart.dim_customer", "mart.dim_channel", "mart.dim_payment"):
        _add_edge(
            source=dim_src,
            target="mart.fact_orders",
            transformation_type="conformed_dimension_fk_binding",
            pii_tags=["PSEUDONYMIZED" if dim_src == "mart.dim_customer" else "PII_FREE"],
            description=f"Binds conformed surrogate key from {dim_src} into fact_orders.",
        )

    _add_edge(
        source="stg.stg_order_details",
        target="mart.fact_order_items",
        transformation_type="watermark_incremental_merge",
        pii_tags=["PII_FREE"],
        description="Loads order line items with exact VND financial reconciliation against fact_orders.",
    )
    _add_edge(
        source="mart.fact_orders",
        target="mart.fact_order_items",
        transformation_type="order_header_fk_alignment",
        pii_tags=["PII_FREE"],
        description="Resolves order_key and order_date_key for each order line item.",
    )
    _add_edge(
        source="mart.dim_product",
        target="mart.fact_order_items",
        transformation_type="conformed_dimension_fk_binding",
        pii_tags=["PII_FREE"],
        description="Binds current product_key to fact_order_items.",
    )
    _add_edge(
        source="mart.dim_product_scd2",
        target="mart.fact_order_items",
        transformation_type="point_in_time_scd2_fact_join",
        pii_tags=["PII_FREE"],
        description="Point-in-time range join on order_date IN [valid_from, valid_to) resolving product_version_key.",
    )
    _add_edge(
        source="mart.dim_date",
        target="mart.fact_order_items",
        transformation_type="conformed_dimension_fk_binding",
        pii_tags=["PII_FREE"],
        description="Binds order_date_key to conformed dim_date.",
    )

    _add_edge(
        source="stg.stg_order_returns",
        target="mart.fact_returns",
        transformation_type="temporal_anomaly_flag_and_quarantine",
        pii_tags=["PII_FREE"],
        description="Loads return events and flags returned_at < order_date rows (is_temporal_anomaly=TRUE).",
    )
    _add_edge(
        source="mart.fact_orders",
        target="mart.fact_returns",
        transformation_type="return_lead_time_calculation",
        pii_tags=["PII_FREE"],
        description="Joins returned orders to compute return_lead_days and detect temporal anomalies.",
    )
    _add_edge(
        source="mart.dim_date",
        target="mart.fact_returns",
        transformation_type="conformed_dimension_fk_binding",
        pii_tags=["PII_FREE"],
        description="Binds returned_date_key to conformed dim_date.",
    )

    # 5. Multi-Snapshot RFM Mart (1 node)
    _add_node(
        node_id="mart.mart_rfm_snapshot",
        name="mart_rfm_snapshot",
        layer="rfm_mart",
        asset_type="analytical_mart",
        zone="Zone B - Governed Analytics Mart",
        pii_classification="PSEUDONYMIZED",
        columns=[
            "snapshot_date",
            "customer_key",
            "customer_id",
            "recency_days",
            "frequency_orders",
            "monetary_value",
            "r_score",
            "f_score",
            "m_score",
            "rfm_score",
            "rfm_segment",
        ],
        pii_restricted_columns=[],
        extra={
            "grain": "(snapshot_date, customer_key)",
            "partition_by": "snapshot_date",
            "cluster_by": ["rfm_segment", "customer_key"],
        },
    )
    _add_edge(
        source="mart.fact_orders",
        target="mart.mart_rfm_snapshot",
        transformation_type="ntile5_rfm_snapshot_aggregation",
        pii_tags=["PSEUDONYMIZED"],
        description="Computes Recency, Frequency, Monetary NTILE(5) quintiles on Completed orders per snapshot_date.",
    )
    _add_edge(
        source="mart.dim_customer",
        target="mart.mart_rfm_snapshot",
        transformation_type="pseudonymized_customer_cohort_join",
        pii_tags=["PSEUDONYMIZED"],
        description="Joins pseudonymized customer_key (zero raw PII) into RFM snapshots.",
    )

    # 6. 5 Semantic BI Views
    bi_views = [
        (
            "bi.vw_monthly_kpi",
            "vw_monthly_kpi",
            ["mart.fact_orders", "mart.dim_date"],
            "Monthly revenue, COGS, gross margin %, AOV, discounts, and Vietnamese Tet/Year-End seasonality.",
        ),
        (
            "bi.vw_channel_performance",
            "vw_channel_performance",
            ["mart.fact_orders", "mart.dim_channel", "mart.dim_date"],
            "Omnichannel comparison across 6 physical stores, Web, and App including missing online ship-date tracking.",
        ),
        (
            "bi.vw_return_metrics",
            "vw_return_metrics",
            ["mart.fact_returns", "mart.fact_orders", "mart.dim_date"],
            "Order-cohort return value and clean operational lead time excluding 4 flagged temporal anomalies.",
        ),
        (
            "bi.vw_product_margin_history",
            "vw_product_margin_history",
            ["mart.fact_order_items", "mart.fact_orders", "mart.dim_product_scd2", "mart.dim_date"],
            "Point-in-time SCD Type 2 SKU margin attribution across 71 historical price/cost versions.",
        ),
        (
            "bi.vw_dq_executive_summary",
            "vw_dq_executive_summary",
            ["mart.fact_orders", "mart.fact_order_items", "mart.fact_returns", "mart.dim_customer", "mart.dim_product_scd2"],
            "Executive roll-up of automated CRITICAL/WARNING DQ gate results and PDPL compliance status.",
        ),
    ]
    for view_id, view_name, upstream_nodes, desc in bi_views:
        _add_node(
            node_id=view_id,
            name=view_name,
            layer="bi_view",
            asset_type="semantic_view",
            zone="Zone B - Governed Semantic Layer",
            pii_classification="NONE",
            pii_restricted_columns=[],
            extra={"description": desc},
        )
        for src in upstream_nodes:
            _add_edge(
                source=src,
                target=view_id,
                transformation_type="dq_audit_aggregation" if view_name == "vw_dq_executive_summary" else "semantic_kpi_view",
                pii_tags=["PII_FREE"],
                description=f"Feeds {view_name} from {src}.",
            )

    # 7. 3 Analytics Mining Marts (exact tables persisted by scripts/analytics_mining.py)
    analytics_mining_marts = [
        (
            "mining.mart_market_basket_rules",
            "mart_market_basket_rules",
            ["mart.fact_order_items", "mart.fact_orders", "mart.dim_product"],
            "market_basket_association_mining",
            "Product affinity & co-purchase association rules (support, confidence, lift) for cross-sell bundles.",
        ),
        (
            "mining.mart_rfm_kmeans_clusters",
            "mart_rfm_kmeans_clusters",
            ["mart.mart_rfm_snapshot", "mart.dim_customer"],
            "rfm_kmeans_plus_plus_clustering",
            "Unsupervised K-Means++ clustering in log1p + Z-score RFM space vs. rule-based NTILE(5) segments (pseudonymized).",
        ),
        (
            "mining.mart_return_risk_scores",
            "mart_return_risk_scores",
            [
                "mart.fact_orders",
                "mart.fact_order_items",
                "mart.dim_product",
                "mart.dim_channel",
                "mart.dim_date",
                "mart.dim_customer",
            ],
            "leakage_free_return_risk_scoring",
            (
                "Leakage-free out-of-time 2023->2024 post-fulfillment return-risk propensity scoring "
                "(L2 class-weighted logistic regression, order-time features only; mart.fact_returns is "
                "explicitly excluded as a feature and used strictly for timestamp-only point-in-time "
                "returned_at label & prior-return observability gating)."
            ),
        ),
    ]
    for mart_id, mart_name, upstream_nodes, tx_type, desc in analytics_mining_marts:
        _add_node(
            node_id=mart_id,
            name=mart_name,
            layer="analytics_mining_mart",
            asset_type="analytics_mining_mart",
            zone="Zone B - Governed Analytics Mart",
            pii_classification="PSEUDONYMIZED" if "customer" in str(upstream_nodes) else "NONE",
            pii_restricted_columns=[],
            extra={"description": desc},
        )
        for src in upstream_nodes:
            _add_edge(
                source=src,
                target=mart_id,
                transformation_type=tx_type,
                pii_tags=["PSEUDONYMIZED" if src in ("mart.dim_customer", "mart.mart_rfm_snapshot") else "PII_FREE"],
                description=f"Feeds analytics mining mart {mart_name} from {src}.",
            )

    # 8. 4 Power BI Dashboard Pages
    pbi_pages = [
        (
            "pbi.page_1_business_performance",
            "Page 1: Business Performance Dashboard",
            ["bi.vw_monthly_kpi", "bi.vw_channel_performance"],
            "powerbi/dashboard1.jpg",
            "Executive revenue, gross profit, margin %, Tet/Year-End seasonality, and omnichannel mix.",
        ),
        (
            "pbi.page_2_product_performance",
            "Page 2: Product Performance Dashboard",
            ["bi.vw_product_margin_history", "mining.mart_market_basket_rules"],
            "powerbi/dashboard2.jpg",
            "Category/brand profitability, SCD2 price-change margin waterfall, and basket cross-sell insights.",
        ),
        (
            "pbi.page_3_rfm_segmentation",
            "Page 3: RFM Customer Segmentation Dashboard",
            ["mart.mart_rfm_snapshot", "mining.mart_rfm_kmeans_clusters"],
            "powerbi/dashboard3.jpg",
            "Multi-snapshot RFM segment distribution, At-Risk win-back pool, and unsupervised K-Means++ customer clusters.",
        ),
        (
            "pbi.page_4_order_operations_dq",
            "Page 4: Order Operations & Data Quality Dashboard",
            [
                "bi.vw_return_metrics",
                "bi.vw_dq_executive_summary",
                "bi.vw_channel_performance",
                "mining.mart_return_risk_scores",
            ],
            "powerbi/dashboard4.jpg",
            "Fulfillment ship-date SLA, return lead-time monitoring, temporal anomaly quarantine, DQ gate health, and leakage-free return-risk scoring.",
        ),
    ]
    for page_id, page_name, upstream_nodes, preview_img, desc in pbi_pages:
        _add_node(
            node_id=page_id,
            name=page_name,
            layer="bi_dashboard_page",
            asset_type="powerbi_report_page",
            zone="Zone B - Executive BI Consumption",
            pii_classification="NONE",
            pii_restricted_columns=[],
            extra={"preview_artifact": preview_img, "description": desc},
        )
        for src in upstream_nodes:
            _add_edge(
                source=src,
                target=page_id,
                transformation_type="powerbi_semantic_consumption",
                pii_tags=["PII_FREE"],
                description=f"Power BI visual query consuming {src} on {page_name}.",
            )

    # Layer summary counts
    layer_counts: Dict[str, int] = {}
    for n in nodes:
        layer = n["layer"]
        layer_counts[layer] = layer_counts.get(layer, 0) + 1

    # Automated PDPL verification on the generated DAG
    raw_cust_node = next(n for n in nodes if n["id"] == "raw.customers")
    stg_cust_node = next(n for n in nodes if n["id"] == "stg.stg_customers")
    dim_cust_node = next(n for n in nodes if n["id"] == "mart.dim_customer")
    dim_cust_col_set = set(dim_cust_node.get("columns", []))
    leaked_pii = sorted(c for c in prohibited_pii if c in dim_cust_col_set)

    manifest: Dict[str, Any] = {
        "manifest_version": "1.0.0",
        "contract_version": contract["contract_version"],
        "generated_at": utc_now_iso(),
        "project": "viettech-bi-project",
        "pdpl_compliance": {
            "regulation": compliance.get("regulation"),
            "effective_date": compliance.get("effective_date"),
            "raw_customers_pii_restricted": raw_cust_node["pii_restricted_columns"],
            "stg_customers_pii_restricted": stg_cust_node["pii_restricted_columns"],
            "dim_customer_columns": dim_cust_node["columns"],
            "prohibited_pii_columns": prohibited_pii,
            "dim_customer_pii_violations": leaked_pii,
            "compliant": len(leaked_pii) == 0,
        },
        "governance_control_tables": [
            "dq_audit_results",
            "dq_quarantine_records",
            "etl_watermarks",
            "etl_run_audit",
            "retailbi_pii_vault.customer_pii_vault",
        ],
        "summary": {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "node_counts_by_layer": layer_counts,
        },
        "nodes": nodes,
        "edges": edges,
    }

    if validation_summary is not None:
        manifest["pre_ingestion_validation_snapshot"] = {
            "run_id": validation_summary.get("run_id"),
            "checked_at": validation_summary.get("checked_at"),
            "overall_status": validation_summary.get("overall_status"),
            "critical_gate_passed": validation_summary.get("critical_gate_passed"),
            "summary": validation_summary.get("summary"),
        }

    return manifest


def export_lineage_manifest(
    output_path: Path | str,
    contract: Optional[Dict[str, Any] | Path | str] = None,
    validation_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generate and write benchmarks/lineage_manifest.json."""
    if isinstance(contract, dict):
        contract_dict = contract
    else:
        contract_dict = load_contract(contract)

    manifest = build_lineage_manifest(contract_dict, validation_summary=validation_summary)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "VietTech BI Pre-Ingestion Data Contract Validator, Quarantine Router, "
            "Volume-Drift Detector & Lineage Manifest Generator."
        )
    )
    parser.add_argument(
        "--raw-dir",
        default=str(DEFAULT_RAW_DIR),
        help="Path to raw CSV directory (default: data/raw).",
    )
    parser.add_argument(
        "--contract",
        default=str(DEFAULT_CONTRACT_PATH),
        help="Path to versioned data_contracts.json (default: config/data_contracts.json).",
    )
    parser.add_argument(
        "--emit-lineage",
        default=None,
        help="Optional output path to emit machine-readable lineage_manifest.json.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Optional SQLite database path to persist quarantined records in dq_quarantine_records.",
    )
    parser.add_argument(
        "--report-out",
        default=None,
        help="Optional JSON file path to save the full validation report.",
    )
    parser.add_argument(
        "--tables",
        nargs="+",
        default=None,
        help="Optional subset of table names to validate.",
    )
    parser.add_argument(
        "--fail-on-warning",
        action="store_true",
        help="Exit non-zero if any WARNING (temporal anomaly, volume drift, null ratio) is raised.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )

    result = validate_raw_directory(
        raw_dir=args.raw_dir,
        contract_path=args.contract,
        db_path=args.db_path,
        tables=args.tables,
    )

    summary = result["summary"]
    LOGGER.info(
        "Contract validation v%s [%s] | tables=%d | rows=%d (clean=%d, quarantined=%d) | "
        "critical_errors=%d | temporal_warnings=%d | volume_alerts=%d | %.1fms",
        result["contract_version"],
        result["overall_status"],
        summary["tables_checked"],
        summary["total_rows"],
        summary["total_clean_rows"],
        summary["total_quarantined_rows"],
        summary["critical_error_count"],
        summary["temporal_warning_count"],
        summary["volume_drift_alert_count"],
        summary["duration_ms"],
    )

    for tname, tres in result["tables"].items():
        LOGGER.info(
            "  %-22s | status=%-4s | rows=%6d | clean=%6d | quarantined=%3d | bom=%s | vol=%s",
            tname,
            tres["status"],
            tres["total_rows"],
            tres["clean_row_count"],
            tres["quarantined_row_count"],
            tres["had_bom"],
            tres["volume_check"]["status"],
        )

    for warn in result["temporal_warnings"]:
        LOGGER.warning("  [WARNING QUARANTINE] %s | key=%s | %s", warn["error_code"], warn["natural_key"], warn["error_detail"])

    for alert in result["volume_alerts"]:
        LOGGER.warning("  [VOLUME DRIFT] %s", alert["detail"])

    for err in result["schema_errors"]:
        LOGGER.error("  [CRITICAL SCHEMA] %s | %s", err["error_code"], err["detail"])

    if args.report_out:
        report_path = Path(args.report_out)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        LOGGER.info("Validation report written to %s", report_path)

    if args.emit_lineage:
        manifest = export_lineage_manifest(
            output_path=args.emit_lineage,
            contract=args.contract,
            validation_summary=result,
        )
        LOGGER.info(
            "Lineage manifest emitted to %s (%d nodes, %d edges, PDPL compliant=%s)",
            args.emit_lineage,
            manifest["summary"]["total_nodes"],
            manifest["summary"]["total_edges"],
            manifest["pdpl_compliance"]["compliant"],
        )

    if not result["critical_gate_passed"]:
        return 1
    if args.fail_on_warning and summary["warning_count"] > 0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
