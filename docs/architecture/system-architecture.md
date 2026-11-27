# VietTech Retail BI — System & Data Warehouse Architecture

> **Dataset Disclosure:** All baseline row counts, KPI figures, and incremental benchmark reductions referenced in this document are verified against the fixed **synthetic VietTech Retail dataset** (`2023-01-06` to `2024-12-31`: `21,435` orders, `39,548` order line items, `1,288` returns, `5,000` customers, `47` SKUs across `14` sub-categories, `8` omnichannel sales channels, and `24` historical price/cost changes across `14` SKUs).

---

## 1. Architectural Overview & Decision Index

The VietTech Retail BI Platform transforms raw omnichannel electronics retail extracts (6 Physical stores in Hanoi, Hai Phong, Hue, Da Nang, Ho Chi Minh City, and Can Tho + Web + App) into a governed Kimball Star Schema, multi-snapshot RFM customer segmentation mart, and five Power BI-ready semantic views.

The architecture incorporates four core engineering upgrades documented in formal Architecture Decision Records (ADRs):

| ADR | Decision | Key Problem Solved |
| :--- | :--- | :--- |
| [ADR-001](../decisions/ADR-001-deterministic-surrogate-keys.md) | **Deterministic SHA-256 Surrogate Keys** | Replaces non-idempotent `GENERATE_UUID()` so incremental `MERGE`, multi-snapshot RFM joins, and cross-engine CI parity never break |
| [ADR-002](../decisions/ADR-002-scd-type2-product-pricing.md) | **SCD Type 2 Product Pricing (`dim_product_scd2`)** | Activates `product_price_history` (`24` price/cost changes on `14` SKUs -> `71` versions) for exact point-in-time historical margin attribution |
| [ADR-003](../decisions/ADR-003-incremental-watermark-and-partitioning.md) | **Watermark `MERGE`, Partitioning & Dual-Engine** | Eliminates full-refresh cost (up to `85.92%`–`98.99%` incremental reduction on synthetic slices), adds BigQuery `PARTITION BY`/`CLUSTER BY`, and provides a zero-cloud local SQLite CI runner |
| [ADR-004](../decisions/ADR-004-data-quality-quarantine-and-pdpl-pii.md) | **Tiered DQ Quarantine & Vietnam PDPL PII Segregation** | Isolates `4` real `returned_at < order_date` anomalies, distinguishes `134` Physical pickups (`-3`) from `333` online missing ship dates (`-1`), and removes raw PII from `dim_customer` per Vietnam PDPL `91/2025/QH15` |

---

## 2. C4 Level 1 — System Context Diagram

Shows how business stakeholders, data engineers, compliance auditors, and external source/BI systems interact with the VietTech Retail BI Platform.

```mermaid
C4Context
  title System Context — VietTech Retail BI Platform

  Person(execAnalyst, "Executive & BI Analyst", "Monitors revenue, margin, Tết seasonality, returns, and RFM cohorts")
  Person(dataEng, "Data Engineer & CI Reviewer", "Operates incremental ETL, verifies DQ checks locally or in cloud")
  Person(dpoAuditor, "DPO & Compliance Auditor", "Verifies Vietnam PDPL 91/2025/QH15 PII segregation and DQ audit trail")

  System(viettechBi, "VietTech Retail BI Platform", "Dual-engine ETL, Star Schema Mart, SCD2 pricing, DQ gate, and semantic views")

  System_Ext(oltpErp, "VietTech OLTP / Retail ERP", "MySQL operational schema exporting 8 CSV snapshots across 8 channels")
  System_Ext(gcpBq, "Google Cloud BigQuery", "Cloud analytical warehouse in asia-southeast1 hosting staging and mart datasets")
  System_Ext(powerBi, "Power BI Service & Desktop", "4-page executive BI report suite consuming semantic views and star schema")

  Rel(oltpErp, viettechBi, "Exports raw operational CSV snapshots", "CSV / Batch")
  Rel(dataEng, viettechBi, "Runs full, incremental, and local CI pipelines", "CLI / Python")
  Rel(viettechBi, gcpBq, "Loads staging and executes partitioned MERGE SQL", "BigQuery API")
  Rel(gcpBq, powerBi, "Serves governed Star Schema and 5 BI views", "BigQuery Connector")
  Rel(execAnalyst, powerBi, "Analyzes KPIs, product margins, and RFM segments", "HTTPS / Desktop")
  Rel(dpoAuditor, viettechBi, "Audits DQ alerts and zero-PII mart schema", "SQL / Audit Views")

  UpdateLayoutConfig($c4ShapeInRow="3", $c4BoundaryInRow="1")
```

---

## 3. C4 Level 2 — Container Diagram (Dual-Engine Architecture)

