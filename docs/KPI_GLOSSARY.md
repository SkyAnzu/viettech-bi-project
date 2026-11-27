# VietTech Retail BI — Enterprise KPI Glossary & Metric Dictionary

> **Single Source of Truth:** Every SQL view in `sql/05_bi_views.sql`, local SQLite verification query, Power BI DAX measure, and executive slide MUST align strictly with the grain, numerator, denominator, and anomaly-handling rules defined in this glossary.
>
> **Synthetic Dataset Disclosure:** All baseline reconciliation numbers listed below are verified against the fixed **synthetic VietTech Retail dataset** (`2023-01-06` to `2024-12-31`, `21,435` orders, `39,548` line items, `1,288` returns, `5,000` customers, `47` SKUs across `14` sub-categories, `8` channels). They serve as deterministic reconciliation benchmarks, not live production traffic claims.

---

## 1. Global Accounting, Calendar & Governance Rules

1. **Currency & Precision:** All monetary amounts are denominated in **Vietnamese Dong (`VND`)**, stored as exact `NUMERIC` / `DECIMAL(15,2)` in SQL to prevent floating-point drift.
2. **Revenue Recognition Rule:** Primary commercial KPIs (`completed_revenue`, `gross_profit`, `gross_margin_pct`, `avg_completed_order_value`) include **only** `fact_orders.status = 'Completed'`. Never compare an all-status order count (`total_orders`) against `completed_revenue` without displaying `total_completed_orders` alongside it.
3. **Vietnamese Retail Seasonality (`dim_date`):**
   - `is_tet = TRUE` (`month IN (1, 2)`): Captures **Tết Nguyên Đán** (Lunar New Year) pre-holiday shopping surges and post-Tết demand normalization.
   - `is_year_end = TRUE` (`month IN (11, 12)`): Captures Black Friday, 11.11 / 12.12 e-commerce mega-sales, and Q4 corporate IT procurement.
4. **Ship-Date Sentinel Keys (`fact_orders.ship_date_key`):**
   - `YYYYMMDD` (`> 0`): Actual recorded shipment date.
   - `-1`: **Unknown / Missing Online Ship Date** — Web/App Completed orders missing `ship_date` (`333` rows; flagged `is_missing_online_ship_date = TRUE`, logged as `WARNING` per [ADR-004](decisions/ADR-004-data-quality-quarantine-and-pdpl-pii.md)).
   - `-2`: **Not Yet Shipped** — `Pending` (`194`) and `Processing` (`661`) orders.
   - `-3`: **Not Applicable** — `Cancelled` (`1,659`) orders and `Physical` store Completed orders fulfilled via immediate in-store pickup (`134` rows).
5. **PDPL `91/2025/QH15` Privacy Rule:** Customer-level metrics reference pseudonymized `customer_key` (SHA-256) or `customer_code` only; raw customer PII (`full_name`, `email`, `phone`, `date_of_birth`) is prohibited in `retailbi_mart` and Power BI reports.

---

## 2. Verified Synthetic Dataset Reconciliation Baseline

Use this table to verify SQL views, incremental `MERGE` runs, local SQLite builds, and Power BI card totals:

