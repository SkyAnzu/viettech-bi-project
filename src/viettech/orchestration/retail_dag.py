"""Canonical 7-task VietTech Retail ELT DAG definition and execution builders."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import time
from typing import Any

from viettech.common.db import open_connection
from viettech.contracts import (
    DEFAULT_CONTRACT_PATH,
    DEFAULT_RAW_DIR,
    PROJECT_ROOT,
    export_lineage_manifest,
    persist_quarantine_records,
    validate_raw_directory,
)
from viettech.mining import run_analytics_mining_pipeline
from viettech.orchestration.engine import DagOrchestrator
from viettech.orchestration.models import DagRunResult, TaskContext, TaskSpec
from viettech.orchestration.state_store import OrchestratorStateStore
from viettech.warehouse import (
    DEFAULT_SNAPSHOT_DATE,
    build_dim_date,
    build_dim_product_scd2,
    build_dimensions,
    build_rfm_snapshot,
    collect_warehouse_summary,
    create_analytical_indexes,
    create_schema,
    load_facts,
    load_staging_tables,
    run_data_quality_audit,
)

RETAIL_DAG_ID = "viettech_retail_elt_daily"

DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "warehouse" / "viettech_mart.db"
DEFAULT_STATE_DB_PATH = PROJECT_ROOT / "data" / "warehouse" / "orchestrator_state.db"

RETAIL_DAG_TOPOLOGY: dict[str, tuple[str, ...]] = {
    "validate_data_contracts": (),
    "load_staging_layer": ("validate_data_contracts",),
    "build_dimensions_scd2": ("load_staging_layer",),
    "load_facts_incremental": ("build_dimensions_scd2",),
    "build_rfm_snapshot": ("load_facts_incremental",),
    "run_dq_reconciliation": ("build_rfm_snapshot",),
    "run_prescriptive_mining": ("run_dq_reconciliation",),
}


def build_retail_tasks(
    *,
    raw_dir: Path | str = DEFAULT_RAW_DIR,
    db_path: Path | str = DEFAULT_DB_PATH,
    state_db_path: Path | str = DEFAULT_STATE_DB_PATH,
    contract_path: Path | str = DEFAULT_CONTRACT_PATH,
    lineage_path: Path | str | None = None,
    mining_report_md: Path | str | None = None,
    mining_report_json: Path | str | None = None,
    retry_delay_sec: float = 0.05,
    quiet: bool = True,
) -> list[TaskSpec]:
    """Construct the 7 cohesive TaskSpecs mirroring VietTech's production Retail ELT DAG."""
    resolved_raw_dir = Path(raw_dir)
    resolved_db_path = Path(db_path)
    resolved_state_db_path = Path(state_db_path)
    resolved_contract_path = Path(contract_path)
    resolved_lineage_path = (
        Path(lineage_path)
        if lineage_path is not None
        else (
            resolved_db_path.parent / "lineage_manifest.json"
            if resolved_db_path != DEFAULT_DB_PATH
            else PROJECT_ROOT / "benchmarks" / "lineage_manifest.json"
        )
    )
    resolved_mining_md = Path(mining_report_md) if mining_report_md is not None else None
    resolved_mining_json = (
        Path(mining_report_json) if mining_report_json is not None else None
    )

    def _task_validate_data_contracts(ctx: TaskContext) -> dict[str, Any]:
        validation = validate_raw_directory(
            raw_dir=resolved_raw_dir,
            contract_path=resolved_contract_path,
            db_path=None,
        )
        combined_quarantine = (
            list(validation["quarantined_records"]) + list(validation["temporal_warnings"])
        )
        overall_status = str(validation["overall_status"])
        critical_passed = bool(validation["critical_gate_passed"])

        if not critical_passed or overall_status in ("FAIL", "FAILED"):
            if combined_quarantine:
                persist_quarantine_records(
                    db_path=resolved_state_db_path,
                    records=combined_quarantine,
                    run_id=ctx.run_id,
                )
            summary = validation["summary"]
            raise RuntimeError(
                "Pre-ingestion data contract gate FAILED: "
                f"status={overall_status}, "
                f"critical_errors={summary['critical_error_count']}, "
                f"schema_errors={summary['critical_schema_errors']}, "
                f"quarantined_rows={summary['total_quarantined_rows']}"
            )

        persisted_count = persist_quarantine_records(
            db_path=resolved_db_path,
            records=combined_quarantine,
            run_id=ctx.run_id,
        )
        manifest = export_lineage_manifest(
            output_path=resolved_lineage_path,
            contract=resolved_contract_path,
            validation_summary=validation,
        )
        summary = validation["summary"]
        return {
            "overall_status": overall_status,
            "tables_checked": int(summary["tables_checked"]),
            "total_rows": int(summary["total_rows"]),
            "total_clean_rows": int(summary["total_clean_rows"]),
            "temporal_warnings": int(summary["temporal_warning_count"]),
            "persisted_quarantine_records": persisted_count,
            "lineage_nodes": int(manifest["summary"]["total_nodes"]),
            "lineage_edges": int(manifest["summary"]["total_edges"]),
            "lineage_path": str(resolved_lineage_path),
        }

    def _task_load_staging_layer(_ctx: TaskContext) -> dict[str, Any]:
        conn = open_connection(resolved_db_path)
        try:
            create_schema(conn)
            stg_counts = load_staging_tables(conn, resolved_raw_dir)
            return {
                "staging_counts": stg_counts,
                "total_staging_rows": sum(stg_counts.values()),
            }
        finally:
            conn.close()

    def _task_build_dimensions_scd2(_ctx: TaskContext) -> dict[str, Any]:
        conn = open_connection(resolved_db_path)
        try:
            dim_date_rows = build_dim_date(conn)
            dim_counts = build_dimensions(conn)
            scd2_versions = build_dim_product_scd2(conn)
            dim_counts["dim_date"] = dim_date_rows
            dim_counts["dim_product_scd2"] = scd2_versions
            return {
                "dimension_counts": dim_counts,
                "dim_date_rows": dim_date_rows,
                "scd2_versions": scd2_versions,
            }
        finally:
            conn.close()

    def _task_load_facts_incremental(ctx: TaskContext) -> dict[str, Any]:
        mode = str(ctx.params.get("mode", "full"))
        raw_watermark = ctx.params.get("watermark")
        watermark = str(raw_watermark) if raw_watermark is not None else None
        conn = open_connection(resolved_db_path)
        try:
            fact_meta = load_facts(conn, mode=mode, watermark=watermark)
            create_analytical_indexes(conn)
            return dict(fact_meta)
        finally:
            conn.close()

    def _task_build_rfm_snapshot(ctx: TaskContext) -> dict[str, Any]:
        conn = open_connection(resolved_db_path)
        try:
            rfm_rows = build_rfm_snapshot(conn, snapshot_date=ctx.logical_date)
            return {
                "snapshot_date": ctx.logical_date,
                "rfm_rows": rfm_rows,
            }
        finally:
            conn.close()

    def _task_run_dq_reconciliation(ctx: TaskContext) -> dict[str, Any]:
        conn = open_connection(resolved_db_path)
        try:
            dq_results = run_data_quality_audit(conn, run_id=ctx.run_id)
            critical_checks = [r for r in dq_results if r["severity"] == "CRITICAL"]
            failed_critical = [r for r in critical_checks if r["status"] != "PASS"]
            warning_flagged = sum(
                1 for r in dq_results if r["severity"] == "WARNING" and r["status"] == "WARN"
            )
            if failed_critical:
                failed_names = ", ".join(str(r["check_name"]) for r in failed_critical)
                raise RuntimeError(
                    f"DQ reconciliation gate FAILED ({len(failed_critical)} critical check(s): {failed_names})"
                )

            summary = collect_warehouse_summary(conn, snapshot_date=ctx.logical_date)
            recon_row = next(
                (r for r in dq_results if r["check_name"] == "three_way_financial_reconciliation"),
                None,
            )
            return {
                "total_checks": len(dq_results),
                "critical_passed": len(critical_checks),
                "critical_failed": 0,
                "warning_flagged": warning_flagged,
                "completed_revenue_vnd": summary["completed_revenue_vnd"],
                "gross_margin_pct": summary["gross_margin_pct"],
                "reconciliation_observed": (
                    str(recon_row["observed_value"]) if recon_row is not None else "PASS"
                ),
            }
        finally:
            conn.close()

    def _task_run_prescriptive_mining(ctx: TaskContext) -> dict[str, Any]:
        if bool(ctx.params.get("skip_mining", False)):
            return {
                "skipped_by_param": True,
                "snapshot_date": ctx.logical_date,
            }
        mining_res = run_analytics_mining_pipeline(
            db_path=resolved_db_path,
            report_md=resolved_mining_md,
            report_json=resolved_mining_json,
            snapshot_date=ctx.logical_date,
            quiet=quiet,
        )
        mba = mining_res["module_1_market_basket"]
        km = mining_res["module_2_rfm_kmeans"]
        rr = mining_res["module_3_return_risk"]["out_of_time_2024_metrics"]
        return {
            "skipped_by_param": False,
            "snapshot_date": ctx.logical_date,
            "persisted_rules_total": int(mba["persisted_rules_total"]),
            "customers_clustered": int(km["customers_clustered"]),
            "selected_k": int(km["selected_k"]),
            "scored_orders": int(rr["scored_orders"]),
            "roc_auc": float(rr["roc_auc"]),
            "pr_auc": float(rr["pr_auc"]),
            "total_duration_ms": float(mining_res["metadata"]["total_duration_ms"]),
        }

    return [
        TaskSpec(
            task_id="validate_data_contracts",
            fn=_task_validate_data_contracts,
            upstream_ids=RETAIL_DAG_TOPOLOGY["validate_data_contracts"],
            retries=1,
            retry_delay_sec=retry_delay_sec,
            sla_seconds=60.0,
            description="Validate raw CSV contracts, persist quarantine audit, and emit 38-node lineage DAG.",
        ),
        TaskSpec(
            task_id="load_staging_layer",
            fn=_task_load_staging_layer,
            upstream_ids=RETAIL_DAG_TOPOLOGY["load_staging_layer"],
            retries=2,
            retry_delay_sec=retry_delay_sec,
            sla_seconds=60.0,
            description="Initialize warehouse DDL and load 8 raw CSV tables into stg_*.",
        ),
        TaskSpec(
            task_id="build_dimensions_scd2",
            fn=_task_build_dimensions_scd2,
            upstream_ids=RETAIL_DAG_TOPOLOGY["build_dimensions_scd2"],
            retries=1,
            retry_delay_sec=retry_delay_sec,
            sla_seconds=60.0,
            description="Build dim_date, PDPL-masked dim_customer, conformed dimensions, and dim_product_scd2.",
        ),
        TaskSpec(
            task_id="load_facts_incremental",
            fn=_task_load_facts_incremental,
            upstream_ids=RETAIL_DAG_TOPOLOGY["load_facts_incremental"],
            retries=2,
            retry_delay_sec=retry_delay_sec,
            sla_seconds=90.0,
            description="Upsert fact_orders, fact_order_items, and fact_returns (full or watermark CDC) + analytical indexes.",
        ),
        TaskSpec(
            task_id="build_rfm_snapshot",
            fn=_task_build_rfm_snapshot,
            upstream_ids=RETAIL_DAG_TOPOLOGY["build_rfm_snapshot"],
            retries=1,
            retry_delay_sec=retry_delay_sec,
            sla_seconds=60.0,
            description="Compute multi-snapshot mart_rfm_snapshot parameterized by logical_date.",
        ),
        TaskSpec(
            task_id="run_dq_reconciliation",
            fn=_task_run_dq_reconciliation,
            upstream_ids=RETAIL_DAG_TOPOLOGY["run_dq_reconciliation"],
            retries=0,
            retry_delay_sec=0.0,
            sla_seconds=60.0,
            description="Execute 15-check DQ & 3-way financial reconciliation gate blocking corrupt mart publication.",
        ),
        TaskSpec(
            task_id="run_prescriptive_mining",
            fn=_task_run_prescriptive_mining,
            upstream_ids=RETAIL_DAG_TOPOLOGY["run_prescriptive_mining"],
            retries=1,
            retry_delay_sec=retry_delay_sec,
            sla_seconds=120.0,
            description="Run Market Basket Lift, K-Means++ RFM clustering, and leakage-free Return-Risk scoring.",
        ),
    ]