Illustrates the **Dual-Engine Architecture**: the **Production Cloud Path** on BigQuery (partitioned, clustered, and PII-segregated) alongside the **Local CI & Portfolio Review Path** on SQLite (zero GCP credentials required, bit-identical SHA-256 keys and KPI reconciliation).

```mermaid
C4Container
  title Container Diagram — Dual-Engine Cloud and Local CI Architecture

  Person(analyst, "Executive & BI Analyst", "Consumes 4-page Power BI dashboards")
  Person(reviewer, "Engineer & Portfolio Reviewer", "Runs pipelines and automated verification")

  System_Ext(rawFiles, "Raw OLTP CSV Snapshots", "8 CSV tables in data/raw including product_price_history")
  System_Ext(powerBiApp, "Power BI Report Suite", "Business Performance, Product, RFM, and Order pages")

  System_Boundary(viettechPlatform, "VietTech Retail BI Platform") {
    Container(etlController, "ETL & Watermark Orchestrator", "Python 3.10+", "Loads staging, tracks etl_watermarks, supports full and incremental modes")
    Container(dqGate, "Automated DQ & Quarantine Engine", "Python + SQL", "Runs CRITICAL, WARNING, and INFO assertions and logs dq_audit_results")

    Container_Boundary(localPath, "Local CI and Review Engine — Zero Cloud Credentials") {
      ContainerDb(sqliteWh, "Local SQLite Warehouse", "SQLite 3 + sha256_hex UDF", "Reproduces Star Schema, SCD2, DQ gate, RFM, and all 5 BI views locally")
      Container(ciSuite, "Automated Verification Suite", "Pytest / Python", "Asserts key idempotency, KPI parity, anomaly counts, and zero PII in mart")
    }

    Container_Boundary(cloudPath, "Production Cloud Path — Google Cloud BigQuery asia-southeast1") {
      ContainerDb(bqStaging, "BigQuery Staging", "retailbi_stg", "8 raw stg_* tables ingested via batch or incremental slice")
      ContainerDb(bqPiiVault, "Restricted PII Vault", "retailbi_pii_vault", "IAM-restricted direct PII isolated per Vietnam PDPL 91/2025/QH15")
      ContainerDb(bqMart, "BigQuery Star Schema Mart", "retailbi_mart", "Partitioned and clustered dims, SCD2, facts, RFM snapshot, and 5 BI views")
    }
  }

  Rel(rawFiles, etlController, "Reads raw CSV extracts", "File I/O")
  Rel(reviewer, etlController, "Invokes full, incremental, or local SQLite build", "CLI")
  Rel(etlController, bqStaging, "Loads staged tables", "BigQuery Load API")
  Rel(etlController, bqPiiVault, "Segregates customer direct PII", "Restricted IAM")
  Rel(bqStaging, bqMart, "Transforms via deterministic SHA-256 MERGE SQL", "BigQuery Standard SQL")
  Rel(bqMart, dqGate, "Validates invariants and records audit status", "SQL Assertions")
  Rel(etlController, sqliteWh, "Executes full local mirror pipeline", "SQLite3")
  Rel(sqliteWh, ciSuite, "Verifies idempotency, DQ flags, and KPI totals", "SQL / Pytest")
  Rel(bqMart, powerBiApp, "Exposes 5 BI views and Star Schema tables", "BigQuery Connector")
  Rel(analyst, powerBiApp, "Explores dashboards and DQ health", "HTTPS / Desktop")

  UpdateLayoutConfig($c4ShapeInRow="3", $c4BoundaryInRow="2")
```

---

## 4. End-to-End Data Lineage & Transformation Pipeline

Tracks data flow from raw operational extracts through PDPL pseudonymization, SCD Type 2 timeline reconstruction, Star Schema `MERGE`, automated DQ quarantine, and the 5 governed BI semantic views.

