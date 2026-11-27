-- 03_load_star_schema.sql
-- Idempotent MERGE-based load for all dimensions and facts.
-- Preserves BigQuery partitioning & clustering DDL (zero CREATE OR REPLACE TABLE).
-- Uses deterministic SHA-256 surrogate keys and incremental watermark filtering.
-- Rendered by scripts/run_sql_pipeline.py.

-- ---------------------------------------------------------------------------
-- 1. dim_customer (PDPL 91/2025/QH15: masked name, zero raw email/phone/DOB)
-- ---------------------------------------------------------------------------
MERGE `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_customer` AS target
USING (
  SELECT
    TO_HEX(SHA256(CONCAT('customer|', CAST(id AS STRING)))) AS customer_key,
    CAST(id AS INT64) AS customer_id,
    customer_code,
    CONCAT(
      SUBSTR(TRIM(COALESCE(first_name, '')), 1, 1),
      '*** ',
      TRIM(COALESCE(last_name, ''))
    ) AS customer_name_masked,
    gender,
    city,
    country,
    segment,
    SAFE_CAST(NULLIF(TRIM(CAST(registered_date AS STRING)), '') AS DATE) AS registered_date,
    LOWER(TRIM(CAST(is_active AS STRING))) IN ('1', 'true') AS is_active
  FROM `{{PROJECT_ID}}.{{BQ_DATASET_STG}}.stg_customers`
) AS source
ON target.customer_key = source.customer_key
WHEN MATCHED THEN
  UPDATE SET
    customer_id = source.customer_id,
    customer_code = source.customer_code,
    customer_name_masked = source.customer_name_masked,
    gender = source.gender,
    city = source.city,
    country = source.country,
    segment = source.segment,
    registered_date = source.registered_date,
    is_active = source.is_active
WHEN NOT MATCHED THEN
  INSERT (
    customer_key, customer_id, customer_code, customer_name_masked,
    gender, city, country, segment, registered_date, is_active
  )
  VALUES (
    source.customer_key, source.customer_id, source.customer_code, source.customer_name_masked,
    source.gender, source.city, source.country, source.segment,
    source.registered_date, source.is_active
  );

-- ---------------------------------------------------------------------------
-- 2. dim_product (Type 1 current state; LEFT JOIN prevents silent category drops)
-- ---------------------------------------------------------------------------
MERGE `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_product` AS target
USING (
  SELECT
    TO_HEX(SHA256(CONCAT('product|', CAST(p.id AS STRING)))) AS product_key,
    CAST(p.id AS INT64) AS product_id,
    p.product_code,
    p.product_name,
    COALESCE(c.category, 'Unknown') AS category,
    COALESCE(c.sub_category, 'Unknown') AS sub_category,
    p.brand,
    CAST(p.msrp AS NUMERIC) AS msrp,
    CAST(p.cost_price AS NUMERIC) AS cost_price,
    LOWER(TRIM(CAST(p.is_active AS STRING))) IN ('1', 'true') AS is_active
  FROM `{{PROJECT_ID}}.{{BQ_DATASET_STG}}.stg_products` AS p
  LEFT JOIN `{{PROJECT_ID}}.{{BQ_DATASET_STG}}.stg_product_categories` AS c
    ON CAST(p.category_id AS INT64) = CAST(c.id AS INT64)
) AS source
ON target.product_key = source.product_key
WHEN MATCHED THEN
  UPDATE SET
    product_id = source.product_id,
    product_code = source.product_code,
    product_name = source.product_name,
    category = source.category,
    sub_category = source.sub_category,
    brand = source.brand,
    msrp = source.msrp,
    cost_price = source.cost_price,
    is_active = source.is_active
WHEN NOT MATCHED THEN
  INSERT (
    product_key, product_id, product_code, product_name,
    category, sub_category, brand, msrp, cost_price, is_active
  )
  VALUES (
    source.product_key, source.product_id, source.product_code, source.product_name,
    source.category, source.sub_category, source.brand, source.msrp,
    source.cost_price, source.is_active
  );

