# KPI Glossary

This project uses a fixed generated dataset and a presentation-first BI scope. Keep KPI definitions explicit and stable across SQL, Power BI, and presentation materials.

## Monthly KPI view

Source: `sql/05_bi_views.sql` -> `vw_monthly_kpi`

| KPI | Definition | Notes |
| --- | --- | --- |
| `total_orders` | Count of all orders in the month | Includes all statuses |
| `total_completed_orders` | Count of orders where `status = 'Completed'` | Use this when comparing orders against completed-only revenue |
| `completed_revenue` | Sum of `net_amount` where `status = 'Completed'` | Primary revenue KPI |
| `gross_profit` | Sum of `net_amount - total_cost` where `status = 'Completed'` | Completed-order only |
| `gross_margin_pct` | `gross_profit / completed_revenue * 100` | Uses `SAFE_DIVIDE` |
| `avg_completed_order_value` | Average `net_amount` for `status = 'Completed'` | Completed-order only |
| `completed_discount` | Sum of `total_discount` where `status = 'Completed'` | Use this when comparing discount directly with completed-only revenue |
| `total_discount` | Sum of `total_discount` across all orders in the month | Current SQL keeps this as all-status discount; rename or recalculate explicitly if completed-only discount is needed |

## Channel KPI view

Source: `sql/05_bi_views.sql` -> `vw_channel_performance`

| KPI | Definition | Notes |
| --- | --- | --- |
| `total_orders` | Count of all orders for the channel | Includes all statuses |
| `total_completed_orders` | Count of channel orders where `status = 'Completed'` | Aligns with revenue denominator |
| `completed_revenue` | Sum of `net_amount` where `status = 'Completed'` | Channel revenue KPI |

## Return metrics view

Source: `sql/05_bi_views.sql` -> `vw_return_metrics`

| KPI | Definition | Notes |
| --- | --- | --- |
| `total_returns` | Count of return records grouped by original order month | Derived from `fact_returns` joined to `fact_orders`; not grouped by actual returned date |
| `total_return_revenue` | Sum of `fact_orders.net_amount` for returned orders, grouped by original order month | Measures revenue tied to orders that were returned |

## Recommended Power BI measures

- `return_rate = DIVIDE(total_returns, total_orders)` when analyzing return behavior by original order month.
- If the dashboard instead uses `total_completed_orders` as the denominator, rename the measure explicitly to avoid ambiguity.
- Treat RFM visuals as a snapshot at `2025-01-01`; do not imply month-over-month RFM movement unless a multi-snapshot design is introduced later.

## Naming rules

- If a metric is completed-only, its definition or name must make that clear.
- Do not compare `total_orders` directly with `completed_revenue` without also showing `total_completed_orders`.
- Keep Power BI calculations aligned with these definitions unless the report page explicitly introduces a different measure name.
