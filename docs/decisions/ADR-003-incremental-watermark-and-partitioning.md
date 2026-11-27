# ADR-003: Incremental Watermark Upserts, Partitioning & Dual-Engine Execution

| Metadata | Value |
| :--- | :--- |
| **Status** | Accepted |
| **Date** | 2025-03-01 |
| **Supersedes** | Full-refresh `WRITE_TRUNCATE` + unpartitioned `CREATE OR REPLACE TABLE` BigQuery-only execution |
| **Affected Components** | `scripts/etl_csv_to_bq.py`, `scripts/run_sql_pipeline.py`, local SQLite pipeline runner, `sql/02_create_star_schema.sql`, `sql/03_load_star_schema.sql`, `sql/04_rfm_snapshot.sql` |
| **Related ADRs** | [ADR-001](ADR-001-deterministic-surrogate-keys.md), [ADR-002](ADR-002-scd-type2-product-pricing.md), [ADR-004](ADR-004-data-quality-quarantine-and-pdpl-pii.md) |

---

## 1. Context

The original VietTech Retail BI pipeline suffered from three operational scalability and accessibility limitations:

1. **Unbounded Full-Refresh Cost:** `scripts/etl_csv_to_bq.py` loaded all 8 staging tables using `WriteDisposition.WRITE_TRUNCATE`, and `sql/03_load_star_schema.sql` rebuilt every dimension and fact table via `CREATE OR REPLACE TABLE`. Even though the OLTP schema (`data/raw/schema.sql`) already defined `INDEX idx_updated_at (updated_at)` on `products`, `customers`, and `orders` specifically for incremental extraction, the pipeline ignored watermarks and reprocessed 100% of history on every run.
2. **Unpartitioned & Unclustered BigQuery Storage:** Fact tables (`fact_orders`, `fact_order_items`, `fact_returns`) and `mart_rfm_snapshot` had no `PARTITION BY` or `CLUSTER BY` clauses. Every monthly Power BI visual or YoY Tết comparison scanned 100% of table bytes, driving unnecessary BigQuery on-demand query costs (`$6.25/TiB` scanned) as data volume grows.
3. **Cloud-Locked Reproducibility:** Reviewers, hiring managers, and CI workflows could not execute or verify the pipeline without provisioning a paid GCP project, BigQuery datasets, and a service account JSON credential.

---

## 2. Decision Drivers

- **Incremental Cost Efficiency:** Process only new or updated records since the last successful high watermark, while safely handling mutable order status transitions (`Pending -> Processing -> Shipped -> Completed -> Returned`).
- **Query Pruning in BigQuery:** Minimize bytes scanned for time-range, channel, status, and RFM snapshot queries via native partitioning and clustering.
- **Zero-Cloud CI & Portfolio Reproducibility:** Enable any reviewer or GitHub Actions runner to execute the full warehouse build, SCD2 logic, DQ gate, RFM snapshot, and BI views locally in seconds with zero cloud credentials.
- **Strict SQL Parity:** Keep transformation business logic synchronized between the production BigQuery path and the local SQLite verification path.

---

## 3. Considered Options

