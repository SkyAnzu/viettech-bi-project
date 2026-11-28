"""End-to-end local warehouse pipeline orchestrator and executive run summary."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, List, Optional
import uuid

from viettech.common.db import open_connection
from viettech.common.utils import utc_now_iso
from viettech.contracts.validator import validate_raw_directory
from viettech.warehouse.dimensions import build_dimensions
from viettech.warehouse.facts import build_rfm_snapshot, load_facts
from viettech.warehouse.quality import run_data_quality_audit
from viettech.warehouse.schema import (
    DEFAULT_SNAPSHOT_DATE,
    create_analytical_indexes,
    create_schema,
)
from viettech.warehouse.staging import load_staging_tables


def collect_warehouse_summary(conn: sqlite3.Connection, snapshot_date: str) -> Dict[str, Any]:
    """Gather verified financial totals, table counts, SCD2 stats, and RFM breakdown."""
    kpi = conn.execute(
        """
        SELECT
            COUNT(*) AS total_orders,
            SUM(CASE WHEN status = 'Completed' THEN 1 ELSE 0 END) AS completed_orders,
            ROUND(SUM(CASE WHEN status = 'Completed' THEN net_amount ELSE 0 END), 2) AS completed_revenue,
            ROUND(SUM(CASE WHEN status = 'Completed' THEN total_cost ELSE 0 END), 2) AS completed_cost,
            ROUND(SUM(CASE WHEN status = 'Completed' THEN net_amount - total_cost ELSE 0 END), 2) AS gross_profit,
            ROUND(SUM(CASE WHEN status = 'Completed' THEN total_discount ELSE 0 END), 2) AS completed_discount
        FROM fact_orders
        """
    ).fetchone()

    ret = conn.execute(
        """
        SELECT
            COUNT(*) AS total_returns,
            ROUND(SUM(fo.net_amount), 2) AS return_revenue,
            SUM(fr.is_temporal_anomaly) AS temporal_anomalies,
            ROUND(AVG(CASE WHEN fr.is_temporal_anomaly = 0 THEN fr.return_lead_days END), 2) AS clean_avg_return_lead_days
        FROM fact_returns fr
        JOIN fact_orders fo ON fr.order_key = fo.order_key
        """
    ).fetchone()

    rfm_rows = conn.execute(
        """
        SELECT
            rfm_segment,
            COUNT(*) AS customers,
            ROUND(AVG(recency_days), 1) AS avg_recency_days,
            ROUND(AVG(frequency_orders), 2) AS avg_frequency,
            ROUND(SUM(monetary_value), 2) AS total_monetary
        FROM mart_rfm_snapshot
        WHERE snapshot_date = ?
        GROUP BY rfm_segment
        ORDER BY total_monetary DESC
        """,
        (snapshot_date,),
    ).fetchall()

    scd2_stats = conn.execute(
        """
        SELECT
            COUNT(*) AS total_versions,
            COUNT(DISTINCT product_id) AS distinct_products,
            SUM(CASE WHEN version_number > 1 THEN 1 ELSE 0 END) AS price_change_versions
        FROM dim_product_scd2
        """
    ).fetchone()

    comp_rev = float(kpi["completed_revenue"] or 0.0)
    gross_profit = float(kpi["gross_profit"] or 0.0)
    margin_pct = round((gross_profit / comp_rev) * 100.0, 2) if comp_rev > 0 else 0.0

    return {
        "total_orders": int(kpi["total_orders"]),
        "completed_orders": int(kpi["completed_orders"]),
        "completed_revenue_vnd": comp_rev,
        "completed_cost_vnd": float(kpi["completed_cost"] or 0.0),
        "gross_profit_vnd": gross_profit,
        "gross_margin_pct": margin_pct,
        "completed_discount_vnd": float(kpi["completed_discount"] or 0.0),
        "total_order_items": int(conn.execute("SELECT COUNT(*) FROM fact_order_items").fetchone()[0]),
        "total_returns": int(ret["total_returns"]),
        "return_revenue_vnd": float(ret["return_revenue"] or 0.0),
        "temporal_return_anomalies": int(ret["temporal_anomalies"] or 0),
        "clean_avg_return_lead_days": float(ret["clean_avg_return_lead_days"] or 0.0),
        "scd2": {
            "total_versions": int(scd2_stats["total_versions"]),
            "distinct_products": int(scd2_stats["distinct_products"]),
            "price_change_versions": int(scd2_stats["price_change_versions"]),
        },
        "rfm_segments": [dict(r) for r in rfm_rows],
    }


def print_run_summary(result: Dict[str, Any]) -> None:
    ws = result["warehouse_summary"]
    dq = result["dq_summary"]
    print(f"\n=== VietTech Local Warehouse Run ({result['run_id']}) ===")
    print(
        f"Mode: {result['mode']} | Watermark: {result['watermark_used'] or 'NONE (Full)'} "
        f"| Snapshot: {result['snapshot_date']} | Status: {result['status']}"
    )
    print(
        f"Processed: orders={result['fact_processed']['orders']:,}, "
        f"items={result['fact_processed']['order_items']:,}, "
        f"returns={result['fact_processed']['returns']:,} "
        f"in {result['timings_ms']['total']:.2f} ms"
    )
    print(
        f"Verified Financials: Completed Orders={ws['completed_orders']:,} | "
        f"Revenue={ws['completed_revenue_vnd']:,.0f} VND | "
        f"Cost={ws['completed_cost_vnd']:,.0f} VND | "
        f"Gross Profit={ws['gross_profit_vnd']:,.0f} VND ({ws['gross_margin_pct']:.2f}%)"
    )
    print(
        f"Returns: {ws['total_returns']:,} rows ({ws['return_revenue_vnd']:,.0f} VND) | "
        f"SCD2 Versions: {ws['scd2']['total_versions']} across {ws['scd2']['distinct_products']} products"
    )
    print(
        f"DQ Gate: {dq['critical_passed']}/{dq['critical_passed'] + dq['critical_failed']} CRITICAL PASSED, "
        f"{dq['warning_flagged']} WARNINGS monitored"
    )


def run_pipeline(
    raw_dir: Path,
    db_path: Path,
    mode: str = "full",
    watermark: Optional[str] = None,
    snapshot_date: str = DEFAULT_SNAPSHOT_DATE,
    quiet: bool = False,
    contract_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Execute the full or incremental local warehouse pipeline end-to-end."""
    run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    started_iso = utc_now_iso()
    t0 = time.perf_counter()

    clean_tables: Optional[Dict[str, List[Dict[str, str]]]] = None
    if contract_path is not None:
        validation = validate_raw_directory(
            raw_dir=raw_dir,
            contract_path=contract_path,
            db_path=db_path,
            include_clean_rows=True,
        )
        if not validation["critical_gate_passed"]:
            raise ValueError(
                f"Pre-ingestion contract gate failed with {validation['summary']['critical_error_count']} critical error(s)."
            )
        clean_tables = {
            tname: tres["clean_rows"]
            for tname, tres in validation["tables"].items()
            if "clean_rows" in tres
        }

    conn = open_connection(db_path)
    try:
        create_schema(conn)

        t_stg = time.perf_counter()
        stg_counts = load_staging_tables(conn, raw_dir, clean_tables=clean_tables)
        stg_ms = (time.perf_counter() - t_stg) * 1000.0

        t_dim = time.perf_counter()
        dim_counts = build_dimensions(conn)
        dim_ms = (time.perf_counter() - t_dim) * 1000.0

        t_fact = time.perf_counter()
        fact_meta = load_facts(conn, mode=mode, watermark=watermark)
        fact_ms = (time.perf_counter() - t_fact) * 1000.0

        t_mart = time.perf_counter()
        rfm_count = build_rfm_snapshot(conn, snapshot_date=snapshot_date)
        create_analytical_indexes(conn)
        mart_ms = (time.perf_counter() - t_mart) * 1000.0

        t_dq = time.perf_counter()
        dq_results = run_data_quality_audit(conn, run_id=run_id)
        dq_ms = (time.perf_counter() - t_dq) * 1000.0

        total_ms = (time.perf_counter() - t0) * 1000.0
        finished_iso = utc_now_iso()

        critical_failed = sum(1 for r in dq_results if r["severity"] == "CRITICAL" and r["status"] != "PASS")
        warning_flagged = sum(1 for r in dq_results if r["severity"] == "WARNING" and r["status"] == "WARN")
        run_status = "SUCCESS" if critical_failed == 0 else "DQ_CRITICAL_FAILURE"

        with conn:
            conn.execute(
                """
                INSERT INTO etl_run_audit (
                    run_id, mode, watermark_used, started_at, finished_at, duration_ms,
                    orders_processed, details_processed, returns_processed, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    fact_meta["effective_mode"],
                    fact_meta["watermark_used"],
                    started_iso,
                    finished_iso,
                    round(total_ms, 2),
                    fact_meta["orders_processed"],
                    fact_meta["details_processed"],
                    fact_meta["returns_processed"],
                    run_status,
                ),
            )

        summary = collect_warehouse_summary(conn, snapshot_date)
        result: Dict[str, Any] = {
            "run_id": run_id,
            "mode": fact_meta["effective_mode"],
            "watermark_used": fact_meta["watermark_used"],
            "snapshot_date": snapshot_date,
            "status": run_status,
            "timings_ms": {
                "staging_load": round(stg_ms, 2),
                "dimensions_and_scd2": round(dim_ms, 2),
                "facts_upsert": round(fact_ms, 2),
                "rfm_and_indexes": round(mart_ms, 2),
                "dq_audit": round(dq_ms, 2),
                "total": round(total_ms, 2),
            },
            "staging_counts": stg_counts,
            "dimension_counts": dim_counts,
            "fact_processed": {
                "orders": fact_meta["orders_processed"],
                "order_items": fact_meta["details_processed"],
                "returns": fact_meta["returns_processed"],
            },
            "rfm_rows": rfm_count,
            "dq_summary": {
                "total_checks": len(dq_results),
                "critical_passed": sum(1 for r in dq_results if r["severity"] == "CRITICAL" and r["status"] == "PASS"),
                "critical_failed": critical_failed,
                "warning_flagged": warning_flagged,
            },
            "dq_results": dq_results,
            "warehouse_summary": summary,
        }

        if not quiet:
            print_run_summary(result)

        return result
    finally:
        conn.close()
