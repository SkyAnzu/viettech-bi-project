-- 05_bi_views.sql
-- Analytical BI views joining dim_date (zero PARSE_DATE string casts),
-- dim_channel, dim_product_scd2, and dq_audit_results.
-- Rendered by scripts/run_sql_pipeline.py.

-- 1. Monthly executive KPI view (joined to dim_date)
CREATE OR REPLACE VIEW `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.vw_monthly_kpi` AS
SELECT
  d.year,
  d.month,
  d.is_tet,
  COUNT(*) AS total_orders,
  COUNTIF(fo.status = 'Completed') AS total_completed_orders,
  SUM(IF(fo.status = 'Completed', fo.net_amount, 0)) AS completed_revenue,
  SUM(IF(fo.status = 'Completed', fo.net_amount - fo.total_cost, 0)) AS gross_profit,
  SAFE_DIVIDE(
    SUM(IF(fo.status = 'Completed', fo.net_amount - fo.total_cost, 0)),
    SUM(IF(fo.status = 'Completed', fo.net_amount, 0))
  ) * 100 AS gross_margin_pct,
  AVG(IF(fo.status = 'Completed', fo.net_amount, NULL)) AS avg_completed_order_value,
  SUM(IF(fo.status = 'Completed', fo.total_discount, 0)) AS completed_discount,
  SUM(fo.total_discount) AS total_discount,
  COUNTIF(fo.is_missing_online_ship_date) AS missing_online_ship_orders
FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_orders` AS fo
JOIN `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_date` AS d
  ON fo.order_date_key = d.date_key
GROUP BY d.year, d.month, d.is_tet;

-- 2. Channel performance view (gross profit, margin %, completion rate)
CREATE OR REPLACE VIEW `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.vw_channel_performance` AS
SELECT
  c.channel_name,
  c.channel_type,
  c.is_online,
  COUNT(*) AS total_orders,
  COUNTIF(fo.status = 'Completed') AS total_completed_orders,
  SAFE_DIVIDE(COUNTIF(fo.status = 'Completed'), COUNT(*)) * 100 AS completion_rate_pct,
  SUM(IF(fo.status = 'Completed', fo.net_amount, 0)) AS completed_revenue,
  SUM(IF(fo.status = 'Completed', fo.net_amount - fo.total_cost, 0)) AS gross_profit,
  SAFE_DIVIDE(
    SUM(IF(fo.status = 'Completed', fo.net_amount - fo.total_cost, 0)),
    SUM(IF(fo.status = 'Completed', fo.net_amount, 0))
  ) * 100 AS gross_margin_pct,
  COUNTIF(fo.is_missing_online_ship_date) AS missing_online_ship_orders
FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_orders` AS fo
JOIN `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_channel` AS c
  ON fo.channel_key = c.channel_key
GROUP BY c.channel_name, c.channel_type, c.is_online;

-- 3. Return metrics view (joined to fact_orders & dim_date; separates temporal anomalies)
CREATE OR REPLACE VIEW `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.vw_return_metrics` AS
SELECT
  d.year,
  d.month,
  COUNTIF(NOT r.is_temporal_anomaly) AS valid_returns,
  COUNTIF(r.is_temporal_anomaly) AS temporal_anomaly_returns,
  COUNT(*) AS total_returns,
  SUM(fo.net_amount) AS total_return_revenue,
  SUM(IF(NOT r.is_temporal_anomaly, fo.net_amount, 0)) AS valid_return_revenue,
  AVG(IF(NOT r.is_temporal_anomaly, CAST(r.return_lead_days AS FLOAT64), NULL)) AS avg_return_lead_days
FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_returns` AS r
JOIN `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_orders` AS fo
  ON r.order_key = fo.order_key
JOIN `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_date` AS d
  ON fo.order_date_key = d.date_key
GROUP BY d.year, d.month;

-- 4. Product margin history view (point-in-time SCD Type 2 version analysis on Completed orders)
CREATE OR REPLACE VIEW `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.vw_product_margin_history` AS
SELECT
  dps.product_version_key,
  dps.product_id,
  dps.product_code,
  dps.product_name,
  dps.category,
  dps.sub_category,
  dps.brand,
  dps.msrp AS version_msrp,
  dps.cost_price AS version_cost_price,
  dps.valid_from,
  dps.valid_to,
  dps.is_current,
  COUNT(DISTINCT foi.order_id) AS completed_orders,
  SUM(foi.quantity) AS units_sold,
  SUM(foi.line_total) AS completed_revenue,
  SUM(foi.line_cost) AS total_cost,
  SUM(foi.line_profit) AS gross_profit,
  SAFE_DIVIDE(SUM(foi.line_profit), SUM(foi.line_total)) * 100 AS gross_margin_pct
FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_order_items` AS foi
JOIN `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_orders` AS fo
  ON foi.order_key = fo.order_key
JOIN `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_product_scd2` AS dps
  ON foi.product_version_key = dps.product_version_key
WHERE fo.status = 'Completed'
GROUP BY
  dps.product_version_key,
  dps.product_id,
  dps.product_code,
  dps.product_name,
  dps.category,
  dps.sub_category,
  dps.brand,
  dps.msrp,
  dps.cost_price,
  dps.valid_from,
  dps.valid_to,
  dps.is_current;

-- 5. Data quality executive summary view (latest DQ audit run)
CREATE OR REPLACE VIEW `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.vw_dq_executive_summary` AS
WITH latest_run AS (
  SELECT MAX(run_ts) AS max_run_ts
  FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dq_audit_results`
)
SELECT
  dq.run_ts,
  dq.check_name,
  dq.severity,
  dq.status,
  dq.failed_rows,
  dq.details
FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dq_audit_results` AS dq
JOIN latest_run AS lr
  ON dq.run_ts = lr.max_run_ts;