| Metric | Scope / Filter | Verified Baseline Value |
| :--- | :--- | :---: |
| **Total Orders** | All statuses (`16,796` Completed, `1,659` Cancelled, `1,288` Returned, `837` Shipped, `661` Processing, `194` Pending) | `21,435` |
| **Total Completed Orders** | `status = 'Completed'` | `16,796` |
| **Order Line Items** | `fact_order_items` (all orders) | `39,548` |
| **Completed Revenue** | `SUM(net_amount)` where `status = 'Completed'` | `380,360,467,955 VND` |
| **Completed Cost (COGS)** | `SUM(total_cost)` where `status = 'Completed'` | `273,216,234,000 VND` |
| **Gross Profit** | `SUM(net_amount - total_cost)` where `status = 'Completed'` | `107,144,233,955 VND` |
| **Gross Margin %** | `Gross Profit / Completed Revenue * 100` | `28.17%` |
| **Completed Discount** | `SUM(total_discount)` where `status = 'Completed'` | `13,977,222,045 VND` |
| **Returned Orders / Return Value** | `fact_returns` joined to `fact_orders` (all `1,288` returns) | `1,288` / `29,446,815,953 VND` |
| **Channel Completed Revenue — Web** | `channel_type = 'Web'` (`8,367` total orders) | `138.78B VND` |
| **Channel Completed Revenue — Physical** | `channel_type = 'Physical'` (`6,386` total orders across 6 stores) | `130.51B VND` |
| **Channel Completed Revenue — App** | `channel_type = 'App'` (`6,682` total orders) | `111.07B VND` |
| **DQ Flagged Return Temporal Anomalies** | `fact_returns.is_temporal_anomaly = TRUE` (`ret_id = 800, 1100, 1187, 1211`) | `4` |
| **DQ Completed `NULL ship_date` Split** | `134` Physical (`-3`, pickup) / `333` Online (`-1`, `WARNING`) | `467` total |

---

## 3. Semantic BI Views Specification (`sql/05_bi_views.sql`)

### 3.1 `vw_monthly_kpi` — Executive Monthly Performance

- **Grain:** One row per `(year, month)` derived from `fact_orders.order_date_key` (joined to `dim_date` for `is_tet` / `is_year_end`).
- **Source:** `retailbi_mart.fact_orders` + `retailbi_mart.dim_date`.

| Column | Formula / SQL Definition | Denominator & Filter Rule | Business Interpretation |
| :--- | :--- | :--- | :--- |
| `year`, `month` | Calendar year and month of `order_date_key` | Order creation date | Primary executive time axis |
| `total_orders` | `COUNT(*)` | All 6 statuses included | Gross demand / order intake volume |
| `total_completed_orders` | `COUNTIF(status = 'Completed')` | Completed orders only | Exact order denominator for revenue and AOV |
| `completed_revenue` | `SUM(IF(status = 'Completed', net_amount, 0))` | Completed orders only | Recognized net revenue after line discounts |
| `completed_cost` | `SUM(IF(status = 'Completed', total_cost, 0))` | Completed orders only | Total COGS for recognized revenue |
| `gross_profit` | `SUM(IF(status = 'Completed', net_amount - total_cost, 0))` | Completed orders only | Gross profit in VND |
| `gross_margin_pct` | `SAFE_DIVIDE(gross_profit, completed_revenue) * 100` | Denominator: `completed_revenue` | Profitability efficiency (`28.17%` overall) |
| `avg_completed_order_value` | `SAFE_DIVIDE(completed_revenue, total_completed_orders)` | Denominator: `total_completed_orders` | Average basket value of fulfilled sales (AOV) |
| `completed_discount` | `SUM(IF(status = 'Completed', total_discount, 0))` | Completed orders only | Realized promotional investment aligned to revenue |
| `total_discount` | `SUM(total_discount)` | All statuses | Gross quoted discount across all placed orders |
| `completion_rate_pct` | `SAFE_DIVIDE(total_completed_orders, total_orders) * 100` | Denominator: `total_orders` | Share of placed orders reaching `Completed` status |

---

### 3.2 `vw_channel_performance` — Omnichannel Comparison

- **Grain:** One row per `(channel_key, channel_name, channel_type)` across VietTech's `8` channels (`6` Physical stores + `Web` + `App`).
- **Source:** `retailbi_mart.fact_orders` JOIN `retailbi_mart.dim_channel` ON `channel_key`.