```mermaid
flowchart LR
  subgraph SRC["1. Source Extracts — data/raw"]
    S1["orders.csv — 21,435 rows"]
    S2["order_details.csv — 39,548 rows"]
    S3["order_returns.csv — 1,288 rows"]
    S4["products.csv & categories — 47 SKUs"]
    S5["product_price_history.csv — 24 changes"]
    S6["customers.csv — 5,000 rows"]
    S7["sales_channels.csv — 8 channels"]
  end

  subgraph STG["2. Staging & Control — retailbi_stg"]
    T1["8 stg_* Tables"]
    T2["etl_watermarks — High Watermark State"]
    T3["PDPL Boundary — Strip Direct PII to Vault"]
  end

  subgraph MART["3. Star Schema & Marts — retailbi_mart"]
    M1["dim_date, dim_channel, dim_payment"]
    M2["dim_customer — Pseudonymized SHA-256"]
    M3["dim_product_scd2 — 71 Version Rows"]
    M4["fact_orders & fact_order_items — Partitioned MERGE"]
    M5["fact_returns — Flagged Temporal Anomalies"]
    M6["mart_rfm_snapshot — Multi-Snapshot Grain"]
  end

  subgraph DQ["4. Automated DQ Gate"]
    D1["dq_audit_results — CRITICAL / WARNING / INFO"]
    D2["dq_quarantine_returns — 4 Temporal Anomalies"]
  end

  subgraph BI["5. Semantic Layer & Power BI"]
    V1["vw_monthly_kpi"]
    V2["vw_channel_performance"]
    V3["vw_return_metrics — Excludes Anomalies in SLA"]
    V4["vw_product_margin_history — SCD2 Attribution"]
    V5["vw_dq_executive_summary"]
    PBI["Power BI — 4 Executive Dashboards"]
  end

  S1 & S2 & S3 & S4 & S5 & S7 --> T1
  S6 --> T3
  T3 --> T1
  T2 --> T1
  T1 --> M1 & M2 & M3
  M1 & M2 & M3 --> M4
  M4 --> M5
  M4 --> M6
  M1 & M2 & M3 & M4 & M5 & M6 --> D1
  M5 --> D2
  M4 --> V1 & V2
  M5 --> V3
  M3 & M4 --> V4
  D1 --> V5
  V1 & V2 & V3 & V4 & V5 & M6 --> PBI
```

---

## 5. Upgraded Star Schema & Governance ERD

Models the full analytical warehouse (`retailbi_mart`), highlighting deterministic SHA-256 surrogate keys, `dim_product_scd2` point-in-time versioning, PDPL-compliant `dim_customer`, anomaly flags on `fact_orders` and `fact_returns`, composite-grain `mart_rfm_snapshot`, and `dq_audit_results` governance observability.

```mermaid
erDiagram
  DIM_DATE ||--o{ FACT_ORDERS : "order_date_key"
  DIM_DATE ||--o{ FACT_ORDERS : "ship_date_key"
  DIM_DATE ||--o{ FACT_ORDER_ITEMS : "order_date_key"
  DIM_DATE ||--o{ FACT_RETURNS : "returned_date_key"
  DIM_CUSTOMER ||--o{ FACT_ORDERS : "places"
  DIM_CUSTOMER ||--o{ MART_RFM_SNAPSHOT : "scored_in"
  DIM_CHANNEL ||--o{ FACT_ORDERS : "sold_via"
  DIM_PAYMENT ||--o{ FACT_ORDERS : "paid_with"
  DIM_PRODUCT_SCD2 ||--o{ FACT_ORDER_ITEMS : "priced_at_version"
  FACT_ORDERS ||--|{ FACT_ORDER_ITEMS : "contains"
  FACT_ORDERS ||--o| FACT_RETURNS : "returned_as"

  DIM_DATE {
    int date_key PK "YYYYMMDD or -1/-2/-3"
    date date
    int year
    int quarter
    int month
    int week_of_year
    int day_of_week
    bool is_weekend
    bool is_tet "Jan-Feb Lunar New Year"
    bool is_year_end "Nov-Dec peak"
  }

  DIM_CUSTOMER {
    string customer_key PK "SHA-256 CUST"
    int customer_id UK
    string customer_code UK
    string gender "PII stripped per PDPL"
    string city
    string country
    string segment "Consumer Corporate HomeOffice"
    bool is_active
  }

  DIM_PRODUCT_SCD2 {
    string product_version_key PK "SHA-256 PROD + valid_from"
    string product_key "SHA-256 PROD_ID"
    int product_id
    string product_code
    string product_name
    string category
    string sub_category
    string brand
    numeric msrp
    numeric cost_price
    numeric catalog_margin_pct
    int version_num
    timestamp valid_from
    timestamp valid_to
    bool is_current
    bool is_active
  }

  DIM_CHANNEL {
    string channel_key PK "SHA-256 CHAN"
    int channel_id UK
    string channel_code UK
    string channel_name
    string channel_type "Physical Web App"
    bool is_online
    bool is_active
  }

  DIM_PAYMENT {
    string payment_key PK "SHA-256 PAY"
    string payment_method UK
    string payment_category "Cash Card Digital"
  }

  FACT_ORDERS {
    string order_key PK "SHA-256 ORD"
    int order_id UK
    string order_code UK
    date order_date "Partition key"
    int order_date_key FK
    int ship_date_key FK "-1 missing -2 pending -3 pickup"
    string customer_key FK
    string channel_key FK
    string payment_key FK
    string status
    bool is_missing_online_ship_date "DQ warning flag"
    numeric total_amount
    numeric total_discount
    numeric net_amount
    numeric total_cost
  }

  FACT_ORDER_ITEMS {
    string order_item_key PK "SHA-256 ITEM"
    int order_id
    string order_key FK
    string product_version_key FK "SCD2 point-in-time FK"
    date order_date "Partition key"
    int order_date_key FK
    int quantity
    numeric unit_price
    numeric unit_cost
    numeric discount_rate
    numeric discount_amount
    numeric line_total
    numeric line_cost
    numeric line_profit
  }

  FACT_RETURNS {
    string return_key PK "SHA-256 RET"
    int return_id UK
    int order_id UK
    string order_key FK
    date returned_date "Partition key"
    int returned_date_key FK
    string reason
    int return_lead_days "NULL when temporal anomaly"
    bool is_temporal_anomaly "4 rows quarantined"
  }

  MART_RFM_SNAPSHOT {
    date snapshot_date PK "Partition key"
    string customer_key PK "FK to DIM_CUSTOMER"
    int customer_id
    int recency_days
    int frequency_orders
    numeric monetary_value
    int r_score
    int f_score
    int m_score
    string rfm_score
    string rfm_segment "Champions Loyal At-risk Churned Regular"
  }

  DQ_AUDIT_RESULTS {
    string audit_id PK
    string run_id
    timestamp checked_at
    string check_name
    string table_name
    string severity "CRITICAL WARNING INFO"
    string status "PASS FAIL WARN"
    int failed_row_count
    string details
  }
```

