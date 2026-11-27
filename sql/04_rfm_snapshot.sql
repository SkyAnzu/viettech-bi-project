-- 04_rfm_snapshot.sql
-- Parameterized, idempotent multi-snapshot RFM customer segmentation mart.
-- Deletes and recomputes only the requested {{SNAPSHOT_DATE}} partition.
-- Rendered by scripts/run_sql_pipeline.py.

DECLARE snapshot_dt DATE DEFAULT DATE '{{SNAPSHOT_DATE}}';

DELETE FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.mart_rfm_snapshot`
WHERE snapshot_date = snapshot_dt;

INSERT INTO `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.mart_rfm_snapshot` (
  snapshot_date,
  customer_key,
  customer_id,
  recency_days,
  frequency_orders,
  monetary_value,
  r_score,
  f_score,
  m_score,
  rfm_score,
  rfm_segment
)
WITH rfm_base AS (
  SELECT
    fo.customer_key,
    DATE_DIFF(snapshot_dt, MAX(fo.order_date), DAY) AS recency_days,
    COUNT(*) AS frequency_orders,
    SUM(fo.net_amount) AS monetary_value
  FROM `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.fact_orders` AS fo
  WHERE fo.status = 'Completed'
    AND fo.order_date < snapshot_dt
  GROUP BY fo.customer_key
),
scored AS (
  SELECT
    customer_key,
    recency_days,
    frequency_orders,
    monetary_value,
    NTILE(5) OVER (ORDER BY recency_days DESC) AS r_score,
    NTILE(5) OVER (ORDER BY frequency_orders ASC) AS f_score,
    NTILE(5) OVER (ORDER BY monetary_value ASC) AS m_score
  FROM rfm_base
)
SELECT
  snapshot_dt AS snapshot_date,
  s.customer_key,
  dc.customer_id,
  s.recency_days,
  s.frequency_orders,
  s.monetary_value,
  s.r_score,
  s.f_score,
  s.m_score,
  CONCAT(
    CAST(s.r_score AS STRING),
    CAST(s.f_score AS STRING),
    CAST(s.m_score AS STRING)
  ) AS rfm_score,
  CASE
    WHEN s.r_score >= 4 AND s.f_score >= 4 AND s.m_score >= 4 THEN 'Champions'
    WHEN s.r_score >= 3 AND s.f_score >= 3 THEN 'Loyal'
    WHEN s.r_score <= 2 AND s.f_score >= 3 THEN 'At-risk'
    WHEN s.r_score <= 2 AND s.f_score <= 2 THEN 'Churned'
    ELSE 'Regular'
  END AS rfm_segment
FROM scored AS s
LEFT JOIN `{{PROJECT_ID}}.{{BQ_DATASET_MART}}.dim_customer` AS dc
  ON s.customer_key = dc.customer_key;
