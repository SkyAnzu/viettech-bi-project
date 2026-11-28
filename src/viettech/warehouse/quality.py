"""15-Check Automated Data Quality & 3-Way Financial Reconciliation Engine."""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List

from viettech.common.utils import utc_now_iso


def _make_dq_record(
    run_id: str,
    checked_at: str,
    num: int,
    name: str,
    severity: str,
    expected_rule: str,
    observed_value: str,
    failed_count: int,
    details: str = "",
) -> Dict[str, Any]:
    if severity == "CRITICAL":
        status = "PASS" if failed_count == 0 else "FAIL"
    else:
        status = "PASS" if failed_count == 0 else "WARN"
    return {
        "run_id": run_id,
        "check_number": num,
        "check_name": name,
        "severity": severity,
        "status": status,
        "expected_rule": expected_rule,
        "observed_value": observed_value,
        "failed_count": int(failed_count),
        "details": details,
        "checked_at": checked_at,
    }


def _check_pk_fk_integrity(
    conn: sqlite3.Connection,
    run_id: str,
    checked_at: str,
) -> List[Dict[str, Any]]:
    """Checks 1-2: PK uniqueness & NOT NULL across 9 tables, and zero orphan FKs."""
    checks: List[Dict[str, Any]] = []

    pk_tables = [
        ("dim_date", "date_key"),
        ("dim_customer", "customer_key"),
        ("dim_product", "product_key"),
        ("dim_product_scd2", "product_version_key"),
        ("dim_channel", "channel_key"),
        ("dim_payment", "payment_key"),
        ("fact_orders", "order_key"),
        ("fact_order_items", "order_item_key"),
        ("fact_returns", "return_key"),
    ]
    pk_violations = 0
    pk_counts: List[str] = []
    for tbl, col in pk_tables:
        total, distinct_cnt, null_cnt = conn.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT {col}), SUM(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) FROM {tbl}"
        ).fetchone()
        pk_violations += (total - distinct_cnt) + (null_cnt or 0)
        pk_counts.append(f"{tbl}={total}")
    checks.append(
        _make_dq_record(
            run_id, checked_at, 1, "pk_uniqueness_and_not_null", "CRITICAL",
            "0 NULL or duplicate PKs across 9 star-schema tables",
            f"violations={pk_violations} ({', '.join(pk_counts)})",
            pk_violations,
        )
    )

    fk_checks = [
        ("fact_orders.customer_key", "SELECT COUNT(*) FROM fact_orders f LEFT JOIN dim_customer d ON f.customer_key = d.customer_key WHERE d.customer_key IS NULL"),
        ("fact_orders.channel_key", "SELECT COUNT(*) FROM fact_orders f LEFT JOIN dim_channel d ON f.channel_key = d.channel_key WHERE d.channel_key IS NULL"),
        ("fact_orders.payment_key", "SELECT COUNT(*) FROM fact_orders f LEFT JOIN dim_payment d ON f.payment_key = d.payment_key WHERE d.payment_key IS NULL"),
        ("fact_orders.order_date_key", "SELECT COUNT(*) FROM fact_orders f LEFT JOIN dim_date d ON f.order_date_key = d.date_key WHERE d.date_key IS NULL"),
        ("fact_orders.ship_date_key", "SELECT COUNT(*) FROM fact_orders f LEFT JOIN dim_date d ON f.ship_date_key = d.date_key WHERE d.date_key IS NULL"),
        ("fact_order_items.order_key", "SELECT COUNT(*) FROM fact_order_items f LEFT JOIN fact_orders d ON f.order_key = d.order_key WHERE d.order_key IS NULL"),
        ("fact_order_items.product_key", "SELECT COUNT(*) FROM fact_order_items f LEFT JOIN dim_product d ON f.product_key = d.product_key WHERE d.product_key IS NULL"),
        ("fact_order_items.product_version_key", "SELECT COUNT(*) FROM fact_order_items f LEFT JOIN dim_product_scd2 d ON f.product_version_key = d.product_version_key WHERE d.product_version_key IS NULL"),
        ("fact_order_items.order_date_key", "SELECT COUNT(*) FROM fact_order_items f LEFT JOIN dim_date d ON f.order_date_key = d.date_key WHERE d.date_key IS NULL"),
        ("fact_returns.order_key", "SELECT COUNT(*) FROM fact_returns f LEFT JOIN fact_orders d ON f.order_key = d.order_key WHERE d.order_key IS NULL"),
        ("fact_returns.returned_date_key", "SELECT COUNT(*) FROM fact_returns f LEFT JOIN dim_date d ON f.returned_date_key = d.date_key WHERE d.date_key IS NULL"),
    ]
    orphan_total = 0
    orphan_details: List[str] = []
    for label, sql in fk_checks:
        cnt = int(conn.execute(sql).fetchone()[0])
        if cnt:
            orphan_total += cnt
            orphan_details.append(f"{label}={cnt}")
    checks.append(
        _make_dq_record(
            run_id, checked_at, 2, "zero_orphan_foreign_keys", "CRITICAL",
            "0 orphan FK references across fact_orders, fact_order_items, fact_returns",
            f"orphans={orphan_total}",
            orphan_total,
            "; ".join(orphan_details) if orphan_details else "All 11 FK paths verified 100% intact",
        )
    )
    return checks