| Column | Formula / SQL Definition | Denominator & Filter Rule | Notes |
| :--- | :--- | :--- | :--- |
| `channel_name`, `channel_type` | `dim_channel.channel_name`, `dim_channel.channel_type` | `Physical`, `Web`, `App` | Use `channel_type` for omnichannel rollups (Web `138.78B`, Physical `130.51B`, App `111.07B` VND) |
| `is_online` | `dim_channel.is_online` | `TRUE` for `Web`/`App`; `FALSE` for `Physical` | Separates digital e-commerce from brick-and-mortar pickup |
| `total_orders` | `COUNT(*)` | All statuses | Channel order intake (`8,367` Web, `6,682` App, `6,386` Physical) |
| `total_completed_orders` | `COUNTIF(o.status = 'Completed')` | Completed orders only | Aligns with `completed_revenue` |
| `completed_revenue` | `SUM(IF(o.status = 'Completed', o.net_amount, 0))` | Completed orders only | Channel recognized net revenue |
| `gross_profit` | `SUM(IF(o.status = 'Completed', o.net_amount - o.total_cost, 0))` | Completed orders only | Channel gross profit contribution |
| `gross_margin_pct` | `SAFE_DIVIDE(gross_profit, completed_revenue) * 100` | Denominator: `completed_revenue` | Identifies margin gaps between online promotions and store sales |
| `avg_completed_order_value` | `SAFE_DIVIDE(completed_revenue, total_completed_orders)` | Denominator: `total_completed_orders` | Basket size comparison across stores, Web, and App |
| `missing_online_ship_orders` | `COUNTIF(o.is_missing_online_ship_date)` | Online Completed orders with `NULL ship_date` | Surfaces the `333` Web/App fulfillment logging gaps without penalizing `134` Physical store pickups |

---

### 3.3 `vw_return_metrics` — Order-Cohort Return & Operational SLA View

- **Grain:** One row per `(year, month)` of the **original `fact_orders.order_date_key`** (cohort-aligned to the order placement month, NOT the return arrival month).
- **Source:** `retailbi_mart.fact_returns` JOIN `retailbi_mart.fact_orders` ON `order_key`.

| Column | Formula / SQL Definition | Denominator & Exclusion Rule | Notes |
| :--- | :--- | :--- | :--- |
| `year`, `month` | Original order year and month (`fo.order_date_key`) | Cohort-aligned to `vw_monthly_kpi` | Enables accurate cohort return rates against original order month |
| `total_returns` | `COUNT(*)` | All `1,288` return records | Financial reconciliation count; includes flagged temporal anomalies |
| `total_return_revenue` | `SUM(fo.net_amount)` | All `1,288` returned orders | Total net revenue value tied to returned orders (`29,446,815,953 VND`) |
| `temporal_anomaly_returns` | `COUNTIF(r.is_temporal_anomaly)` | `returned_at < order_date` (`4` rows) | Explicitly exposes quarantined anomaly volume per cohort month |
| `valid_return_count` | `COUNTIF(NOT r.is_temporal_anomaly)` | `1,284` temporally valid returns | Exact denominator for `avg_return_lead_days` |
| `avg_return_lead_days` | `AVG(IF(NOT r.is_temporal_anomaly, r.return_lead_days, NULL))` | **STRICT EXCLUSION: `is_temporal_anomaly = FALSE`** | Excludes `ret_id = 800, 1100, 1187, 1211` so negative durations (`-1` to `-3` days) never distort operational SLA |

> [!IMPORTANT]
> **Return Rate Denominator Rule:**
> - **Standard Order Return Rate** = `total_returns / total_orders` (percentage of all placed orders in that order-cohort month that were returned).
> - If a stakeholder requests **Return-to-Completed Ratio** (`total_returns / total_completed_orders`), it MUST carry that explicit name and must never be labeled simply "Return Rate", because returned orders carry `status = 'Returned'` and are mutually exclusive from `status = 'Completed'`.

---

### 3.4 `vw_product_margin_history` — SCD Type 2 Price & Margin Attribution

- **Grain:** One row per **`product_version_key`** (`dim_product_scd2`: `71` total version rows representing `47` base SKUs + `24` historical price/cost revisions across `14` SKUs; see [ADR-002](decisions/ADR-002-scd-type2-product-pricing.md)).
- **Source:** `retailbi_mart.dim_product_scd2` LEFT JOIN completed `retailbi_mart.fact_order_items` (filtered via `fact_orders.status = 'Completed'`).