-- ---------------------------------------------------------------------------
-- 3. dim_product_scd2 (SCD Type 2 reconstructed from stg_products + price history)
--    Builds continuous non-overlapping [valid_from, valid_to) intervals per SKU
--    starting at 2022-12-01 00:00:00 through 9999-12-31 23:59:59.
-- ---------------------------------------------------------------------------
MERGE `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_product_scd2` AS target
USING (
  WITH base_products AS (
    SELECT
      CAST(p.id AS INT64) AS product_id,
      TO_HEX(SHA256(CONCAT('product|', CAST(p.id AS STRING)))) AS product_key,
      p.product_code,
      p.product_name,
      COALESCE(c.category, 'Unknown') AS category,
      COALESCE(c.sub_category, 'Unknown') AS sub_category,
      p.brand,
      CAST(p.msrp AS NUMERIC) AS current_msrp,
      CAST(p.cost_price AS NUMERIC) AS current_cost_price
    FROM `{{PROJECT_ID}}.{{BQ_DATASET_STG}}.stg_products` AS p
    LEFT JOIN `{{PROJECT_ID}}.{{BQ_DATASET_STG}}.stg_product_categories` AS c
      ON CAST(p.category_id AS INT64) = CAST(c.id AS INT64)
  ),
  history_clean AS (
    SELECT
      CAST(id AS INT64) AS history_id,
      CAST(product_id AS INT64) AS product_id,
      UPPER(TRIM(price_type)) AS price_type,
      CAST(old_price AS NUMERIC) AS old_price,
      CAST(new_price AS NUMERIC) AS new_price,
      TIMESTAMP(changed_at) AS changed_at
    FROM `{{PROJECT_ID}}.{{BQ_DATASET_STG}}.stg_product_price_history`
  ),
  -- Recover the initial MSRP/COST before any historical price change
  first_msrp AS (
    SELECT
      product_id,
      old_price AS initial_msrp
    FROM (
      SELECT
        product_id,
        old_price,
        ROW_NUMBER() OVER (PARTITION BY product_id ORDER BY changed_at ASC, history_id ASC) AS rn
      FROM history_clean
      WHERE price_type = 'MSRP'
    )
    WHERE rn = 1
  ),
  first_cost AS (
    SELECT
      product_id,
      old_price AS initial_cost
    FROM (
      SELECT
        product_id,
        old_price,
        ROW_NUMBER() OVER (PARTITION BY product_id ORDER BY changed_at ASC, history_id ASC) AS rn
      FROM history_clean
      WHERE price_type = 'COST'
    )
    WHERE rn = 1
  ),
  -- Collapse multiple changes occurring at the exact same timestamp for a product
  change_points AS (
    SELECT
      product_id,
      changed_at AS valid_from,
      ARRAY_AGG(IF(price_type = 'MSRP', new_price, NULL) IGNORE NULLS ORDER BY history_id DESC LIMIT 1)[SAFE_OFFSET(0)] AS msrp_val,
      ARRAY_AGG(IF(price_type = 'COST', new_price, NULL) IGNORE NULLS ORDER BY history_id DESC LIMIT 1)[SAFE_OFFSET(0)] AS cost_val
    FROM history_clean
    GROUP BY product_id, changed_at
  ),
  -- Union baseline version (2022-12-01 00:00:00) with every change timestamp
  timeline_events AS (
    SELECT
      bp.product_id,
      TIMESTAMP('2022-12-01 00:00:00') AS valid_from,
      COALESCE(fm.initial_msrp, bp.current_msrp) AS msrp_val,
      COALESCE(fc.initial_cost, bp.current_cost_price) AS cost_val
    FROM base_products AS bp
    LEFT JOIN first_msrp AS fm
      ON bp.product_id = fm.product_id
    LEFT JOIN first_cost AS fc
      ON bp.product_id = fc.product_id

    UNION ALL

    SELECT
      cp.product_id,
      cp.valid_from,
      cp.msrp_val,
      cp.cost_val
    FROM change_points AS cp
    WHERE cp.valid_from > TIMESTAMP('2022-12-01 00:00:00')
  ),
  -- Carry forward MSRP & COST across intervals and compute non-overlapping valid_to
  reconstructed AS (
    SELECT
      te.product_id,
      te.valid_from,
      COALESCE(
        LEAD(te.valid_from) OVER (PARTITION BY te.product_id ORDER BY te.valid_from ASC),
        TIMESTAMP('9999-12-31 23:59:59')
      ) AS valid_to,
      LEAD(te.valid_from) OVER (PARTITION BY te.product_id ORDER BY te.valid_from ASC) IS NULL AS is_current,
      LAST_VALUE(te.msrp_val IGNORE NULLS) OVER (
        PARTITION BY te.product_id
        ORDER BY te.valid_from ASC
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
      ) AS msrp,
      LAST_VALUE(te.cost_val IGNORE NULLS) OVER (
        PARTITION BY te.product_id
        ORDER BY te.valid_from ASC
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
      ) AS cost_price
    FROM timeline_events AS te
  )
  SELECT
    TO_HEX(SHA256(CONCAT(
      'product_scd2|',
      CAST(r.product_id AS STRING),
      '|',
      FORMAT_TIMESTAMP('%Y-%m-%d %H:%M:%S', r.valid_from)
    ))) AS product_version_key,
    bp.product_key,
    r.product_id,
    bp.product_code,
    bp.product_name,
    bp.category,
    bp.sub_category,
    bp.brand,
    r.msrp,
    r.cost_price,
    r.valid_from,
    r.valid_to,
    r.is_current
  FROM reconstructed AS r
  JOIN base_products AS bp
    ON r.product_id = bp.product_id
) AS source
ON target.product_version_key = source.product_version_key
WHEN MATCHED THEN
  UPDATE SET
    product_key = source.product_key,
    product_id = source.product_id,
    product_code = source.product_code,
    product_name = source.product_name,
    category = source.category,
    sub_category = source.sub_category,
    brand = source.brand,
    msrp = source.msrp,
    cost_price = source.cost_price,
    valid_from = source.valid_from,
    valid_to = source.valid_to,
    is_current = source.is_current