def build_retail_orchestrator(
    *,
    raw_dir: Path | str = DEFAULT_RAW_DIR,
    db_path: Path | str = DEFAULT_DB_PATH,
    state_db_path: Path | str = DEFAULT_STATE_DB_PATH,
    contract_path: Path | str = DEFAULT_CONTRACT_PATH,
    lineage_path: Path | str | None = None,
    mining_report_md: Path | str | None = None,
    mining_report_json: Path | str | None = None,
    retry_delay_sec: float = 0.05,
    sleep_fn: Callable[[float], None] = time.sleep,
    on_failure_callback: Callable[[TaskContext, Exception], None] | None = None,
    quiet: bool = True,
) -> DagOrchestrator:
    """Create a configured DagOrchestrator for the canonical VietTech Retail ELT DAG."""
    store = OrchestratorStateStore(state_db_path)
    tasks = build_retail_tasks(
        raw_dir=raw_dir,
        db_path=db_path,
        state_db_path=state_db_path,
        contract_path=contract_path,
        lineage_path=lineage_path,
        mining_report_md=mining_report_md,
        mining_report_json=mining_report_json,
        retry_delay_sec=retry_delay_sec,
        quiet=quiet,
    )
    return DagOrchestrator(
        dag_id=RETAIL_DAG_ID,
        tasks=tasks,
        state_store=store,
        sleep_fn=sleep_fn,
        on_failure_callback=on_failure_callback,
    )


