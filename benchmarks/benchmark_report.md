# VietTech BI Local Warehouse — Empirical Benchmark & Data Quality Report

> **[SYNTHETIC DATASET DISCLOSURE]** All metrics below are measured empirically on the local
> VietTech Electronics synthetic retail dataset (`21,435` orders, `39,548` order items, `1,288` returns,
> `5,000` customers, `47` products, `24` price history events spanning `2023-01-06` to `2024-12-31`).
> Results reflect local SQLite execution on this workstation and are **not** production BigQuery traffic.

## 1. Test Environment & Dataset Ground Truth

| Parameter | Verified Value |
| --- | --- |
| Generated At (UTC) | `2026-11-27 19:33:16` |
| Runtime | Python `3.12.10` / SQLite `3.49.1` (pure stdlib, 0 external deps) |
| Platform | `Windows 11 (AMD64)` (16 logical CPUs) |
| RFM Snapshot Date | `2025-01-01` |
| Total / Completed Orders | `21,435` / `16,796` |
| Completed Revenue | `380,360,467,955` VND |
| Completed Cost | `273,216,234,000` VND |
| Gross Profit / Margin | `107,144,233,955` VND (`28.17%`) |
| Completed Discount | `13,977,222,045` VND |
| Returns | `1,288` rows totaling `29,446,815,953` VND (`4` temporal anomalies isolated; clean avg lead time `13.11` days) |
| SCD Type 2 Product Versions | `71` total versions across `47` products (`24` historical price transitions) |

---

## 2. Benchmark A — Full Refresh vs. Incremental Watermark CDC (`updated_at`)

Incremental runs filter `stg_orders` on `updated_at >= watermark` (true CDC so late status transitions
from prior days are never silently dropped) and execute idempotent `ON CONFLICT(...) DO UPDATE` upserts.

| Scenario | Watermark | Orders Processed | Items Processed | Returns Processed | Order Row Reduction | Total Fact Row Reduction | p50 Time (ms) | p95 Time (ms) | Speedup |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Full Refresh | `NONE` | `21,435` | `39,548` | `1,288` | `0.00%` | `0.00%` | `841.92` | `903.14` | `1.00x` |
| 30-Day Incremental | `2024-12-02 00:00:00` | `3,116` | `5,762` | `201` | **`85.46%`** | **`85.42%`** | `122.85` | `126.84` | **`6.85x`** |
| 1-Day Incremental | `2024-12-31 00:00:00` | `485` | `897` | `4` | **`97.74%`** | **`97.77%`** | `36.61` | `41.26` | **`23.00x`** |

**CDC Late-Arriving Update Insight:**
- In the 30-day window (`>= 2024-12-02`), `3,017` orders had `order_date >= watermark` (`85.92%` reduction), plus **`99`** earlier orders whose `updated_at` crossed the watermark (status updates / returns). Watermarking on `updated_at` captures 100% of late status mutations.
- In the 1-day window (`>= 2024-12-31`), `216` orders were created on Dec 31 plus **`269`** late-updated orders from Dec 28–30.

---

## 3. Benchmark B — Unindexed vs. Indexed Analytical Queries (50 Measured Runs Each)

| Query | Rows | Unindexed p50 (ms) | Indexed p50 (ms) | p50 Speedup | Unindexed p95 (ms) | Indexed p95 (ms) | p95 Speedup |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Q1: 24-Month Executive KPI Rollup (`vw_monthly_kpi`) | `24` | `27.040` | `19.596` | **`1.38x`** | `36.706` | `23.483` | **`1.56x`** |
| Q2: Filtered Completed Web Orders by Date (2024) | `366` | `8.119` | `2.484` | **`3.27x`** | `11.979` | `3.635` | **`3.30x`** |
| Q3: Historical Product Margin by SCD2 Version (`vw_product_margin_history`) | `118` | `130.617` | `99.322` | **`1.32x`** | `169.344` | `129.998` | **`1.30x`** |
| Q4: RFM Segment x Customer Tier Aggregation (`mart_rfm_snapshot`) | `15` | `7.447` | `5.020` | **`1.48x`** | `10.605` | `6.939` | **`1.53x`** |