WHEN NOT MATCHED THEN
  INSERT (
    product_version_key, product_key, product_id, product_code, product_name,
    category, sub_category, brand, msrp, cost_price, valid_from, valid_to, is_current
  )
  VALUES (
    source.product_version_key, source.product_key, source.product_id, source.product_code,
    source.product_name, source.category, source.sub_category, source.brand,
    source.msrp, source.cost_price, source.valid_from, source.valid_to, source.is_current
  );

-- ---------------------------------------------------------------------------
-- 4. dim_channel
-- ---------------------------------------------------------------------------
MERGE `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_channel` AS target
USING (
  SELECT
    TO_HEX(SHA256(CONCAT('channel|', CAST(id AS STRING)))) AS channel_key,
    CAST(id AS INT64) AS channel_id,
    channel_code,
    channel_name,
    channel_type,
    channel_type IN ('Web', 'App') AS is_online,
    LOWER(TRIM(CAST(is_active AS STRING))) IN ('1', 'true') AS is_active
  FROM `{{PROJECT_ID}}.{{BQ_DATASET_STG}}.stg_sales_channels`
) AS source
ON target.channel_key = source.channel_key
WHEN MATCHED THEN
  UPDATE SET
    channel_id = source.channel_id,
    channel_code = source.channel_code,
    channel_name = source.channel_name,
    channel_type = source.channel_type,
    is_online = source.is_online,
    is_active = source.is_active
WHEN NOT MATCHED THEN
  INSERT (
    channel_key, channel_id, channel_code, channel_name,
    channel_type, is_online, is_active
  )
  VALUES (
    source.channel_key, source.channel_id, source.channel_code, source.channel_name,
    source.channel_type, source.is_online, source.is_active
  );

-- ---------------------------------------------------------------------------
-- 5. dim_payment
-- ---------------------------------------------------------------------------
MERGE `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_payment` AS target
USING (
  WITH distinct_payments AS (
    SELECT DISTINCT
      COALESCE(NULLIF(TRIM(payment_method), ''), 'UNKNOWN') AS payment_method
    FROM `{{PROJECT_ID}}.{{BQ_DATASET_STG}}.stg_orders`
  )
  SELECT
    TO_HEX(SHA256(CONCAT('payment|', UPPER(TRIM(COALESCE(payment_method, 'UNKNOWN')))))) AS payment_key,
    payment_method,
    CASE
      WHEN payment_method = 'Cash' THEN 'Cash'
      WHEN payment_method = 'Credit Card' THEN 'Card'
      WHEN payment_method = 'UNKNOWN' THEN 'Unknown'
      ELSE 'Digital'
    END AS payment_category
  FROM distinct_payments
) AS source
ON target.payment_key = source.payment_key
WHEN MATCHED THEN
  UPDATE SET
    payment_method = source.payment_method,
    payment_category = source.payment_category
