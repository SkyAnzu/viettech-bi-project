# ADR-002: SCD Type 2 Product Pricing Dimension (`dim_product_scd2`)

| Metadata | Value |
| :--- | :--- |
| **Status** | Accepted |
| **Date** | 2025-03-01 |
| **Supersedes** | Legacy SCD Type 1 `dim_product` (ignoring `stg_product_price_history`) |
| **Affected Components** | `sql/02_create_star_schema.sql`, `sql/03_load_star_schema.sql`, `sql/05_bi_views.sql` (`vw_product_margin_history`), Power BI Product page |
| **Related ADRs** | [ADR-001](ADR-001-deterministic-surrogate-keys.md), [ADR-003](ADR-003-incremental-watermark-and-partitioning.md) |

---

## 1. Context

In the initial VietTech Retail BI implementation, `scripts/etl_csv_to_bq.py` faithfully extracted `data/raw/product_price_history.csv` into `retailbi_stg.stg_product_price_history`, yet `sql/03_load_star_schema.sql` never queried it. Instead, `dim_product` was built purely as a **Slowly Changing Dimension Type 1 (overwrite)** directly from `stg_products`.

Forensic inspection of the synthetic VietTech dataset (`47` SKUs across `14` sub-categories, `2023-01-06` to `2024-12-31`) revealed **`24` historical price and cost changes across `14` SKUs**:
- **`13` `COST` adjustments** (supplier cost fluctuations)
- **`11` `MSRP` adjustments** (list-price markdowns and repricing)

### Forensic Trace: How Type 1 Silently Distorted Historical Margin Analysis

Consider two verified SKUs from `data/raw/product_price_history.csv` and `data/raw/products.csv`:

1. **`SAM-TAB-A9` (`product_id = 25`, Samsung Galaxy Tab A9)** — experienced **3 price/cost revisions (4 distinct catalog versions)**:

| Version | Effective Window `[valid_from, valid_to)` | Change Type | Catalog MSRP (VND) | Catalog Cost (VND) | Catalog Gross Margin % |
| :---: | :--- | :--- | :---: | :---: | :---: |
| v1 | `2022-12-01 08:00:00` to `2023-02-12 08:00:00` | Initial Launch | 7,990,000 | 5,833,000 | **27.00%** |
| v2 | `2023-02-12 08:00:00` to `2024-01-28 08:00:00` | `MSRP` (-250,000) | 7,740,000 | 5,833,000 | **24.64%** |
| v3 | `2024-01-28 08:00:00` to `2024-05-27 08:00:00` | `COST` (+374,000) | 7,740,000 | 6,207,000 | **19.81%** |
| v4 | `2024-05-27 08:00:00` to `9999-12-31 23:59:59` | `MSRP` (-598,000) | 7,142,000 | 6,207,000 | **13.09%** |

2. **`HP-ENVY-X360` (`product_id = 16`, HP Envy x360 15)** — experienced **3 revisions (4 distinct versions)** where catalog margin expanded from **`30.00%`** (`25,990,000` MSRP / `18,193,000` COST on `2022-12-01`) to **`36.51%`** (`28,388,000` MSRP / `18,023,000` COST on `2024-04-05`).

Under SCD Type 1, joining 2023 line items in `fact_order_items` to `dim_product` compared 2023 realized transaction prices against late-2024 overwritten `msrp` and `cost_price`. For `SAM-TAB-A9`, this understated early-2023 catalog margin by **13.91 percentage points**, making it impossible to separate **supplier cost inflation**, **list-price repositioning**, and **promotional discounting** (critical during Vietnamese electronics retail cycles such as Tết Nguyên Đán and year-end clearance).

---

## 2. Decision Drivers

- **Point-in-Time Historical Attribution:** Every order line item (`39,548` rows) must join to the exact catalog `msrp` and `cost_price` active at `orders.order_date`.
- **Full Lineage Activation:** Eliminate dead staging data by activating `stg_product_price_history` inside the Star Schema.
- **Dual Analytical Perspectives:** Support both **as-was** historical pricing analysis (via `product_version_key`) and **as-is** SKU rollups across time (via `product_id` / `product_code` or current-flag filtering).
- **Power BI Simplicity:** Maintain standard Star Schema single-column `1:N` joins (`dim_product_scd2[product_version_key] -> fact_order_items[product_version_key]`) without complex DAX `TREATAS` range joins at report query time.

---

## 3. Considered Options

| Option | Mechanism | Pros | Cons | Trade-offs & Verdict |
| :--- | :--- | :--- | :--- | :--- |
| **Option 1: Retain SCD Type 1** | Overwrite `dim_product` from `stg_products` only | Minimal SQL; 1 row per SKU (`47` rows) | Destroys all `24` historical price/cost transitions; corrupts historical catalog-vs-realized margin variance | **Rejected** — fails core retail margin-attribution requirement |
| **Option 2: SCD Type 3** | Add `previous_msrp` and `previous_cost_price` columns | Keeps 1 row per SKU (`47` rows) | Stores at most 1 prior state; cannot model SKUs with 3 changes / 4 versions (`product_id` 16, 19, 25, 45) | **Rejected** — structurally truncates multi-change price history |
| **Option 3: Separate Price Snapshot Bridge** | Keep `dim_product` Type 1 + separate `fact_product_price_history` range table | Clean separation of catalog metadata and pricing | Requires non-equi timestamp joins at query time (unsupported natively in Power BI relationships); slows DAX evaluation | **Rejected** — pushes range-join complexity onto BI analysts |
| **Option 4: SCD Type 2 (`dim_product_scd2`)** | Versioned dimension (`valid_from`, `valid_to`, `is_current`) + `product_version_key` on `fact_order_items` | Exact point-in-time joins resolved once in ETL; simple `1:N` Star Schema join in Power BI; handles arbitrary change counts | Modest row growth (`47` base SKUs + `24` change events = `71` rows); requires `is_current = TRUE` filter when counting active SKUs | **Selected** — industry-standard Kimball solution for historical pricing & margin analysis |

