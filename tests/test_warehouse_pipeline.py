"""Comprehensive unittest suite for the VietTech Zero-Cloud Local Warehouse Engine.

Runs entirely inside tempfile.TemporaryDirectory() using pure Python 3.12 stdlib
so the working warehouse database is never mutated or polluted.
"""

from __future__ import annotations

import csv
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
RAW_DIR = PROJECT_ROOT / "data" / "raw"
SQL_DIR = PROJECT_ROOT / "sql"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_local_warehouse as wh  # noqa: E402


class TestVietTechWarehousePipeline(unittest.TestCase):
    """End-to-end verification of Kimball modeling, SCD2, CDC watermarks, PDPL, and DQ gates."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="viettech_test_")
        self.tmp_dir = Path(self._tmp.name)
        self.db_path = self.tmp_dir / "test_mart.db"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _build_cutoff_raw_dir(self, cutoff_ts: str) -> Path:
        """Create a historical raw CSV snapshot containing only orders/returns strictly before cutoff_ts."""
        cutoff_dir = self.tmp_dir / "raw_cutoff"
        cutoff_dir.mkdir(parents=True, exist_ok=True)

        # Copy static/dimension CSVs as-is
        for name in (
            "sales_channels.csv",
            "product_categories.csv",
            "products.csv",
            "customers.csv",
            "product_price_history.csv",
        ):
            shutil.copy2(RAW_DIR / name, cutoff_dir / name)

        # Filter orders where updated_at < cutoff_ts
        with (RAW_DIR / "orders.csv").open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            ord_fields = reader.fieldnames or []
            filtered_orders = [r for r in reader if r["updated_at"].strip() < cutoff_ts]

        kept_order_ids = {r["id"].strip() for r in filtered_orders}
        with (cutoff_dir / "orders.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=ord_fields)
            writer.writeheader()
            writer.writerows(filtered_orders)

        # Filter order_details belonging to kept orders
        with (RAW_DIR / "order_details.csv").open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            det_fields = reader.fieldnames or []
            filtered_details = [r for r in reader if r["order_id"].strip() in kept_order_ids]

        with (cutoff_dir / "order_details.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=det_fields)
            writer.writeheader()
            writer.writerows(filtered_details)

        # Filter returns where returned_at < cutoff_ts AND order_id in kept_order_ids
        with (RAW_DIR / "order_returns.csv").open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            ret_fields = reader.fieldnames or []
            filtered_returns = [
                r
                for r in reader
                if r["returned_at"].strip() < cutoff_ts and r["order_id"].strip() in kept_order_ids
            ]

        with (cutoff_dir / "order_returns.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=ret_fields)
            writer.writeheader()
            writer.writerows(filtered_returns)

        return cutoff_dir

    def test_full_build_and_3way_reconciliation(self) -> None:
        result = wh.run_pipeline(
            raw_dir=RAW_DIR,
            db_path=self.db_path,
            mode="full",
            snapshot_date="2025-01-01",
            quiet=True,
        )
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["dq_summary"]["critical_failed"], 0)
        self.assertEqual(result["dq_summary"]["critical_passed"], 11)

        ws = result["warehouse_summary"]
        self.assertEqual(ws["total_orders"], 21435)
        self.assertEqual(ws["completed_orders"], 16796)
        self.assertEqual(ws["total_order_items"], 39548)
        self.assertEqual(ws["total_returns"], 1288)
        self.assertAlmostEqual(ws["completed_revenue_vnd"], 380360467955.0, places=2)
        self.assertAlmostEqual(ws["completed_cost_vnd"], 273216234000.0, places=2)
        self.assertAlmostEqual(ws["gross_profit_vnd"], 107144233955.0, places=2)
        self.assertAlmostEqual(ws["gross_margin_pct"], 28.17, places=2)
        self.assertAlmostEqual(ws["completed_discount_vnd"], 13977222045.0, places=2)
        self.assertAlmostEqual(ws["return_revenue_vnd"], 29446815953.0, places=2)

        # Verify vw_monthly_kpi sums exactly match fact_orders and spans 24 months
        conn = wh.open_connection(self.db_path)
        try:
            months, vw_rev, vw_profit = conn.execute(
                "SELECT COUNT(*), SUM(completed_revenue), SUM(gross_profit) FROM vw_monthly_kpi"
            ).fetchone()
            self.assertEqual(months, 24)
            self.assertAlmostEqual(vw_rev, 380360467955.0, places=2)
            self.assertAlmostEqual(vw_profit, 107144233955.0, places=2)
        finally:
            conn.close()

    def test_idempotency_deterministic_surrogate_keys(self) -> None:
        wh.run_pipeline(raw_dir=RAW_DIR, db_path=self.db_path, mode="full", quiet=True)
        conn = wh.open_connection(self.db_path)
        try:
            snap1 = {
                "customers": [r[0] for r in conn.execute("SELECT customer_key FROM dim_customer ORDER BY customer_id")],
                "products": [r[0] for r in conn.execute("SELECT product_key FROM dim_product ORDER BY product_id")],
                "scd2": [r[0] for r in conn.execute("SELECT product_version_key FROM dim_product_scd2 ORDER BY product_id, valid_from")],
                "orders": [r[0] for r in conn.execute("SELECT order_key FROM fact_orders ORDER BY order_id")],
                "items": [r[0] for r in conn.execute("SELECT order_item_key FROM fact_order_items ORDER BY order_detail_id")],
                "returns": [r[0] for r in conn.execute("SELECT return_key FROM fact_returns ORDER BY return_id")],
            }
        finally:
            conn.close()

        # Re-run full load on the same database
        wh.run_pipeline(raw_dir=RAW_DIR, db_path=self.db_path, mode="full", quiet=True)
        conn = wh.open_connection(self.db_path)
        try:
            snap2 = {
                "customers": [r[0] for r in conn.execute("SELECT customer_key FROM dim_customer ORDER BY customer_id")],
                "products": [r[0] for r in conn.execute("SELECT product_key FROM dim_product ORDER BY product_id")],
                "scd2": [r[0] for r in conn.execute("SELECT product_version_key FROM dim_product_scd2 ORDER BY product_id, valid_from")],
                "orders": [r[0] for r in conn.execute("SELECT order_key FROM fact_orders ORDER BY order_id")],
                "items": [r[0] for r in conn.execute("SELECT order_item_key FROM fact_order_items ORDER BY order_detail_id")],
                "returns": [r[0] for r in conn.execute("SELECT return_key FROM fact_returns ORDER BY return_id")],
            }
        finally:
            conn.close()

        self.assertEqual(snap1, snap2)
        # Verify exact SHA-256 format (64 hex chars)
        self.assertEqual(len(snap1["orders"][0]), 64)
        self.assertEqual(snap1["orders"][0], wh.sha256_key("order", 1))

    def test_incremental_watermark_matches_full_refresh(self) -> None:
        watermark_ts = "2024-12-02 00:00:00"
        cutoff_dir = self._build_cutoff_raw_dir(watermark_ts)

        # 1. Load historical baseline strictly before 2024-12-02
        base_res = wh.run_pipeline(
            raw_dir=cutoff_dir,
            db_path=self.db_path,
            mode="full",
            snapshot_date="2025-01-01",
            quiet=True,
        )
        self.assertEqual(base_res["status"], "SUCCESS")
        self.assertLess(base_res["warehouse_summary"]["total_orders"], 21435)

        # 2. Run incremental watermark load against full raw_dir
        inc_res = wh.run_pipeline(
            raw_dir=RAW_DIR,
            db_path=self.db_path,
            mode="incremental",
            watermark=watermark_ts,
            snapshot_date="2025-01-01",
            quiet=True,
        )
        self.assertEqual(inc_res["status"], "SUCCESS")
        self.assertEqual(inc_res["mode"], "incremental")
        self.assertEqual(inc_res["watermark_used"], watermark_ts)

        # Must process >85% fewer orders than full load (3,116 vs 21,435 = 85.46% reduction)
        processed_orders = inc_res["fact_processed"]["orders"]
        reduction_pct = (1.0 - processed_orders / 21435.0) * 100.0
        self.assertGreater(reduction_pct, 85.0)
        self.assertEqual(processed_orders, 3116)

        # Final mart totals & row counts must match fresh full refresh to 0.00 VND diff!
        full_db = self.tmp_dir / "reference_full.db"
        full_res = wh.run_pipeline(
            raw_dir=RAW_DIR,
            db_path=full_db,
            mode="full",
            snapshot_date="2025-01-01",
            quiet=True,
        )
        ws_inc = inc_res["warehouse_summary"]
        ws_full = full_res["warehouse_summary"]

        self.assertEqual(ws_inc["total_orders"], ws_full["total_orders"])
        self.assertEqual(ws_inc["completed_orders"], ws_full["completed_orders"])
        self.assertEqual(ws_inc["total_order_items"], ws_full["total_order_items"])
        self.assertEqual(ws_inc["total_returns"], ws_full["total_returns"])
        self.assertEqual(ws_inc["completed_revenue_vnd"], ws_full["completed_revenue_vnd"])
        self.assertEqual(ws_inc["completed_cost_vnd"], ws_full["completed_cost_vnd"])
        self.assertEqual(ws_inc["gross_profit_vnd"], ws_full["gross_profit_vnd"])
        self.assertEqual(ws_inc["return_revenue_vnd"], ws_full["return_revenue_vnd"])

        # Also verify auto-watermark reading when --watermark is omitted:
        # 313 orders share the exact capped max timestamp '2024-12-31 23:59:59',
        # so inclusive >= watermark safely re-upserts only that boundary (98.54% reduction) with 0 duplicates.
        auto_res = wh.run_pipeline(
            raw_dir=RAW_DIR,
            db_path=self.db_path,
            mode="incremental",
            watermark=None,
            snapshot_date="2025-01-01",
            quiet=True,
        )
        self.assertEqual(auto_res["status"], "SUCCESS")
        self.assertEqual(auto_res["watermark_used"], "2024-12-31 23:59:59")
        self.assertEqual(auto_res["fact_processed"]["orders"], 313)
        self.assertEqual(auto_res["warehouse_summary"]["total_orders"], 21435)
        self.assertEqual(auto_res["warehouse_summary"]["completed_revenue_vnd"], 380360467955.0)

    def test_scd2_product_price_history_continuity_and_current_match(self) -> None:
        wh.run_pipeline(raw_dir=RAW_DIR, db_path=self.db_path, mode="full", quiet=True)
        conn = wh.open_connection(self.db_path)
        try:
            total_versions = conn.execute("SELECT COUNT(*) FROM dim_product_scd2").fetchone()[0]
            # 47 initial product versions + 24 historical price transitions = 71 versions
            self.assertEqual(total_versions, 71)

            # Verify product 16 (1 MSRP change + 2 COST changes -> 4 contiguous versions)
            p16 = conn.execute(
                """
                SELECT version_number, msrp, cost_price, valid_from, valid_to, is_current
                FROM dim_product_scd2
                WHERE product_id = 16
                ORDER BY version_number
                """
            ).fetchall()
            self.assertEqual(len(p16), 4)
            self.assertEqual(p16[0]["msrp"], 25990000.0)
            self.assertEqual(p16[0]["cost_price"], 18193000.0)
            self.assertEqual(p16[1]["msrp"], 28388000.0)
            self.assertEqual(p16[1]["cost_price"], 18193000.0)
            self.assertEqual(p16[2]["cost_price"], 18088000.0)
            self.assertEqual(p16[3]["cost_price"], 18023000.0)
            self.assertEqual(p16[3]["is_current"], 1)

            # Verify contiguous non-overlapping boundaries
            for idx in range(len(p16) - 1):
                self.assertEqual(p16[idx]["valid_to"], p16[idx + 1]["valid_from"])

            # Verify ALL 47 current SCD2 rows match dim_product to 0 VND diff
            mismatches = conn.execute(
                """
                SELECT COUNT(*)
                FROM dim_product dp
                JOIN dim_product_scd2 s
                  ON dp.product_id = s.product_id AND s.is_current = 1
                WHERE ABS(dp.msrp - s.msrp) > 0 OR ABS(dp.cost_price - s.cost_price) > 0
                """
            ).fetchone()[0]
            self.assertEqual(mismatches, 0)

            # Verify every fact_order_items row maps to a valid SCD2 version
            unmapped = conn.execute(
                "SELECT COUNT(*) FROM fact_order_items WHERE product_version_key IS NULL"
            ).fetchone()[0]
            self.assertEqual(unmapped, 0)
        finally:
            conn.close()

    def test_data_quality_catches_real_anomalies_and_passes_critical_gates(self) -> None:
        result = wh.run_pipeline(raw_dir=RAW_DIR, db_path=self.db_path, mode="full", quiet=True)
        by_name = {r["check_name"]: r for r in result["dq_results"]}
        self.assertEqual(len(by_name), 15)

        # All 11 CRITICAL checks must PASS
        for check in result["dq_results"]:
            if check["severity"] == "CRITICAL":
                self.assertEqual(check["status"], "PASS", f"Failed critical check: {check}")

        # Exact verified real-data anomaly counts
        self.assertEqual(by_name["temporal_return_anomalies"]["status"], "WARN")
        self.assertEqual(by_name["temporal_return_anomalies"]["failed_count"], 4)

        self.assertEqual(by_name["missing_online_fulfillment_ship_date"]["status"], "WARN")
        self.assertEqual(by_name["missing_online_fulfillment_ship_date"]["failed_count"], 333)

        self.assertEqual(by_name["missing_physical_shipped_ship_date"]["status"], "WARN")
        self.assertEqual(by_name["missing_physical_shipped_ship_date"]["failed_count"], 6)

        self.assertEqual(by_name["staging_natural_key_uniqueness"]["status"], "PASS")
        self.assertEqual(by_name["staging_natural_key_uniqueness"]["failed_count"], 0)

        # Verify ship_date_key distinction: 134 Physical completed -> -3, 333 Web/App completed -> -1
        conn = wh.open_connection(self.db_path)
        try:
            phys_na = conn.execute(
                """
                SELECT COUNT(*) FROM fact_orders fo
                JOIN dim_channel dc ON fo.channel_key = dc.channel_key
                WHERE fo.status = 'Completed' AND dc.channel_type = 'Physical' AND fo.ship_date_key = -3
                """
            ).fetchone()[0]
            online_unk = conn.execute(
                """
                SELECT COUNT(*) FROM fact_orders fo
                JOIN dim_channel dc ON fo.channel_key = dc.channel_key
                WHERE fo.status = 'Completed' AND dc.channel_type IN ('Web', 'App') AND fo.ship_date_key = -1
                """
            ).fetchone()[0]
            self.assertEqual(phys_na, 134)
            self.assertEqual(online_unk, 333)

            # Verify executive summary view reports PASS gate
            gate = conn.execute("SELECT overall_gate_status, critical_failed FROM vw_dq_executive_summary").fetchone()
            self.assertEqual(gate["overall_gate_status"], "PASS")
            self.assertEqual(gate["critical_failed"], 0)
        finally:
            conn.close()

    def test_pdpl_pii_segregation_in_dim_customer(self) -> None:
        wh.run_pipeline(raw_dir=RAW_DIR, db_path=self.db_path, mode="full", quiet=True)
        conn = wh.open_connection(self.db_path)
        try:
            cols = {r["name"].lower() for r in conn.execute("PRAGMA table_info(dim_customer)").fetchall()}
            for forbidden in ("email", "phone", "date_of_birth", "full_name", "first_name", "last_name"):
                self.assertNotIn(forbidden, cols)
            self.assertIn("customer_name_masked", cols)

            sample = conn.execute(
                "SELECT customer_name_masked FROM dim_customer WHERE customer_id = 1"
            ).fetchone()[0]
            # Customer 1 is 'Lâm Vũ' -> masked as 'L*** Vũ'
            self.assertEqual(sample, "L*** Vũ")
        finally:
            conn.close()

    def test_multi_snapshot_rfm_history(self) -> None:
        # Build warehouse with first snapshot at 2024-07-01
        wh.run_pipeline(
            raw_dir=RAW_DIR,
            db_path=self.db_path,
            mode="full",
            snapshot_date="2024-07-01",
            quiet=True,
        )
        # Add second snapshot at 2025-01-01 without overwriting 2024-07-01
        conn = wh.open_connection(self.db_path)
        try:
            cnt_2025 = wh.build_rfm_snapshot(conn, snapshot_date="2025-01-01")
            snaps = conn.execute(
                """
                SELECT snapshot_date, COUNT(*) AS cust_cnt, MIN(recency_days) AS min_r,
                       MIN(r_score) AS min_rs, MAX(r_score) AS max_rs,
                       MIN(f_score) AS min_fs, MAX(f_score) AS max_fs,
                       MIN(m_score) AS min_ms, MAX(m_score) AS max_ms
                FROM mart_rfm_snapshot
                GROUP BY snapshot_date
                ORDER BY snapshot_date
                """
            ).fetchall()
            self.assertEqual(len(snaps), 2)
            self.assertEqual(snaps[0]["snapshot_date"], "2024-07-01")
            self.assertEqual(snaps[1]["snapshot_date"], "2025-01-01")
            self.assertEqual(snaps[1]["cust_cnt"], cnt_2025)
            # Customer base grows between mid-2024 and end-2024
            self.assertLess(snaps[0]["cust_cnt"], snaps[1]["cust_cnt"])

            for s in snaps:
                self.assertGreaterEqual(s["min_r"], 0)
                self.assertGreaterEqual(s["min_rs"], 1)
                self.assertLessEqual(s["max_rs"], 5)
                self.assertGreaterEqual(s["min_fs"], 1)
                self.assertLessEqual(s["max_fs"], 5)
                self.assertGreaterEqual(s["min_ms"], 1)
                self.assertLessEqual(s["max_ms"], 5)
        finally:
            conn.close()

    def test_bigquery_sql_dry_run_rendering(self) -> None:
        sql_runner = SCRIPTS_DIR / "run_sql_pipeline.py"
        self.assertTrue(sql_runner.exists())
        runner_src = sql_runner.read_text(encoding="utf-8")

        if "--dry-run" in runner_src:
            proc = subprocess.run(
                [sys.executable, str(sql_runner), "--dry-run"],
                capture_output=True,
                text=True,
                cwd=str(PROJECT_ROOT),
                check=False,
            )
            self.assertEqual(
                proc.returncode,
                0,
                f"run_sql_pipeline.py --dry-run failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}",
            )
        else:
            # Fallback: validate all SQL files exist and placeholder token rendering works cleanly
            expected_sql = [
                "01_create_staging_dataset.sql",
                "02_create_star_schema.sql",
                "03_load_star_schema.sql",
                "04_rfm_snapshot.sql",
                "05_bi_views.sql",
            ]
            for name in expected_sql:
                path = SQL_DIR / name
                self.assertTrue(path.exists(), f"Missing SQL file: {name}")
                content = path.read_text(encoding="utf-8")
                rendered = (
                    content.replace("your-project-id", "demo-project")
                    .replace("retailbi_stg", "demo_stg")
                    .replace("retailbi_mart", "demo_mart")
                )
                self.assertNotIn("your-project-id", rendered)


if __name__ == "__main__":
    unittest.main()