WHEN NOT MATCHED THEN
  INSERT (payment_key, payment_method, payment_category)
  VALUES (source.payment_key, source.payment_method, source.payment_category);

-- ---------------------------------------------------------------------------
-- 6. fact_orders (Incremental watermark MERGE + Physical vs Web/App ship_date logic)
-- ---------------------------------------------------------------------------
MERGE `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_orders` AS target
USING (
  WITH orders_parsed AS (
    SELECT
      o.*,
      TIMESTAMP(o.order_date) AS order_ts,
      TIMESTAMP(o.updated_at) AS updated_ts,
      SAFE_CAST(NULLIF(TRIM(CAST(o.ship_date AS STRING)), '') AS DATE) AS parsed_ship_date,
      COALESCE(NULLIF(TRIM(o.payment_method), ''), 'UNKNOWN') AS clean_payment_method
    FROM `{{PROJECT_ID}}.{{BQ_DATASET_STG}}.stg_orders` AS o
    WHERE TIMESTAMP(o.updated_at) >= TIMESTAMP('{{WATERMARK_TS}}')
  )
  SELECT
    TO_HEX(SHA256(CONCAT('order|', CAST(o.id AS STRING)))) AS order_key,
    CAST(o.id AS INT64) AS order_id,
    o.order_code,
    DATE(o.order_ts) AS order_date,
    o.updated_ts AS updated_at,
    CAST(FORMAT_DATE('%Y%m%d', DATE(o.order_ts)) AS INT64) AS order_date_key,
    CASE
      WHEN o.parsed_ship_date IS NOT NULL
        THEN CAST(FORMAT_DATE('%Y%m%d', o.parsed_ship_date) AS INT64)
      WHEN o.status IN ('Pending', 'Processing')
        THEN -2  -- Not Shipped
      WHEN o.status = 'Cancelled'
        THEN -3  -- Not Applicable
      WHEN o.status = 'Completed' AND sc.channel_type = 'Physical'
        THEN -3  -- In-Store Pickup (Not Applicable)
      ELSE -1    -- Unknown (e.g., Web/App Completed order missing ship_date)
    END AS ship_date_key,
    (
      o.parsed_ship_date IS NULL
      AND o.status = 'Completed'
      AND sc.channel_type IN ('Web', 'App')
    ) AS is_missing_online_ship_date,
    dc.customer_key,
    dch.channel_key,
    dp.payment_key,
    o.status,
    CAST(o.total_amount AS NUMERIC) AS total_amount,
    CAST(o.total_discount AS NUMERIC) AS total_discount,
    CAST(o.net_amount AS NUMERIC) AS net_amount,
    CAST(o.total_cost AS NUMERIC) AS total_cost
  FROM orders_parsed AS o
  LEFT JOIN `{{PROJECT_ID}}.{{BQ_DATASET_STG}}.stg_sales_channels` AS sc
    ON CAST(o.channel_id AS INT64) = CAST(sc.id AS INT64)
  LEFT JOIN `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_customer` AS dc
    ON CAST(o.customer_id AS INT64) = dc.customer_id
  LEFT JOIN `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_channel` AS dch
    ON CAST(o.channel_id AS INT64) = dch.channel_id
  LEFT JOIN `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_payment` AS dp
    ON o.clean_payment_method = dp.payment_method
) AS source
ON target.order_key = source.order_key
WHEN MATCHED THEN
  UPDATE SET
    order_id = source.order_id,
    order_code = source.order_code,
    order_date = source.order_date,
    updated_at = source.updated_at,
    order_date_key = source.order_date_key,
    ship_date_key = source.ship_date_key,
    is_missing_online_ship_date = source.is_missing_online_ship_date,
    customer_key = source.customer_key,
    channel_key = source.channel_key,
    payment_key = source.payment_key,
    status = source.status,
    total_amount = source.total_amount,
    total_discount = source.total_discount,
    net_amount = source.net_amount,
    total_cost = source.total_cost
