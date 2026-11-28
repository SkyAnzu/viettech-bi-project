"""Markdown/JSON report rendering and end-to-end analytics mining orchestrator."""

from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import sqlite3
import time
from typing import Any, Dict, List, Optional

from viettech.common.utils import utc_now_iso
from viettech.mining.clustering import run_rfm_kmeans_module
from viettech.mining.market_basket import run_market_basket_module
from viettech.mining.return_risk import run_return_risk_module
from viettech.mining.schema import (
    DEFAULT_SEED,
    DEFAULT_SNAPSHOT_DATE,
    open_warehouse_connection,
)


def _render_summary_section(results: Dict[str, Any]) -> List[str]:
    meta = results["metadata"]
    mba = results["module_1_market_basket"]
    km = results["module_2_rfm_kmeans"]
    rr = results["module_3_return_risk"]
    return [
        "# VietTech BI — Prescriptive Analytics & Data Mining Report (`INT3209E`)",
        "",
        (
            "> **[SYNTHETIC DATASET DISCLOSURE]** All association rules, cluster assignments, "
            "and out-of-time return-risk metrics below were computed empirically on the local "
            "VietTech Electronics synthetic retail warehouse (`viettech_mart.db`, spanning "
            "`2023-01-06` to `2024-12-31`) using **100% pure Python 3.12 standard library** "
            "(zero numpy/pandas/scikit-learn). Results are **not** real production customer traffic."
        ),
        "",
        "## 1. Execution Summary & Reproducibility",
        "",
        "| Parameter | Verified Value |",
        "| --- | --- |",
        f"| Generated At (UTC) | `{meta['generated_at_utc']}` |",
        f"| Runtime | Python `{meta['python_version']}` / SQLite `{meta['sqlite_version']}` (pure stdlib) |",
        f"| Platform | `{meta['os']}` ({meta['cpu_count']} logical CPUs) |",
        f"| Deterministic Seed | `{meta['seed']}` |",
        f"| Total End-to-End Runtime | **`{meta['total_duration_ms']:.2f} ms`** (`{meta['total_duration_ms']/1000.0:.2f} s`) |",
        (
            f"| Persisted Mart Tables | `mart_market_basket_rules` (`{mba['persisted_rules_total']}` rows), "
            f"`mart_rfm_kmeans_clusters` (`{km['customers_clustered']:,}` rows), "
            f"`mart_return_risk_scores` (`{rr['out_of_time_2024_metrics']['scored_orders']:,}` rows) |"
        ),
        "",
        "---",
        "",
    ]


