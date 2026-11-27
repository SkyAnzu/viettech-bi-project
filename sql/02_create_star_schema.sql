-- 02_create_star_schema.sql
-- Explicit Star Schema DDL with BigQuery partitioning, clustering,
-- PDPL 91/2025/QH15 privacy-by-design dim_customer, and SCD Type 2 dim_product_scd2.
-- Rendered by scripts/run_sql_pipeline.py.

CREATE SCHEMA IF NOT EXISTS `{{PROJECT_ID}}.{{BQ_DATASET_MART}}`;

-- Calendar dimension (2020-01-01..2027-12-31) plus sentinel keys:
--   -1 = Unknown, -2 = Not Shipped, -3 = Not Applicable (e.g. Cancelled / In-Store Pickup)
CREATE TABLE IF NOT EXISTS `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_date` (
  date_key INT64 NOT NULL,
  date DATE,
  year INT64,
  quarter INT64,
  month INT64,
  week_of_year INT64,
  day_of_week INT64,
  is_weekend BOOL,
  is_tet BOOL,
  is_year_end BOOL
);

MERGE `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_date` AS target
USING (
  SELECT
    CAST(FORMAT_DATE('%Y%m%d', d) AS INT64) AS date_key,
    d AS date,
    EXTRACT(YEAR FROM d) AS year,
    EXTRACT(QUARTER FROM d) AS quarter,
    EXTRACT(MONTH FROM d) AS month,
    EXTRACT(WEEK FROM d) AS week_of_year,
    EXTRACT(DAYOFWEEK FROM d) AS day_of_week,
    EXTRACT(DAYOFWEEK FROM d) IN (1, 7) AS is_weekend,
    EXTRACT(MONTH FROM d) IN (1, 2) AS is_tet,
    EXTRACT(MONTH FROM d) IN (11, 12) AS is_year_end
  FROM UNNEST(GENERATE_DATE_ARRAY(DATE '2020-01-01', DATE '2027-12-31')) AS d
  UNION ALL
  SELECT -1, CAST(NULL AS DATE), CAST(NULL AS INT64), CAST(NULL AS INT64), CAST(NULL AS INT64), CAST(NULL AS INT64), CAST(NULL AS INT64), CAST(NULL AS BOOL), CAST(NULL AS BOOL), CAST(NULL AS BOOL)
  UNION ALL
  SELECT -2, CAST(NULL AS DATE), CAST(NULL AS INT64), CAST(NULL AS INT64), CAST(NULL AS INT64), CAST(NULL AS INT64), CAST(NULL AS INT64), CAST(NULL AS BOOL), CAST(NULL AS BOOL), CAST(NULL AS BOOL)
  UNION ALL
  SELECT -3, CAST(NULL AS DATE), CAST(NULL AS INT64), CAST(NULL AS INT64), CAST(NULL AS INT64), CAST(NULL AS INT64), CAST(NULL AS INT64), CAST(NULL AS BOOL), CAST(NULL AS BOOL), CAST(NULL AS BOOL)
) AS source
ON target.date_key = source.date_key
WHEN MATCHED THEN
  UPDATE SET
    date = source.date,
    year = source.year,
    quarter = source.quarter,
    month = source.month,
    week_of_year = source.week_of_year,
    day_of_week = source.day_of_week,
    is_weekend = source.is_weekend,
    is_tet = source.is_tet,
    is_year_end = source.is_year_end
WHEN NOT MATCHED THEN
  INSERT (
    date_key, date, year, quarter, month, week_of_year,
    day_of_week, is_weekend, is_tet, is_year_end
  )
  VALUES (
    source.date_key, source.date, source.year, source.quarter, source.month,
    source.week_of_year, source.day_of_week, source.is_weekend, source.is_tet,
    source.is_year_end
  );

-- Customer dimension (PDPL 91/2025/QH15 Privacy-by-Design: zero raw email/phone/DOB in mart)
CREATE TABLE IF NOT EXISTS `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_customer` (
  customer_key STRING NOT NULL,
  customer_id INT64 NOT NULL,
  customer_code STRING,
  customer_name_masked STRING,
  gender STRING,
  city STRING,
  country STRING,
  segment STRING,
  registered_date DATE,
  is_active BOOL
);

