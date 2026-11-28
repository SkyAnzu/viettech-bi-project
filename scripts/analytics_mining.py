"""Thin CLI facade for VietTech Prescriptive Analytics & Data Mining (INT3209E)."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from viettech.common.utils import utc_now_iso  # noqa: E402
from viettech.mining import (  # noqa: E402
    DEFAULT_SEED,
    DEFAULT_SNAPSHOT_DATE,
    FEATURE_NAMES,
    FORBIDDEN_LEAKAGE_COLUMNS,
    MINING_MARTS_DDL,
    MODEL_VERSION,
    _assign_cluster_business_labels,
    _compute_contingency_and_nmi,
    _kmeans_plus_plus_init,
    _sigmoid,
    _sq_dist,
    apply_standard_scaler,
    compute_decile_breakdown,
    compute_pr_auc,
    compute_roc_auc,
    compute_silhouette_score,
    compute_topk_metrics,
    ensure_mining_mart_tables,
    extract_order_features_chronological,
    fit_standard_scaler,
    kmeans_plus_plus,
    log1p_zscore_scale,
    mine_association_rules,
    open_warehouse_connection,
    predict_proba_logistic,
    render_markdown_report,
    run_all_analytics_mining,
    run_analytics_mining_pipeline,
    run_kmeans_module,
    run_market_basket_module,
    run_return_risk_module,
    run_rfm_kmeans_module,
    train_l2_logistic_regression,
)

__all__ = [
    "DEFAULT_SEED",
    "DEFAULT_SNAPSHOT_DATE",
    "FEATURE_NAMES",
    "FORBIDDEN_LEAKAGE_COLUMNS",
    "MINING_MARTS_DDL",
    "MODEL_VERSION",
    "_assign_cluster_business_labels",
    "_compute_contingency_and_nmi",
    "_kmeans_plus_plus_init",
    "_sigmoid",
    "_sq_dist",
    "apply_standard_scaler",
    "compute_decile_breakdown",
    "compute_pr_auc",
    "compute_roc_auc",
    "compute_silhouette_score",
    "compute_topk_metrics",
    "ensure_mining_mart_tables",
    "extract_order_features_chronological",
    "fit_standard_scaler",
    "kmeans_plus_plus",
    "log1p_zscore_scale",
    "main",
    "mine_association_rules",
    "open_warehouse_connection",
    "parse_args",
    "predict_proba_logistic",
    "render_markdown_report",
    "run_all_analytics_mining",
    "run_analytics_mining_pipeline",
    "run_kmeans_module",
    "run_market_basket_module",
    "run_return_risk_module",
    "run_rfm_kmeans_module",
    "train_l2_logistic_regression",
    "utc_now_iso",
]


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run VietTech Prescriptive Analytics & Data Mining modules on the local SQLite warehouse."
    )
    parser.add_argument("--db-path", type=Path, default=PROJECT_ROOT / "data" / "warehouse" / "viettech_mart.db")
    parser.add_argument("--report-md", type=Path, default=PROJECT_ROOT / "benchmarks" / "analytics_mining_report.md")
    parser.add_argument("--report-json", type=Path, default=PROJECT_ROOT / "benchmarks" / "analytics_mining_results.json")
    parser.add_argument("--snapshot-date", default=DEFAULT_SNAPSHOT_DATE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--no-reports", action="store_true", help="Persist mart tables only without writing reports.")
    parser.add_argument("--quiet", action="store_true", help="Suppress stdout summary output.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    run_all_analytics_mining(
        db_path=args.db_path,
        report_md=None if args.no_reports else args.report_md,
        report_json=None if args.no_reports else args.report_json,
        snapshot_date=args.snapshot_date,
        seed=args.seed,
        quiet=args.quiet,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
