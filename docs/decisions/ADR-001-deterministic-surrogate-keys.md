# ADR-001: Deterministic SHA-256 Surrogate Keys

| Metadata | Value |
| :--- | :--- |
| **Status** | Accepted |
| **Date** | 2025-03-01 |
| **Supersedes** | Legacy `GENERATE_UUID()` + `CREATE OR REPLACE TABLE` key generation |
| **Affected Components** | `sql/02_create_star_schema.sql`, `sql/03_load_star_schema.sql`, `sql/04_rfm_snapshot.sql`, local SQLite runner, Power BI relationships |
| **Related ADRs** | [ADR-002](ADR-002-scd-type2-product-pricing.md), [ADR-003](ADR-003-incremental-watermark-and-partitioning.md) |

---

## 1. Context

In the initial iteration of the VietTech Retail BI warehouse (`sql/03_load_star_schema.sql`), every dimension (`dim_customer`, `dim_product`, `dim_channel`, `dim_payment`) and fact table (`fact_orders`, `fact_order_items`, `fact_returns`) generated surrogate keys at runtime using BigQuery's non-deterministic `GENERATE_UUID()` inside full-refresh `CREATE OR REPLACE TABLE` statements.

While `GENERATE_UUID()` is convenient for a one-off static build, it breaks fundamental enterprise data warehouse guarantees as soon as the pipeline moves to incremental loading, multi-snapshot history, or dual-engine verification:

1. **Zero Idempotency Across Reruns:** Re-running `03_load_star_schema.sql` against identical input data (`21,435` orders, `39,548` order line items, `1,288` returns, `5,000` customers) regenerates 100% of surrogate keys.
2. **Broken Incremental `MERGE` Semantics:** Incremental upserts cannot match incoming staged records to existing mart rows by surrogate key if every run mints random UUIDs.
3. **Orphaned Historical Snapshots:** Multi-snapshot analytical marts such as `mart_rfm_snapshot` (grain: `(snapshot_date, customer_key)`) lose referential integrity with `dim_customer` whenever `dim_customer` is refreshed and assigns new UUIDs to existing `customer_id` values.
4. **Power BI Cache & Incremental Refresh Churn:** Replacing every key invalidates Power BI relationship dictionaries, forces full model reloads, and breaks saved bookmarks or drill-through parameters tied to surrogate keys.
5. **Cross-Engine Parity Impossibility:** The Dual-Engine architecture (BigQuery production + local SQLite CI/portfolio runner) requires bit-identical keys across both engines so automated tests can reconcile row hashes and foreign keys deterministically.

---

## 2. Decision Drivers

- **Strict Idempotency:** Running the pipeline `N` times on the same source snapshot must produce identical surrogate keys across all dimensions and facts.
- **Incremental `MERGE` Compatibility:** Fact tables must be able to derive dimension foreign keys independently or match existing keys without centralized locking.
- **SCD Type 2 Support:** Surrogate keys must uniquely identify *versions* of a business entity (e.g., `product_id + valid_from` in `dim_product_scd2`), not merely the natural key.
- **Cross-Engine Determinism:** BigQuery Standard SQL (`TO_HEX(SHA256(...))`) and Python/SQLite (`hashlib.sha256(...).hexdigest()`) must emit identical 64-character lowercase hexadecimal strings.
- **Zero Sequence Bottleneck:** Avoid single-writer auto-increment sequences that serialize parallel ETL loads in cloud analytical warehouses like BigQuery.

---

## 3. Considered Options

| Option | Mechanism | Pros | Cons | Trade-offs & Verdict |
| :--- | :--- | :--- | :--- | :--- |
| **Option 1: Keep `GENERATE_UUID()`** | Random UUIDv4 generated per `CREATE OR REPLACE TABLE` | Zero design effort; no key-collision math required | Non-idempotent; breaks incremental `MERGE`, multi-snapshot RFM joins, and cross-engine CI parity | **Rejected** — incompatible with incremental warehousing and reproducible CI |
| **Option 2: Sequential Integer (`ROW_NUMBER()` / `IDENTITY`)** | Monotonic `INT64` assigned via sequence or `MAX(key) + ROW_NUMBER()` | Compact 8-byte storage; fastest possible integer joins | BigQuery lacks native transactional auto-increment sequences; `ROW_NUMBER()` shifts on new inserts/deletes; forces sequential lookup joins before loading facts | **Rejected** — operational complexity and fragile key stability across rebuilds |
| **Option 3: Natural Business Keys Only** | Use `customer_id`, `product_id`, `order_id` directly as PK/FK | Simple joins; zero hashing overhead | Breaks SCD Type 2 (multiple versions share one `product_id`); couples BI layer directly to OLTP integer schemas; breaks if multi-source ERP codes collide | **Rejected** — cannot support `dim_product_scd2` or multi-source extensibility |
| **Option 4: Deterministic `SHA-256` Hash Keys** | `TO_HEX(SHA256(CONCAT(prefix, natural_keys)))` | 100% idempotent; stateless parallel computation; natively supports SCD2 composite keys; bit-identical across BigQuery and Python/SQLite | Larger column width (64-byte UTF-8 hex string vs. 8-byte `INT64`); slightly higher raw byte scan before dictionary encoding | **Selected** — best balance of enterprise idempotency, SCD2 support, and dual-engine parity |

---

## 4. Decision Outcome

We adopt **Option 4: Deterministic `SHA-256` Hash Surrogate Keys** (`TO_HEX(SHA256(...))` in BigQuery; `hashlib.sha256(...).hexdigest()` in the local SQLite runner) for all non-calendar dimensions and fact tables.