### `EXPLAIN QUERY PLAN` Comparison

#### Q1: 24-Month Executive KPI Rollup (`vw_monthly_kpi`)
- **Before (Unindexed):**
  - `CO-ROUTINE vw_monthly_kpi`
  - `SCAN fo`
  - `SEARCH dd USING INTEGER PRIMARY KEY (rowid=?)`
  - `USE TEMP B-TREE FOR GROUP BY`
  - `SCAN vw_monthly_kpi`
  - `USE TEMP B-TREE FOR ORDER BY`
- **After (Indexed + `ANALYZE`):**
  - `CO-ROUTINE vw_monthly_kpi`
  - `SCAN fo USING COVERING INDEX idx_fact_orders_status_date_cover`
  - `SEARCH dd USING COVERING INDEX idx_dim_date_ym (date_key=?)`
  - `USE TEMP B-TREE FOR GROUP BY`
  - `SCAN vw_monthly_kpi`
  - `USE TEMP B-TREE FOR ORDER BY`

#### Q2: Filtered Completed Web Orders by Date (2024)
- **Before (Unindexed):**
  - `SCAN fo`
  - `SEARCH dc USING INDEX sqlite_autoindex_dim_channel_1 (channel_key=?)`
  - `USE TEMP B-TREE FOR GROUP BY`
  - `USE TEMP B-TREE FOR ORDER BY`
- **After (Indexed + `ANALYZE`):**
  - `SEARCH dc USING COVERING INDEX idx_dim_channel_type (channel_type=?)`
  - `SEARCH fo USING COVERING INDEX idx_fact_orders_status_channel_date (status=? AND channel_key=? AND order_date>? AND order_date<?)`
  - `USE TEMP B-TREE FOR GROUP BY`
  - `USE TEMP B-TREE FOR ORDER BY`

#### Q3: Historical Product Margin by SCD2 Version (`vw_product_margin_history`)
- **Before (Unindexed):**
  - `CO-ROUTINE vw_product_margin_history`
  - `SCAN foi`
  - `SEARCH fo USING INDEX sqlite_autoindex_fact_orders_1 (order_key=?)`
  - `SEARCH dp2 USING INDEX sqlite_autoindex_dim_product_scd2_1 (product_version_key=?)`
  - `SEARCH dd USING INTEGER PRIMARY KEY (rowid=?)`
  - `USE TEMP B-TREE FOR GROUP BY`
  - `SCAN vw_product_margin_history`
  - `USE TEMP B-TREE FOR ORDER BY`
- **After (Indexed + `ANALYZE`):**
  - `CO-ROUTINE vw_product_margin_history`
  - `SEARCH fo USING COVERING INDEX idx_fact_orders_status_order_key (status=?)`
  - `SEARCH foi USING COVERING INDEX idx_fact_order_items_order_key (order_key=?)`
  - `SEARCH dp2 USING INDEX sqlite_autoindex_dim_product_scd2_1 (product_version_key=?)`
  - `SEARCH dd USING INTEGER PRIMARY KEY (rowid=?)`
  - `USE TEMP B-TREE FOR GROUP BY`
  - `SCAN vw_product_margin_history`
  - `USE TEMP B-TREE FOR ORDER BY`

#### Q4: RFM Segment x Customer Tier Aggregation (`mart_rfm_snapshot`)
- **Before (Unindexed):**
  - `SEARCH r USING INDEX sqlite_autoindex_mart_rfm_snapshot_1 (snapshot_date=?)`
  - `SEARCH c USING INDEX sqlite_autoindex_dim_customer_1 (customer_key=?)`
  - `USE TEMP B-TREE FOR GROUP BY`
  - `USE TEMP B-TREE FOR ORDER BY`