-- Product dimension (SCD Type 1 — current catalog state)
CREATE TABLE IF NOT EXISTS `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_product` (
  product_key STRING NOT NULL,
  product_id INT64 NOT NULL,
  product_code STRING,
  product_name STRING,
  category STRING,
  sub_category STRING,
  brand STRING,
  msrp NUMERIC,
  cost_price NUMERIC,
  is_active BOOL
);

-- Product dimension (SCD Type 2 — full historical MSRP & cost_price timeline)
CREATE TABLE IF NOT EXISTS `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_product_scd2` (
  product_version_key STRING NOT NULL,
  product_key STRING NOT NULL,
  product_id INT64 NOT NULL,
  product_code STRING,
  product_name STRING,
  category STRING,
  sub_category STRING,
  brand STRING,
  msrp NUMERIC,
  cost_price NUMERIC,
  valid_from TIMESTAMP NOT NULL,
  valid_to TIMESTAMP NOT NULL,
  is_current BOOL NOT NULL
);

CREATE TABLE IF NOT EXISTS `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_channel` (
  channel_key STRING NOT NULL,
  channel_id INT64 NOT NULL,
  channel_code STRING,
  channel_name STRING,
  channel_type STRING,
  is_online BOOL,
  is_active BOOL
);

CREATE TABLE IF NOT EXISTS `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_payment` (
  payment_key STRING NOT NULL,
  payment_method STRING NOT NULL,
  payment_category STRING
);

-- Partitioned & Clustered Fact Tables
CREATE TABLE IF NOT EXISTS `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_orders` (
  order_key STRING NOT NULL,
  order_id INT64 NOT NULL,
  order_code STRING,
  order_date DATE NOT NULL,
  updated_at TIMESTAMP,
  order_date_key INT64 NOT NULL,
  ship_date_key INT64 NOT NULL,
  is_missing_online_ship_date BOOL NOT NULL,
  customer_key STRING,
  channel_key STRING,
  payment_key STRING,
  status STRING,
  total_amount NUMERIC,
  total_discount NUMERIC,
  net_amount NUMERIC,
  total_cost NUMERIC
)
PARTITION BY order_date
CLUSTER BY status, channel_key, customer_key;

CREATE TABLE IF NOT EXISTS `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_order_items` (
  order_item_key STRING NOT NULL,
  order_item_id INT64 NOT NULL,
  order_id INT64 NOT NULL,
  order_key STRING,
  product_key STRING,
  product_version_key STRING,
  order_date DATE NOT NULL,
  order_date_key INT64 NOT NULL,
  quantity INT64,
  unit_price NUMERIC,
  unit_cost NUMERIC,
  discount_rate NUMERIC,
  discount_amount NUMERIC,
  line_total NUMERIC,
  line_cost NUMERIC,
  line_profit NUMERIC
)
PARTITION BY order_date
CLUSTER BY product_key, order_key;

CREATE TABLE IF NOT EXISTS `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_returns` (
  return_key STRING NOT NULL,
  return_id INT64 NOT NULL,
  order_id INT64 NOT NULL,
  order_key STRING,
  returned_date DATE NOT NULL,
  returned_at TIMESTAMP NOT NULL,
  returned_date_key INT64 NOT NULL,
  return_lead_days INT64,
  is_temporal_anomaly BOOL NOT NULL,
  reason STRING
)
PARTITION BY returned_date
CLUSTER BY is_temporal_anomaly, reason;

CREATE TABLE IF NOT EXISTS `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.mart_rfm_snapshot` (
  snapshot_date DATE NOT NULL,
  customer_key STRING NOT NULL,
  customer_id INT64,
  recency_days INT64,
  frequency_orders INT64,
  monetary_value NUMERIC,
  r_score INT64,
  f_score INT64,
  m_score INT64,
  rfm_score STRING,
  rfm_segment STRING
)
PARTITION BY snapshot_date
CLUSTER BY rfm_segment, customer_key;

CREATE TABLE IF NOT EXISTS `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dq_audit_results` (
  run_ts TIMESTAMP NOT NULL,
  check_name STRING NOT NULL,
  severity STRING NOT NULL,
  status STRING NOT NULL,
  failed_rows INT64 NOT NULL,
  details STRING
);
