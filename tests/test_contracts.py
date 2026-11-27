"""Comprehensive unittest suite for VietTech BI Pre-Ingestion Data Contracts,
Dead-Letter Quarantine Routing, Volume-Drift Detection, PDPL PII Governance,
and Lineage Manifest Generation.

Runs purely on Python 3.12 stdlib using tempfile.TemporaryDirectory() so live
raw data and warehouse files are never mutated.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
RAW_DIR = PROJECT_ROOT / "data" / "raw"
CONTRACT_PATH = PROJECT_ROOT / "config" / "data_contracts.json"
LINEAGE_PATH = PROJECT_ROOT / "benchmarks" / "lineage_manifest.json"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import contract_validator as cv  # noqa: E402


class TestVietTechDataContracts(unittest.TestCase):
    """Verify pre-ingestion data contracts, quarantine routing, volume drift, and PDPL lineage."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="viettech_contracts_test_")
        self.tmp_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _copy_all_raw_csvs(self, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        for csv_file in RAW_DIR.glob("*.csv"):
            shutil.copy2(csv_file, dest_dir / csv_file.name)
        return dest_dir

    def test_clean_validation_pass_on_real_raw_data_and_lineage_emission(self) -> None:
        db_path = self.tmp_dir / "quarantine_test.db"
        lineage_out = self.tmp_dir / "lineage_manifest.json"

        result = cv.validate_raw_directory(
            raw_dir=RAW_DIR,
            contract_path=CONTRACT_PATH,
            db_path=db_path,
        )

        # 0 critical errors across all 8 CSVs and all 67,364 rows
        self.assertTrue(result["critical_gate_passed"])
        self.assertEqual(result["overall_status"], "PASS_WITH_WARNINGS")
        summary = result["summary"]
        self.assertEqual(summary["tables_checked"], 8)
        self.assertEqual(summary["total_rows"], 67364)
        self.assertEqual(summary["total_clean_rows"], 67364)
        self.assertEqual(summary["total_quarantined_rows"], 0)
        self.assertEqual(summary["critical_error_count"], 0)
        self.assertEqual(summary["volume_drift_alert_count"], 0)
        self.assertEqual(summary["null_ratio_alert_count"], 0)

        # Every raw CSV should have BOM cleanly detected and stripped
        for table_name, tres in result["tables"].items():
            self.assertEqual(tres["status"], "PASS", f"Unexpected non-PASS status for {table_name}: {tres}")
            self.assertTrue(tres["had_bom"], f"Expected UTF-8 BOM detection on {table_name}.csv")
            self.assertEqual(tres["volume_check"]["status"], "PASS")

        # Exact 4 temporal return warnings (ret_id = 800, 1100, 1187, 1211)
        warnings = result["temporal_warnings"]
        self.assertEqual(len(warnings), 4)
        self.assertEqual(summary["temporal_warning_count"], 4)
        flagged_return_ids = {w["natural_key"] for w in warnings}
        self.assertEqual(flagged_return_ids, {"800", "1100", "1187", "1211"})
        for w in warnings:
            self.assertEqual(w["severity"], "WARNING")
            self.assertEqual(w["error_code"], "TEMPORAL_RETURN_BEFORE_ORDER")
            self.assertEqual(w["table_name"], "order_returns")
            self.assertLess(w["lead_time_days"], 0)

        # Verify SQLite quarantine persistence wrote the 4 WARNING candidates
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute(
                "SELECT table_name, natural_key, severity, error_code FROM dq_quarantine_records ORDER BY CAST(natural_key AS INTEGER)"
            ).fetchall()
            self.assertEqual(len(rows), 4)
            self.assertEqual([r[1] for r in rows], ["800", "1100", "1187", "1211"])
            self.assertTrue(all(r[2] == "WARNING" and r[3] == "TEMPORAL_RETURN_BEFORE_ORDER" for r in rows))
        finally:
            conn.close()

        # Emit and validate lineage manifest DAG structure
        manifest = cv.export_lineage_manifest(
            output_path=lineage_out,
            contract=CONTRACT_PATH,
            validation_summary=result,
        )
        self.assertTrue(lineage_out.exists())
        loaded = json.loads(lineage_out.read_text(encoding="utf-8"))
        self.assertEqual(loaded["contract_version"], "1.0.0")
        self.assertEqual(loaded["summary"]["node_counts_by_layer"], {
            "raw_csv": 8,
            "staging": 8,
            "dimension": 6,
            "fact": 3,
            "rfm_mart": 1,
            "bi_view": 5,
            "analytics_mining_mart": 3,
            "bi_dashboard_page": 4,
        })

        node_ids = {n["id"] for n in loaded["nodes"]}
        nodes_by_id = {n["id"]: n for n in loaded["nodes"]}
        self.assertIn("mart.dim_product_scd2", node_ids)
        self.assertIn("mart.mart_rfm_snapshot", node_ids)

        # Exact 3 analytics mining marts built by scripts/analytics_mining.py
        mining_node_ids = {
            n["id"] for n in loaded["nodes"] if n["layer"] == "analytics_mining_mart"
        }
        self.assertEqual(
            mining_node_ids,
            {
                "mining.mart_market_basket_rules",
                "mining.mart_rfm_kmeans_clusters",
                "mining.mart_return_risk_scores",
            },
        )
        # SCD2 surrogate key prefix must match sql/03_load_star_schema.sql & run_local_warehouse.py
        scd2_sk = nodes_by_id["mart.dim_product_scd2"]["surrogate_key"]
        self.assertIn("product_scd2|", scd2_sk)
        self.assertNotIn("product_v2|", scd2_sk)

        # Anti-leakage DAG check: fact_returns must NOT feed mart_return_risk_scores as a feature
        return_risk_sources = {
            e["source"] for e in loaded["edges"] if e["target"] == "mining.mart_return_risk_scores"
        }
        self.assertNotIn("mart.fact_returns", return_risk_sources)
        self.assertEqual(
            return_risk_sources,
            {
                "mart.fact_orders",
                "mart.fact_order_items",
                "mart.dim_product",
                "mart.dim_channel",
                "mart.dim_date",
                "mart.dim_customer",
            },
        )

        self.assertGreater(len(loaded["edges"]), 30)
        for edge in loaded["edges"]:
            self.assertIn(edge["source"], node_ids, f"Dangling edge source: {edge}")
            self.assertIn(edge["target"], node_ids, f"Dangling edge target: {edge}")
            self.assertTrue(edge["transformation_type"])
            self.assertIsInstance(edge["pii_tags"], list)
            self.assertGreater(len(edge["pii_tags"]), 0)

        # Verify CLI exits 0 on real data/raw
        cli_lineage = self.tmp_dir / "cli_lineage.json"
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "contract_validator.py"),
                "--raw-dir",
                str(RAW_DIR),
                "--contract",
                str(CONTRACT_PATH),
                "--emit-lineage",
                str(cli_lineage),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"CLI failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
        self.assertTrue(cli_lineage.exists())

    def test_schema_drift_failure_on_missing_and_extra_columns(self) -> None:
        contract = cv.load_contract(CONTRACT_PATH)

        # 1. Missing required column ('payment_method') in orders.csv
        bad_orders_csv = self.tmp_dir / "orders_missing_col.csv"
        with (RAW_DIR / "orders.csv").open("r", encoding="utf-8-sig", newline="") as src:
            reader = csv.DictReader(src)
            kept_fields = [c for c in (reader.fieldnames or []) if c != "payment_method"]
            sample_rows = [next(reader) for _ in range(5)]

        with bad_orders_csv.open("w", encoding="utf-8", newline="") as dst:
            writer = csv.DictWriter(dst, fieldnames=kept_fields)
            writer.writeheader()
            for r in sample_rows:
                writer.writerow({k: r[k] for k in kept_fields})

        res_missing = cv.validate_table_csv(bad_orders_csv, "orders", contract)
        self.assertEqual(res_missing["status"], "FAIL")
        self.assertTrue(any(e["error_code"] == "SCHEMA_MISSING_COLUMN" for e in res_missing["schema_errors"]))
        missing_err = next(e for e in res_missing["schema_errors"] if e["error_code"] == "SCHEMA_MISSING_COLUMN")
        self.assertEqual(missing_err["missing_columns"], ["payment_method"])

        # 2. Unexpected extra column ('rogue_uncontracted_col') in sales_channels.csv
        bad_channels_csv = self.tmp_dir / "sales_channels_extra_col.csv"
        with (RAW_DIR / "sales_channels.csv").open("r", encoding="utf-8-sig", newline="") as src:
            reader = csv.DictReader(src)
            extra_fields = list(reader.fieldnames or []) + ["rogue_uncontracted_col"]
            rows = list(reader)

        with bad_channels_csv.open("w", encoding="utf-8", newline="") as dst:
            writer = csv.DictWriter(dst, fieldnames=extra_fields)
            writer.writeheader()
            for r in rows:
                r_copy = dict(r)
                r_copy["rogue_uncontracted_col"] = "unexpected"
                writer.writerow(r_copy)

        res_extra = cv.validate_table_csv(bad_channels_csv, "sales_channels", contract)
        self.assertEqual(res_extra["status"], "FAIL")
        self.assertTrue(any(e["error_code"] == "SCHEMA_EXTRA_COLUMN" for e in res_extra["schema_errors"]))
        extra_err = next(e for e in res_extra["schema_errors"] if e["error_code"] == "SCHEMA_EXTRA_COLUMN")
        self.assertEqual(extra_err["extra_columns"], ["rogue_uncontracted_col"])

    def test_row_level_quarantine_routing_without_crashing_clean_rows(self) -> None:
        raw_copy = self._copy_all_raw_csvs(self.tmp_dir / "raw_injected")
        db_path = self.tmp_dir / "injected_quarantine.db"

        # 1. Inject invalid enum status into orders.csv
        orders_csv = raw_copy / "orders.csv"
        with orders_csv.open("a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "999001", "ORD-2024-999001", "1", "7",
                "2024-12-30 10:00:00", "2024-12-31", "Teleported", "Credit Card",
                "1000000.0", "0.0", "1000000.0", "700000.0", "",
                "2024-12-30 10:00:00", "2024-12-30 10:00:00",
            ])

        # 2. Inject negative quantity AND invalid decimal into order_details.csv
        details_csv = raw_copy / "order_details.csv"
        with details_csv.open("a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            # Negative quantity (-3)
            writer.writerow([
                "999001", "1", "99", "-3",
                "500000.0", "300000.0", "0.0", "0.0",
                "1500000.0", "900000.0", "600000.0", "2024-12-30 10:00:00",
            ])
            # Invalid decimal for unit_price
            writer.writerow([
                "999002", "1", "98", "1",
                "NOT_A_DECIMAL", "300000.0", "0.0", "0.0",
                "500000.0", "300000.0", "200000.0", "2024-12-30 10:00:00",
            ])

        # 3. Inject duplicate PK (id=1) into products.csv
        products_csv = raw_copy / "products.csv"
        with products_csv.open("a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "1", "DUPLICATE-PK-TEST", "Duplicate PK Product", "2", "Apple",
                "20000000.0", "30000000.0", "10", "1",
                "2024-12-30 08:00:00", "2024-12-30 08:00:00",
            ])

        result = cv.validate_raw_directory(
            raw_dir=raw_copy,
            contract_path=CONTRACT_PATH,
            db_path=db_path,
        )

        self.assertFalse(result["critical_gate_passed"])
        self.assertEqual(result["overall_status"], "FAIL")

        # Clean rows must remain 100% intact across every table!
        self.assertEqual(result["tables"]["orders"]["clean_row_count"], 21435)
        self.assertEqual(result["tables"]["orders"]["quarantined_row_count"], 1)
        self.assertEqual(result["tables"]["order_details"]["clean_row_count"], 39548)
        self.assertEqual(result["tables"]["order_details"]["quarantined_row_count"], 2)
        self.assertEqual(result["tables"]["products"]["clean_row_count"], 47)
        self.assertEqual(result["tables"]["products"]["quarantined_row_count"], 1)
        self.assertEqual(result["summary"]["total_clean_rows"], 67364)
        self.assertEqual(result["summary"]["total_quarantined_rows"], 4)

        # Verify exact quarantine error codes and required payload fields
        q_records = result["quarantined_records"]
        error_codes = {r["error_code"] for r in q_records}
        self.assertEqual(
            error_codes,
            {"INVALID_ENUM_VALUE", "MIN_VALUE_VIOLATION", "INVALID_DECIMAL", "DUPLICATE_PRIMARY_KEY"},
        )
        for rec in q_records:
            for field in ("table_name", "row_number", "natural_key", "error_code", "error_detail", "raw_payload_json"):
                self.assertIn(field, rec)
            parsed_payload = json.loads(rec["raw_payload_json"])
            self.assertIsInstance(parsed_payload, dict)

        # Verify SQLite persistence contains 4 CRITICAL + 4 WARNING temporal records = 8 rows
        conn = sqlite3.connect(str(db_path))
        try:
            crit_codes = {
                r[0]
                for r in conn.execute(
                    "SELECT error_code FROM dq_quarantine_records WHERE severity = 'CRITICAL'"
                ).fetchall()
            }
            self.assertEqual(
                crit_codes,
                {"INVALID_ENUM_VALUE", "MIN_VALUE_VIOLATION", "INVALID_DECIMAL", "DUPLICATE_PRIMARY_KEY"},
            )
            total_persisted = conn.execute("SELECT COUNT(*) FROM dq_quarantine_records").fetchone()[0]
            self.assertEqual(total_persisted, 8)
        finally:
            conn.close()

    def test_volume_drift_alert_below_floor_and_above_ceiling(self) -> None:
        contract = cv.load_contract(CONTRACT_PATH)

        # Below floor: orders expects [15000, 200000], feed only 50 clean rows
        small_orders_csv = self.tmp_dir / "orders_truncated.csv"
        with (RAW_DIR / "orders.csv").open("r", encoding="utf-8-sig", newline="") as src:
            reader = csv.reader(src)
            header = next(reader)
            rows = [next(reader) for _ in range(50)]

        with small_orders_csv.open("w", encoding="utf-8", newline="") as dst:
            writer = csv.writer(dst)
            writer.writerow(header)
            writer.writerows(rows)

        res_low = cv.validate_table_csv(small_orders_csv, "orders", contract)
        self.assertEqual(res_low["status"], "WARN")
        self.assertEqual(res_low["quarantined_row_count"], 0)
        self.assertEqual(res_low["volume_check"]["status"], "ALERT")
        self.assertEqual(res_low["volume_check"]["error_code"], "VOLUME_DRIFT_BELOW_FLOOR")
        self.assertEqual(res_low["volume_check"]["direction"], "BELOW_FLOOR")
        self.assertEqual(res_low["volume_check"]["observed_rows"], 50)

        # Above ceiling: sales_channels expects [5, 50], feed 55 valid channels
        large_channels_csv = self.tmp_dir / "sales_channels_spike.csv"
        with large_channels_csv.open("w", encoding="utf-8", newline="") as dst:
            writer = csv.writer(dst)
            writer.writerow(["id", "channel_code", "channel_name", "channel_type", "is_active", "opened_date"])
            for i in range(1, 56):
                writer.writerow([str(i), f"STORE-{i:03d}", f"Store {i}", "Physical", "1", "2023-01-01"])

        res_high = cv.validate_table_csv(large_channels_csv, "sales_channels", contract)
        self.assertEqual(res_high["status"], "WARN")
        self.assertEqual(res_high["volume_check"]["status"], "ALERT")
        self.assertEqual(res_high["volume_check"]["error_code"], "VOLUME_DRIFT_ABOVE_CEILING")
        self.assertEqual(res_high["volume_check"]["direction"], "ABOVE_CEILING")

    def test_pdpl_pii_tag_verification_in_contract_and_lineage_manifest(self) -> None:
        # 1. Verify contract PII tags on customers
        contract = cv.load_contract(CONTRACT_PATH)
        self.assertIn("91/2025/QH15", contract["compliance"]["regulation"])
        cust_cols = {c["name"]: c for c in contract["tables"]["customers"]["columns"]}
        for pii_col in ("email", "phone", "date_of_birth", "first_name", "last_name"):
            self.assertIn(pii_col, cust_cols)
            self.assertEqual(
                cust_cols[pii_col]["pii_classification"],
                "PII_RESTRICTED",
                f"Expected customers.{pii_col} to be tagged PII_RESTRICTED",
            )

        # 2. Verify generated benchmarks/lineage_manifest.json (and fresh build)
        manifest = cv.build_lineage_manifest(contract)
        if LINEAGE_PATH.exists():
            manifest = json.loads(LINEAGE_PATH.read_text(encoding="utf-8"))

        nodes_by_id = {n["id"]: n for n in manifest["nodes"]}
        raw_cust = nodes_by_id["raw.customers"]
        stg_cust = nodes_by_id["stg.stg_customers"]
        dim_cust = nodes_by_id["mart.dim_customer"]

        for node in (raw_cust, stg_cust):
            for pii_col in ("email", "phone", "date_of_birth"):
                self.assertIn(pii_col, node["pii_restricted_columns"])
                self.assertEqual(node["column_pii_tags"][pii_col], "PII_RESTRICTED")

        # Prohibited PII MUST be completely absent from dim_customer
        dim_cust_cols = set(dim_cust["columns"])
        for forbidden in ("email", "phone", "date_of_birth", "first_name", "last_name", "full_name"):
            self.assertNotIn(forbidden, dim_cust_cols)
        self.assertEqual(dim_cust["pii_restricted_columns"], [])
        self.assertTrue(manifest["pdpl_compliance"]["compliant"])
        self.assertEqual(manifest["pdpl_compliance"]["dim_customer_pii_violations"], [])

        # Verify PDPL trust-boundary edge between stg_customers and dim_customer
        boundary_edges = [
            e
            for e in manifest["edges"]
            if e["source"] == "stg.stg_customers" and e["target"] == "mart.dim_customer"
        ]
        self.assertEqual(len(boundary_edges), 1)
        self.assertTrue(boundary_edges[0]["pdpl_boundary_crossing"])
        self.assertIn("PII_STRIPPED_PDPL_91_2025_QH15", boundary_edges[0]["pii_tags"])

    def test_cross_table_temporal_quarantine_preserves_original_csv_row_number_and_non_null_contracts(self) -> None:
        """Verify true original CSV row_number is preserved when earlier rows are quarantined,
        and verify strict NOT NULL contracts on order_returns.reason and product_price_history.old_price.
        """
        mini_dir = self.tmp_dir / "shifted_temporal"
        mini_dir.mkdir(parents=True, exist_ok=True)
        db_path = self.tmp_dir / "shifted_quarantine.db"

        # Minimal valid orders.csv with 2 orders
        with (mini_dir / "orders.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "id", "order_code", "customer_id", "channel_id", "order_date",
                "ship_date", "status", "payment_method", "total_amount",
                "total_discount", "net_amount", "total_cost", "note",
                "created_at", "updated_at",
            ])
            writer.writerow([
                "101", "ORD-101", "1", "1", "2024-06-10 12:00:00",
                "2024-06-11", "Returned", "Cash", "1000000.0",
                "0.0", "1000000.0", "700000.0", "",
                "2024-06-10 12:00:00", "2024-06-15 12:00:00",
            ])
            writer.writerow([
                "102", "ORD-102", "1", "1", "2024-06-20 12:00:00",
                "2024-06-21", "Returned", "Cash", "1000000.0",
                "0.0", "1000000.0", "700000.0", "",
                "2024-06-20 12:00:00", "2024-06-25 12:00:00",
            ])

        # order_returns.csv with 4 data rows (distinct order_ids so only intended rules fire):
        #   Row 1: malformed (non-int id -> quarantined)
        #   Row 2: clean normal return (returned_at >= order_date for order 101)
        #   Row 3: malformed (empty reason -> quarantined under NOT NULL contract)
        #   Row 4: clean CSV row with temporal anomaly (returned_at < order_date for order 102)
        with (mini_dir / "order_returns.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["id", "order_id", "reason", "returned_at"])
            writer.writerow(["BAD_ID", "103", "Defective", "2024-06-12 10:00:00"])
            writer.writerow(["1", "101", "Defective", "2024-06-15 10:00:00"])
            writer.writerow(["2", "104", "", "2024-06-22 10:00:00"])
            writer.writerow(["3", "102", "Wrong Item", "2024-06-18 09:00:00"])

        # product_price_history.csv with NULL old_price -> MUST trigger NULL_VIOLATION
        with (mini_dir / "product_price_history.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["id", "product_id", "price_type", "old_price", "new_price", "changed_at"])
            writer.writerow(["1", "1", "MSRP", "", "25000000.0", "2024-01-15 00:00:00"])

        result = cv.validate_raw_directory(
            raw_dir=mini_dir,
            contract_path=CONTRACT_PATH,
            db_path=db_path,
            tables=["orders", "order_returns", "product_price_history"],
        )

        # Row 1 (INVALID_INT) and Row 3 (NULL_VIOLATION on reason) quarantined in order_returns
        or_q = [r for r in result["quarantined_records"] if r["table_name"] == "order_returns"]
        self.assertEqual({(r["row_number"], r["error_code"]) for r in or_q}, {(1, "INVALID_INT"), (3, "NULL_VIOLATION")})

        # product_price_history.old_price NULL triggers NULL_VIOLATION
        pph_q = [r for r in result["quarantined_records"] if r["table_name"] == "product_price_history"]
        self.assertEqual(len(pph_q), 1)
        self.assertEqual(pph_q[0]["error_code"], "NULL_VIOLATION")

        # Temporal anomaly on Row 4 MUST preserve exact original CSV row_number == 4 (NOT shifted index 2!)
        self.assertEqual(len(result["temporal_warnings"]), 1)
        warn = result["temporal_warnings"][0]
        self.assertEqual(warn["natural_key"], "3")
        self.assertEqual(warn["row_number"], 4)

        conn = sqlite3.connect(str(db_path))
        try:
            persisted_row_num = conn.execute(
                "SELECT row_number FROM dq_quarantine_records WHERE error_code = 'TEMPORAL_RETURN_BEFORE_ORDER'"
            ).fetchone()[0]
            self.assertEqual(persisted_row_num, 4)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