| Option | Mechanism | Pros | Cons | Trade-offs & Verdict |
| :--- | :--- | :--- | :--- | :--- |
| **Option 1: Keep Full Refresh (BigQuery Only)** | `WRITE_TRUNCATE` + `CREATE OR REPLACE TABLE` | Simplest code path | Linear compute/cost growth; cannot prune partitions; unrunnable without GCP credentials | **Rejected** — fails production scalability and portfolio verifiability |
| **Option 2: Pure Append-Only (`WRITE_APPEND`)** | Filter `order_date > last_run` and append to facts | Fast ingestion; avoids `MERGE` scan | Produces duplicate order rows whenever an order's `status` or `ship_date` updates after creation | **Rejected** — corrupts order counts and completed revenue totals |
| **Option 3: Real-Time CDC Streaming** | Debezium + Kafka + BigQuery Storage Write API | Sub-minute analytical latency | Heavy infrastructure footprint (brokers, connectors, schema registry) unjustified for daily/hourly retail BI dashboards | **Rejected** — over-engineered for batch BI SLA |
| **Option 4: Watermark `MERGE` + BigQuery Partition/Cluster + Dual-Engine Local SQLite** | `etl_watermarks` control table + idempotent `MERGE` + `PARTITION BY`/`CLUSTER BY` + local SQLite CI runner | Up to 85–99% incremental row reduction; handles mutable order updates cleanly; zero-cloud CI & reviewer verification | Must maintain dialect compatibility for minor SQL syntax differences (e.g., `SAFE_DIVIDE`, `TO_HEX(SHA256(...))`) | **Selected** — production-grade cloud warehouse paired with zero-friction local verification |

---

## 4. Decision Outcome

We implement a **three-part operational upgrade**: Watermark-driven `MERGE` upserts, BigQuery physical partitioning/clustering, and a Dual-Engine local SQLite execution path.

### 4.1 Watermark Control & Incremental `MERGE` Pattern

A dedicated metadata table `etl_watermarks` tracks high-water timestamps per target table:
- Columns: `pipeline_name`, `table_name`, `high_watermark_ts`, `last_run_at`, `rows_processed`, `run_mode`, `run_status`.
- Supported execution modes:
  - `--mode full`: Initial backfill or schema-rebuild run across full source history (`2023-01-06` to `2024-12-31`).
  - `--mode incremental [--since YYYY-MM-DDTHH:MM:SS]`: Extracts only records where `updated_at >= high_watermark_ts` (or `returned_at` / `changed_at` for append-oriented event tables) with a configurable safety lookback window (default 1 day) to capture late-arriving updates.
- **Idempotent Upsert:** Using deterministic SHA-256 surrogate keys ([ADR-001](ADR-001-deterministic-surrogate-keys.md)), `MERGE INTO <mart_table> T USING <delta_source> S ON T.<surrogate_key> = S.<surrogate_key>` updates mutable columns (`status`, `ship_date_key`, `is_missing_online_ship_date`, amounts) when matched and inserts new rows when not matched.

#### Verified Incremental Workload Reduction (Synthetic Dataset Benchmark)

Measured directly on the fixed **synthetic VietTech dataset** (`21,435` orders across `2023-01-06..2024-12-31`):

| Execution Slice | Date Filter | Orders Processed | Share of Full Dataset | Row-Processing Reduction |
| :--- | :--- | :---: | :---: | :---: |
| **Full Backfill Baseline** | `2023-01-06 .. 2024-12-31` | `21,435` | `100.00%` | `0.00%` (baseline) |
| **Rolling 30-Day Incremental** | `order_date >= 2024-12-02` | `3,017` | `14.08%` | **85.92% reduction** |
| **Daily Incremental Batch** | `order_date = 2024-12-31` | `216` | `1.01%` | **98.99% reduction** |

> [!NOTE]
> These figures are **verified synthetic dataset measurements**, not production traffic claims. At enterprise scale (e.g., 10M+ orders across 3+ years), BigQuery partition pruning compounds these row reductions into direct slot and on-demand scan savings.

### 4.2 BigQuery Physical Layout (`PARTITION BY` & `CLUSTER BY`)

Each analytical table adds an explicit `DATE` column alongside integer `*_date_key` to unlock native BigQuery partition pruning without casting:

