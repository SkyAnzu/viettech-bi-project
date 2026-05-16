-- Rendered by run_sql_pipeline.py.
CREATE SCHEMA IF NOT EXISTS `your-project-id.retailbi_mart`;

CREATE OR REPLACE TABLE `your-project-id.retailbi_mart.dim_date` AS
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
SELECT -1, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL  -- unknown
UNION ALL
SELECT -2, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL  -- not shipped
UNION ALL
SELECT -3, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL; -- not applicable

CREATE TABLE IF NOT EXISTS `your-project-id.retailbi_mart.dim_customer` (
  customer_key STRING,
  customer_id INT64,
  customer_code STRING,
  full_name STRING,
  gender STRING,
  city STRING,
  country STRING,
  segment STRING,
  is_active BOOL
);

CREATE TABLE IF NOT EXISTS `your-project-id.retailbi_mart.dim_product` (
  product_key STRING,
  product_id INT64,
  product_code STRING,
  product_name STRING,
  category STRING,
  sub_category STRING,
  brand STRING,
  msrp NUMERIC,
  cost_price NUMERIC,
  is_active BOOL
);

CREATE TABLE IF NOT EXISTS `your-project-id.retailbi_mart.dim_channel` (
  channel_key STRING,
  channel_id INT64,
  channel_code STRING,
  channel_name STRING,
  channel_type STRING,
  is_online BOOL,
  is_active BOOL
);

CREATE TABLE IF NOT EXISTS `your-project-id.retailbi_mart.dim_payment` (
  payment_key STRING,
  payment_method STRING,
  payment_category STRING
);

CREATE TABLE IF NOT EXISTS `your-project-id.retailbi_mart.fact_orders` (
  order_key STRING,
  order_id INT64,
  order_code STRING,
  order_date_key INT64,
  ship_date_key INT64,
  customer_key STRING,    -- FK dim_customer
  channel_key STRING,     -- FK dim_channel
  payment_key STRING,     -- FK dim_payment
  status STRING,
  total_amount NUMERIC,
  total_discount NUMERIC,
  net_amount NUMERIC,
  total_cost NUMERIC
);

CREATE TABLE IF NOT EXISTS `your-project-id.retailbi_mart.fact_order_items` (
  order_item_key STRING,
  order_id INT64,
  order_key STRING,       -- FK fact_orders
  product_key STRING,     -- FK dim_product
  order_date_key INT64,
  quantity INT64,
  unit_price NUMERIC,
  unit_cost NUMERIC,
  discount_rate NUMERIC,
  discount_amount NUMERIC,
  line_total NUMERIC,
  line_cost NUMERIC,
  line_profit NUMERIC
);

CREATE TABLE IF NOT EXISTS `your-project-id.retailbi_mart.fact_returns` (
  return_key STRING,
  order_id INT64,
  order_key STRING,       -- FK fact_orders
  returned_date_key INT64,
  reason STRING
);