| Column | Formula / SQL Definition | Denominator & Filter Rule | Notes |
| :--- | :--- | :--- | :--- |
| `product_version_key`, `product_id`, `product_code`, `product_name`, `category`, `sub_category`, `brand` | Version and product identifiers | `1` row per SKU version | Use `COUNT(DISTINCT product_id)` or `is_current = TRUE` when counting active SKUs (`47`) |
| `version_num`, `valid_from`, `valid_to`, `is_current` | SCD Type 2 effective interval `[valid_from, valid_to)` | Half-open window | Active version carries `is_current = TRUE` and `valid_to = 9999-12-31 23:59:59` |
| `catalog_msrp`, `catalog_cost_price` | Point-in-time `msrp` and `cost_price` active during window | From `dim_product_scd2` | Preserves historical catalog pricing prior to repricing |
| `catalog_margin_pct` | `SAFE_DIVIDE(catalog_msrp - catalog_cost_price, catalog_msrp) * 100` | Denominator: `catalog_msrp` | Theoretical pre-discount catalog margin (e.g., `SAM-TAB-A9`: `27.00%` v1 -> `13.09%` v4) |
| `units_sold` | `COALESCE(SUM(fi.quantity), 0)` | Completed orders only | Quantity sold while that price version was active |
| `realized_revenue` | `COALESCE(SUM(fi.line_total), 0)` | Completed orders only | Net line revenue after discounts |
| `realized_cost` | `COALESCE(SUM(fi.line_cost), 0)` | Completed orders only | Actual line COGS |
| `realized_gross_profit` | `COALESCE(SUM(fi.line_profit), 0)` | Completed orders only | `realized_revenue - realized_cost` |
| `realized_margin_pct` | `SAFE_DIVIDE(realized_gross_profit, realized_revenue) * 100` | Denominator: `realized_revenue` | Actual post-discount realized margin |
| `margin_variance_pct_pts` | `realized_margin_pct - catalog_margin_pct` | Percentage-point spread | Isolates discount erosion vs. catalog cost/MSRP repricing |

---

### 3.5 `vw_dq_executive_summary` — Data Quality & Governance Health

- **Grain:** Latest pipeline `run_id` summary aggregated across `dq_audit_results` (with drill-down by `severity` and `check_name`).
- **Source:** `retailbi_mart.dq_audit_results` (see [ADR-004](decisions/ADR-004-data-quality-quarantine-and-pdpl-pii.md)).

| Column | Formula / SQL Definition | Expected Healthy Baseline | Business Purpose |
| :--- | :--- | :---: | :--- |
| `run_id`, `checked_at` | Pipeline execution identifier and UTC timestamp | Latest run | Data freshness and audit lineage |
| `total_checks` | `COUNT(*)` | All configured assertions | Coverage of automated DQ gate |
| `failed_critical_checks` | `COUNTIF(severity = 'CRITICAL' AND status = 'FAIL')` | **`0`** | Release-blocking integrity & PDPL compliance gate |
| `warning_checks` | `COUNTIF(severity = 'WARNING' AND status = 'WARN')` | `2` known source defects | Surfaces non-blocking upstream ERP data bugs |
| `return_temporal_anomaly_count` | Failed rows from `return_temporal_anomaly` check | `4` (quarantined) | Tracks `returned_at < order_date` defects (`ret_id = 800, 1100, 1187, 1211`) |
| `missing_online_ship_date_count` | Failed rows from `online_completed_missing_ship_date` check | `333` (`WARNING`) | Tracks Web/App fulfillment logging gaps |
| `physical_pickup_null_ship_count` | Rows logged by `physical_pickup_ship_date_info` check | `134` (`INFO`, mapped `-3`) | Confirms in-store pickups are not miscounted as missing data |
| `pdpl_pii_exposure_count` | Prohibited PII columns detected in `dim_customer` | **`0`** | Continuous proof of Vietnam PDPL `91/2025/QH15` compliance |

