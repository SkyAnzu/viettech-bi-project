"""Fast, isolated White-Box unit tests targeting pure viettech.* package modules.

Runs in <100ms with zero full-database builds and 100% pure Python 3.12 stdlib,
covering boundary, degenerate, and error-handling branches across common, contracts,
warehouse, and mining modules.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from viettech.common.utils import (  # noqa: E402
    UTF8_BOM,
    date_str_to_key,
    mask_customer_name,
    parse_bool_int,
    parse_optional_text,
    sha256_key,
    strip_utf8_bom_bytes,
)
from viettech.contracts.types import (  # noqa: E402
    _build_natural_key,
    _parse_bool_int,
    _parse_date,
    _parse_decimal,
    _parse_int,
    _parse_timestamp,
    _parse_typed_value,
)
from viettech.mining.clustering import (  # noqa: E402
    compute_silhouette_score,
    kmeans_plus_plus,
    log1p_zscore_scale,
)
from viettech.mining.market_basket import mine_association_rules  # noqa: E402
from viettech.mining.return_risk import (  # noqa: E402
    _sigmoid,
    apply_standard_scaler,
    compute_decile_breakdown,
    compute_pr_auc,
    compute_roc_auc,
    compute_topk_metrics,
    fit_standard_scaler,
)
from viettech.warehouse.benchmark import percentile  # noqa: E402


class TestCommonUtilsWhiteBox(unittest.TestCase):
    """White-Box unit tests for viettech.common.utils."""

    def test_sha256_key_determinism_and_domain_separation(self) -> None:
        k1 = sha256_key("order", 1001)
        k2 = sha256_key("order", "1001")
        k3 = sha256_key("customer", 1001)
        self.assertEqual(k1, k2)
        self.assertEqual(len(k1), 64)
        self.assertNotEqual(k1, k3)

    def test_mask_customer_name_edge_cases(self) -> None:
        self.assertEqual(mask_customer_name("", ""), "***")
        self.assertEqual(mask_customer_name(None, None), "***")
        self.assertEqual(mask_customer_name("   ", "   "), "***")
        self.assertEqual(mask_customer_name("A", ""), "A***")
        self.assertEqual(mask_customer_name("", "Nguyễn"), "*** Nguyễn")
        self.assertEqual(mask_customer_name("Lâm", "Vũ"), "L*** Vũ")
        self.assertEqual(mask_customer_name("Minh Anh", "Nguyễn Trần"), "M*** Nguyễn Trần")

    def test_date_str_to_key_valid_and_invalid(self) -> None:
        self.assertEqual(date_str_to_key("2024-02-29"), 20240229)
        self.assertEqual(date_str_to_key("2024-12-31 23:59:59"), 20241231)
        self.assertIsNone(date_str_to_key(None))
        self.assertIsNone(date_str_to_key(""))
        self.assertIsNone(date_str_to_key("2024-01"))
        self.assertIsNone(date_str_to_key("2023-02-29"))
        self.assertIsNone(date_str_to_key("not-a-date-at-all"))

    def test_parse_bool_int_and_optional_text(self) -> None:
        for truthy in ("1", "true", "True", "YES", " yes ", True):
            self.assertEqual(parse_bool_int(truthy), 1)
        for falsy in ("0", "false", "no", "", None, False, "2"):
            self.assertEqual(parse_bool_int(falsy), 0)
        self.assertIsNone(parse_optional_text(None))
        self.assertIsNone(parse_optional_text("   "))
        self.assertEqual(parse_optional_text("  hello "), "hello")

    def test_strip_utf8_bom_bytes(self) -> None:
        clean, had_bom = strip_utf8_bom_bytes(UTF8_BOM + b"id,name\n1,a\n")
        self.assertTrue(had_bom)
        self.assertEqual(clean, b"id,name\n1,a\n")

        clean2, had_bom2 = strip_utf8_bom_bytes(b"id,name\n")
        self.assertFalse(had_bom2)
        self.assertEqual(clean2, b"id,name\n")


class TestContractTypesWhiteBox(unittest.TestCase):
    """White-Box boundary & negative unit tests for viettech.contracts.types."""

    def test_parse_int_boundaries_and_rejections(self) -> None:
        self.assertEqual(_parse_int("0"), (0, None))
        self.assertEqual(_parse_int(" -42 "), (-42, None))
        self.assertEqual(_parse_int("+99"), (99, None))
        self.assertEqual(_parse_int(str((1 << 63) - 1))[0], (1 << 63) - 1)
        self.assertEqual(_parse_int(str(-(1 << 63)))[0], -(1 << 63))

        # Reject bool, float strings, empty, sign-only, and 64-bit overflow
        for bad in (True, False, "", "   ", "+", "-", "1.0", "3.14", "1e5", str(1 << 63), str(-(1 << 63) - 1)):
            val, err = _parse_int(bad)
            self.assertIsNone(val, f"Expected None for {bad!r}")
            self.assertIsNotNone(err, f"Expected error message for {bad!r}")

    def test_parse_decimal_rejects_nan_inf_and_invalid(self) -> None:
        self.assertEqual(_parse_decimal("12345.67"), (Decimal("12345.67"), None))
        self.assertEqual(_parse_decimal("-0.00"), (Decimal("-0.00"), None))
        for bad in ("", "NaN", "nan", "Infinity", "-Infinity", "inf", "abc"):
            val, err = _parse_decimal(bad)
            self.assertIsNone(val, f"Expected None for {bad!r}")
            self.assertIsNotNone(err, f"Expected error for {bad!r}")

    def test_parse_date_timestamp_bool_int_and_typed_dispatch(self) -> None:
        dt, err = _parse_date("2024-02-29")
        self.assertIsNotNone(dt)
        self.assertIsNone(err)
        self.assertIsNone(_parse_date("2023-02-29")[0])
        self.assertIsNone(_parse_date("2024-1-1")[0])

        ts, err = _parse_timestamp("2024-06-15T08:30:00.123456")
        self.assertIsNotNone(ts)
        self.assertIsNone(err)
        self.assertIsNone(_parse_timestamp("2024/06/15")[0])

        self.assertEqual(_parse_bool_int("0"), (0, None))
        self.assertEqual(_parse_bool_int("1"), (1, None))
        self.assertIsNone(_parse_bool_int("true")[0])

        self.assertEqual(_parse_typed_value("string", "  abc  "), ("abc", None, None))
        _, code, detail = _parse_typed_value("int", "1.5")
        self.assertEqual(code, "INVALID_INT")
        self.assertIsNotNone(detail)
        _, code_u, _ = _parse_typed_value("uuid_unknown", "xyz")
        self.assertEqual(code_u, "UNSUPPORTED_CONTRACT_TYPE")

        self.assertEqual(_build_natural_key({"id": "10", "code": "A"}, ["id", "code"], 3), "10|A")
        self.assertEqual(_build_natural_key({}, ["id"], 7), "ROW#7")


class TestWarehouseBenchmarkWhiteBox(unittest.TestCase):
    """White-Box unit tests for viettech.warehouse.benchmark.percentile."""

    def test_percentile_edge_cases(self) -> None:
        self.assertEqual(percentile([], 50), 0.0)
        self.assertEqual(percentile([42.0], 0), 42.0)
        self.assertEqual(percentile([42.0], 50), 42.0)
        self.assertEqual(percentile([42.0], 100), 42.0)
        self.assertAlmostEqual(percentile([10.0, 20.0, 30.0, 40.0], 50), 25.0, places=9)
        self.assertAlmostEqual(percentile([40.0, 10.0, 30.0, 20.0], 25), 17.5, places=9)
        self.assertEqual(percentile([1.0, 5.0], -10.0), 1.0)
        self.assertEqual(percentile([1.0, 5.0], 150.0), 5.0)


class TestMiningAlgorithmsWhiteBox(unittest.TestCase):
    """White-Box unit tests for clustering, market basket, and return risk math."""

    def test_clustering_zero_variance_empty_single_point_and_silhouette_edges(self) -> None:
        with self.assertRaises(ValueError):
            log1p_zscore_scale([])

        # Column 1 has zero variance -> std fallback 1.0, scaled column ~ 0.0
        mat = [[1.0, 5.0, 100.0], [10.0, 5.0, 1000.0], [100.0, 5.0, 10000.0]]
        scaled, _means, stds, _diag = log1p_zscore_scale(mat)
        self.assertEqual(stds[1], 1.0)
        for row in scaled:
            self.assertAlmostEqual(row[1], 0.0, places=12)

        with self.assertRaises(ValueError):
            kmeans_plus_plus([], k=1)
        with self.assertRaises(ValueError):
            kmeans_plus_plus([[0.0, 0.0]], k=2)

        single = kmeans_plus_plus([[1.0, 2.0]], k=1, seed=42, n_init=2)
        self.assertEqual(single["labels"], [0])
        self.assertAlmostEqual(single["wcss"], 0.0, places=9)

        # Determinism on identical seed (non-3D path too)
        pts = [[0.0, 0.0], [0.1, 0.2], [10.0, 10.0], [10.2, 9.9]]
        km1 = kmeans_plus_plus(pts, k=2, seed=7, n_init=3)
        km2 = kmeans_plus_plus(pts, k=2, seed=7, n_init=3)
        self.assertEqual(km1["labels"], km2["labels"])
        self.assertEqual(km1["centroids"], km2["centroids"])

        # Silhouette edge branches: single cluster or < 3 samples
        sil_one, _, _ = compute_silhouette_score(pts, [0, 0, 0, 0])
        self.assertEqual(sil_one, 0.0)
        sil_small, _, _ = compute_silhouette_score([[0.0, 0.0], [5.0, 5.0]], [0, 1])
        self.assertEqual(sil_small, 0.0)

    def test_market_basket_empty_single_item_and_deduplication(self) -> None:
        empty_res = mine_association_rules([])
        self.assertEqual(empty_res["total_baskets"], 0)
        self.assertEqual(empty_res["rules"], [])

        only_empty_inner = mine_association_rules([[], []])
        self.assertEqual(only_empty_inner["total_baskets"], 0)

        single_items = mine_association_rules([["A"], ["B"], ["A"]])
        self.assertEqual(single_items["total_baskets"], 3)
        self.assertEqual(single_items["multi_item_baskets"], 0)
        self.assertEqual(single_items["rules"], [])

        # Duplicate SKUs inside the same basket must be deduplicated before counting!
        dup_baskets = [["SKU1", "SKU1", "SKU2", "SKU2"], ["SKU1", "SKU2"]]
        res = mine_association_rules(dup_baskets, min_support=0.5, min_co_occurrences=2)
        self.assertEqual(res["total_baskets"], 2)
        self.assertEqual(res["rules"][0]["co_occurrences"], 2)
        self.assertEqual(res["rules"][0]["antecedent_count"], 2)

    def test_return_risk_metrics_and_scaler_edge_branches(self) -> None:
        # All zeros / all ones
        self.assertEqual(compute_roc_auc([0, 0, 0], [0.1, 0.5, 0.9]), 0.5)
        self.assertEqual(compute_roc_auc([1, 1, 1], [0.1, 0.5, 0.9]), 0.5)
        self.assertEqual(compute_pr_auc([0, 0, 0], [0.1, 0.5, 0.9]), 0.0)
        self.assertEqual(compute_pr_auc([], []), 0.0)
        self.assertAlmostEqual(compute_pr_auc([1, 1, 1], [0.9, 0.5, 0.1]), 1.0, places=9)

        # Tied scores
        self.assertAlmostEqual(compute_roc_auc([1, 0, 1, 0], [0.5, 0.5, 0.5, 0.5]), 0.5, places=9)

        # Top-K metrics on empty & all-zero labels
        empty_topk = compute_topk_metrics([], [], top_fraction=0.10)
        self.assertEqual(empty_topk["top_k_orders"], 0)
        self.assertEqual(empty_topk["lift_at_k"], 0.0)

        zero_topk = compute_topk_metrics([0, 0, 0, 0], [0.9, 0.8, 0.2, 0.1], top_fraction=0.25)
        self.assertEqual(zero_topk["lift_at_k"], 0.0)
        self.assertEqual(zero_topk["recall_at_k"], 0.0)

        # Decile breakdown on tiny input
        decs, summary = compute_decile_breakdown([1, 0], [0.8, 0.2])
        self.assertEqual(len(decs), 2)
        self.assertEqual(len(summary), 10)

        # Zero-variance feature column in scaler
        means, stds = fit_standard_scaler([[1.0, 7.0], [3.0, 7.0]])
        self.assertEqual(stds[1], 1.0)
        scaled = apply_standard_scaler([[1.0, 7.0], [3.0, 7.0]], means, stds)
        self.assertEqual([r[1] for r in scaled], [0.0, 0.0])

        # Sigmoid overflow protection
        self.assertEqual(_sigmoid(100.0), 1.0)
        self.assertEqual(_sigmoid(-100.0), 0.0)
        self.assertAlmostEqual(_sigmoid(0.0), 0.5, places=9)


if __name__ == "__main__":
    unittest.main()