---

## 4. Decision Outcome

We promote `dim_product` to **`dim_product_scd2`** (Kimball SCD Type 2) and stamp **`product_version_key`** onto `fact_order_items` during warehouse transformation.

### 4.1 Version Timeline Reconstruction

Because `stg_products` holds the latest state (`msrp`, `cost_price`, `created_at`) while `stg_product_price_history` logs incremental changes (`price_type IN ('MSRP', 'COST')`, `old_price`, `new_price`, `changed_at`), the SQL transformation reconstructs the complete version chain per `product_id`:

1. **Anchor Initial State (v1):** For each of the `47` SKUs, recover initial `msrp` and `cost_price` at `products.created_at` (`2022-12-01 08:00:00`) using the earliest `old_price` per `price_type` (falling back to current `stg_products` price when a price type never changed).
2. **Apply Chronological Events:** Order all `24` rows in `stg_product_price_history` by `(product_id, changed_at, id)` and carry forward the unchanged price component (`MSRP` or `COST`) using `LAST_VALUE(... IGNORE NULLS)` window functions.
3. **Construct Half-Open Effective Windows:**
   - `valid_from`: `created_at` for v1; `changed_at` for subsequent versions.
   - `valid_to`: `LEAD(valid_from) OVER (PARTITION BY product_id ORDER BY valid_from)` with open-ended terminal sentinel `TIMESTAMP '9999-12-31 23:59:59'` for the active version.
   - `is_current`: `TRUE` exclusively for the terminal version (`47` rows total).
4. **Mint Deterministic Version Key:** Per [ADR-001](ADR-001-deterministic-surrogate-keys.md), `product_version_key = TO_HEX(SHA256(CONCAT('PROD|', CAST(product_id AS STRING), '|', FORMAT_TIMESTAMP('%Y-%m-%dT%H:%M:%S', valid_from))))`.

### 4.2 Point-in-Time Fact Resolution

During `fact_order_items` load, each order line item resolves its exact historical product version once via half-open interval matching:

```sql
JOIN retailbi_mart.dim_product_scd2 dp
  ON CAST(od.product_id AS INT64) = dp.product_id
 AND TIMESTAMP(o.order_date) >= dp.valid_from
 AND TIMESTAMP(o.order_date) < dp.valid_to
```

### 4.3 Semantic Exposure

- **`vw_product_margin_history`** (`sql/05_bi_views.sql`) aggregates completed sales per `product_version_key`, pairing catalog margin (`(msrp - cost_price) / msrp`) against realized line margin (`SUM(line_profit) / SUM(line_total)`).
- Legacy catalog queries requiring exactly one row per SKU filter `WHERE is_current = TRUE` (or consume a compatibility projection of `dim_product_scd2`).

---

## 5. Consequences & Verification

### Positive Consequences
- **Full Margin Attribution:** Finance and merchandising teams can isolate whether a SKU's gross margin shift came from supplier cost hikes, MSRP markdowns, or heavier promotional discounting.
- **Zero Query-Time Range Joins:** Power BI joins `fact_order_items[product_version_key]` to `dim_product_scd2[product_version_key]` using a fast `1:N` single-column relationship.
- **Auditability:** Every historical price change in `stg_product_price_history` (`24` events across `14` SKUs) is preserved and traceable by `version_num` and `valid_from`.

### Negative Consequences & Mitigations
- **SKU Counting Trap:** `COUNT(*)` on `dim_product_scd2` returns `71` versions rather than `47` distinct SKUs.
  - *Mitigation:* Document explicitly in [KPI_GLOSSARY.md](../KPI_GLOSSARY.md) that catalog SKU counts must use `COUNT(DISTINCT product_id)` or `WHERE is_current = TRUE`.
- **Retroactive History Backfills:** If an ERP system retroactively corrects an old `changed_at` timestamp, impacted `fact_order_items` partitions must be re-merged so `product_version_key` reflects the updated timeline.

### Verification Criteria
1. **Version Count Reconciliation:** `dim_product_scd2` contains `71` rows (`47` base versions + `24` change events), `COUNT(DISTINCT product_id) = 47`, and `SUM(CASE WHEN is_current THEN 1 ELSE 0 END) = 47`.
2. **Non-Overlapping Interval Assertion:** Zero pairs of versions for the same `product_id` satisfy `a.valid_from < b.valid_to AND b.valid_from < a.valid_to`, and no gaps exist between consecutive `valid_to` and `valid_from`.
3. **Exact 1:1 Line-Item Resolution:** Joining `39,548` `stg_order_details` rows against `dim_product_scd2` produces exactly `39,548` rows in `fact_order_items` (`0` NULL `product_version_key`, `0` fan-out duplicates).
4. **Margin Accounting Integrity:** Total completed `line_profit` across `fact_order_items` reconciles cleanly with `fact_orders` completed Gross Profit (`107,144,233,955 VND`, `28.17%` gross margin on the synthetic dataset).
