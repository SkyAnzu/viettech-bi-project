"""Thin CLI facade for VietTech BI Pre-Ingestion Data Contracts & Lineage DAG."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
from typing import Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from viettech.common.utils import (  # noqa: E402
    UTF8_BOM,
    strip_utf8_bom_bytes,
    utc_now_iso_z as utc_now_iso,
)
from viettech.contracts import (  # noqa: E402
    DEFAULT_CONTRACT_PATH,
    DEFAULT_RAW_DIR,
    QUARANTINE_DDL,
    _build_natural_key,
    _parse_bool_int,
    _parse_date,
    _parse_decimal,
    _parse_int,
    _parse_timestamp,
    _parse_typed_value,
    _run_cross_table_temporal_checks,
    build_lineage_manifest,
    export_lineage_manifest,
    load_contract,
    persist_quarantine_records,
    validate_raw_directory,
    validate_table_csv,
)

LOGGER = logging.getLogger("viettech.contract_validator")

__all__ = [
    "DEFAULT_CONTRACT_PATH", "DEFAULT_RAW_DIR", "LOGGER", "PROJECT_ROOT",
    "QUARANTINE_DDL", "UTF8_BOM", "_build_natural_key", "_parse_bool_int",
    "_parse_date", "_parse_decimal", "_parse_int", "_parse_timestamp",
    "_parse_typed_value", "_run_cross_table_temporal_checks",
    "build_lineage_manifest", "export_lineage_manifest", "load_contract",
    "main", "parse_args", "persist_quarantine_records",
    "strip_utf8_bom_bytes", "utc_now_iso", "validate_raw_directory",
    "validate_table_csv",
]


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "VietTech BI Pre-Ingestion Data Contract Validator, Quarantine Router, "
            "Volume-Drift Detector & Lineage Manifest Generator."
        )
    )
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR), help="Path to raw CSV directory (default: data/raw).")
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT_PATH), help="Path to versioned data_contracts.json.")
    parser.add_argument("--emit-lineage", default=None, help="Optional output path to emit lineage_manifest.json.")
    parser.add_argument("--db-path", default=None, help="Optional SQLite path to persist dq_quarantine_records.")
    parser.add_argument("--report-out", default=None, help="Optional JSON file path to save the full validation report.")
    parser.add_argument("--tables", nargs="+", default=None, help="Optional subset of table names to validate.")
    parser.add_argument("--fail-on-warning", action="store_true", help="Exit non-zero if any WARNING is raised.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
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
        result["contract_version"], result["overall_status"], summary["tables_checked"],
        summary["total_rows"], summary["total_clean_rows"], summary["total_quarantined_rows"],
        summary["critical_error_count"], summary["temporal_warning_count"],
        summary["volume_drift_alert_count"], summary["duration_ms"],
    )
    for tname, tres in result["tables"].items():
        LOGGER.info(
            "  %-22s | status=%-4s | rows=%6d | clean=%6d | quarantined=%3d | bom=%s | vol=%s",
            tname, tres["status"], tres["total_rows"], tres["clean_row_count"],
            tres["quarantined_row_count"], tres["had_bom"], tres["volume_check"]["status"],
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

    if args.emit_lineage:
        manifest = export_lineage_manifest(
            output_path=args.emit_lineage,
            contract=args.contract,
            validation_summary=result,
        )
        LOGGER.info(
            "Lineage manifest emitted to %s (%d nodes, %d edges, PDPL compliant=%s)",
            args.emit_lineage, manifest["summary"]["total_nodes"],
            manifest["summary"]["total_edges"], manifest["pdpl_compliance"]["compliant"],
        )

    if not result["critical_gate_passed"]:
        return 1
    if args.fail_on_warning and summary["warning_count"] > 0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
