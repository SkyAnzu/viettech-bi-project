-- Rendered by run_sql_pipeline.py.

CREATE OR REPLACE TABLE `your-project-id.retailbi_mart.dim_customer` AS
SELECT
  GENERATE_UUID() AS customer_key,
  CAST(id AS INT64) AS customer_id,
  customer_code,
  CONCAT(first_name, ' ', last_name) AS full_name,
  gender,
  city,
  country,
  segment,
  LOWER(TRIM(CAST(is_active AS STRING))) IN ('1', 'true') AS is_active
FROM `your-project-id.retailbi_stg.stg_customers`;

CREATE OR REPLACE TABLE `your-project-id.retailbi_mart.dim_product` AS
SELECT
  GENERATE_UUID() AS product_key,
  CAST(p.id AS INT64) AS product_id,
  p.product_code,
  p.product_name,
  c.category,
  c.sub_category,
  p.brand,
  CAST(p.msrp AS NUMERIC) AS msrp,
  CAST(p.cost_price AS NUMERIC) AS cost_price,
  LOWER(TRIM(CAST(p.is_active AS STRING))) IN ('1', 'true') AS is_active
FROM `your-project-id.retailbi_stg.stg_products` p
JOIN `your-project-id.retailbi_stg.stg_product_categories` c
  ON CAST(p.category_id AS INT64) = CAST(c.id AS INT64);

CREATE OR REPLACE TABLE `your-project-id.retailbi_mart.dim_channel` AS
SELECT
  GENERATE_UUID() AS channel_key,
  CAST(id AS INT64) AS channel_id,
  channel_code,
  channel_name,
  channel_type,
  channel_type IN ('Web', 'App') AS is_online,
  LOWER(TRIM(CAST(is_active AS STRING))) IN ('1', 'true') AS is_active
FROM `your-project-id.retailbi_stg.stg_sales_channels`;

-- Deduplicate payment methods.
CREATE OR REPLACE TABLE `your-project-id.retailbi_mart.dim_payment` AS
WITH distinct_payments AS (
  SELECT DISTINCT TRIM(payment_method) AS payment_method
  FROM `your-project-id.retailbi_stg.stg_orders`
)
SELECT
  GENERATE_UUID() AS payment_key,
  payment_method,
  CASE
    WHEN payment_method = 'Cash' THEN 'Cash'
    WHEN payment_method = 'Credit Card' THEN 'Card'
    ELSE 'Digital'
  END AS payment_category
FROM distinct_payments;

-- Build order facts.
CREATE OR REPLACE TABLE `your-project-id.retailbi_mart.fact_orders` AS
SELECT
  GENERATE_UUID() AS order_key,
  CAST(o.id AS INT64) AS order_id,
  o.order_code,
  CAST(FORMAT_TIMESTAMP('%Y%m%d', TIMESTAMP(o.order_date)) AS INT64) AS order_date_key,
  CASE
    WHEN o.ship_date IS NOT NULL
      THEN CAST(FORMAT_DATE('%Y%m%d', DATE(o.ship_date)) AS INT64)
    WHEN o.ship_date IS NULL AND o.status IN ('Pending', 'Processing')
      THEN -2  -- not shipped
    WHEN o.ship_date IS NULL AND o.status = 'Cancelled'
      THEN -3  -- not applicable
    ELSE -1    -- unknown
  END AS ship_date_key,
  dc.customer_key,
  dch.channel_key,
  dp.payment_key,
  o.status,
  CAST(o.total_amount AS NUMERIC) AS total_amount,
  CAST(o.total_discount AS NUMERIC) AS total_discount,
  CAST(o.net_amount AS NUMERIC) AS net_amount,
  CAST(o.total_cost AS NUMERIC) AS total_cost
FROM `your-project-id.retailbi_stg.stg_orders` o
LEFT JOIN `your-project-id.retailbi_mart.dim_customer` dc
  ON CAST(o.customer_id AS INT64) = dc.customer_id
LEFT JOIN `your-project-id.retailbi_mart.dim_channel` dch
  ON CAST(o.channel_id AS INT64) = dch.channel_id
LEFT JOIN `your-project-id.retailbi_mart.dim_payment` dp
  ON TRIM(o.payment_method) = dp.payment_method;

-- Use order header date.
CREATE OR REPLACE TABLE `your-project-id.retailbi_mart.fact_order_items` AS
SELECT
  GENERATE_UUID() AS order_item_key,
  CAST(od.order_id AS INT64) AS order_id,
  fo.order_key,
  dp.product_key,
  CAST(FORMAT_TIMESTAMP('%Y%m%d', TIMESTAMP(o.order_date)) AS INT64) AS order_date_key,
  CAST(od.quantity AS INT64) AS quantity,
  CAST(od.unit_price AS NUMERIC) AS unit_price,
  CAST(od.unit_cost AS NUMERIC) AS unit_cost,
  CAST(od.discount_rate AS NUMERIC) AS discount_rate,
  CAST(od.discount_amount AS NUMERIC) AS discount_amount,
  CAST(od.line_total AS NUMERIC) AS line_total,
  CAST(od.line_cost AS NUMERIC) AS line_cost,
  CAST(od.line_profit AS NUMERIC) AS line_profit
FROM `your-project-id.retailbi_stg.stg_order_details` od
JOIN `your-project-id.retailbi_stg.stg_orders` o
  ON CAST(od.order_id AS INT64) = CAST(o.id AS INT64)
LEFT JOIN `your-project-id.retailbi_mart.dim_product` dp
  ON CAST(od.product_id AS INT64) = dp.product_id
LEFT JOIN `your-project-id.retailbi_mart.fact_orders` fo
  ON CAST(od.order_id AS INT64) = fo.order_id;

-- Keep order_key for BI joins.
CREATE OR REPLACE TABLE `your-project-id.retailbi_mart.fact_returns` AS
SELECT
  GENERATE_UUID() AS return_key,
  CAST(r.order_id AS INT64) AS order_id,
  fo.order_key,
  CAST(FORMAT_TIMESTAMP('%Y%m%d', TIMESTAMP(r.returned_at)) AS INT64) AS returned_date_key,
  r.reason
FROM `your-project-id.retailbi_stg.stg_order_returns` r
LEFT JOIN `your-project-id.retailbi_mart.fact_orders` fo
  ON CAST(r.order_id AS INT64) = fo.order_id;