| Mart Table | `PARTITION BY` | `CLUSTER BY` | Primary Query Pattern Optimized |
| :--- | :--- | :--- | :--- |
| `fact_orders` | `DATE_TRUNC(order_date, MONTH)` | `status, channel_key, customer_key` | Monthly KPI trends, completed-only revenue, channel breakdown, RFM aggregation |
| `fact_order_items` | `DATE_TRUNC(order_date, MONTH)` | `product_version_key, order_key` | Historical product margin analysis (`vw_product_margin_history`), basket joins |
| `fact_returns` | `DATE_TRUNC(returned_date, MONTH)` | `is_temporal_anomaly, order_key` | Return cohort rate, clean lead-time SLA (`is_temporal_anomaly = FALSE`) |
| `mart_rfm_snapshot` | `snapshot_date` | `rfm_segment, customer_key` | Point-in-time RFM segmentation and multi-snapshot cohort migration |
| `dq_audit_results` | `DATE(checked_at)` | `severity, check_name` | Executive DQ trend monitoring (`vw_dq_executive_summary`) |

Monthly partitioning (`DATE_TRUNC(..., MONTH)`) is chosen over daily partitioning for the retail fact tables to stay comfortably under BigQuery's 4,000-partition-per-table limit across multi-decade retention while ensuring each partition exceeds BigQuery's optimal minimum partition size as data scales.

### 4.3 Dual-Engine Local SQLite Runner

To eliminate cloud friction during development, automated CI, and portfolio review:
- **Production Engine (BigQuery):** Executes parameterized SQL under `sql/` with native BigQuery partitioning, clustering, `TO_HEX(SHA256(...))`, and `MERGE`.
- **Local Verification Engine (SQLite + Python stdlib `sqlite3` / `hashlib`):** Registers a deterministic `sha256_hex()` UDF matching BigQuery's `TO_HEX(SHA256(...))`, loads all 8 raw CSVs from `data/raw/`, builds the exact same Star Schema (`dim_product_scd2`, PII-segregated `dim_customer`, `fact_*`, `mart_rfm_snapshot`), executes the automated DQ gate (`dq_audit_results`), creates all 5 BI views, and verifies KPI reconciliation locally in seconds.

---

## 5. Consequences & Verification

### Positive Consequences
- **Predictable Cloud Economics:** Incremental `MERGE` avoids rewriting historical partitions, and `PARTITION BY` / `CLUSTER BY` minimizes BigQuery bytes scanned on every Power BI refresh.
- **Safe Late-Arriving Updates:** Orders that transition from `Pending` / `Processing` to `Completed` or `Returned` update cleanly in-place without duplicate rows.
- **One-Command Local Reproducibility:** Any reviewer or CI pipeline can validate the entire warehouse end-to-end without GCP IAM setup or billing risk.

### Negative Consequences & Mitigations
- **Dual-Dialect Maintenance:** BigQuery SQL and SQLite SQL differ on functions (`SAFE_DIVIDE` vs. `NULLIF`, `NTILE` behavior, `MERGE` vs. `INSERT ... ON CONFLICT DO UPDATE`).
  - *Mitigation:* Enforce a shared automated reconciliation test asserting both engines produce identical row counts, surrogate key hashes, and KPI totals (`Completed Revenue = 380,360,467,955 VND`, `Gross Profit = 107,144,233,955 VND`).
- **Watermark Drift Risk:** Updating `etl_watermarks` before a downstream transform finishes could skip failed records.
  - *Mitigation:* Advance `etl_watermarks.high_watermark_ts` atomically only after both fact `MERGE` and `CRITICAL` DQ assertions pass.

### Verification Criteria
1. **Incremental Idempotency Test:** Load data up to `2024-12-01`, advance watermark, run incremental slice for `>= 2024-12-02` (`3,017` orders), then re-run the exact same incremental slice; assert final row counts (`21,435` orders) and `Completed Revenue` (`380,360,467,955 VND`) exactly match a full-refresh build.
2. **Partition Pruning Inspection:** Verify BigQuery `DRY_RUN` / query plan prunes non-target month partitions on `vw_monthly_kpi` filtered to a single quarter.
3. **Zero-Cloud CI Check:** Run the local SQLite pipeline end-to-end in an offline environment with zero GCP credentials and confirm exit code `0` plus all 5 BI views populated.