---

## 4. Multi-Snapshot Customer RFM Mart (`mart_rfm_snapshot`)

- **Grain:** Composite primary key **`(snapshot_date, customer_key)`** — upgraded from a single static table to a partitioned multi-snapshot mart (`PARTITION BY snapshot_date`, `CLUSTER BY rfm_segment, customer_key`).
- **Initial Reference Snapshot:** `2025-01-01` (covering completed order history through `2024-12-31`). Additional monthly or quarterly snapshots append cleanly under new `snapshot_date` partitions without overwriting prior cohorts.
- **Eligibility Filter:** Computed exclusively from `fact_orders WHERE status = 'Completed' AND order_date <= snapshot_date`.

### 4.1 RFM Scoring & Segment Definitions

| Metric / Segment | SQL Rule | Notes |
| :--- | :--- | :--- |
| `recency_days` | `DATE_DIFF(snapshot_date, MAX(order_date), DAY)` | Lower is better; scored via `NTILE(5) OVER (PARTITION BY snapshot_date ORDER BY recency_days DESC)` -> `r_score` (`5` = most recent) |
| `frequency_orders` | `COUNT(*)` of completed orders | Higher is better; scored via `NTILE(5) OVER (PARTITION BY snapshot_date ORDER BY frequency_orders ASC)` -> `f_score` (`5` = highest frequency) |
| `monetary_value` | `SUM(net_amount)` of completed orders | Higher is better; scored via `NTILE(5) OVER (PARTITION BY snapshot_date ORDER BY monetary_value ASC)` -> `m_score` (`5` = highest spend) |
| **`Champions`** | `r_score >= 4 AND f_score >= 4 AND m_score >= 4` | Best recent, frequent, high-value customers |
| **`Loyal`** | `r_score >= 3 AND f_score >= 3` *(not Champions)* | Consistent repeat buyers |
| **`At-risk`** | `r_score <= 2 AND f_score >= 3` | Previously frequent buyers showing recency decay — priority win-back segment |
| **`Churned`** | `r_score <= 2 AND f_score <= 2` | Dormant low-frequency accounts |
| **`Regular`** | All remaining combinations (`ELSE`) | Developing or occasional buyers |

> [!WARNING]
> **Multi-Snapshot Aggregation Guardrail:** Because `mart_rfm_snapshot` has grain `(snapshot_date, customer_key)`, Power BI cards and distribution charts MUST filter to a single `snapshot_date` (defaulting to `MAX(mart_rfm_snapshot[snapshot_date])`) unless explicitly plotting cohort migration across `snapshot_date`. Summing `monetary_value` or counting rows across multiple `snapshot_date` partitions without filtering will double-count customers.

---

## 5. Power BI DAX Alignment Reference

Define centralized measures in a dedicated `_Measures` table in Power BI to guarantee 1:1 parity with SQL views:

