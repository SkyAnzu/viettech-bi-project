# ADR-004: Automated Data Quality Quarantine Gate & Vietnam PDPL PII Segregation

| Metadata | Value |
| :--- | :--- |
| **Status** | Accepted |
| **Date** | 2025-03-01 |
| **Supersedes** | Unchecked staging-to-mart loads and raw PII propagation (`full_name`) into `dim_customer` |
| **Affected Components** | `sql/02_create_star_schema.sql`, `sql/03_load_star_schema.sql`, `sql/05_bi_views.sql` (`vw_return_metrics`, `vw_dq_executive_summary`), DQ gate runner |
| **Regulatory Alignment** | Vietnam Personal Data Protection Law — *Luật Bảo vệ dữ liệu cá nhân số `91/2025/QH15`* (effective January 1, 2026) |
| **Related ADRs** | [ADR-001](ADR-001-deterministic-surrogate-keys.md), [ADR-002](ADR-002-scd-type2-product-pricing.md), [ADR-003](ADR-003-incremental-watermark-and-partitioning.md) |

---

## 1. Context

A forensic audit of the VietTech Retail synthetic dataset (`21,435` orders, `39,548` order line items, `1,288` returns, `5,000` customers) and the legacy SQL pipeline (`sql/03_load_star_schema.sql`) exposed two high-severity enterprise governance defects:

### 1.1 Uncaught Real Data Anomalies in the Source Dataset

1. **Four Return Temporal Anomalies (`returned_at < orders.order_date`):**
   Four real rows in `data/raw/order_returns.csv` have return timestamps earlier than their corresponding `orders.order_date` (caused by year-end `23:59:59` timestamp clamping — a classic real-world ERP timezone/cutoff defect):

   | `ret_id` | `order_id` | `order_code` | `orders.order_date` | `order_returns.returned_at` | Raw Lead Time | Return Reason |
   | :---: | :---: | :--- | :--- | :--- | :---: | :--- |
   | `800` | `13110` | `ORD-2024-013110` | `2024-12-29 10:10:45` | `2024-12-26 23:59:59` | **-3 days** | Thay đổi quyết định mua hàng |
   | `1100` | `17850` | `ORD-2024-017850` | `2024-12-27 11:59:12` | `2024-12-26 23:59:59` | **-1 day** | Không đúng mô tả |
   | `1187` | `19369` | `ORD-2024-019369` | `2024-12-29 11:50:43` | `2024-12-28 23:59:59` | **-1 day** | Sản phẩm không đúng màu sắc |
   | `1211` | `19788` | `ORD-2024-019788` | `2024-12-27 18:36:05` | `2024-12-26 23:59:59` | **-1 day** | Không đúng mô tả |

   Without a quality gate, these four records inject negative lead days into operational return-SLA averages. Conversely, silently deleting them would understate financial return totals (`1,288` returned orders totaling `29,446,815,953 VND`) and break reconciliation against the ERP ledger.

2. **Conflation of 467 Completed Orders with `NULL ship_date`:**
   Legacy `sql/03_load_star_schema.sql` mapped every Completed order with `ship_date IS NULL` to `ship_date_key = -1` (Unknown). Channel-level root-cause analysis revealed two completely different business phenomena inside those `467` orders:
   - **`134` Physical store orders** (`channel_type = 'Physical'`): Customers purchased and picked up goods directly in-store. Shipping is **Not Applicable (`-3`)**, not a data bug.
   - **`333` Online orders** (`channel_type IN ('Web', 'App')`): Completed e-commerce deliveries genuinely missing fulfillment timestamps (`ship_date_key = -1`), representing an actionable fulfillment-logging defect requiring a `WARNING` alert.

### 1.2 PII Exposure Under Vietnam PDPL (`91/2025/QH15`)

Vietnam's **Personal Data Protection Law (*Luật Bảo vệ dữ liệu cá nhân số `91/2025/QH15`*, effective January 1, 2026)** mandates strict **data minimization**, **purpose limitation**, and technical access segregation for personal data.

In the legacy warehouse:
- `stg_customers` ingested raw `first_name`, `last_name`, `email`, `phone`, and `date_of_birth`.
- `dim_customer` concatenated `first_name || ' ' || last_name` into `full_name` and published it directly into `retailbi_mart`, which is broadly queried by Power BI analysts and exported to dashboards — despite **zero** BI KPIs (Revenue, Margin, Channel, Return Rate, or RFM Segmentation) requiring a customer's real name, email, phone number, or exact birth date.

