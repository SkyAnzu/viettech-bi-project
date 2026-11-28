"""Pre-ingestion CSV contract validator, quarantine dead-letter router, and volume-drift detector."""

from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal
import io
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
import uuid

from viettech.common.utils import strip_utf8_bom_bytes, utc_now_iso_z
from viettech.contracts.types import (
    DEFAULT_CONTRACT_PATH,
    DEFAULT_RAW_DIR,
    PROJECT_ROOT,
    _build_natural_key,
    _parse_timestamp,
    _parse_typed_value,
    load_contract,
)

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


def _check_header_schema(
    table_name: str,
    file_name: str,
    header: List[str],
    expected_columns: List[str],
    strict_order: bool,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Validate CSV header columns against contract specification."""
    schema_errors: List[Dict[str, Any]] = []
    schema_warnings: List[Dict[str, Any]] = []
    if not header:
        schema_errors.append(
            {
                "table_name": table_name,
                "severity": "CRITICAL",
                "error_code": "SCHEMA_EMPTY_FILE",
                "detail": f"CSV file {file_name} is empty or missing a header row.",
            }
        )
        return schema_errors, schema_warnings

    expected_set = set(expected_columns)
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
    return schema_errors, schema_warnings


def _validate_single_row(
    raw_row: Dict[Any, Any],
    expected_columns: List[str],
    col_specs: List[Dict[str, Any]],
    pk_cols: List[str],
    unique_keys: List[List[str]],
    seen_unique_sets: List[Set[Tuple[str, ...]]],
    column_null_counts: Dict[str, int],
) -> List[Tuple[str, str]]:
    """Validate a single CSV data row against column types, bounds, enums, and unique keys."""
    row_violations: List[Tuple[str, str]] = []
    if None in raw_row:
        row_violations.append(
            ("ROW_COLUMN_COUNT_MISMATCH", f"Row has extra unkeyed CSV fields: {raw_row[None]!r}")
        )
        return row_violations
    if any(raw_row.get(col) is None for col in expected_columns):
        missing_vals = [col for col in expected_columns if raw_row.get(col) is None]
        row_violations.append(
            ("ROW_COLUMN_COUNT_MISMATCH", f"Row has fewer fields than header; missing values for: {missing_vals}")
        )
        return row_violations

    for spec in col_specs:
        col_name = spec["name"]
        col_type = spec["type"]
        nullable = bool(spec.get("nullable", False))
        raw_val = (raw_row.get(col_name) or "").strip()

        if raw_val == "":
            column_null_counts[col_name] += 1
            if not nullable:
                row_violations.append(
                    ("NULL_VIOLATION", f"Column '{col_name}' is non-nullable but received empty value.")
                )
            continue

        parsed_val, err_code, err_detail = _parse_typed_value(col_type, raw_val)
        if err_code is not None:
            row_violations.append((err_code, f"Column '{col_name}': {err_detail}"))
            continue

        allowed = spec.get("allowed_values")
        if allowed is not None and raw_val not in allowed:
            row_violations.append(
                ("INVALID_ENUM_VALUE", f"Column '{col_name}' value {raw_val!r} not in allowed enum {allowed}.")
            )

        if col_type in ("int", "decimal") and parsed_val is not None:
            min_v = spec.get("min_value")
            if min_v is not None:
                min_cmp: Any = Decimal(str(min_v)) if col_type == "decimal" else int(min_v)
                if parsed_val < min_cmp:
                    row_violations.append(
                        ("MIN_VALUE_VIOLATION", f"Column '{col_name}' value {parsed_val} < contract min_value {min_v}.")
                    )
            max_v = spec.get("max_value")
            if max_v is not None:
                max_cmp: Any = Decimal(str(max_v)) if col_type == "decimal" else int(max_v)
                if parsed_val > max_cmp:
                    row_violations.append(
                        ("MAX_VALUE_VIOLATION", f"Column '{col_name}' value {parsed_val} > contract max_value {max_v}.")
                    )

    for idx, uk_cols in enumerate(unique_keys):
        key_tuple = tuple((raw_row.get(c) or "").strip() for c in uk_cols)
        if all(part != "" for part in key_tuple):
            if key_tuple in seen_unique_sets[idx]:
                is_pk = uk_cols == pk_cols
                code = "DUPLICATE_PRIMARY_KEY" if is_pk else "DUPLICATE_UNIQUE_KEY"
                row_violations.append(
                    (code, f"Duplicate {'primary' if is_pk else 'unique'} key {uk_cols}={list(key_tuple)}.")
                )
            else:
                seen_unique_sets[idx].add(key_tuple)

    return row_violations


def _evaluate_null_ratios(
    table_name: str,
    col_specs: List[Dict[str, Any]],
    column_null_counts: Dict[str, int],
    total_rows: int,
) -> List[Dict[str, Any]]:
    alerts: List[Dict[str, Any]] = []
    if total_rows <= 0:
        return alerts
    for spec in col_specs:
        col_name = spec["name"]
        if not spec.get("nullable", False):
            continue
        max_ratio = spec.get("max_null_ratio")
        if max_ratio is None:
            continue
        observed_ratio = column_null_counts[col_name] / total_rows
        if observed_ratio > float(max_ratio):
            alerts.append(
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
    return alerts


def _evaluate_volume_drift(
    table_name: str,
    table_spec: Dict[str, Any],
    total_rows: int,
) -> Dict[str, Any]:
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
            round(((total_rows - baseline_rows) / baseline_rows) * 100.0, 2) if baseline_rows else 0.0
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
                    "detail": f"Volume drift in '{table_name}': observed {total_rows} rows < contract floor {min_rows}.",
                }
            )
        elif total_rows > max_rows:
            volume_check.update(
                {
                    "status": "ALERT",
                    "severity": "WARNING",
                    "error_code": "VOLUME_DRIFT_ABOVE_CEILING",
                    "direction": "ABOVE_CEILING",
                    "detail": f"Volume drift in '{table_name}': observed {total_rows} rows > contract ceiling {max_rows}.",
                }
            )
    return volume_check


def validate_table_csv(
    csv_path: Path | str,
    table_name: str,
    contract: Dict[str, Any],
    delimiter: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate a single raw CSV file against its contract definition."""
    t0 = time.perf_counter()
    path = Path(csv_path)
    tables_spec = contract.get("tables", {})
    if table_name not in tables_spec:
        raise KeyError(f"Table '{table_name}' is not defined in data contract.")

    table_spec = tables_spec[table_name]
    csv_delim = delimiter or contract.get("sla", {}).get("delimiter", ",")
    col_specs: List[Dict[str, Any]] = table_spec["columns"]
    expected_columns: List[str] = [c["name"] for c in col_specs]
    pk_cols: List[str] = list(table_spec.get("primary_key", ["id"]))
    unique_keys: List[List[str]] = [list(uk) for uk in table_spec.get("unique_keys", [pk_cols])]
    strict_order: bool = bool(table_spec.get("strict_column_order", True))

    column_null_counts: Dict[str, int] = {col: 0 for col in expected_columns}

    if not path.exists():
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
            "schema_errors": [
                {
                    "table_name": table_name,
                    "severity": "CRITICAL",
                    "error_code": "MISSING_SOURCE_FILE",
                    "detail": f"Expected raw CSV file does not exist: {path}",
                }
            ],
            "schema_warnings": [],
            "volume_check": {
                "status": "ALERT",
                "severity": "CRITICAL",
                "error_code": "MISSING_SOURCE_FILE",
                "observed_rows": 0,
                "expected_range": table_spec.get("expected_row_count_range"),
                "baseline_row_count": table_spec.get("baseline_row_count"),
            },
            "null_ratio_alerts": [],
            "column_null_counts": column_null_counts,
            "clean_rows": [],
            "clean_row_numbers": [],
            "quarantined_rows": [],
            "duration_ms": round((time.perf_counter() - t0) * 1000.0, 3),
        }

    raw_bytes = path.read_bytes()
    clean_bytes, had_bom = strip_utf8_bom_bytes(raw_bytes)
    text = clean_bytes.decode("utf-8")
    stream = io.StringIO(text, newline="")
    reader = csv.DictReader(stream, delimiter=csv_delim)
    header: List[str] = list(reader.fieldnames or [])

    schema_errors, schema_warnings = _check_header_schema(
        table_name, path.name, header, expected_columns, strict_order
    )

    total_rows = 0
    quarantined_row_numbers: Set[int] = set()
    quarantined_rows: List[Dict[str, Any]] = []
    clean_rows: List[Dict[str, str]] = []
    clean_row_numbers: List[int] = []
    null_ratio_alerts: List[Dict[str, Any]] = []

    if not schema_errors:
        seen_unique_sets: List[Set[Tuple[str, ...]]] = [set() for _ in unique_keys]
        for row_number, raw_row in enumerate(reader, start=1):
            total_rows += 1
            natural_key = _build_natural_key(raw_row, pk_cols, row_number)
            row_violations = _validate_single_row(
                raw_row,
                expected_columns,
                col_specs,
                pk_cols,
                unique_keys,
                seen_unique_sets,
                column_null_counts,
            )
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

        null_ratio_alerts = _evaluate_null_ratios(table_name, col_specs, column_null_counts, total_rows)

    volume_check = _evaluate_volume_drift(table_name, table_spec, total_rows)

    has_critical = bool(schema_errors) or bool(quarantined_rows)
    has_warnings = bool(schema_warnings) or bool(null_ratio_alerts) or volume_check["status"] == "ALERT"
    status = "FAIL" if has_critical else ("WARN" if has_warnings else "PASS")

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


def _run_cross_table_temporal_checks(
    table_results: Dict[str, Dict[str, Any]],
    contract: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Evaluate cross-table temporal rules (e.g., order_returns.returned_at < orders.order_date)."""
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
    now_iso = utc_now_iso_z()

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
    """Validate all raw CSV tables in a directory against the versioned data contract."""
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
        "checked_at": utc_now_iso_z(),
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
