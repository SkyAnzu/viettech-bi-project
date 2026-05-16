# VietTech BI Project (University Scope)

Simple end-to-end BI project:

`Raw CSV -> ETL -> BigQuery Staging -> Star Schema -> Power BI`

This repository is intentionally scoped for a university dashboard deliverable:

- The raw dataset is generated once with fixed business constraints and baseline row counts.
- There is no real-time ingestion or ongoing production refresh requirement.
- Prefer clear modeling, stable KPI definitions, and demo-ready outputs over production-style over-engineering.

## Folder structure
- `config/`: environment template
- `data/raw/`: input CSV files
- `docs/`: project report and KPI reference
- `scripts/`: ETL and SQL runner scripts
- `sql/`: star schema and BI query SQL files
- `powerbi/`: dashboard screenshots used in the report

## Quick start
Run all commands from the project root.

1. Create `.env` from `config/.env.example`.
2. Install dependencies:
   `pip install -r scripts/requirements.txt`
3. Load CSV data to BigQuery staging:
   `python scripts/etl_csv_to_bq.py`
4. Build star schema + marts:
   `python scripts/run_sql_pipeline.py`
5. Connect Power BI to dataset from `BQ_DATASET_MART` (default: `retailbi_mart`).

## Prerequisites
- Python 3.10+
- GCP project with BigQuery enabled
- Service account JSON with BigQuery permissions

## Expected outputs
- Staging dataset: `BQ_DATASET_STG`
- Mart dataset: `BQ_DATASET_MART`
- Tables:
  - `dim_date`, `dim_customer`, `dim_product`, `dim_channel`, `dim_payment`
  - `fact_orders`, `fact_order_items`, `fact_returns`
  - `mart_rfm_snapshot`
- Views:
  - `vw_monthly_kpi`, `vw_channel_performance`, `vw_return_metrics`

## Power BI
- Connect to dataset `BQ_DATASET_MART`.
- Use surrogate-key relationships from the mart model, not natural foreign IDs from the raw tables.
- Use `vw_monthly_kpi` for executive KPI cards and monthly trends.
- Use `vw_return_metrics` together with `vw_monthly_kpi` for return-related dashboard measures.
- Treat the RFM page as a fixed snapshot at `2025-01-01`, not a dynamic date-sliced mart.
- Dashboard pages: Business Performance, Product, RFM, Order.
- Recommended slicers: year, month, channel, status.

## SQL execution order
1. `sql/01_create_staging_dataset.sql`
2. `sql/02_create_star_schema.sql`
3. `sql/03_load_star_schema.sql`
4. `sql/04_rfm_snapshot.sql`
5. `sql/05_bi_views.sql`

## Configuration
- `PROJECT_ID`: GCP project ID
- `BQ_LOCATION`: BigQuery location (default: `asia-southeast1`)
- `BQ_DATASET_STG`: staging dataset (default: `retailbi_stg`)
- `BQ_DATASET_MART`: mart dataset (default: `retailbi_mart`)
- `RAW_DATA_DIR`: local raw CSV folder (default: `data/raw`)
- `CSV_DELIMITER`: CSV delimiter (default: `,`)
- `GOOGLE_APPLICATION_CREDENTIALS`: path to service account JSON

## Security notes
- Do not commit `.env` or service account JSON files.
- Keep credentials outside the repo and reference them via `GOOGLE_APPLICATION_CREDENTIALS`.

## Core business rules
- Revenue KPI always filters `status = 'Completed'`.
- Include `total_completed_orders` whenever order counts are compared with completed-only revenue.
- `avg_completed_order_value` is completed-order only.
- `completed_discount` is completed-order only; `total_discount` remains all-status unless a report explicitly defines otherwise.
- `ship_date_key` uses special values:
  - `-1`: unknown or data anomaly
  - `-2`: not yet shipped
  - `-3`: not applicable, such as cancelled orders
- `customers.segment` is business segment, not RFM segment.
- `vw_return_metrics` groups returns by original order month, not by actual returned date.
- RFM uses a fixed snapshot date of `2025-01-01` for the current fixed-dataset scope.

## KPI references
- `docs/KPI_GLOSSARY.md`: dashboard KPI definitions and intended semantics.