WHEN NOT MATCHED THEN
  INSERT (
    order_key, order_id, order_code, order_date, updated_at,
    order_date_key, ship_date_key, is_missing_online_ship_date,
    customer_key, channel_key, payment_key, status,
    total_amount, total_discount, net_amount, total_cost
  )
  VALUES (
    source.order_key, source.order_id, source.order_code, source.order_date, source.updated_at,
    source.order_date_key, source.ship_date_key, source.is_missing_online_ship_date,
    source.customer_key, source.channel_key, source.payment_key, source.status,
    source.total_amount, source.total_discount, source.net_amount, source.total_cost
  );

-- ---------------------------------------------------------------------------
-- 7. fact_order_items (Incremental watermark via updated orders + SCD2 point-in-time join)
-- ---------------------------------------------------------------------------
MERGE `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_order_items` AS target
USING (
  SELECT
    TO_HEX(SHA256(CONCAT('order_item|', CAST(od.id AS STRING)))) AS order_item_key,
    CAST(od.id AS INT64) AS order_item_id,
    CAST(od.order_id AS INT64) AS order_id,
    fo.order_key,
    dp.product_key,
    dps.product_version_key,
    DATE(TIMESTAMP(o.order_date)) AS order_date,
    CAST(FORMAT_DATE('%Y%m%d', DATE(TIMESTAMP(o.order_date))) AS INT64) AS order_date_key,
    CAST(od.quantity AS INT64) AS quantity,
    CAST(od.unit_price AS NUMERIC) AS unit_price,
    CAST(od.unit_cost AS NUMERIC) AS unit_cost,
    CAST(od.discount_rate AS NUMERIC) AS discount_rate,
    CAST(od.discount_amount AS NUMERIC) AS discount_amount,
    CAST(od.line_total AS NUMERIC) AS line_total,
    CAST(od.line_cost AS NUMERIC) AS line_cost,
    CAST(od.line_profit AS NUMERIC) AS line_profit
  FROM `{{PROJECT_ID}}.{{BQ_DATASET_STG}}.stg_order_details` AS od
  JOIN `{{PROJECT_ID}}.{{BQ_DATASET_STG}}.stg_orders` AS o
    ON CAST(od.order_id AS INT64) = CAST(o.id AS INT64)
  LEFT JOIN `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_orders` AS fo
    ON CAST(od.order_id AS INT64) = fo.order_id
  LEFT JOIN `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_product` AS dp
    ON CAST(od.product_id AS INT64) = dp.product_id
  LEFT JOIN `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_product_scd2` AS dps
    ON CAST(od.product_id AS INT64) = dps.product_id
    AND TIMESTAMP(o.order_date) >= dps.valid_from
    AND TIMESTAMP(o.order_date) < dps.valid_to
  WHERE TIMESTAMP(o.updated_at) >= TIMESTAMP('{{WATERMARK_TS}}')
) AS source
ON target.order_item_key = source.order_item_key
WHEN MATCHED THEN
  UPDATE SET
    order_item_id = source.order_item_id,
    order_id = source.order_id,
    order_key = source.order_key,
    product_key = source.product_key,
    product_version_key = source.product_version_key,
    order_date = source.order_date,
    order_date_key = source.order_date_key,
    quantity = source.quantity,
    unit_price = source.unit_price,
    unit_cost = source.unit_cost,
    discount_rate = source.discount_rate,
    discount_amount = source.discount_amount,
    line_total = source.line_total,
    line_cost = source.line_cost,
    line_profit = source.line_profit
WHEN NOT MATCHED THEN
  INSERT (
    order_item_key, order_item_id, order_id, order_key, product_key, product_version_key,
    order_date, order_date_key, quantity, unit_price, unit_cost, discount_rate,
    discount_amount, line_total, line_cost, line_profit
  )
  VALUES (
    source.order_item_key, source.order_item_id, source.order_id, source.order_key,
    source.product_key, source.product_version_key, source.order_date, source.order_date_key,
    source.quantity, source.unit_price, source.unit_cost, source.discount_rate,
    source.discount_amount, source.line_total, source.line_cost, source.line_profit
  );