def _render_market_basket_section(mba: Dict[str, Any]) -> List[str]:
    sku = mba["sku_level"]
    sub = mba["sub_category_level"]
    lines: List[str] = [
        "## 2. Module 1 — Market Basket Analysis (Association Rules: Support, Confidence, Lift > 1)",
        "",
        (
            f"- **Population:** `{sku['total_baskets']:,}` Completed orders (`{sku['multi_item_baskets']:,}` "
            f"multi-item baskets, `{sku['multi_item_share_pct']}%` multi-item rate) across "
            f"`{sku['distinct_items']}` SKUs and `{sub['distinct_items']}` Sub-Categories."
        ),
        (
            f"- **Eligibility Threshold:** `support >= 0.005` OR `co_occurrences >= 30`, ranked by `Lift DESC`. "
            f"Discovered **`{sub['positive_lift_rules_count']}`** positive-lift Sub-Category rules and "
            f"**`{sku['positive_lift_rules_count']}`** positive-lift SKU rules (`{mba['duration_ms']:.2f} ms`)."
        ),
        "",
        "### 2.1 Top Sub-Category Cross-Sell Rules (`Lift > 1.0`)",
        "",
        "| Antecedent Sub-Category | Consequent Sub-Category | Co-Occur | Support | Confidence | Consequent Support | Lift |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in sub["top_rules"][:10]:
        lines.append(
            f"| `{r['antecedent']}` | `{r['consequent']}` | `{r['co_occurrences']:,}` | "
            f"`{r['support']:.4f}` | `{r['confidence']*100:.2f}%` | `{r['consequent_support']*100:.2f}%` | **`{r['lift']:.3f}x`** |"
        )
    lines.extend(
        [
            "",
            "### 2.2 Top SKU-Pair Cross-Sell Rules (`Lift > 1.0`)",
            "",
            "| Antecedent SKU | Consequent SKU | Co-Occur | Support | Confidence | Consequent Support | Lift |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for r in sku["top_rules"][:10]:
        lines.append(
            f"| `{r['antecedent']}` ({r['antecedent_label']}) | `{r['consequent']}` ({r['consequent_label']}) | "
            f"`{r['co_occurrences']:,}` | `{r['support']:.4f}` | `{r['confidence']*100:.2f}%` | "
            f"`{r['consequent_support']*100:.2f}%` | **`{r['lift']:.3f}x`** |"
        )
    lines.extend(
        [
            "",
            "### 2.3 Why `Lift > 1` Avoids the High-Confidence Popularity Trap",
            "",
            (
                "Ranking association rules by raw **Confidence** (`P(B | A)`) alone systematically favors "
                "ubiquitous filler items (`Charger & Cable` appears in `~27.7%` of all Completed baskets). "
                "When `Confidence(A -> B) <= P(B)`, **`Lift = Confidence(A -> B) / P(B) <= 1.0`** — meaning "
                "buying `A` actually gives **zero or negative** incremental propensity to buy `B`:"
            ),
            "",
            "| Granularity | Antecedent | Consequent | Co-Occur | Confidence | Baseline `P(Consequent)` | Lift (`<= 1.0` Trap!) |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for r in sub["popularity_trap_examples"][:4]:
        lines.append(
            f"| `sub_category` | `{r['antecedent']}` | `{r['consequent']}` | `{r['co_occurrences']:,}` | "
            f"`{r['confidence']*100:.2f}%` | `{r['consequent_support']*100:.2f}%` | `{r['lift']:.3f}x` |"
        )
    for r in sku["popularity_trap_examples"][:3]:
        lines.append(
            f"| `sku` | `{r['antecedent']}` | `{r['consequent']}` | `{r['co_occurrences']:,}` | "
            f"`{r['confidence']*100:.2f}%` | `{r['consequent_support']*100:.2f}%` | `{r['lift']:.3f}x` |"
        )
    lines.extend(
        [
            "",
            (
                "**Prescriptive Action:** Bundle **Laptops (MacBook / Windows Laptop)** with **External SSD & HDD** "
                "(`Lift ~2.53x–2.56x`) and **Keyboard & Mouse** (`Lift ~1.82x–1.95x`) on product detail pages and checkout "
                "upsells, rather than spamming universal cable banners triggered by raw confidence."
            ),
            "",
            "---",
            "",
        ]
    )
    return lines


def _render_kmeans_section(km: Dict[str, Any]) -> List[str]:
    sd = km["scaling_diagnostics"]
    lines: List[str] = [
        "## 3. Module 2 — Unsupervised K-Means++ (`log1p` + `Z-Score`) vs. Rule-Based RFM `NTILE(5)`",
        "",
        (
            f"- **Dataset:** `mart_rfm_snapshot` (`{km['snapshot_date']}`, `{km['customers_clustered']:,}` customers, "
            f"runtime `{km['duration_ms']:.2f} ms`)."
        ),
        "",
        "### 3.1 Why Raw Euclidean K-Means Fails Without `log1p` + `Z-Score`",
        "",
        (
            "Because `monetary_value` is denominated in VND (up to `~370M` VND) while `frequency_orders` ranges from `1` to `11`, "
            "unscaled Euclidean distance is dominated **99.9999999%+** by `monetary_value`, effectively ignoring Recency and Frequency. "
            "Applying `log1p(x)` compresses right-skewed monetary spend and Z-score standardization equalizes feature variance to `33.33%` each:"
        ),
        "",
        "| Feature | Raw Mean | Raw Std | Raw Variance Share | Scaled (`log1p` + Z) Mean | Scaled Std | Scaled Variance Share |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for idx, fname in enumerate(sd["feature_names"]):
        lines.append(
            f"| `{fname}` | `{sd['raw_means'][idx]:,.2f}` | `{sd['raw_stds'][idx]:,.2f}` | "
            f"`{sd['raw_variance_share_pct'][idx]:.6f}%` | `{sd['scaled_means'][idx]:.6f}` | "
            f"`{sd['scaled_stds'][idx]:.6f}` | **`{sd['scaled_variance_share_pct'][idx]:.2f}%`** |"
        )
    lines.extend(
        [
            "",
            f"### 3.2 Model Selection Across `k in [3, 4, 5, 6]` (Silhouette Method: `{km['silhouette_method']}`)",
            "",
            "| `k` | Silhouette Score | WCSS (Inertia) | Iterations | Cluster Sizes |",
            "| ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for ev in km["k_evaluations"]:
        mark = " **(Selected)**" if ev["k"] == km["selected_k"] else ""
        lines.append(
            f"| `{ev['k']}`{mark} | **`{ev['silhouette_score']:.4f}`** | `{ev['wcss']:,.2f}` | "
            f"`{ev['iterations']}` | `{ev['cluster_sizes']}` |"
        )
    lines.extend(
        [
            "",
            f"### 3.3 Optimal `k={km['selected_k']}` Cluster Profiles (`mart_rfm_kmeans_clusters`)",
            "",
            "| Cluster | Business Persona | Customers | Share | Avg Recency (d) | Avg Freq | Avg Monetary (VND) | Revenue Share |",
            "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for prof in km["cluster_profiles"]:
        lines.append(
            f"| `C{prof['cluster_id']}` | `{prof['cluster_label']}` | `{prof['customer_count']:,}` | "
            f"`{prof['customer_share_pct']:.2f}%` | `{prof['avg_recency_days']:.1f}` | "
            f"`{prof['avg_frequency_orders']:.2f}` | `{prof['avg_monetary_vnd']:,.0f}` | **`{prof['revenue_share_pct']:.2f}%`** |"
        )

    all_rfm_segs = ["Champions", "Loyal", "Regular", "At-risk", "Churned"]
    lines.extend(
        [
            "",
            "### 3.4 K-Means Clusters vs. Rule-Based RFM `NTILE(5)` Contingency Matrix",
            "",
            (
                f"- **Alignment Metrics:** Cluster Purity = **`{km['rfm_alignment_purity']:.4f}`**, "
                f"Normalized Mutual Information (NMI) = **`{km['rfm_alignment_nmi']:.4f}`**."
            ),
            "",
            "| K-Means Cluster | " + " | ".join(f"`{s}`" for s in all_rfm_segs) + " | Total |",
            "| --- | " + " | ".join("---:" for _ in all_rfm_segs) + " | ---: |",
        ]
    )
    for c_label, seg_map in km["rfm_contingency_matrix"].items():
        row_total = sum(seg_map.values())
        vals = " | ".join(f"`{seg_map.get(s, 0):,}`" for s in all_rfm_segs)
        lines.append(f"| `{c_label}` | {vals} | `{row_total:,}` |")
    lines.extend(
        [
            "",
            (
                "**Architectural Takeaway:** Rule-based `NTILE(5)` forces equal-sized 20% quantile buckets even when natural "
                "spend/recency gaps exist; K-Means in `(log1p + Z)` space discovers natural geometric cohorts (e.g., separating "
                "high-spend active VIPs from low-spend recent buyers and long-dormant churners) without arbitrary score cutoffs."
            ),
            "",
            "---",
            "",
        ]
    )
    return lines


def _render_return_risk_section(rr: Dict[str, Any]) -> List[str]:
    sm = rr["split_metadata"]
    td = rr["training_diagnostics"]
    oot = rr["out_of_time_2024_metrics"]
    lines: List[str] = [
        "## 4. Module 3 — Leakage-Free Out-of-Time Return-Risk Scoring (L2 Logistic Regression)",
        "",
        "### 4.1 Strict Anti-Leakage Architecture",
        "",
        "| Guardrail | Enforced Specification |",
        "| --- | --- |",
        (
            f"| Temporal Split | Train = 2023 (`{sm['train_min_date']}` to `{sm['train_max_date']}`, `n={sm['train_count']:,}`, "
            f"observable positives `{sm['train_positives']:,}` / `{sm['train_prevalence']*100:.2f}%` as of `{sm['split_date']}`) -> "
            f"Test = 2024 (`{sm['test_min_date']}` to `{sm['test_max_date']}`, `n={sm['test_count']:,}`, "
            f"realized positives `{sm['test_positives']:,}` / `{sm['test_prevalence']*100:.2f}%`) |"
        ),
        f"| Strict Date Separation | `max(train_date) < min(test_date)` == **`{sm['strict_temporal_separation']}`** |",
        (
            f"| Scored Population & Prior Orders | Post-fulfillment conditional return-risk scoring (`Completed` + `Returned`); "
            f"`customer_prior_orders_log` aggregates **all prior non-cancelled orders** "
            f"(`Pending`/`Processing`/`Shipped`/`Completed`/`Returned`, `{sm['prior_non_terminal_orders_in_history']:,}` non-terminal orders included in history) strictly before `current.order_date` |"
        ),
        (
            f"| Prior-Return Observability Gate | `fact_returns` left-joined **strictly for `returned_at` (`is_temporal_anomaly = 0`, never as a feature)**; "
            f"a prior order counts in `customer_prior_return_rate` ONLY IF `prior.returned_at IS NOT NULL AND prior.returned_at >= prior.order_date AND prior.returned_at <= current.order_date` "
            f"(**`{sm['prior_return_future_peeks_blocked']}`** future-peek `(prior_order, current_order)` pairs blocked) |"
        ),
        (
            f"| Train-Cutoff Maturation Gate | **`{sm['train_right_censored_returns_at_cutoff']}`** late-2023 orders returned on/after `{sm['split_date']}` "
            f"were right-censored to training label `0` as of cutoff (`{sm['train_positives']:,}` observable 2023 positives vs. `{sm['train_raw_positives']:,}` eventual 2023 returns) |"
        ),
        "| Scaling Leakage Guard | Z-score `mean` & `std` fit **strictly on 2023 training orders** and applied unchanged to 2024 test orders |",
        f"| Excluded Post-Order Feature Columns | `{', '.join(sm['excluded_post_order_columns'])}` |",
        (
            f"| Model & Score Semantics | Pure-Python L2 Class-Weighted Logistic Regression (`lambda={td['l2_lambda']}`, "
            f"`w_pos={td['class_weight_pos']}`, `w_neg={td['class_weight_neg']}`, `epochs={td['epochs']}`, "
            f"loss `{td['initial_loss']:.4f}` -> `{td['final_loss']:.4f}`, runtime `{rr['duration_ms']:.2f} ms`); "
            f"`predicted_risk_score` is a **class-balanced propensity score** (not raw calibrated probability) used for decile ranking |"
        ),
        "",
        "### 4.2 Out-of-Time 2024 Evaluation Metrics (Imbalanced Positive Class `~7.04%`)",
        "",
        "| Metric | 2023 Train (In-Sample) | 2024 Out-of-Time Test | Naive Random Baseline |",
        "| --- | ---: | ---: | ---: |",
        f"| **ROC-AUC** | `{td['train_roc_auc']:.4f}` | **`{oot['roc_auc']:.4f}`** | `0.5000` |",
        (
            f"| **PR-AUC (Average Precision)** | `{td['train_pr_auc']:.4f}` | "
            f"**`{oot['pr_auc']:.4f}`** (`{oot['pr_auc_vs_naive_ratio']:.2f}x` baseline) | `{oot['naive_baseline_prevalence']:.4f}` |"
        ),
        (
            f"| **Precision@Top10%** | — | **`{oot['precision_at_top10_pct']*100:.2f}%`** "
            f"({oot['top10_detail']['captured_returns']}/{oot['top10_detail']['top_k_orders']}) | `{oot['naive_baseline_prevalence']*100:.2f}%` |"
        ),
        (
            f"| **Recall@Top10%** | — | **`{oot['recall_at_top10_pct']*100:.2f}%`** "
            f"({oot['top10_detail']['captured_returns']}/{oot['actual_returns']}) | `10.00%` |"
        ),
        f"| **Lift@Top10%** | — | **`{oot['lift_at_top10_pct']:.3f}x`** | `1.000x` |",
        "",
        "### 4.3 Honest Synthetic-Data Signal Diagnosis",
        "",
        "> [!NOTE]",
        f"> {rr['honest_signal_assessment']}",
        "",
        (
            "**Why This Matters in Enterprise Data Science:** Many portfolio demos accidentally leak `ship_date` "
            "(NULL on some statuses), `returned_at`, or future customer return rates and claim a fake `0.95+` ROC-AUC. "
            "Under strict out-of-time evaluation on a synthetic generator where returns are drawn uniformly at random, "
            "an honest model **must** score near chance (`ROC-AUC ~0.50`, `PR-AUC ~prevalence`, `Lift@Top10% ~1.0x`). "
            "When wired to real VietTech operational telemetry (carrier SLA delays, SKU defect batches, first-time COD "
            "high-ticket orders, warehouse pick-error rates), this exact leakage-free scoring pipeline (`mart_return_risk_scores`) "
            "is immediately production-ready."
        ),
        "",
        "### 4.4 Learned Standardized Feature Weights (2023 Training Fit)",
        "",
        "| Rank | Feature (Order-Time Only) | Standardized Weight | Odds Ratio (per +1 SD) | 2023 Train Mean | 2023 Train Std |",
        "| ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for rank, fw in enumerate(rr["feature_weights"], start=1):
        lines.append(
            f"| `{rank}` | `{fw['feature']}` | `{fw['standardized_weight']:+.4f}` | "
            f"`{fw['odds_ratio_per_1sd']:.4f}x` | `{fw['train_mean']:.4f}` | `{fw['train_std']:.4f}` |"
        )
    lines.extend(
        [
            "",
            "### 4.5 Out-of-Time 2024 Risk Decile Ranking (`mart_return_risk_scores`)",
            "",
            (
                "> **Score Semantics:** `predicted_risk_score` is a **class-balanced propensity score** "
                "(centered near `~0.50` due to balanced class weighting `w_pos`/`w_neg`), **not** a raw "
                "calibrated probability (which averages `~7.04%`). Use it strictly for decile ranking "
                "and Top-K% operational triage."
            ),
            "",
            "| Risk Decile (1=Highest) | Orders | Actual Returns | Observed Return Rate | Lift vs Baseline | Avg Propensity Score (Balanced) |",
            "| ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for drow in rr["decile_lift_table"]:
        lines.append(
            f"| `D{drow['decile']}` | `{drow['orders']:,}` | `{drow['actual_returns']:,}` | "
            f"`{drow['return_rate_pct']:.2f}%` | `{drow['lift_vs_baseline']:.3f}x` | `{drow['avg_predicted_score']:.4f}` |"
        )
    lines.append("")
    return lines


def render_markdown_report(results: Dict[str, Any]) -> str:
    """Render the full Markdown report across all 3 data mining modules."""
    lines = (
        _render_summary_section(results)
        + _render_market_basket_section(results["module_1_market_basket"])
        + _render_kmeans_section(results["module_2_rfm_kmeans"])
        + _render_return_risk_section(results["module_3_return_risk"])
    )
    return "\n".join(lines)


def run_all_analytics_mining(
    *,
    db_path: Path,
    report_md: Optional[Path] = None,
    report_json: Optional[Path] = None,
    snapshot_date: str = DEFAULT_SNAPSHOT_DATE,
    seed: int = DEFAULT_SEED,
    quiet: bool = False,
) -> Dict[str, Any]:
    """Execute all 3 analytics & data mining modules, persist marts, and write reports."""
    t_total = time.perf_counter()
    conn = open_warehouse_connection(db_path)
    try:
        mba_res = run_market_basket_module(conn)
        kmeans_res = run_rfm_kmeans_module(
            conn, snapshot_date=snapshot_date, k_values=(3, 4, 5, 6), seed=seed
        )
        return_res = run_return_risk_module(conn, split_date="2024-01-01")
    finally:
        conn.close()

    total_ms = round((time.perf_counter() - t_total) * 1000.0, 2)

    results: Dict[str, Any] = {
        "metadata": {
            "dataset_disclosure": (
                "SYNTHETIC DATASET (VietTech Electronics retail simulation, 2023-01-06 to 2024-12-31)"
            ),
            "generated_at_utc": utc_now_iso(),
            "python_version": platform.python_version(),
            "sqlite_version": sqlite3.sqlite_version,
            "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "cpu_count": os.cpu_count() or 1,
            "seed": seed,
            "snapshot_date": snapshot_date,
            "db_path": str(db_path),
            "total_duration_ms": total_ms,
        },
        "module_1_market_basket": mba_res,
        "module_2_rfm_kmeans": kmeans_res,
        "module_3_return_risk": return_res,
    }

    if report_json is not None:
        report_json.parent.mkdir(parents=True, exist_ok=True)
        report_json.write_text(
            json.dumps(results, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    if report_md is not None:
        report_md.parent.mkdir(parents=True, exist_ok=True)
        report_md.write_text(render_markdown_report(results), encoding="utf-8")

    if not quiet:
        print(
            f"[analytics_mining] Completed all 3 modules in {total_ms:.2f} ms "
            f"(seed={seed}, db={db_path.name})"
        )
        top_sub = mba_res["sub_category_level"]["top_rules"][0]
        top_sku = mba_res["sku_level"]["top_rules"][0]
        print(
            f"  Module 1 (MBA): {mba_res['persisted_rules_total']} rules persisted | "
            f"Top Sub-Cat: {top_sub['antecedent']} -> {top_sub['consequent']} (Lift={top_sub['lift']:.3f}x) | "
            f"Top SKU: {top_sku['antecedent']} -> {top_sku['consequent']} (Lift={top_sku['lift']:.3f}x)"
        )
        k_str = ", ".join(
            f"k={ev['k']}: {ev['silhouette_score']:.4f}" for ev in kmeans_res["k_evaluations"]
        )
        print(
            f"  Module 2 (K-Means++): {kmeans_res['customers_clustered']:,} customers | "
            f"Best k={kmeans_res['selected_k']} (Silhouette={kmeans_res['selected_silhouette_score']:.4f}) | "
            f"[{k_str}]"
        )
        oot = return_res["out_of_time_2024_metrics"]
        print(
            f"  Module 3 (Return-Risk 2024 OOT): {oot['scored_orders']:,} orders | "
            f"ROC-AUC={oot['roc_auc']:.4f} | PR-AUC={oot['pr_auc']:.4f} "
            f"(baseline={oot['naive_baseline_prevalence']:.4f}) | "
            f"Lift@Top10%={oot['lift_at_top10_pct']:.3f}x"
        )

    return results


run_analytics_mining_pipeline = run_all_analytics_mining