```dax
-- 1. Core Commercial KPIs (Aligned to vw_monthly_kpi & vw_channel_performance)
Total Orders =
COUNTROWS ( fact_orders )

Completed Orders =
CALCULATE (
    COUNTROWS ( fact_orders ),
    fact_orders[status] = "Completed"
)

Completed Revenue =
CALCULATE (
    SUM ( fact_orders[net_amount] ),
    fact_orders[status] = "Completed"
)

Completed Cost =
CALCULATE (
    SUM ( fact_orders[total_cost] ),
    fact_orders[status] = "Completed"
)

Gross Profit =
[Completed Revenue] - [Completed Cost]

Gross Margin % =
DIVIDE ( [Gross Profit], [Completed Revenue], 0 )

AOV (Completed) =
DIVIDE ( [Completed Revenue], [Completed Orders], 0 )

Completed Discount =
CALCULATE (
    SUM ( fact_orders[total_discount] ),
    fact_orders[status] = "Completed"
)

Order Completion Rate % =
DIVIDE ( [Completed Orders], [Total Orders], 0 )

-- 2. Return & Operational SLA Measures (Aligned to vw_return_metrics & ADR-004)
Total Returns =
COUNTROWS ( fact_returns )

Return Value =
CALCULATE (
    SUM ( fact_orders[net_amount] ),
    TREATAS ( VALUES ( fact_returns[order_key] ), fact_orders[order_key] )
)

Order Return Rate (All Orders) =
DIVIDE ( [Total Returns], [Total Orders], 0 )

Return-to-Completed Ratio =
DIVIDE ( [Total Returns], [Completed Orders], 0 )

Avg Return Lead Days (Clean) =
CALCULATE (
    AVERAGE ( fact_returns[return_lead_days] ),
    fact_returns[is_temporal_anomaly] = FALSE ()
)

Quarantined Return Anomalies =
CALCULATE (
    COUNTROWS ( fact_returns ),
    fact_returns[is_temporal_anomaly] = TRUE ()
)

-- 3. SCD Type 2 Product Catalog & Margin Measures (Aligned to vw_product_margin_history & ADR-002)
Active Catalog SKUs =
CALCULATE (
    DISTINCTCOUNT ( dim_product_scd2[product_id] ),
    dim_product_scd2[is_current] = TRUE ()
)

Realized Line Margin % =
DIVIDE (
    CALCULATE ( SUM ( fact_order_items[line_profit] ), fact_orders[status] = "Completed" ),
    CALCULATE ( SUM ( fact_order_items[line_total] ), fact_orders[status] = "Completed" ),
    0
)

-- 4. Multi-Snapshot RFM Guardrail Measures (Aligned to mart_rfm_snapshot)
RFM Customers (Latest Snapshot) =
VAR LatestSnapshot = MAX ( mart_rfm_snapshot[snapshot_date] )
RETURN
CALCULATE (
    DISTINCTCOUNT ( mart_rfm_snapshot[customer_key] ),
    mart_rfm_snapshot[snapshot_date] = LatestSnapshot
)

RFM Monetary Value (Latest Snapshot) =
VAR LatestSnapshot = MAX ( mart_rfm_snapshot[snapshot_date] )
RETURN
CALCULATE (
    SUM ( mart_rfm_snapshot[monetary_value] ),
    mart_rfm_snapshot[snapshot_date] = LatestSnapshot
)

-- 5. Data Quality Executive Card Measures (Aligned to vw_dq_executive_summary & ADR-004)
DQ Critical Failures =
CALCULATE (
    COUNTROWS ( dq_audit_results ),
    dq_audit_results[severity] = "CRITICAL",
    dq_audit_results[status] = "FAIL"
)

Missing Online Ship Dates =
CALCULATE (
    COUNTROWS ( fact_orders ),
    fact_orders[is_missing_online_ship_date] = TRUE ()
)
```

---

## 6. Naming & Governance Checklist for Contributors

1. **Completed-Only Transparency:** Any measure restricted to `status = 'Completed'` must explicitly state `Completed` in its name or definition (or be a recognized accounting metric like `Gross Profit` / `Gross Margin %` explicitly documented above).
2. **Denominator Clarity:** Never change a denominator (e.g., `total_orders` vs. `total_completed_orders`, or `1,288` total returns vs. `1,284` valid returns) without renaming the measure and updating this glossary.
3. **SCD2 SKU Counting:** Always use `DISTINCTCOUNT(dim_product_scd2[product_id])` or filter `is_current = TRUE` when reporting SKU counts; never count raw rows of `dim_product_scd2` (`71` versions) as distinct products (`47` SKUs).
4. **Snapshot Safety:** Always pin `mart_rfm_snapshot` visuals to a single `snapshot_date` unless plotting time-series segment migration.
5. **Anomaly Disclosure:** Whenever displaying `Avg Return Lead Days (Clean)`, keep a companion indicator or tooltip referencing `Quarantined Return Anomalies` (`4` excluded rows) for full analytical transparency.