def run_retail_dag(
    *,
    logical_date: str = DEFAULT_SNAPSHOT_DATE,
    raw_dir: Path | str = DEFAULT_RAW_DIR,
    db_path: Path | str = DEFAULT_DB_PATH,
    state_db_path: Path | str = DEFAULT_STATE_DB_PATH,
    contract_path: Path | str = DEFAULT_CONTRACT_PATH,
    lineage_path: Path | str | None = None,
    mining_report_md: Path | str | None = None,
    mining_report_json: Path | str | None = None,
    mode: str = "full",
    watermark: str | None = None,
    skip_mining: bool = False,
    resume_from_run_id: str | None = None,
    run_id: str | None = None,
    trigger_mode: str = "manual",
    retry_delay_sec: float = 0.05,
    sleep_fn: Callable[[float], None] = time.sleep,
    on_failure_callback: Callable[[TaskContext, Exception], None] | None = None,
    quiet: bool = True,
) -> DagRunResult:
    """Build and execute a single VietTech Retail ELT DAG run."""
    orchestrator = build_retail_orchestrator(
        raw_dir=raw_dir,
        db_path=db_path,
        state_db_path=state_db_path,
        contract_path=contract_path,
        lineage_path=lineage_path,
        mining_report_md=mining_report_md,
        mining_report_json=mining_report_json,
        retry_delay_sec=retry_delay_sec,
        sleep_fn=sleep_fn,
        on_failure_callback=on_failure_callback,
        quiet=quiet,
    )
    params: dict[str, Any] = {
        "mode": mode,
        "watermark": watermark,
        "skip_mining": skip_mining,
    }
    return orchestrator.run(
        logical_date=logical_date,
        run_id=run_id,
        resume_from_run_id=resume_from_run_id,
        params=params,
        trigger_mode=trigger_mode,
    )
