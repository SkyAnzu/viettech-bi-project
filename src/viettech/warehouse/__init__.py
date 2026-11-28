"""Zero-Cloud Local SQLite Kimball Warehouse, SCD2, Watermark CDC, DQ & Benchmarks."""

from __future__ import annotations

from viettech.warehouse.benchmark import (
    BENCHMARK_QUERIES,
    percentile,
    render_benchmark_markdown,
    run_benchmark_suite,
    time_query,
)
from viettech.warehouse.dimensions import (
    build_dim_date,
    build_dim_product_scd2,
    build_dimensions,
)
from viettech.warehouse.facts import (
    build_rfm_snapshot,
    get_stored_watermark,
    load_facts,
    save_watermark,
)
from viettech.warehouse.pipeline import (
    collect_warehouse_summary,
    print_run_summary,
    run_pipeline,
)
from viettech.warehouse.quality import run_data_quality_audit
from viettech.warehouse.schema import (
    ANALYTICAL_INDEXES,
    BI_VIEWS_DDL,
    DDL_SCHEMA,
    DEFAULT_SNAPSHOT_DATE,
    PIPELINE_NAME,
    SCD2_EPOCH_START,
    SCD2_OPEN_END,
    create_analytical_indexes,
    create_schema,
    drop_analytical_indexes,
)
from viettech.warehouse.staging import load_staging_tables

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
    "drop_analytical_indexes",
    "get_stored_watermark",
    "load_facts",
    "load_staging_tables",
    "percentile",
    "print_run_summary",
    "render_benchmark_markdown",
    "run_benchmark_suite",
    "run_data_quality_audit",
    "run_pipeline",
    "save_watermark",
    "time_query",
]
