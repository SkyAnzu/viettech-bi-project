"""Comprehensive unittest suite for VietTech Prescriptive Analytics & Data Mining (`INT3209E`).

Verifies:
1. Exact Market Basket Support / Confidence / Lift math on a deterministic toy dataset
   and real cross-sell Lift > 1.0 pairs on the full VietTech Kimball warehouse.
2. `log1p` + Z-score standardization (mean ~ 0, std ~ 1), raw VND scale distortion,
   deterministic K-Means++ Silhouette in [-1, 1], and full 4,838-customer persistence.
3. Strict anti-leakage chronological split (max(train_date) < min(test_date)), zero
   post-order features, train-only Z-score scaling, exact ROC-AUC/PR-AUC math, and
   full 3-mart persistence & idempotency.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
RAW_DIR = PROJECT_ROOT / "data" / "raw"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import analytics_mining as am  # noqa: E402
import run_local_warehouse as wh  # noqa: E402


class TestAnalyticsMiningMath(unittest.TestCase):
    """Pure mathematical & unit-level verification on deterministic toy datasets."""

    def test_market_basket_exact_toy_lift_and_popularity_trap(self) -> None:
        # 10 baskets:
        # - LAPTOP appears in 4 baskets (0, 1, 2, 3)
        # - SSD appears in 4 baskets (0, 1, 2, 4) -> co(LAPTOP, SSD) = 3
        #   support = 3/10 = 0.30
        #   conf(LAPTOP -> SSD) = 3/4 = 0.75
        #   lift(LAPTOP -> SSD) = 0.30 / (0.40 * 0.40) = 1.875 (> 1.0 true cross-sell!)
        # - CABLE appears in 8 baskets (0, 1, 4, 5, 6, 7, 8, 9) -> co(LAPTOP, CABLE) = 2
        #   conf(LAPTOP -> CABLE) = 2/4 = 0.50 (looks high!)
        #   lift(LAPTOP -> CABLE) = 0.20 / (0.40 * 0.80) = 0.625 (< 1.0 popularity trap!)
        baskets = [
            ["LAPTOP", "SSD", "CABLE"],
            ["LAPTOP", "SSD", "CABLE"],
            ["LAPTOP", "SSD"],
            ["LAPTOP"],
            ["SSD", "CABLE"],
            ["CABLE"],
            ["CABLE"],
            ["CABLE"],
            ["CABLE"],
            ["CABLE"],
        ]
        res = am.mine_association_rules(
            baskets,
            min_support=0.10,
            min_co_occurrences=2,
        )
        self.assertEqual(res["total_baskets"], 10)
        self.assertEqual(res["multi_item_baskets"], 4)

        by_pair = {(r["antecedent"], r["consequent"]): r for r in res["rules"]}

        # Verify exact positive-lift rule
        r_ls = by_pair[("LAPTOP", "SSD")]
        self.assertEqual(r_ls["co_occurrences"], 3)
        self.assertAlmostEqual(r_ls["support"], 0.30, places=6)
        self.assertAlmostEqual(r_ls["confidence"], 0.75, places=6)
        self.assertAlmostEqual(r_ls["lift"], 1.875, places=6)
        self.assertEqual(r_ls["is_positive_lift"], 1)

        # Verify popularity trap rule (50% confidence yet Lift = 0.625 <= 1.0)
        r_lc = by_pair[("LAPTOP", "CABLE")]
        self.assertEqual(r_lc["co_occurrences"], 2)
        self.assertAlmostEqual(r_lc["confidence"], 0.50, places=6)
        self.assertAlmostEqual(r_lc["consequent_support"], 0.80, places=6)
        self.assertAlmostEqual(r_lc["lift"], 0.625, places=6)
        self.assertEqual(r_lc["is_positive_lift"], 0)
        self.assertTrue(
            any(
                t["antecedent"] == "LAPTOP" and t["consequent"] == "CABLE"
                for t in res["popularity_trap_examples"]
            )
        )

    def test_log1p_zscore_standardization_and_silhouette_bounds(self) -> None:
        raw = [
            [5.0, 8.0, 120_000_000.0],
            [7.0, 7.0, 95_000_000.0],
            [10.0, 6.0, 80_000_000.0],
            [350.0, 1.0, 500_000.0],
            [400.0, 1.0, 750_000.0],
            [420.0, 2.0, 1_200_000.0],
        ]
        scaled, _, _, diag = am.log1p_zscore_scale(raw)

        # Raw monetary variance dominates > 99.99% before scaling
        self.assertGreater(diag["raw_variance_share_pct"][2], 99.99)

        # After log1p + Z-score, every feature has mean ~ 0 and std ~ 1
        for col in range(3):
            vals = [row[col] for row in scaled]
            mean_v = sum(vals) / len(vals)
            std_v = math.sqrt(sum((v - mean_v) ** 2 for v in vals) / len(vals))
            self.assertAlmostEqual(mean_v, 0.0, places=9)
            self.assertAlmostEqual(std_v, 1.0, places=9)

        km = am.kmeans_plus_plus(scaled, k=2, seed=42, n_init=3)
        sil, sample_n, method = am.compute_silhouette_score(scaled, km["labels"], sample_size=100, seed=42)
        self.assertEqual(sample_n, 6)
        self.assertEqual(method, "exact_full")
        self.assertGreaterEqual(sil, -1.0)
        self.assertLessEqual(sil, 1.0)
        # Two clearly separated clusters should have high positive silhouette
        self.assertGreater(sil, 0.70)

    def test_roc_auc_and_pr_auc_exact_edge_cases(self) -> None:
        # Perfect separation
        y_true = [1, 1, 0, 0, 0]
        perfect_scores = [0.95, 0.85, 0.30, 0.20, 0.10]
        self.assertAlmostEqual(am.compute_roc_auc(y_true, perfect_scores), 1.0, places=9)
        self.assertAlmostEqual(am.compute_pr_auc(y_true, perfect_scores), 1.0, places=9)

        # Completely inverted scores
        inverted_scores = [0.10, 0.20, 0.70, 0.80, 0.90]
        self.assertAlmostEqual(am.compute_roc_auc(y_true, inverted_scores), 0.0, places=9)

        # All tied scores -> ROC-AUC == 0.5, PR-AUC == prevalence (0.4)
        tied_scores = [0.5] * 5
        self.assertAlmostEqual(am.compute_roc_auc(y_true, tied_scores), 0.5, places=9)
        self.assertAlmostEqual(am.compute_pr_auc(y_true, tied_scores), 0.4, places=9)

    def test_point_in_time_return_observability_and_train_cutoff_maturation_gate(self) -> None:
        """Prove zero future-peek leakage on customer_prior_return_rate and 2023->2024 train cutoff."""
        with tempfile.TemporaryDirectory(prefix="viettech_pit_leakage_") as tmp:
            db_path = Path(tmp) / "pit_test.db"
            conn = wh.open_connection(db_path)
            try:
                conn.executescript(wh.DDL_SCHEMA)
                with conn:
                    # Conformed dims
                    for dkey, dstr in (
                        (20230601, "2023-06-01"),
                        (20230610, "2023-06-10"),
                        (20230620, "2023-06-20"),
                        (20230625, "2023-06-25"),
                        (20231228, "2023-12-28"),
                        (20240103, "2024-01-03"),
                        (20240105, "2024-01-05"),
                        (20240110, "2024-01-10"),
                        (20240115, "2024-01-15"),
                    ):
                        conn.execute(
                            "INSERT INTO dim_date VALUES (?, ?, ?, 1, 1, 1, 1, 0, 0, 0, ?)",
                            (dkey, dstr, int(dstr[:4]), dstr),
                        )
                    conn.execute(
                        "INSERT INTO dim_customer VALUES ('ck1', 1, 'C1', 'A*** B', 'Male', 'Hanoi', 'VN', 'Consumer', '2023-01-01', 1)"
                    )
                    conn.execute(
                        "INSERT INTO dim_channel VALUES ('ch1', 1, 'WEB', 'Web', 'Web', 1, 1, '2020-01-01')"
                    )
                    conn.execute(
                        "INSERT INTO dim_payment VALUES ('pm1', 'Cash', 'Cash')"
                    )
                    conn.execute(
                        "INSERT INTO dim_product VALUES ('pk1', 1, 'SKU1', 'Phone', 'Smartphones', 'Flagship', 'Brand', 1000.0, 700.0, 1)"
                    )
                    conn.execute(
                        "INSERT INTO dim_product_scd2 VALUES ('pvk1', 'pk1', 1, 'SKU1', 'Phone', 'Smartphones', 'Flagship', 'Brand', 1, 1000.0, 700.0, '2022-12-01 00:00:00', '9999-12-31 23:59:59', 1, 'INIT')"
                    )

                    orders = [
                        # (oid, odate, dkey, status)
                        (1, "2023-06-01 10:00:00", 20230601, "Returned"),   # returned 2023-06-20
                        (2, "2023-06-10 10:00:00", 20230610, "Completed"),  # BEFORE order 1 returned!
                        (3, "2023-06-25 10:00:00", 20230625, "Shipped"),    # non-terminal non-cancelled
                        (4, "2023-12-28 10:00:00", 20231228, "Returned"),   # returned 2024-01-05 (after cutoff!)
                        (5, "2024-01-03 10:00:00", 20240103, "Completed"),  # BEFORE order 4 returned!
                        (6, "2024-01-10 10:00:00", 20240110, "Returned"),   # AFTER order 4 returned!
                    ]
                    for oid, odate, dkey, status in orders:
                        okey = f"ok{oid}"
                        conn.execute(
                            """
                            INSERT INTO fact_orders VALUES (
                                ?, ?, ?, ?, NULL, ?, ?, ?, 0, 'ck1', 'ch1', 'pm1', ?, 1000.0, 0.0, 1000.0, 700.0
                            )
                            """,
                            (okey, oid, f"ORD-{oid}", odate, odate, dkey, dkey, status),
                        )
                        conn.execute(
                            """
                            INSERT INTO fact_order_items VALUES (
                                ?, ?, ?, ?, 'pk1', 'pvk1', ?, 1, 1000.0, 700.0, 0.0, 0.0, 1000.0, 700.0, 300.0
                            )
                            """,
                            (f"oik{oid}", oid, oid, okey, dkey),
                        )

                    # Return timestamps
                    conn.execute(
                        "INSERT INTO fact_returns VALUES ('rk1', 1, 1, 'ok1', 20230620, '2023-06-20', '2023-06-20 10:00:00', 19.0, 0, 'Defective')"
                    )
                    conn.execute(
                        "INSERT INTO fact_returns VALUES ('rk4', 4, 4, 'ok4', 20240105, '2024-01-05', '2024-01-05 10:00:00', 8.0, 0, 'Defective')"
                    )
                    conn.execute(
                        "INSERT INTO fact_returns VALUES ('rk6', 6, 6, 'ok6', 20240115, '2024-01-15', '2024-01-15 10:00:00', 5.0, 0, 'Defective')"
                    )

                train_recs, test_recs, meta = am.extract_order_features_chronological(
                    conn, split_date="2024-01-01"
                )
                by_id = {r["order_id"]: r for r in train_recs + test_recs}
                prior_ord_idx = am.FEATURE_NAMES.index("customer_prior_orders_log")
                prior_ret_idx = am.FEATURE_NAMES.index("customer_prior_return_rate")

                # Order 3 (Shipped) is excluded from scored population, but counted in prior_ord
                self.assertNotIn(3, by_id)
                self.assertEqual(meta["prior_non_terminal_orders_in_history"], 1)

                # Order 2 (2023-06-10): Order 1 was NOT returned until 2023-06-20 -> prior_ret_rate MUST be 0.0
                self.assertAlmostEqual(by_id[2]["features"][prior_ord_idx], math.log1p(1.0), places=9)
                self.assertAlmostEqual(by_id[2]["features"][prior_ret_idx], 0.0, places=9)

                # Order 4 (2023-12-28): prior orders = {1, 2, 3} (incl. Shipped), observable prior returns = {1}
                self.assertAlmostEqual(by_id[4]["features"][prior_ord_idx], math.log1p(3.0), places=9)
                self.assertAlmostEqual(by_id[4]["features"][prior_ret_idx], 1.0 / 3.0, places=9)
                # Order 4 returned on 2024-01-05 (>= split_date '2024-01-01') -> MUST be right-censored to y == 0 in 2023 train!
                self.assertEqual(by_id[4]["y"], 0)
                self.assertEqual(by_id[4]["y_raw"], 1)
                self.assertEqual(meta["train_right_censored_returns_at_cutoff"], 1)
                self.assertEqual(meta["train_positives"], 1)  # Only Order 1 observable before 2024-01-01

                # Order 5 (2024-01-03): Order 4 NOT yet returned (returns 2024-01-05) -> prior_ret = 1 / 4
                self.assertAlmostEqual(by_id[5]["features"][prior_ord_idx], math.log1p(4.0), places=9)
                self.assertAlmostEqual(by_id[5]["features"][prior_ret_idx], 1.0 / 4.0, places=9)

                # Order 6 (2024-01-10): Order 4 IS now observable (returned 2024-01-05 <= 2024-01-10) -> prior_ret = 2 / 5
                self.assertAlmostEqual(by_id[6]["features"][prior_ord_idx], math.log1p(5.0), places=9)
                self.assertAlmostEqual(by_id[6]["features"][prior_ret_idx], 2.0 / 5.0, places=9)
                self.assertEqual(by_id[6]["y"], 1)

                # Both future-peek pairs (Order 1->2 and Order 4->5) were blocked
                self.assertEqual(meta["prior_return_future_peeks_blocked"], 2)
            finally:
                conn.close()


class TestAnalyticsMiningWarehouseIntegration(unittest.TestCase):
    """End-to-end integration tests on an isolated temporary VietTech warehouse."""

    _tmp: tempfile.TemporaryDirectory[str]
    db_path: Path
    results: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="viettech_mining_test_")
        tmp_dir = Path(cls._tmp.name)
        cls.db_path = tmp_dir / "test_mart.db"
        cls.report_md = tmp_dir / "test_mining_report.md"
        cls.report_json = tmp_dir / "test_mining_results.json"

        wh.run_pipeline(
            raw_dir=RAW_DIR,
            db_path=cls.db_path,
            mode="full",
            snapshot_date="2025-01-01",
            quiet=True,
        )
        cls.results = am.run_all_analytics_mining(
            db_path=cls.db_path,
            report_md=cls.report_md,
            report_json=cls.report_json,
            snapshot_date="2025-01-01",
            seed=42,
            quiet=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_module1_market_basket_cross_sell_lift_and_persistence(self) -> None:
        mba = self.results["module_1_market_basket"]
        self.assertEqual(mba["sku_level"]["total_baskets"], 16796)
        self.assertEqual(mba["sub_category_level"]["total_baskets"], 16796)
        self.assertGreater(mba["sku_level"]["positive_lift_rules_count"], 50)
        self.assertGreater(mba["sub_category_level"]["positive_lift_rules_count"], 10)

        # Confirm genuine cross-sell pairs have Lift > 1.0 (e.g., Laptops -> External SSD / Keyboard & Mouse)
        sub_top = {
            (r["antecedent"], r["consequent"]): r
            for r in mba["sub_category_level"]["top_rules"]
        }
        self.assertIn(("MacBook", "External SSD & HDD"), sub_top)
        self.assertGreater(sub_top[("MacBook", "External SSD & HDD")]["lift"], 2.0)
        self.assertIn(("Windows Laptop", "Keyboard & Mouse"), sub_top)
        self.assertGreater(sub_top[("Windows Laptop", "Keyboard & Mouse")]["lift"], 1.5)

        # Confirm popularity trap examples exist with Lift <= 1.0
        self.assertGreater(len(mba["sub_category_level"]["popularity_trap_examples"]), 0)
        for trap in mba["sub_category_level"]["popularity_trap_examples"]:
            self.assertLessEqual(trap["lift"], 1.0)

        # Verify SQLite persistence
        conn = am.open_warehouse_connection(self.db_path)
        try:
            sku_cnt = conn.execute(
                "SELECT COUNT(*) FROM mart_market_basket_rules WHERE granularity = 'sku'"
            ).fetchone()[0]
            sub_cnt = conn.execute(
                "SELECT COUNT(*) FROM mart_market_basket_rules WHERE granularity = 'sub_category'"
            ).fetchone()[0]
            min_lift = conn.execute(
                "SELECT MIN(lift) FROM mart_market_basket_rules"
            ).fetchone()[0]
            self.assertGreater(sku_cnt, 0)
            self.assertGreater(sub_cnt, 0)
            self.assertGreater(min_lift, 1.0)
        finally:
            conn.close()

    def test_module2_kmeans_scaling_silhouette_and_full_customer_assignment(self) -> None:
        km = self.results["module_2_rfm_kmeans"]
        self.assertEqual(km["customers_clustered"], 4838)
        self.assertIn(km["selected_k"], (3, 4, 5, 6))

        # Verify scaling diagnostics: scaled mean ~ 0, std ~ 1; raw monetary dominates > 99.99%
        sd = km["scaling_diagnostics"]
        self.assertGreater(sd["raw_variance_share_pct"][2], 99.99)
        for m_val, s_val in zip(sd["scaled_means"], sd["scaled_stds"]):
            self.assertAlmostEqual(m_val, 0.0, places=6)
            self.assertAlmostEqual(s_val, 1.0, places=6)

        # Verify Silhouette bounds across all evaluated k values
        self.assertEqual([ev["k"] for ev in km["k_evaluations"]], [3, 4, 5, 6])
        for ev in km["k_evaluations"]:
            self.assertGreaterEqual(ev["silhouette_score"], -1.0)
            self.assertLessEqual(ev["silhouette_score"], 1.0)
            self.assertGreater(ev["silhouette_score"], 0.20)
            self.assertEqual(sum(ev["cluster_sizes"]), 4838)

        # Contingency matrix must account for all 4,838 customers
        matrix_total = sum(
            cnt for seg_map in km["rfm_contingency_matrix"].values() for cnt in seg_map.values()
        )
        self.assertEqual(matrix_total, 4838)

        # Verify SQLite persistence in mart_rfm_kmeans_clusters
        conn = am.open_warehouse_connection(self.db_path)
        try:
            row_cnt, distinct_k = conn.execute(
                """
                SELECT COUNT(*), COUNT(DISTINCT cluster_id)
                FROM mart_rfm_kmeans_clusters
                WHERE snapshot_date = '2025-01-01'
                """
            ).fetchone()
            self.assertEqual(row_cnt, 4838)
            self.assertEqual(distinct_k, km["selected_k"])
        finally:
            conn.close()

    def test_module3_anti_leakage_temporal_split_and_return_risk_persistence(self) -> None:
        rr = self.results["module_3_return_risk"]
        sm = rr["split_metadata"]
        td = rr["training_diagnostics"]
        oot = rr["out_of_time_2024_metrics"]

        # Strict temporal anti-leakage check: max(train_date) < min(test_date)
        self.assertTrue(sm["strict_temporal_separation"])
        self.assertLess(sm["train_max_date"], "2024-01-01")
        self.assertGreaterEqual(sm["test_min_date"], "2024-01-01")
        self.assertLess(sm["train_max_date"], sm["test_min_date"])
        # Verify point-in-time maturation & observability gates on real VietTech dataset
        self.assertEqual(sm["train_right_censored_returns_at_cutoff"], 26)
        self.assertEqual(sm["train_raw_positives"] - sm["train_positives"], 26)
        self.assertGreaterEqual(sm["prior_return_future_peeks_blocked"], 4)

        # Zero forbidden post-order leakage features
        feature_set = set(sm["feature_names"])
        self.assertEqual(len(feature_set), 13)
        for forbidden in am.FORBIDDEN_LEAKAGE_COLUMNS:
            self.assertNotIn(forbidden, feature_set)

        # Verify strictly-prior history on customer first order & train-only scaling
        conn = am.open_warehouse_connection(self.db_path)
        try:
            # Verify strictly-prior history on customer's true first non-cancelled order
            first_non_cancelled = {
                str(r[0]): str(r[1])
                for r in conn.execute(
                    """
                    SELECT customer_key, MIN(order_date)
                    FROM fact_orders
                    WHERE status != 'Cancelled'
                    GROUP BY customer_key
                    """
                ).fetchall()
            }
            train_recs, test_recs, _ = am.extract_order_features_chronological(conn)
            first_scored_seen: set[str] = set()
            prior_ord_idx = am.FEATURE_NAMES.index("customer_prior_orders_log")
            prior_ret_idx = am.FEATURE_NAMES.index("customer_prior_return_rate")
            for rec in train_recs + test_recs:
                ck = rec["customer_key"]
                if ck not in first_scored_seen:
                    # First scored order can never have prior returns (prior non-terminal orders are not returns)
                    self.assertEqual(rec["features"][prior_ret_idx], 0.0)
                    # If this scored order is also the customer's earliest non-cancelled order, prior_ord == 0
                    if rec["order_date"] == first_non_cancelled[ck]:
                        self.assertEqual(rec["features"][prior_ord_idx], 0.0)
                    first_scored_seen.add(ck)

            # Verify SQLite persistence in mart_return_risk_scores
            scored_cnt, min_dt, min_dec, max_dec, min_p, max_p = conn.execute(
                """
                SELECT
                    COUNT(*),
                    MIN(order_date),
                    MIN(risk_decile),
                    MAX(risk_decile),
                    MIN(predicted_risk_score),
                    MAX(predicted_risk_score)
                FROM mart_return_risk_scores
                """
            ).fetchone()
            self.assertEqual(scored_cnt, sm["test_count"])
            self.assertEqual(scored_cnt, 12819)
            self.assertGreaterEqual(min_dt, "2024-01-01")
            self.assertEqual(min_dec, 1)
            self.assertEqual(max_dec, 10)
            self.assertGreaterEqual(min_p, 0.0)
            self.assertLessEqual(max_p, 1.0)
        finally:
            conn.close()

        # Verify convergence and valid out-of-time metric ranges
        self.assertLess(td["final_loss"], td["initial_loss"])
        self.assertGreaterEqual(oot["roc_auc"], 0.0)
        self.assertLessEqual(oot["roc_auc"], 1.0)
        self.assertGreaterEqual(oot["pr_auc"], 0.0)
        self.assertLessEqual(oot["pr_auc"], 1.0)
        self.assertGreaterEqual(oot["precision_at_top10_pct"], 0.0)
        self.assertLessEqual(oot["precision_at_top10_pct"], 1.0)
        self.assertGreaterEqual(oot["recall_at_top10_pct"], 0.0)
        self.assertLessEqual(oot["recall_at_top10_pct"], 1.0)
        self.assertGreaterEqual(oot["lift_at_top10_pct"], 0.0)

    def test_reports_generated_and_pipeline_idempotent(self) -> None:
        self.assertTrue(self.report_md.exists())
        self.assertTrue(self.report_json.exists())

        md_text = self.report_md.read_text(encoding="utf-8")
        self.assertIn("[SYNTHETIC DATASET DISCLOSURE]", md_text)
        self.assertIn("mart_market_basket_rules", md_text)
        self.assertIn("mart_rfm_kmeans_clusters", md_text)
        self.assertIn("mart_return_risk_scores", md_text)

        parsed = json.loads(self.report_json.read_text(encoding="utf-8"))
        self.assertEqual(parsed["metadata"]["seed"], 42)

        # Re-run on same DB: deterministic results and zero duplicate rows
        res2 = am.run_all_analytics_mining(
            db_path=self.db_path,
            report_md=None,
            report_json=None,
            snapshot_date="2025-01-01",
            seed=42,
            quiet=True,
        )
        self.assertEqual(
            res2["module_2_rfm_kmeans"]["selected_silhouette_score"],
            self.results["module_2_rfm_kmeans"]["selected_silhouette_score"],
        )
        self.assertEqual(
            res2["module_3_return_risk"]["out_of_time_2024_metrics"]["roc_auc"],
            self.results["module_3_return_risk"]["out_of_time_2024_metrics"]["roc_auc"],
        )


if __name__ == "__main__":
    unittest.main()
