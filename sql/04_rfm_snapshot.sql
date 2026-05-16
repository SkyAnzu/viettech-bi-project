-- Rendered by run_sql_pipeline.py.
-- Fixed snapshot date.

CREATE OR REPLACE TABLE `your-project-id.retailbi_mart.mart_rfm_snapshot` AS
WITH params AS (
  SELECT DATE '2025-01-01' AS snapshot_date
),
rfm_base AS (
  SELECT
    fo.customer_key,
    DATE_DIFF(
      p.snapshot_date,
      PARSE_DATE('%Y%m%d', CAST(MAX(fo.order_date_key) AS STRING)),
      DAY
    ) AS recency_days,
    COUNT(*) AS frequency_orders,
    SUM(fo.net_amount) AS monetary_value,
    p.snapshot_date
  FROM `your-project-id.retailbi_mart.fact_orders` fo
  CROSS JOIN params p
  WHERE fo.status = 'Completed'
  GROUP BY fo.customer_key, p.snapshot_date
),
scored AS (
  SELECT
    *,
    NTILE(5) OVER (ORDER BY recency_days DESC) AS r_score,
    NTILE(5) OVER (ORDER BY frequency_orders) AS f_score,
    NTILE(5) OVER (ORDER BY monetary_value) AS m_score
  FROM rfm_base
)
SELECT
  s.snapshot_date,
  s.customer_key,
  dc.customer_id,
  s.recency_days,
  s.frequency_orders,
  s.monetary_value,
  s.r_score,
  s.f_score,
  s.m_score,
  CONCAT(CAST(s.r_score AS STRING), CAST(s.f_score AS STRING), CAST(s.m_score AS STRING)) AS rfm_score,
  CASE
    WHEN s.r_score >= 4 AND s.f_score >= 4 AND s.m_score >= 4 THEN 'Champions'
    WHEN s.r_score >= 3 AND s.f_score >= 3 THEN 'Loyal'
    WHEN s.r_score <= 2 AND s.f_score >= 3 THEN 'At-risk'
    WHEN s.r_score <= 2 AND s.f_score <= 2 THEN 'Churned'
    ELSE 'Regular'
  END AS rfm_segment
FROM scored s
LEFT JOIN `your-project-id.retailbi_mart.dim_customer` dc
  ON s.customer_key = dc.customer_key;