def _check_reconciliation_and_scd2(
    conn: sqlite3.Connection,
    run_id: str,
    checked_at: str,
) -> List[Dict[str, Any]]:
    """Checks 3-8: 3-way reconciliation, KPI view parity, arithmetic invariants, and SCD2 integrity."""
    checks: List[Dict[str, Any]] = []

    stg_rev, stg_cost = conn.execute(
        "SELECT COALESCE(SUM(net_amount), 0), COALESCE(SUM(total_cost), 0) FROM stg_orders WHERE status = 'Completed'"
    ).fetchone()
    fo_rev, fo_cost = conn.execute(
        "SELECT COALESCE(SUM(net_amount), 0), COALESCE(SUM(total_cost), 0) FROM fact_orders WHERE status = 'Completed'"
    ).fetchone()
    foi_rev, foi_cost = conn.execute(
        """
        SELECT COALESCE(SUM(foi.line_total), 0), COALESCE(SUM(foi.line_cost), 0)
        FROM fact_order_items foi
        JOIN fact_orders fo ON foi.order_key = fo.order_key
        WHERE fo.status = 'Completed'
        """
    ).fetchone()
    max_diff = max(
        abs(stg_rev - fo_rev),
        abs(fo_rev - foi_rev),
        abs(stg_cost - fo_cost),
        abs(fo_cost - foi_cost),
    )
    checks.append(
        _make_dq_record(
            run_id, checked_at, 3, "three_way_financial_reconciliation", "CRITICAL",
            "ABS(stg_orders - fact_orders) < 0.01 AND ABS(fact_orders - fact_order_items) < 0.01",
            f"completed_rev={fo_rev:,.0f} VND, completed_cost={fo_cost:,.0f} VND, max_diff={max_diff:.4f} VND",
            0 if max_diff < 0.01 else 1,
            f"stg_rev={stg_rev:,.2f} | fact_orders_rev={fo_rev:,.2f} | fact_items_rev={foi_rev:,.2f}",
        )
    )

    vw_rev, vw_profit = conn.execute(
        "SELECT COALESCE(SUM(completed_revenue), 0), COALESCE(SUM(gross_profit), 0) FROM vw_monthly_kpi"
    ).fetchone()
    vw_diff = max(abs(vw_rev - fo_rev), abs(vw_profit - (fo_rev - fo_cost)))
    checks.append(
        _make_dq_record(
            run_id, checked_at, 4, "vw_monthly_kpi_reconciliation", "CRITICAL",
            "vw_monthly_kpi revenue & gross_profit match fact_orders (diff < 0.01 VND)",
            f"vw_rev={vw_rev:,.0f} VND, vw_profit={vw_profit:,.0f} VND, diff={vw_diff:.4f} VND",
            0 if vw_diff < 0.01 else 1,
        )
    )

    ord_arith_fail = int(
        conn.execute(
            "SELECT COUNT(*) FROM fact_orders WHERE ABS(net_amount - (total_amount - total_discount)) >= 0.01"
        ).fetchone()[0]
    )
    checks.append(
        _make_dq_record(
            run_id, checked_at, 5, "fact_orders_arithmetic_invariant", "CRITICAL",
            "net_amount = total_amount - total_discount (tol < 0.01 VND)",
            f"mismatched_rows={ord_arith_fail}",
            ord_arith_fail,
        )
    )

    item_arith_fail = int(
        conn.execute(
            "SELECT COUNT(*) FROM fact_order_items WHERE ABS(line_profit - (line_total - line_cost)) >= 0.01"
        ).fetchone()[0]
    )
    checks.append(
        _make_dq_record(
            run_id, checked_at, 6, "fact_order_items_arithmetic_invariant", "CRITICAL",
            "line_profit = line_total - line_cost (tol < 0.01 VND)",
            f"mismatched_rows={item_arith_fail}",
            item_arith_fail,
        )
    )

    bad_bounds = int(conn.execute("SELECT COUNT(*) FROM dim_product_scd2 WHERE valid_from >= valid_to").fetchone()[0])
    overlaps_or_gaps = int(
        conn.execute(
            """
            WITH ordered AS (
                SELECT
                    product_id, valid_from, valid_to,
                    LEAD(valid_from) OVER (PARTITION BY product_id ORDER BY valid_from) AS next_valid_from
                FROM dim_product_scd2
            )
            SELECT COUNT(*) FROM ordered
            WHERE next_valid_from IS NOT NULL AND valid_to != next_valid_from
            """
        ).fetchone()[0]
    )
    bad_current_counts = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT product_id, SUM(is_current) AS curr_cnt
                FROM dim_product_scd2 GROUP BY product_id HAVING curr_cnt != 1
            )
            """
        ).fetchone()[0]
    )
    price_mismatches = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM dim_product dp
            LEFT JOIN dim_product_scd2 s ON dp.product_id = s.product_id AND s.is_current = 1
            WHERE s.product_version_key IS NULL
               OR ABS(dp.msrp - s.msrp) >= 0.01
               OR ABS(dp.cost_price - s.cost_price) >= 0.01
            """
        ).fetchone()[0]
    )
    scd2_total_versions = int(conn.execute("SELECT COUNT(*) FROM dim_product_scd2").fetchone()[0])
    scd2_fail = bad_bounds + overlaps_or_gaps + bad_current_counts + price_mismatches
    checks.append(
        _make_dq_record(
            run_id, checked_at, 7, "dim_product_scd2_integrity", "CRITICAL",
            "Contiguous non-overlapping SCD2 intervals and is_current=1 matches dim_product 100%",
            f"versions={scd2_total_versions}, violations={scd2_fail}",
            scd2_fail,
            f"bad_bounds={bad_bounds}, gaps_or_overlaps={overlaps_or_gaps}, bad_current={bad_current_counts}, price_mismatch={price_mismatches}",
        )
    )

    scd2_unmapped = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM fact_order_items foi
            JOIN fact_orders fo ON foi.order_key = fo.order_key
            LEFT JOIN dim_product_scd2 dp2
              ON foi.product_version_key = dp2.product_version_key
             AND dp2.valid_from <= fo.order_date
             AND fo.order_date < dp2.valid_to
            WHERE dp2.product_version_key IS NULL
            """
        ).fetchone()[0]
    )
    total_items = int(conn.execute("SELECT COUNT(*) FROM fact_order_items").fetchone()[0])
    checks.append(
        _make_dq_record(
            run_id, checked_at, 8, "fact_order_items_scd2_coverage", "CRITICAL",
            "100% of fact_order_items map to valid point-in-time dim_product_scd2 version",
            f"mapped={total_items - scd2_unmapped}/{total_items}, unmapped={scd2_unmapped}",
            scd2_unmapped,
        )
    )
    return checks


def _check_pdpl_and_rfm(
    conn: sqlite3.Connection,
    run_id: str,
    checked_at: str,
) -> List[Dict[str, Any]]:
    """Checks 9-11: PDPL privacy, RFM bounds/uniqueness, and return status integrity."""
    checks: List[Dict[str, Any]] = []

    forbidden_cols = {"email", "phone", "date_of_birth", "full_name", "first_name", "last_name"}
    actual_cols = {row["name"].lower() for row in conn.execute("PRAGMA table_info(dim_customer)").fetchall()}
    exposed_pii_cols = sorted(forbidden_cols.intersection(actual_cols))
    unmasked_emails = int(
        conn.execute("SELECT COUNT(*) FROM dim_customer WHERE customer_name_masked LIKE '%@%'").fetchone()[0]
    )
    checks.append(
        _make_dq_record(
            run_id, checked_at, 9, "dim_customer_pdpl_privacy", "CRITICAL",
            "Zero PII columns (email, phone, date_of_birth, raw name) exposed in dim_customer",
            f"exposed_cols={exposed_pii_cols}, email_leaks={unmasked_emails}",
            len(exposed_pii_cols) + unmasked_emails,
            f"Approved mart columns: {sorted(actual_cols)}",
        )
    )

    rfm_bounds_fail = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM mart_rfm_snapshot
            WHERE r_score NOT BETWEEN 1 AND 5
               OR f_score NOT BETWEEN 1 AND 5
               OR m_score NOT BETWEEN 1 AND 5
               OR recency_days < 0
            """
        ).fetchone()[0]
    )
    rfm_dups = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT snapshot_date, customer_key, COUNT(*) AS c
                FROM mart_rfm_snapshot
                GROUP BY snapshot_date, customer_key
                HAVING c > 1
            )
            """
        ).fetchone()[0]
    )
    rfm_rows = int(conn.execute("SELECT COUNT(*) FROM mart_rfm_snapshot").fetchone()[0])
    checks.append(
        _make_dq_record(
            run_id, checked_at, 10, "mart_rfm_snapshot_bounds_and_uniqueness", "CRITICAL",
            "r/f/m scores in 1..5, recency_days >= 0, 0 duplicate (snapshot_date, customer_key)",
            f"rows={rfm_rows}, bounds_fail={rfm_bounds_fail}, duplicates={rfm_dups}",
            rfm_bounds_fail + rfm_dups,
        )
    )

    ret_status_fail = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM fact_returns fr
            JOIN fact_orders fo ON fr.order_key = fo.order_key
            WHERE fo.status != 'Returned'
            """
        ).fetchone()[0]
    )
    checks.append(
        _make_dq_record(
            run_id, checked_at, 11, "return_order_status_integrity", "CRITICAL",
            "Every fact_returns row links to a fact_orders row with status = 'Returned'",
            f"mismatched_status_rows={ret_status_fail}",
            ret_status_fail,
        )
    )
    return checks