-- ---------------------------------------------------------------------------
-- 8. fact_returns (Incremental watermark + return_lead_days & temporal anomaly quarantine flag)
-- ---------------------------------------------------------------------------
MERGE `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_returns` AS target
USING (
  SELECT
    TO_HEX(SHA256(CONCAT('return|', CAST(r.id AS STRING)))) AS return_key,
    CAST(r.id AS INT64) AS return_id,
    CAST(r.order_id AS INT64) AS order_id,
    fo.order_key,
    DATE(TIMESTAMP(r.returned_at)) AS returned_date,
    TIMESTAMP(r.returned_at) AS returned_at,
    CAST(FORMAT_DATE('%Y%m%d', DATE(TIMESTAMP(r.returned_at))) AS INT64) AS returned_date_key,
    DATE_DIFF(DATE(TIMESTAMP(r.returned_at)), DATE(TIMESTAMP(o.order_date)), DAY) AS return_lead_days,
    TIMESTAMP(r.returned_at) < TIMESTAMP(o.order_date) AS is_temporal_anomaly,
    r.reason
  FROM `{{PROJECT_ID}}.{{BQ_DATASET_STG}}.stg_order_returns` AS r
  LEFT JOIN `{{PROJECT_ID}}.{{BQ_DATASET_STG}}.stg_orders` AS o
    ON CAST(r.order_id AS INT64) = CAST(o.id AS INT64)
  LEFT JOIN `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_orders` AS fo
    ON CAST(r.order_id AS INT64) = fo.order_id
  WHERE TIMESTAMP(r.returned_at) >= TIMESTAMP('{{WATERMARK_TS}}')
) AS source
ON target.return_key = source.return_key
WHEN MATCHED THEN
  UPDATE SET
    return_id = source.return_id,
    order_id = source.order_id,
    order_key = source.order_key,
    returned_date = source.returned_date,
    returned_at = source.returned_at,
    returned_date_key = source.returned_date_key,
    return_lead_days = source.return_lead_days,
    is_temporal_anomaly = source.is_temporal_anomaly,
    reason = source.reason
WHEN NOT MATCHED THEN
  INSERT (
    return_key, return_id, order_id, order_key,
    returned_date, returned_at, returned_date_key,
    return_lead_days, is_temporal_anomaly, reason
  )
  VALUES (
    source.return_key, source.return_id, source.order_id, source.order_key,
    source.returned_date, source.returned_at, source.returned_date_key,
    source.return_lead_days, source.is_temporal_anomaly, source.reason
  );

-- ---------------------------------------------------------------------------
-- 9. Update high-water marks in etl_watermarks
-- ---------------------------------------------------------------------------
MERGE `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.etl_watermarks` AS target
USING (
  SELECT
    'viettech_star_schema' AS pipeline_name,
    'fact_orders' AS table_name,
    MAX(updated_at) AS last_watermark_ts,
    CURRENT_TIMESTAMP() AS updated_at
  FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_orders`
  WHERE updated_at IS NOT NULL

  UNION ALL

  SELECT
    'viettech_star_schema' AS pipeline_name,
    'fact_order_items' AS table_name,
    MAX(updated_at) AS last_watermark_ts,
    CURRENT_TIMESTAMP() AS updated_at
  FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_orders`
  WHERE updated_at IS NOT NULL

  UNION ALL

  SELECT
    'viettech_star_schema' AS pipeline_name,
    'fact_returns' AS table_name,
    MAX(returned_at) AS last_watermark_ts,
    CURRENT_TIMESTAMP() AS updated_at
  FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_returns`
  WHERE returned_at IS NOT NULL
) AS source
ON target.pipeline_name = source.pipeline_name
AND target.table_name = source.table_name
WHEN MATCHED AND source.last_watermark_ts IS NOT NULL THEN
  UPDATE SET
    last_watermark_ts = source.last_watermark_ts,
    updated_at = source.updated_at
WHEN NOT MATCHED AND source.last_watermark_ts IS NOT NULL THEN
  INSERT (pipeline_name, table_name, last_watermark_ts, updated_at)
  VALUES (source.pipeline_name, source.table_name, source.last_watermark_ts, source.updated_at);