---

## 2. Decision Drivers

- **Zero Silent KPI Corruption:** Operational SLAs (such as `avg_return_lead_days`) must never be poisoned by negative durations or misclassified in-store pickups.
- **Financial Reconciliation Preservation:** Anomaly handling must quarantine/flag rather than silently delete financial transactions so `1,288` returns (`29,446,815,953 VND`) and `16,796` completed orders (`380,360,467,955 VND`) remain 100% auditable.
- **Executive Observability:** Surface data health directly in Power BI via `dq_audit_results` and `vw_dq_executive_summary`.
- **Privacy by Design (PDPL `91/2025/QH15`):** Eliminate raw PII from the BI mart (`retailbi_mart`) completely rather than relying on fragile report-level hiding.

---

## 3. Considered Options

| Option | Mechanism | Pros | Cons | Trade-offs & Verdict |
| :--- | :--- | :--- | :--- | :--- |
| **Option 1: Status Quo (No DQ Gate, Raw PII in Mart)** | Load all rows as-is; keep `full_name` in `dim_customer` | Zero pipeline overhead | Negative return lead times; `134` in-store pickups misclassified as missing data; violates PDPL `91/2025/QH15` minimization | **Rejected** — unacceptable governance and compliance risk |
| **Option 2: Hard-Drop Anomalous Rows** | `WHERE returned_at >= order_date` and drop missing-ship orders | Keeps downstream metrics clean | Breaks ERP financial reconciliation (drops `4` returned orders and `333` completed online orders from revenue/returns) | **Rejected** — violates financial auditability |
| **Option 3: Report-Layer Masking Only** | Hide `full_name` in Power BI semantic model; filter anomalies in DAX | Fast to patch visually | Raw PII still lives in `retailbi_mart` for any SQL user; every new report must remember to re-implement anomaly filters | **Rejected** — fails defense-in-depth and single-source-of-truth governance |
| **Option 4: Tiered DQ Quarantine/Flag Gate + Physical PII Segregation** | `dq_audit_results` (`CRITICAL`/`WARNING`/`INFO`) + `is_temporal_anomaly` / `is_missing_online_ship_date` flags + pseudonymized `dim_customer` + restricted PII vault | Preserves 100% financial reconciliation; cleans operational SLAs centrally; enforces PDPL `91/2025/QH15` at the schema boundary; exposes DQ health in BI | Adds audit table maintenance and requires authorized CRM joins against PII vault when outreach lists are needed | **Selected** — enterprise-grade data governance and regulatory compliance |

---

## 4. Decision Outcome

We introduce an **Automated Tiered Data Quality Gate** alongside **Physical PDPL PII Segregation** at the staging-to-mart boundary.

### 4.1 Tiered Data Quality Gate & Quarantine Pattern

Every pipeline run writes structured check outcomes to **`dq_audit_results`** (`run_id`, `checked_at`, `check_name`, `table_name`, `severity`, `status`, `failed_row_count`, `details`) and publishes an executive roll-up via **`vw_dq_executive_summary`**:

| Severity | Enforcement Policy | Mandatory Checks | Verified Synthetic Dataset Outcome |
| :--- | :--- | :--- | :--- |
| **`CRITICAL`** | **Fail pipeline / block watermark advance** | PK uniqueness on all dims/facts; zero orphan FKs; accounting identities (`net_amount = total_amount - total_discount`, `line_profit = line_total - line_cost`); SCD2 non-overlapping intervals; **zero PII columns in `dim_customer`** | `PASS` (`0` failures) |
| **`WARNING`** | **Flag & quarantine, preserve financial ledger, log audit alert** | `return_temporal_anomaly`: `returned_at < order_date` flagged via `fact_returns.is_temporal_anomaly = TRUE` and copied to `dq_quarantine_returns`; `online_completed_missing_ship_date`: Web/App Completed orders with `NULL ship_date` mapped to `ship_date_key = -1` and flagged `is_missing_online_ship_date = TRUE` | `WARNING` logged: **`4`** return temporal anomalies (`ret_id = 800, 1100, 1187, 1211`); **`333`** online missing ship dates |
| **`INFO`** | **Informational audit baseline** | Physical Completed orders with `NULL ship_date` remapped to `ship_date_key = -3` (Not Applicable — in-store pickup); row-count drift; watermark freshness | `INFO` logged: **`134`** Physical in-store pickups cleanly mapped to `-3` |