def _check_source_warnings(
    conn: sqlite3.Connection,
    run_id: str,
    checked_at: str,
) -> List[Dict[str, Any]]:
    """Checks 12-15: Monitored WARNING anomaly & staging hygiene checks."""
    checks: List[Dict[str, Any]] = []

    temporal_rows = conn.execute(
        """
        SELECT fr.return_id, fr.order_id, fo.order_date, fr.returned_at
        FROM fact_returns fr
        JOIN fact_orders fo ON fr.order_key = fo.order_key
        WHERE fr.is_temporal_anomaly = 1
        ORDER BY fr.return_id
        """
    ).fetchall()
    anom_ids = [r["return_id"] for r in temporal_rows]
    checks.append(
        _make_dq_record(
            run_id, checked_at, 12, "temporal_return_anomalies", "WARNING",
            "Monitor returns with returned_at < order_date and isolate from avg_return_lead_days",
            f"flagged_rows={len(temporal_rows)} (return_ids={anom_ids})",
            len(temporal_rows),
            "Isolated via is_temporal_anomaly = 1 so vw_return_metrics.avg_return_lead_days remains unskewed",
        )
    )

    missing_online_ship = int(
        conn.execute("SELECT COUNT(*) FROM fact_orders WHERE is_missing_online_ship_date = 1").fetchone()[0]
    )
    phys_pickup = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM fact_orders fo
            JOIN dim_channel dc ON fo.channel_key = dc.channel_key
            WHERE fo.status = 'Completed' AND fo.ship_date IS NULL AND dc.channel_type = 'Physical'
            """
        ).fetchone()[0]
    )
    checks.append(
        _make_dq_record(
            run_id, checked_at, 13, "missing_online_fulfillment_ship_date", "WARNING",
            "Monitor Web/App Completed orders missing ship_date (mapped to sentinel -1; Physical pickup mapped to -3)",
            f"online_missing={missing_online_ship}, physical_pickup_sentinels={phys_pickup}",
            missing_online_ship,
            "Distinguishes genuine online logistics gaps (-1) from valid in-store Physical pickup (-3)",
        )
    )

    missing_phys_shipped = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM fact_orders fo
            JOIN dim_channel dc ON fo.channel_key = dc.channel_key
            WHERE fo.status = 'Shipped'
              AND dc.channel_type = 'Physical'
              AND (fo.ship_date IS NULL OR TRIM(fo.ship_date) = '')
            """
        ).fetchone()[0]
    )
    checks.append(
        _make_dq_record(
            run_id, checked_at, 14, "missing_physical_shipped_ship_date", "WARNING",
            "Monitor Physical channel orders with status='Shipped' but NULL ship_date",
            f"flagged_rows={missing_phys_shipped}",
            missing_phys_shipped,
            "Mapped to ship_date_key = -1 (Unknown) for fulfillment ops follow-up",
        )
    )

    dup_ord = int(conn.execute("SELECT COUNT(*) - COUNT(DISTINCT order_code) FROM stg_orders").fetchone()[0])
    dup_cust = int(conn.execute("SELECT COUNT(*) - COUNT(DISTINCT customer_code) FROM stg_customers").fetchone()[0])
    dup_prod = int(conn.execute("SELECT COUNT(*) - COUNT(DISTINCT product_code) FROM stg_products").fetchone()[0])
    dup_total = dup_ord + dup_cust + dup_prod
    checks.append(
        _make_dq_record(
            run_id, checked_at, 15, "staging_natural_key_uniqueness", "WARNING",
            "0 duplicate business codes across stg_orders, stg_customers, stg_products",
            f"duplicates={dup_total} (orders={dup_ord}, customers={dup_cust}, products={dup_prod})",
            dup_total,
        )
    )
    return checks


def run_data_quality_audit(conn: sqlite3.Connection, run_id: str) -> List[Dict[str, Any]]:
    """Run all 15 automated DQ checks (11 CRITICAL + 4 WARNING) and persist into dq_audit_results."""
    checked_at = utc_now_iso()
    results = (
        _check_pk_fk_integrity(conn, run_id, checked_at)
        + _check_reconciliation_and_scd2(conn, run_id, checked_at)
        + _check_pdpl_and_rfm(conn, run_id, checked_at)
        + _check_source_warnings(conn, run_id, checked_at)
    )

    with conn:
        conn.executemany(
            """
            INSERT INTO dq_audit_results (
                run_id, check_number, check_name, severity, status,
                expected_rule, observed_value, failed_count, details, checked_at
            )
            VALUES (
                :run_id, :check_number, :check_name, :severity, :status,
                :expected_rule, :observed_value, :failed_count, :details, :checked_at
            )
            """,
            results,
        )

    return results
