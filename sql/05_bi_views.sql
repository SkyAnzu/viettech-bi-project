-- Rendered by run_sql_pipeline.py.

CREATE OR REPLACE VIEW `your-project-id.retailbi_mart.vw_monthly_kpi` AS
SELECT
  EXTRACT(YEAR FROM PARSE_DATE('%Y%m%d', CAST(order_date_key AS STRING))) AS year,
  EXTRACT(MONTH FROM PARSE_DATE('%Y%m%d', CAST(order_date_key AS STRING))) AS month,
  COUNT(*) AS total_orders,
  COUNT(IF(status = 'Completed', 1, NULL)) AS total_completed_orders,
  SUM(IF(status = 'Completed', net_amount, 0)) AS completed_revenue,
  SUM(IF(status = 'Completed', net_amount - total_cost, 0)) AS gross_profit,
  SAFE_DIVIDE(
    SUM(IF(status = 'Completed', net_amount - total_cost, 0)),
    SUM(IF(status = 'Completed', net_amount, 0))
  ) * 100 AS gross_margin_pct,
  AVG(IF(status = 'Completed', net_amount, NULL)) AS avg_completed_order_value,
  SUM(IF(status = 'Completed', total_discount, 0)) AS completed_discount,
  SUM(total_discount) AS total_discount
FROM `your-project-id.retailbi_mart.fact_orders`
GROUP BY year, month;

-- Join via channel_key.
CREATE OR REPLACE VIEW `your-project-id.retailbi_mart.vw_channel_performance` AS
SELECT
  c.channel_name,
  c.channel_type,
  COUNT(*) AS total_orders,
  COUNT(IF(o.status = 'Completed', 1, NULL)) AS total_completed_orders,
  SUM(IF(o.status = 'Completed', o.net_amount, 0)) AS completed_revenue
FROM `your-project-id.retailbi_mart.fact_orders` o
JOIN `your-project-id.retailbi_mart.dim_channel` c
  ON o.channel_key = c.channel_key
GROUP BY c.channel_name, c.channel_type;

-- Group by original order month.
CREATE OR REPLACE VIEW `your-project-id.retailbi_mart.vw_return_metrics` AS
SELECT
  EXTRACT(YEAR FROM PARSE_DATE('%Y%m%d', CAST(fo.order_date_key AS STRING))) AS year,
  EXTRACT(MONTH FROM PARSE_DATE('%Y%m%d', CAST(fo.order_date_key AS STRING))) AS month,
  COUNT(*) AS total_returns,
  SUM(fo.net_amount) AS total_return_revenue
FROM `your-project-id.retailbi_mart.fact_returns` r
JOIN `your-project-id.retailbi_mart.fact_orders` fo
  ON r.order_key = fo.order_key
GROUP BY year, month;
