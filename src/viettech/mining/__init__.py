"""Prescriptive Analytics & Data Mining package (INT3209E) on VietTech Star Schema."""

from __future__ import annotations

from viettech.mining.clustering import (
    _assign_cluster_business_labels,
    _compute_contingency_and_nmi,
    _kmeans_plus_plus_init,
    _sq_dist,
    compute_silhouette_score,
    kmeans_plus_plus,
    log1p_zscore_scale,
    run_kmeans_module,
    run_rfm_kmeans_module,
)
from viettech.mining.market_basket import (
    mine_association_rules,
    run_market_basket_module,
)
from viettech.mining.reporting import (
    render_markdown_report,
    run_all_analytics_mining,
    run_analytics_mining_pipeline,
)
from viettech.mining.return_risk import (
    FEATURE_NAMES,
    FORBIDDEN_LEAKAGE_COLUMNS,
    _sigmoid,
    apply_standard_scaler,
    compute_decile_breakdown,
    compute_pr_auc,
    compute_roc_auc,
    compute_topk_metrics,
    extract_order_features_chronological,
    fit_standard_scaler,
    predict_proba_logistic,
    run_return_risk_module,
    train_l2_logistic_regression,
)
from viettech.mining.schema import (
    DEFAULT_SEED,
    DEFAULT_SNAPSHOT_DATE,
    MINING_MARTS_DDL,
    MODEL_VERSION,
    ensure_mining_mart_tables,
    open_warehouse_connection,
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
    "mine_association_rules",
    "open_warehouse_connection",
    "predict_proba_logistic",
    "render_markdown_report",
    "run_all_analytics_mining",
    "run_analytics_mining_pipeline",
    "run_kmeans_module",
    "run_market_basket_module",
    "run_return_risk_module",
    "run_rfm_kmeans_module",
    "train_l2_logistic_regression",
]