#### Return SLA Exclusion Rule
- Financial return metrics (`total_returns = 1,288`, `total_return_revenue = 29,446,815,953 VND`) include **all** return records to preserve exact ERP accounting reconciliation.
- Operational return lead-time metrics (`avg_return_lead_days` in `vw_return_metrics`) strictly filter **`WHERE is_temporal_anomaly = FALSE`** (`1,284` valid returns) and set `return_lead_days = NULL` on flagged rows so negative durations never pollute SLA reporting.

### 4.2 Vietnam PDPL (`91/2025/QH15`) PII Segregation Architecture

We establish a hard **Privacy Trust Boundary** between ingestion (`retailbi_stg` / `retailbi_pii_vault`) and analytics (`retailbi_mart`):

| Column Group | Source (`stg_customers`) | `retailbi_pii_vault` (Restricted IAM / DPO Only) | `retailbi_mart.dim_customer` (BI & Power BI) | PDPL `91/2025/QH15` Rationale |
| :--- | :--- | :---: | :---: | :--- |
| **Direct PII** (`first_name`, `last_name`, `full_name`, `email`, `phone`) | Present | Retained (encrypted, IAM-restricted) | **Removed** | Data minimization; zero analytical KPI requires direct contact PII |
| **Quasi-Identifier** (`date_of_birth`) | Present | Retained (encrypted, IAM-restricted) | **Removed** (optional coarse `age_band` only if approved) | Prevents singling-out re-identification in small city/segment cohorts |
| **Pseudonymized Key** (`customer_key` SHA-256, `customer_id`, `customer_code`) | Derived | Link key for DSAR / Erasure | **Retained** | Enables RFM segmentation and cohort tracking without exposing personal identity |
| **Analytical Attributes** (`gender`, `city`, `country`, `segment`, `is_active`) | Present | Not duplicated | **Retained** | Required for geographic, demographic, and B2B/B2C segment KPIs |

This segregation also simplifies **Right to Erasure (*quyền yêu cầu xóa dữ liệu*)** under PDPL `91/2025/QH15`: purging a customer's record in `retailbi_pii_vault` irreversibly breaks re-identification while preserving anonymized historical revenue and order aggregates in `fact_orders`.

---

## 5. Consequences & Verification

### Positive Consequences
- **Clean Operational SLAs Without Financial Distortion:** Leadership sees accurate `avg_return_lead_days`, transparent counts of `333` online fulfillment logging gaps, and properly classified `134` in-store pickups — all while financial revenue (`380.36B VND`) and return value (`29.45B VND`) reconcile 100% with source totals.
- **Compliance-by-Architecture:** Even if a Power BI dataset or `retailbi_mart` view is exported, zero customer names, emails, phone numbers, or birth dates can leak.
- **Audit Trail for Engineering Remediation:** Quarantining `ret_id = 800, 1100, 1187, 1211` and flagging `333` online orders gives upstream ERP/fulfillment engineers exact `order_code` lists to fix root-cause timestamp bugs.

### Negative Consequences & Mitigations
- **Marketing Activation Requires Authorized Join:** CRM campaigns targeting `Champions` or `At-risk` RFM segments cannot pull emails directly from Power BI.
  - *Mitigation:* Export only `customer_key` / `customer_code` segments from `mart_rfm_snapshot` to an authorized CRM activation job holding scoped read access to `retailbi_pii_vault` with full audit logging.

### Verification Criteria
1. **PDPL Schema Audit (`CRITICAL`):** Assert `dim_customer` column list contains **zero** prohibited PII fields (`first_name`, `last_name`, `full_name`, `email`, `phone`, `date_of_birth`).
2. **Return Anomaly Detection:** Assert `SELECT COUNT(*) FROM fact_returns WHERE is_temporal_anomaly = TRUE` equals `4` (`ret_id IN (800, 1100, 1187, 1211)`) and `MIN(return_lead_days) >= 0` across all non-anomalous rows.
3. **Ship Date Classification:** Assert Completed orders with `NULL` source `ship_date` split into exactly `134` rows with `ship_date_key = -3` (Physical) and `333` rows with `ship_date_key = -1` and `is_missing_online_ship_date = TRUE` (Web/App).
4. **Executive DQ View Check:** Confirm `vw_dq_executive_summary` reports `0` failed `CRITICAL` checks and surfaces both `WARNING` findings transparently.