---

## 6. Storage Optimization, Trust Boundaries & Dataset Baseline

### 6.1 Physical Optimization Matrix (BigQuery vs. Local SQLite)

| Table | BigQuery Partitioning | BigQuery Clustering | Local SQLite Indexing |
| :--- | :--- | :--- | :--- |
| `fact_orders` | `DATE_TRUNC(order_date, MONTH)` | `status, channel_key, customer_key` | Composite index on `(status, order_date_key)` and `(channel_key)` |
| `fact_order_items` | `DATE_TRUNC(order_date, MONTH)` | `product_version_key, order_key` | Indexes on `(order_key)`, `(product_version_key)`, `(order_date_key)` |
| `fact_returns` | `DATE_TRUNC(returned_date, MONTH)` | `is_temporal_anomaly, order_key` | Indexes on `(order_key)`, `(is_temporal_anomaly)` |
| `dim_product_scd2` | Unpartitioned (`71` rows) | `product_id, is_current` | Index on `(product_id, valid_from, valid_to)` |
| `mart_rfm_snapshot` | `snapshot_date` | `rfm_segment, customer_key` | Primary key `(snapshot_date, customer_key)` |
| `dq_audit_results` | `DATE(checked_at)` | `severity, check_name` | Index on `(run_id, severity)` |

### 6.2 Vietnam PDPL (`91/2025/QH15`) Privacy Trust Boundary

- **Zone A — Restricted Staging & PII Vault (`retailbi_stg`, `retailbi_pii_vault`):** Contains raw operational customer fields (`first_name`, `last_name`, `email`, `phone`, `date_of_birth`). Access is restricted to ETL service accounts and DPO-authorized erasure workflows.
- **Zone B — Governed Analytics Mart (`retailbi_mart`):** Strictly **PII-free**. Customers are identified solely by deterministic SHA-256 `customer_key` and non-identifying analytical attributes (`gender`, `city`, `country`, `segment`, `is_active`). Enforced automatically on every build by a `CRITICAL` DQ schema check.

### 6.3 Verified Synthetic Dataset Baseline

| Domain Metric | Verified Value (Synthetic Dataset) | Architectural Relevance |
| :--- | :---: | :--- |
| Date Span | `2023-01-06` to `2024-12-31` | Captures 2 full Vietnamese Tết & Year-End retail cycles |
| Total Orders / Completed Orders | `21,435` / `16,796` | `1,659` Cancelled, `1,288` Returned, `837` Shipped, `661` Processing, `194` Pending |
| Order Line Items | `39,548` | 100% resolved to point-in-time `product_version_key` in `dim_product_scd2` |
| Completed Revenue / Gross Profit | `380,360,467,955 VND` / `107,144,233,955 VND` | `28.17%` gross margin (`273,216,234,000 VND` cost; `13,977,222,045 VND` completed discount) |
| Returned Orders / Return Value | `1,288` / `29,446,815,953 VND` | `4` rows flagged `is_temporal_anomaly = TRUE` (`ret_id = 800, 1100, 1187, 1211`) |
| Completed `NULL ship_date` Audit | `467` total (`134` Physical / `333` Online) | `134` remapped to `-3` (in-store pickup); `333` flagged `-1` + `WARNING` |
| Catalog & Price History | `47` SKUs / `24` changes (`14` SKUs) | `13` `COST` + `11` `MSRP` changes -> `71` SCD2 version rows |
| Incremental Slice Savings | `3,017` (30d) / `216` (1d) orders | **`85.92%`** (30-day) and **`98.99%`** (1-day) workload reduction |