- **After (Indexed + `ANALYZE`):**
  - `SEARCH r USING COVERING INDEX idx_mart_rfm_snapshot_seg (snapshot_date=?)`
  - `SEARCH c USING COVERING INDEX idx_dim_customer_key_seg (customer_key=?)`
  - `USE TEMP B-TREE FOR GROUP BY`
  - `USE TEMP B-TREE FOR ORDER BY`

---

## 4. RFM Customer Segmentation Snapshot (`2025-01-01`)

| RFM Segment | Customers | Avg Recency (Days) | Avg Frequency (Orders) | Total Monetary Value (VND) |
| --- | ---: | ---: | ---: | ---: |
| `Champions` | `754` | `9.1` | `5.72` | `114,069,485,634` |
| `Loyal` | `1,305` | `21.7` | `4.32` | `111,959,129,507` |
| `At-risk` | `843` | `143.9` | `4.30` | `81,351,454,226` |
| `Churned` | `1,093` | `374.5` | `1.52` | `39,200,233,710` |
| `Regular` | `843` | `19.3` | `1.85` | `33,780,164,878` |

---

## 5. Automated 15-Check Data Quality & Reconciliation Scorecard (`11/11` CRITICAL PASS, `3` WARNINGS Monitored)

| # | Check Name | Severity | Status | Observed Result | Failed / Flagged |
| ---: | --- | --- | --- | --- | ---: |
| 1 | `pk_uniqueness_and_not_null` | `CRITICAL` | **`PASS`** | violations=0 (dim_date=2925, dim_customer=5000, dim_product=47, dim_product_scd2=71, dim_channel=8, dim_payment=4, fact_orders=21435, fact_order_items=39548, fact_returns=1288) | `0` |
| 2 | `zero_orphan_foreign_keys` | `CRITICAL` | **`PASS`** | orphans=0 | `0` |
| 3 | `three_way_financial_reconciliation` | `CRITICAL` | **`PASS`** | completed_rev=380,360,467,955 VND, completed_cost=273,216,234,000 VND, max_diff=0.0000 VND | `0` |
| 4 | `vw_monthly_kpi_reconciliation` | `CRITICAL` | **`PASS`** | vw_rev=380,360,467,955 VND, vw_profit=107,144,233,955 VND, diff=0.0000 VND | `0` |
| 5 | `fact_orders_arithmetic_invariant` | `CRITICAL` | **`PASS`** | mismatched_rows=0 | `0` |
| 6 | `fact_order_items_arithmetic_invariant` | `CRITICAL` | **`PASS`** | mismatched_rows=0 | `0` |
| 7 | `dim_product_scd2_integrity` | `CRITICAL` | **`PASS`** | versions=71, violations=0 | `0` |
| 8 | `fact_order_items_scd2_coverage` | `CRITICAL` | **`PASS`** | mapped=39548/39548, unmapped=0 | `0` |
| 9 | `dim_customer_pdpl_privacy` | `CRITICAL` | **`PASS`** | exposed_cols=[], email_leaks=0 | `0` |
| 10 | `mart_rfm_snapshot_bounds_and_uniqueness` | `CRITICAL` | **`PASS`** | rows=4838, bounds_fail=0, duplicates=0 | `0` |
| 11 | `return_order_status_integrity` | `CRITICAL` | **`PASS`** | mismatched_status_rows=0 | `0` |
| 12 | `temporal_return_anomalies` | `WARNING` | **`WARN`** | flagged_rows=4 (return_ids=[800, 1100, 1187, 1211]) | `4` |
| 13 | `missing_online_fulfillment_ship_date` | `WARNING` | **`WARN`** | online_missing=333, physical_pickup_sentinels=134 | `333` |
| 14 | `missing_physical_shipped_ship_date` | `WARNING` | **`WARN`** | flagged_rows=6 | `6` |
| 15 | `staging_natural_key_uniqueness` | `WARNING` | **`PASS`** | duplicates=0 (orders=0, customers=0, products=0) | `0` |