### 4.1 Canonical Key Formulation Standard

Every hash input follows a strict canonical contract to prevent hash collisions across entities or ambiguous concatenation (e.g., `"12" || "3"` vs. `"1" || "23"`):

1. **Entity Namespace Prefix:** Fixed uppercase domain token (`CUST`, `PROD`, `CHAN`, `PAY`, `ORD`, `ITEM`, `RET`).
2. **Explicit Delimiter:** Pipe separator (`|`) between all components.
3. **Deterministic Null & Whitespace Normalization:** `COALESCE(UPPER(TRIM(CAST(col AS STRING))), '')` for string keys; exact `INT64` string cast for integer IDs; ISO-8601 UTC string (`%Y-%m-%dT%H:%M:%S`) for timestamps.

| Table | Surrogate Key Column | Canonical Hash Formula (BigQuery Standard SQL) |
| :--- | :--- | :--- |
| `dim_customer` | `customer_key` | `TO_HEX(SHA256(CONCAT('CUST\|', CAST(customer_id AS STRING))))` |
| `dim_product_scd2` | `product_version_key` | `TO_HEX(SHA256(CONCAT('PROD\|', CAST(product_id AS STRING), '\|', FORMAT_TIMESTAMP('%Y-%m-%dT%H:%M:%S', valid_from))))` |
| `dim_product_scd2` | `product_key` *(entity key)* | `TO_HEX(SHA256(CONCAT('PROD_ID\|', CAST(product_id AS STRING))))` |
| `dim_channel` | `channel_key` | `TO_HEX(SHA256(CONCAT('CHAN\|', CAST(channel_id AS STRING))))` |
| `dim_payment` | `payment_key` | `TO_HEX(SHA256(CONCAT('PAY\|', UPPER(TRIM(payment_method)))))` |
| `fact_orders` | `order_key` | `TO_HEX(SHA256(CONCAT('ORD\|', CAST(order_id AS STRING))))` |
| `fact_order_items` | `order_item_key` | `TO_HEX(SHA256(CONCAT('ITEM\|', CAST(order_id AS STRING), '\|', CAST(product_id AS STRING))))` |
| `fact_returns` | `return_key` | `TO_HEX(SHA256(CONCAT('RET\|', CAST(return_id AS STRING))))` |

### 4.2 Calendar Dimension Exception (`dim_date`)

`dim_date.date_key` intentionally remains a **smart integer (`INT64`)** formatted as `YYYYMMDD` (e.g., `20241231`), accompanied by three negative sentinel keys:
- `-1`: **Unknown / Missing Online Fulfillment Date** (e.g., the `333` Web/App Completed orders missing `ship_date` — see [ADR-004](ADR-004-data-quality-quarantine-and-pdpl-pii.md)).
- `-2`: **Not Yet Shipped** (`Pending` / `Processing` orders).
- `-3`: **Not Applicable** (`Cancelled` orders and `134` Physical store Completed orders fulfilled via immediate in-store pickup).

Keeping `date_key` as `INT64` enables human-readable date range filters, zero-join year/month inspection, and lightweight integer sorting in Power BI.

---

## 5. Consequences & Verification

### Positive Consequences
- **100% Idempotent Reruns & Backfills:** Rebuilding any dimension or replaying any historical watermark slice reproduces exact surrogate keys without invalidating downstream foreign keys or `mart_rfm_snapshot` history.
- **Stateless Parallel Loading:** `fact_orders` and `fact_returns` can compute `customer_key`, `channel_key`, `payment_key`, and `order_key` directly from natural keys during `MERGE` without blocking on random UUID lookups.
- **Cross-Engine Reproducibility:** Local SQLite builds and BigQuery production builds share identical key values, allowing reviewers and CI suites to verify referential integrity without GCP credentials.

### Negative Consequences & Mitigations
- **Storage & Memory Footprint:** A 64-character hex `STRING` consumes more uncompressed bytes than an 8-byte `INT64` (or 32-byte raw `BYTES`).
  - *Mitigation:* BigQuery's Capacitor columnar format applies dictionary encoding to low-cardinality dimension keys (`dim_channel` = `8` rows, `dim_payment` = `4` rows, `dim_product_scd2` = `71` rows, `dim_customer` = `5,000` rows), minimizing real storage and scan overhead. On the synthetic VietTech dataset (`39,548` max fact rows), the extra footprint is well under 5 MB. If future production volume exceeds 100M+ rows, migrating from `TO_HEX(SHA256(...))` (`STRING`) to raw `SHA256(...)` (`BYTES`) or `FARM_FINGERPRINT` (`INT64`) is an isolated DDL optimization.
- **Theoretical Hash Collision Risk:** `SHA-256` provides `2^256` output space; collision probability at retail scale (`< 10^10` rows) is mathematically negligible (`< 10^-57`).

### Verification Criteria
1. **Idempotency Test:** Run the full pipeline twice sequentially on the synthetic VietTech dataset (`21,435` orders, `39,548` order line items, `1,288` returns, `5,000` customers) and assert `0` key diffs across every table.
2. **Referential Integrity Test:** Assert `0` orphan foreign keys between `fact_orders`, `fact_order_items`, `fact_returns`, `mart_rfm_snapshot`, and all five dimensions.
3. **Uniqueness Test:** Assert `COUNT(*) = COUNT(DISTINCT <surrogate_key>)` on every dimension and fact table.
4. **Cross-Engine Parity Test:** Verify `TO_HEX(SHA256('CUST|1'))` in BigQuery equals Python `hashlib.sha256(b'CUST|1').hexdigest()` (`64` lowercase hex characters).
