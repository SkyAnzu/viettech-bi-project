-- 06_data_quality_checks.sql
-- Automated post-load data quality audit gate for VietTech BigQuery warehouse.
-- Logs CRITICAL and WARNING checks to dq_audit_results and fails via ASSERT
-- ONLY when any CRITICAL check fails.
-- Rendered by scripts/run_sql_pipeline.py.

DECLARE dq_run_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP();
DECLARE snapshot_dt DATE DEFAULT DATE '{{SNAPSHOT_DATE}}';

INSERT INTO `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dq_audit_results` (
  run_ts,
  check_name,
  severity,
  status,
  failed_rows,
  details
)
WITH checks AS (
  -- 1. Primary key uniqueness checks across all dimensions and facts
  SELECT
    'pk_unique_dim_customer' AS check_name,
    'CRITICAL' AS severity,
    COUNT(*) - COUNT(DISTINCT customer_key) AS failed_rows,
    'Duplicate customer_key rows in dim_customer' AS details
  FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_customer`

  UNION ALL

  SELECT
    'pk_unique_dim_product' AS check_name,
    'CRITICAL' AS severity,
    COUNT(*) - COUNT(DISTINCT product_key) AS failed_rows,
    'Duplicate product_key rows in dim_product' AS details
  FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_product`

  UNION ALL

  SELECT
    'pk_unique_dim_product_scd2' AS check_name,
    'CRITICAL' AS severity,
    COUNT(*) - COUNT(DISTINCT product_version_key) AS failed_rows,
    'Duplicate product_version_key rows in dim_product_scd2' AS details
  FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_product_scd2`

  UNION ALL

  SELECT
    'pk_unique_dim_channel' AS check_name,
    'CRITICAL' AS severity,
    COUNT(*) - COUNT(DISTINCT channel_key) AS failed_rows,
    'Duplicate channel_key rows in dim_channel' AS details
  FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_channel`

  UNION ALL

  SELECT
    'pk_unique_dim_payment' AS check_name,
    'CRITICAL' AS severity,
    COUNT(*) - COUNT(DISTINCT payment_key) AS failed_rows,
    'Duplicate payment_key rows in dim_payment' AS details
  FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_payment`

  UNION ALL

  SELECT
    'pk_unique_fact_orders' AS check_name,
    'CRITICAL' AS severity,
    COUNT(*) - COUNT(DISTINCT order_key) AS failed_rows,
    'Duplicate order_key rows in fact_orders' AS details
  FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_orders`

  UNION ALL

  SELECT
    'pk_unique_fact_order_items' AS check_name,
    'CRITICAL' AS severity,
    COUNT(*) - COUNT(DISTINCT order_item_key) AS failed_rows,
    'Duplicate order_item_key rows in fact_order_items' AS details
  FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_order_items`

  UNION ALL

  SELECT
    'pk_unique_fact_returns' AS check_name,
    'CRITICAL' AS severity,
    COUNT(*) - COUNT(DISTINCT return_key) AS failed_rows,
    'Duplicate return_key rows in fact_returns' AS details
  FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_returns`

  UNION ALL

  -- 2. Orphan foreign key checks
  SELECT
    'fk_fact_orders_dims' AS check_name,
    'CRITICAL' AS severity,
    COUNT(*) AS failed_rows,
    'Orders with missing dim_customer, dim_channel, dim_payment, or dim_date reference' AS details
  FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_orders` AS fo
  LEFT JOIN `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_customer` AS dc
    ON fo.customer_key = dc.customer_key
  LEFT JOIN `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_channel` AS dch
    ON fo.channel_key = dch.channel_key
  LEFT JOIN `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_payment` AS dp
    ON fo.payment_key = dp.payment_key
  LEFT JOIN `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_date` AS dd
    ON fo.order_date_key = dd.date_key
  LEFT JOIN `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_date` AS sd
    ON fo.ship_date_key = sd.date_key
  WHERE dc.customer_key IS NULL
     OR dch.channel_key IS NULL
     OR dp.payment_key IS NULL
     OR dd.date_key IS NULL
     OR sd.date_key IS NULL

  UNION ALL

  SELECT
    'fk_fact_order_items_refs' AS check_name,
    'CRITICAL' AS severity,
    COUNT(*) AS failed_rows,
    'Order items with missing fact_orders, dim_product, or dim_product_scd2 reference' AS details
  FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_order_items` AS foi
  LEFT JOIN `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_orders` AS fo
    ON foi.order_key = fo.order_key
  LEFT JOIN `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_product` AS dp
    ON foi.product_key = dp.product_key
  LEFT JOIN `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_product_scd2` AS dps
    ON foi.product_version_key = dps.product_version_key
  WHERE fo.order_key IS NULL
     OR dp.product_key IS NULL
     OR dps.product_version_key IS NULL

  UNION ALL

  SELECT
    'fk_fact_returns_orders' AS check_name,
    'CRITICAL' AS severity,
    COUNT(*) AS failed_rows,
    'Returns with missing fact_orders reference' AS details
  FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_returns` AS fr
  LEFT JOIN `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_orders` AS fo
    ON fr.order_key = fo.order_key
  WHERE fo.order_key IS NULL

  UNION ALL

  -- 3. Three-way financial reconciliation: stg_orders vs fact_orders vs fact_order_items
  SELECT
    'reconcile_3way_financials' AS check_name,
    'CRITICAL' AS severity,
    CASE
      WHEN ABS(stg.stg_net - fo.fact_net) > NUMERIC '0.01'
        OR ABS(fo.fact_net - foi.item_net) > NUMERIC '0.01'
        OR ABS(stg.stg_cost - fo.fact_cost) > NUMERIC '0.01'
        OR ABS(fo.fact_cost - foi.item_cost) > NUMERIC '0.01'
      THEN 1
      ELSE 0
    END AS failed_rows,
    CONCAT(
      'stg_net=', CAST(stg.stg_net AS STRING),
      ', fact_orders_net=', CAST(fo.fact_net AS STRING),
      ', fact_items_net=', CAST(foi.item_net AS STRING),
      '; stg_cost=', CAST(stg.stg_cost AS STRING),
      ', fact_orders_cost=', CAST(fo.fact_cost AS STRING),
      ', fact_items_cost=', CAST(foi.item_cost AS STRING)
    ) AS details
  FROM (
    SELECT
      COALESCE(SUM(CAST(net_amount AS NUMERIC)), NUMERIC '0') AS stg_net,
      COALESCE(SUM(CAST(total_cost AS NUMERIC)), NUMERIC '0') AS stg_cost
    FROM `{{PROJECT_ID}}.{{BQ_DATASET_STG}}.stg_orders`
  ) AS stg
  CROSS JOIN (
    SELECT
      COALESCE(SUM(net_amount), NUMERIC '0') AS fact_net,
      COALESCE(SUM(total_cost), NUMERIC '0') AS fact_cost
    FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_orders`
  ) AS fo
  CROSS JOIN (
    SELECT
      COALESCE(SUM(line_total), NUMERIC '0') AS item_net,
      COALESCE(SUM(line_cost), NUMERIC '0') AS item_cost
    FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_order_items`
  ) AS foi

  UNION ALL

  -- 4. SCD Type 2 integrity: no overlapping validity intervals & single current version per SKU
  SELECT
    'scd2_no_overlapping_intervals' AS check_name,
    'CRITICAL' AS severity,
    COUNT(*) AS failed_rows,
    'Overlapping [valid_from, valid_to) intervals detected in dim_product_scd2' AS details
  FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_product_scd2` AS a
  JOIN `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_product_scd2` AS b
    ON a.product_id = b.product_id
    AND a.product_version_key < b.product_version_key
    AND a.valid_from < b.valid_to
    AND b.valid_from < a.valid_to

  UNION ALL

  SELECT
    'scd2_single_current_version' AS check_name,
    'CRITICAL' AS severity,
    COUNT(*) AS failed_rows,
    'Products in dim_product_scd2 without exactly 1 is_current = TRUE row' AS details
  FROM (
    SELECT product_id, COUNTIF(is_current) AS current_cnt
    FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_product_scd2`
    GROUP BY product_id
    HAVING current_cnt != 1
  )

  UNION ALL

  -- 5. PDPL 91/2025/QH15 masking verification
  SELECT
    'pdpl_customer_name_masked' AS check_name,
    'CRITICAL' AS severity,
    COUNT(*) AS failed_rows,
    'Rows in dim_customer missing masked name pattern (***)' AS details
  FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_customer`
  WHERE customer_name_masked IS NULL
     OR STRPOS(customer_name_masked, '***') = 0

  UNION ALL

  -- 6. RFM score bounds check (1..5) and non-empty snapshot check
  SELECT
    'rfm_score_bounds_1_to_5' AS check_name,
    'CRITICAL' AS severity,
    CASE
      WHEN COUNT(*) = 0 THEN 1
      ELSE COUNTIF(
        r_score IS NULL OR r_score NOT BETWEEN 1 AND 5
        OR f_score IS NULL OR f_score NOT BETWEEN 1 AND 5
        OR m_score IS NULL OR m_score NOT BETWEEN 1 AND 5
      )
    END AS failed_rows,
    CONCAT('Validated RFM snapshot ', CAST(snapshot_dt AS STRING), ' for scores within 1..5') AS details
  FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.mart_rfm_snapshot`
  WHERE snapshot_date = snapshot_dt

  UNION ALL

  -- 7. Temporal return anomalies (WARNING — quarantined via is_temporal_anomaly = TRUE)
  SELECT
    'warn_temporal_return_anomalies' AS check_name,
    'WARNING' AS severity,
    COUNT(*) AS failed_rows,
    'Returns where returned_at < orders.order_date (quarantined from lead-time KPIs)' AS details
  FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_returns`
  WHERE is_temporal_anomaly = TRUE

  UNION ALL

  -- 8. Missing online fulfillment ship_date (WARNING — mapped to ship_date_key = -1)
  SELECT
    'warn_missing_online_ship_dates' AS check_name,
    'WARNING' AS severity,
    COUNT(*) AS failed_rows,
    'Completed Web/App orders with NULL ship_date (mapped to ship_date_key = -1)' AS details
  FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_orders`
  WHERE is_missing_online_ship_date = TRUE
)
SELECT
  dq_run_ts AS run_ts,
  check_name,
  severity,
  CASE
    WHEN failed_rows = 0 THEN 'PASS'
    WHEN severity = 'WARNING' THEN 'WARN'
    ELSE 'FAIL'
  END AS status,
  failed_rows,
  details
FROM checks;

-- Fail the pipeline ONLY if any CRITICAL check failed in this run
ASSERT (
  SELECT COUNT(*) = 0
  FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dq_audit_results`
  WHERE run_ts = dq_run_ts
    AND severity = 'CRITICAL'
    AND status = 'FAIL'
) AS 'One or more CRITICAL data quality checks failed. Inspect dq_audit_results for details.';
