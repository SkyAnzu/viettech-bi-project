"""Thin CLI facade for the VietTech Zero-Cloud Local Kimball Warehouse Engine."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from viettech.common import (  # noqa: E402
    date_str_to_key,
    explain_query_plan,
    mask_customer_name,
    open_connection,
    parse_bool_int,
    parse_optional_text,
    read_csv_rows,
    sha256_key,
    table_exists,
    utc_now_iso,
)
from viettech.warehouse import (  # noqa: E402
    ANALYTICAL_INDEXES,
    BENCHMARK_QUERIES,
    BI_VIEWS_DDL,
    DDL_SCHEMA,
    DEFAULT_SNAPSHOT_DATE,
    PIPELINE_NAME,
    SCD2_EPOCH_START,
    SCD2_OPEN_END,
    build_dim_date,
    build_dim_product_scd2,
    build_dimensions,
    build_rfm_snapshot,
    collect_warehouse_summary,
    create_analytical_indexes,
    create_schema,
    drop_analytical_indexes,
    get_stored_watermark,
    load_facts,
    load_staging_tables,
    percentile,
    print_run_summary,
    render_benchmark_markdown,
    run_benchmark_suite,
    run_data_quality_audit,
    run_pipeline,
    save_watermark,
    time_query,
)

__all__ = [
    "ANALYTICAL_INDEXES",
    "BENCHMARK_QUERIES",
    "BI_VIEWS_DDL",
    "DDL_SCHEMA",
    "DEFAULT_SNAPSHOT_DATE",
    "PIPELINE_NAME",
    "SCD2_EPOCH_START",
    "SCD2_OPEN_END",
    "build_dim_date",
    "build_dim_product_scd2",
    "build_dimensions",
    "build_rfm_snapshot",
    "collect_warehouse_summary",
    "create_analytical_indexes",
    "create_schema",
    "date_str_to_key",
    "drop_analytical_indexes",
    "explain_query_plan",
    "get_stored_watermark",
    "load_facts",
    "load_staging_tables",
    "main",
    "mask_customer_name",
    "open_connection",
    "parse_args",
    "parse_bool_int",
    "parse_optional_text",
    "percentile",
    "print_run_summary",
    "read_csv_rows",
    "render_benchmark_markdown",
    "run_benchmark_suite",
    "run_data_quality_audit",
    "run_pipeline",
    "save_watermark",
    "sha256_key",
    "table_exists",
    "time_query",
    "utc_now_iso",
]


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="VietTech Zero-Cloud Local Kimball Warehouse & Incremental Watermark Engine"
    )
    parser.add_argument("--raw-dir", type=Path, default=PROJECT_ROOT / "data" / "raw", help="Path to raw CSV directory.")
    parser.add_argument("--db-path", type=Path, default=PROJECT_ROOT / "data" / "warehouse" / "viettech_mart.db")
    parser.add_argument("--mode", choices=["full", "incremental"], default="full")
    parser.add_argument("--watermark", type=str, default=None, help="Optional ISO timestamp watermark.")
    parser.add_argument("--snapshot-date", type=str, default=DEFAULT_SNAPSHOT_DATE)
    parser.add_argument("--benchmark", action="store_true", help="Run empirical benchmark suite.")
    parser.add_argument("--benchmark-dir", type=Path, default=PROJECT_ROOT / "benchmarks")
    parser.add_argument("--quiet", action="store_true", help="Suppress console summary output.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    result = run_pipeline(
        raw_dir=args.raw_dir,
        db_path=args.db_path,
        mode=args.mode,
        watermark=args.watermark,
        snapshot_date=args.snapshot_date,
        quiet=args.quiet,
    )
    if result["dq_summary"]["critical_failed"] > 0:
        if not args.quiet:
            print("ERROR: Critical Data Quality gate failed!", file=sys.stderr)
        return 2
    if args.benchmark:
        run_benchmark_suite(
            raw_dir=args.raw_dir,
            benchmark_dir=args.benchmark_dir,
            snapshot_date=args.snapshot_date,
            quiet=args.quiet,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
