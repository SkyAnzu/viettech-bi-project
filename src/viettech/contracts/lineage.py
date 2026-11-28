"""End-to-end machine-readable data lineage DAG builder & PDPL compliance verifier."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from viettech.common.utils import utc_now_iso_z
from viettech.contracts.types import load_contract

_BI_VIEWS: Tuple[Tuple[str, str, List[str], str], ...] = (
    (
        "bi.vw_monthly_kpi",
        "vw_monthly_kpi",
        ["mart.fact_orders", "mart.dim_date"],
        "Monthly revenue, COGS, gross margin %, AOV, discounts, and Vietnamese Tet/Year-End seasonality.",
    ),
    (
        "bi.vw_channel_performance",
        "vw_channel_performance",
        ["mart.fact_orders", "mart.dim_channel", "mart.dim_date"],
        "Omnichannel comparison across 6 physical stores, Web, and App including missing online ship-date tracking.",
    ),
    (
        "bi.vw_return_metrics",
        "vw_return_metrics",
        ["mart.fact_returns", "mart.fact_orders", "mart.dim_date"],
        "Order-cohort return value and clean operational lead time excluding 4 flagged temporal anomalies.",
    ),
    (
        "bi.vw_product_margin_history",
        "vw_product_margin_history",
        ["mart.fact_order_items", "mart.fact_orders", "mart.dim_product_scd2", "mart.dim_date"],
        "Point-in-time SCD Type 2 SKU margin attribution across 71 historical price/cost versions.",
    ),
    (
        "bi.vw_dq_executive_summary",
        "vw_dq_executive_summary",
        ["mart.fact_orders", "mart.fact_order_items", "mart.fact_returns", "mart.dim_customer", "mart.dim_product_scd2"],
        "Executive roll-up of automated CRITICAL/WARNING DQ gate results and PDPL compliance status.",
    ),
)

_MINING_MARTS: Tuple[Tuple[str, str, List[str], str, str], ...] = (
    (
        "mining.mart_market_basket_rules",
        "mart_market_basket_rules",
        ["mart.fact_order_items", "mart.fact_orders", "mart.dim_product"],
        "market_basket_association_mining",
        "Product affinity & co-purchase association rules (support, confidence, lift) for cross-sell bundles.",
    ),
    (
        "mining.mart_rfm_kmeans_clusters",
        "mart_rfm_kmeans_clusters",
        ["mart.mart_rfm_snapshot", "mart.dim_customer"],
        "rfm_kmeans_plus_plus_clustering",
        "Unsupervised K-Means++ clustering in log1p + Z-score RFM space vs. rule-based NTILE(5) segments (pseudonymized).",
    ),
    (
        "mining.mart_return_risk_scores",
        "mart_return_risk_scores",
        [
            "mart.fact_orders",
            "mart.fact_order_items",
            "mart.dim_product",
            "mart.dim_channel",
            "mart.dim_date",
            "mart.dim_customer",
        ],
        "leakage_free_return_risk_scoring",
        (
            "Leakage-free out-of-time 2023->2024 post-fulfillment return-risk propensity scoring "
            "(L2 class-weighted logistic regression, order-time features only; mart.fact_returns is "
            "explicitly excluded as a feature and used strictly for timestamp-only point-in-time "
            "returned_at label & prior-return observability gating)."
        ),
    ),
)

_PBI_PAGES: Tuple[Tuple[str, str, List[str], str, str], ...] = (
    (
        "pbi.page_1_business_performance",
        "Page 1: Business Performance Dashboard",
        ["bi.vw_monthly_kpi", "bi.vw_channel_performance"],
        "powerbi/dashboard1.jpg",
        "Executive revenue, gross profit, margin %, Tet/Year-End seasonality, and omnichannel mix.",
    ),
    (
        "pbi.page_2_product_performance",
        "Page 2: Product Performance Dashboard",
        ["bi.vw_product_margin_history", "mining.mart_market_basket_rules"],
        "powerbi/dashboard2.jpg",
        "Category/brand profitability, SCD2 price-change margin waterfall, and basket cross-sell insights.",
    ),
    (
        "pbi.page_3_rfm_segmentation",
        "Page 3: RFM Customer Segmentation Dashboard",
        ["mart.mart_rfm_snapshot", "mining.mart_rfm_kmeans_clusters"],
        "powerbi/dashboard3.jpg",
        "Multi-snapshot RFM segment distribution, At-Risk win-back pool, and unsupervised K-Means++ customer clusters.",
    ),
    (
        "pbi.page_4_order_operations_dq",
        "Page 4: Order Operations & Data Quality Dashboard",
        [
            "bi.vw_return_metrics",
            "bi.vw_dq_executive_summary",
            "bi.vw_channel_performance",
            "mining.mart_return_risk_scores",
        ],
        "powerbi/dashboard4.jpg",
        "Fulfillment ship-date SLA, return lead-time monitoring, temporal anomaly quarantine, DQ gate health, and leakage-free return-risk scoring.",
    ),
)


def _make_node(
    node_id: str,
    name: str,
    layer: str,
    asset_type: str,
    zone: str,
    pii_classification: str,
    columns: Optional[List[str]] = None,
    pii_restricted_columns: Optional[List[str]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    node: Dict[str, Any] = {
        "id": node_id,
        "name": name,
        "layer": layer,
        "asset_type": asset_type,
        "trust_zone": zone,
        "pii_classification": pii_classification,
        "pii_restricted_columns": pii_restricted_columns or [],
    }
    if columns is not None:
        node["columns"] = columns
    if extra:
        node.update(extra)
    return node


def _make_edge(
    source: str,
    target: str,
    transformation_type: str,
    pii_tags: List[str],
    description: str,
    pdpl_boundary_crossing: bool = False,
) -> Dict[str, Any]:
    return {
        "edge_id": f"{source}->{target}",
        "source": source,
        "target": target,
        "transformation_type": transformation_type,
        "pii_tags": pii_tags,
        "pdpl_boundary_crossing": pdpl_boundary_crossing,
        "description": description,
    }


def _raw_nodes(
    tables_spec: Dict[str, Any],
    validation_summary: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []
    for table_name, spec in tables_spec.items():
        cols = [c["name"] for c in spec["columns"]]
        pii_cols = [c["name"] for c in spec["columns"] if c.get("pii_classification") == "PII_RESTRICTED"]
        col_pii_map = {c["name"]: c.get("pii_classification", "NONE") for c in spec["columns"]}
        observed_rows = (
            validation_summary["tables"][table_name]["total_rows"]
            if validation_summary and table_name in validation_summary.get("tables", {})
            else spec.get("baseline_row_count")
        )
        nodes.append(
            _make_node(
                node_id=f"raw.{table_name}",
                name=spec["file_name"],
                layer="raw_csv",
                asset_type="csv_snapshot",
                zone="Zone A - Restricted Ingestion",
                pii_classification=spec.get("pii_classification", "NONE"),
                columns=cols,
                pii_restricted_columns=pii_cols,
                extra={
                    "source_path": f"data/raw/{spec['file_name']}",
                    "owner": spec.get("owner"),
                    "row_count": observed_rows,
                    "column_pii_tags": col_pii_map,
                },
            )
        )
    return nodes


def _staging_nodes(tables_spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []
    for spec in tables_spec.values():
        cols = [c["name"] for c in spec["columns"]]
        pii_cols = [c["name"] for c in spec["columns"] if c.get("pii_classification") == "PII_RESTRICTED"]
        col_pii_map = {c["name"]: c.get("pii_classification", "NONE") for c in spec["columns"]}
        stg_name = spec["staging_table"]
        nodes.append(
            _make_node(
                node_id=f"stg.{stg_name}",
                name=stg_name,
                layer="staging",
                asset_type="staging_table",
                zone="Zone A - Restricted Staging",
                pii_classification=spec.get("pii_classification", "NONE"),
                columns=cols,
                pii_restricted_columns=pii_cols,
                extra={
                    "bigquery_dataset": "retailbi_stg",
                    "sqlite_table": stg_name,
                    "column_pii_tags": col_pii_map,
                },
            )
        )
    return nodes


def _mart_nodes(compliance: Dict[str, Any]) -> List[Dict[str, Any]]:
    prohibited_pii: List[str] = list(compliance.get("mart_prohibited_pii_columns", []))
    dim_customer_cols: List[str] = list(compliance.get("dim_customer_allowed_columns", []))
    zone_b = "Zone B - Governed Analytics Mart"
    return [
        _make_node(
            node_id="mart.dim_date",
            name="dim_date",
            layer="dimension",
            asset_type="dimension_table",
            zone=zone_b,
            pii_classification="NONE",
            columns=[
                "date_key", "date", "year", "quarter", "month",
                "week_of_year", "day_of_week", "is_weekend", "is_tet", "is_year_end",
            ],
            extra={
                "grain": "1 row per calendar day (2020-2027) + sentinel keys (-1, -2, -3)",
                "seasonality_flags": ["is_tet", "is_year_end"],
            },
        ),
        _make_node(
            node_id="mart.dim_customer",
            name="dim_customer",
            layer="dimension",
            asset_type="dimension_table",
            zone=zone_b,
            pii_classification="PSEUDONYMIZED",
            columns=dim_customer_cols,
            extra={
                "surrogate_key": "SHA-256('customer|' || id)",
                "pdpl_compliance": "91/2025/QH15",
                "prohibited_pii_columns_removed": prohibited_pii,
                "pii_vault_target": "retailbi_pii_vault.customer_pii_vault",
            },
        ),
        _make_node(
            node_id="mart.dim_product",
            name="dim_product",
            layer="dimension",
            asset_type="dimension_table",
            zone=zone_b,
            pii_classification="NONE",
            columns=[
                "product_key", "product_id", "product_code", "product_name",
                "category", "sub_category", "brand", "msrp", "cost_price", "is_active",
            ],
            extra={"scd_type": "Type 1 (current catalog state)", "surrogate_key": "SHA-256('product|' || id)"},
        ),
        _make_node(
            node_id="mart.dim_product_scd2",
            name="dim_product_scd2",
            layer="dimension",
            asset_type="dimension_table_scd2",
            zone=zone_b,
            pii_classification="INTERNAL",
            columns=[
                "product_version_key", "product_key", "product_id", "product_code", "product_name",
                "category", "sub_category", "brand", "msrp", "cost_price",
                "valid_from", "valid_to", "is_current",
            ],
            extra={
                "scd_type": "Type 2 (half-open [valid_from, valid_to) intervals)",
                "baseline_versions": 71,
                "surrogate_key": "SHA-256('product_scd2|' || product_id || '|' || valid_from)",
            },
        ),
        _make_node(
            node_id="mart.dim_channel",
            name="dim_channel",
            layer="dimension",
            asset_type="dimension_table",
            zone=zone_b,
            pii_classification="NONE",
            columns=[
                "channel_key", "channel_id", "channel_code", "channel_name",
                "channel_type", "is_online", "is_active", "opened_date",
            ],
            extra={"surrogate_key": "SHA-256('channel|' || id)"},
        ),
        _make_node(
            node_id="mart.dim_payment",
            name="dim_payment",
            layer="dimension",
            asset_type="dimension_table",
            zone=zone_b,
            pii_classification="NONE",
            columns=["payment_key", "payment_method", "payment_category"],
            extra={"surrogate_key": "SHA-256('payment|' || payment_method)"},
        ),
        _make_node(
            node_id="mart.fact_orders",
            name="fact_orders",
            layer="fact",
            asset_type="fact_table",
            zone=zone_b,
            pii_classification="INTERNAL",
            columns=[
                "order_key", "order_id", "order_code", "order_date", "updated_at",
                "order_date_key", "ship_date_key", "is_missing_online_ship_date",
                "customer_key", "channel_key", "payment_key", "status",
                "total_amount", "total_discount", "net_amount", "total_cost",
            ],
            extra={"partition_by": "order_date", "cluster_by": ["status", "channel_key", "customer_key"]},
        ),
        _make_node(
            node_id="mart.fact_order_items",
            name="fact_order_items",
            layer="fact",
            asset_type="fact_table",
            zone=zone_b,
            pii_classification="INTERNAL",
            columns=[
                "order_item_key", "order_detail_id", "order_id", "order_key",
                "product_key", "product_version_key", "order_date_key",
                "quantity", "unit_price", "unit_cost", "discount_rate",
                "discount_amount", "line_total", "line_cost", "line_profit",
            ],
            extra={"partition_by": "order_date", "cluster_by": ["product_key", "order_key"]},
        ),
        _make_node(
            node_id="mart.fact_returns",
            name="fact_returns",
            layer="fact",
            asset_type="fact_table",
            zone=zone_b,
            pii_classification="INTERNAL",
            columns=[
                "return_key", "return_id", "order_id", "order_key",
                "returned_date_key", "returned_date", "returned_at",
                "return_lead_days", "is_temporal_anomaly", "reason",
            ],
            extra={"partition_by": "returned_date", "cluster_by": ["is_temporal_anomaly", "reason"]},
        ),
        _make_node(
            node_id="mart.mart_rfm_snapshot",
            name="mart_rfm_snapshot",
            layer="rfm_mart",
            asset_type="analytical_mart",
            zone=zone_b,
            pii_classification="PSEUDONYMIZED",
            columns=[
                "snapshot_date", "customer_key", "customer_id", "recency_days",
                "frequency_orders", "monetary_value", "r_score", "f_score",
                "m_score", "rfm_score", "rfm_segment",
            ],
            extra={
                "grain": "(snapshot_date, customer_key)",
                "partition_by": "snapshot_date",
                "cluster_by": ["rfm_segment", "customer_key"],
            },
        ),
    ]


def _mining_and_pbi_nodes() -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []
    for view_id, view_name, _upstream, desc in _BI_VIEWS:
        nodes.append(
            _make_node(
                node_id=view_id,
                name=view_name,
                layer="bi_view",
                asset_type="semantic_view",
                zone="Zone B - Governed Semantic Layer",
                pii_classification="NONE",
                extra={"description": desc},
            )
        )
    for mart_id, mart_name, upstream_nodes, _tx_type, desc in _MINING_MARTS:
        nodes.append(
            _make_node(
                node_id=mart_id,
                name=mart_name,
                layer="analytics_mining_mart",
                asset_type="analytics_mining_mart",
                zone="Zone B - Governed Analytics Mart",
                pii_classification="PSEUDONYMIZED" if "customer" in str(upstream_nodes) else "NONE",
                extra={"description": desc},
            )
        )
    for page_id, page_name, _upstream, preview_img, desc in _PBI_PAGES:
        nodes.append(
            _make_node(
                node_id=page_id,
                name=page_name,
                layer="bi_dashboard_page",
                asset_type="powerbi_report_page",
                zone="Zone B - Executive BI Consumption",
                pii_classification="NONE",
                extra={"preview_artifact": preview_img, "description": desc},
            )
        )
    return nodes


def _build_edges(tables_spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    edges: List[Dict[str, Any]] = []

    # Raw -> Staging (8 edges)
    for table_name, spec in tables_spec.items():
        pii_cols = [c["name"] for c in spec["columns"] if c.get("pii_classification") == "PII_RESTRICTED"]
        stg_name = spec["staging_table"]
        edges.append(
            _make_edge(
                source=f"raw.{table_name}",
                target=f"stg.{stg_name}",
                transformation_type="csv_bom_strip_and_contract_gate",
                pii_tags=["PII_RESTRICTED"] if pii_cols else ["PII_FREE"],
                description=(
                    f"UTF-8 BOM strip, pre-ingestion data contract check, and dead-letter quarantine routing "
                    f"from data/raw/{spec['file_name']} into {stg_name}."
                ),
            )
        )

    # Staging -> Dimensions (9 edges)
    edges.extend(
        [
            _make_edge(
                "stg.stg_customers",
                "mart.dim_customer",
                "pdpl_pii_strip_and_sha256_pseudonymization",
                ["PII_STRIPPED_PDPL_91_2025_QH15", "PSEUDONYMIZED"],
                (
                    "Removes first_name, last_name, email, phone, date_of_birth at the PDPL 91/2025/QH15 "
                    "trust boundary, generates deterministic SHA-256 customer_key, and masks display name."
                ),
                pdpl_boundary_crossing=True,
            ),
            _make_edge(
                "stg.stg_products", "mart.dim_product", "scd1_catalog_join", ["PII_FREE"],
                "Joins stg_products with category taxonomy into SCD Type 1 dim_product.",
            ),
            _make_edge(
                "stg.stg_product_categories", "mart.dim_product", "category_hierarchy_enrichment", ["PII_FREE"],
                "Denormalizes category and sub_category onto dim_product.",
            ),
            _make_edge(
                "stg.stg_products", "mart.dim_product_scd2", "scd2_price_history_timeline", ["PII_FREE"],
                "Provides base catalog attributes and initial pricing for dim_product_scd2.",
            ),
            _make_edge(
                "stg.stg_product_categories", "mart.dim_product_scd2", "category_hierarchy_enrichment", ["PII_FREE"],
                "Denormalizes category and sub_category onto every SCD Type 2 version.",
            ),
            _make_edge(
                "stg.stg_product_price_history", "mart.dim_product_scd2", "scd2_price_history_timeline", ["PII_FREE"],
                "Applies 24 MSRP/COST price transitions across 14 SKUs to build 71 contiguous SCD2 intervals.",
            ),
            _make_edge(
                "stg.stg_sales_channels", "mart.dim_channel", "sha256_dimension_upsert", ["PII_FREE"],
                "Derives channel_key and is_online flag (Web/App vs Physical stores).",
            ),
            _make_edge(
                "stg.stg_orders", "mart.dim_payment", "distinct_payment_extraction", ["PII_FREE"],
                "Extracts conformed payment_method dimension with Cash/Card/Digital category.",
            ),
            _make_edge(
                "stg.stg_orders", "mart.dim_date", "calendar_date_coverage_validation", ["PII_FREE"],
                "Ensures generated calendar dimension covers all order_date/ship_date values + sentinels (-1/-2/-3).",
            ),
        ]
    )

    # Staging & Dimensions -> Facts (13 edges)
    edges.append(
        _make_edge(
            "stg.stg_orders", "mart.fact_orders", "watermark_incremental_merge", ["PII_FREE"],
            "Watermark-driven incremental MERGE on updated_at with ship_date sentinel mapping (-1/-2/-3).",
        )
    )
    for dim_src in ("mart.dim_date", "mart.dim_customer", "mart.dim_channel", "mart.dim_payment"):
        edges.append(
            _make_edge(
                dim_src,
                "mart.fact_orders",
                "conformed_dimension_fk_binding",
                ["PSEUDONYMIZED" if dim_src == "mart.dim_customer" else "PII_FREE"],
                f"Binds conformed surrogate key from {dim_src} into fact_orders.",
            )
        )
    edges.extend(
        [
            _make_edge(
                "stg.stg_order_details", "mart.fact_order_items", "watermark_incremental_merge", ["PII_FREE"],
                "Loads order line items with exact VND financial reconciliation against fact_orders.",
            ),
            _make_edge(
                "mart.fact_orders", "mart.fact_order_items", "order_header_fk_alignment", ["PII_FREE"],
                "Resolves order_key and order_date_key for each order line item.",
            ),
            _make_edge(
                "mart.dim_product", "mart.fact_order_items", "conformed_dimension_fk_binding", ["PII_FREE"],
                "Binds current product_key to fact_order_items.",
            ),
            _make_edge(
                "mart.dim_product_scd2", "mart.fact_order_items", "point_in_time_scd2_fact_join", ["PII_FREE"],
                "Point-in-time range join on order_date IN [valid_from, valid_to) resolving product_version_key.",
            ),
            _make_edge(
                "mart.dim_date", "mart.fact_order_items", "conformed_dimension_fk_binding", ["PII_FREE"],
                "Binds order_date_key to conformed dim_date.",
            ),
            _make_edge(
                "stg.stg_order_returns", "mart.fact_returns", "temporal_anomaly_flag_and_quarantine", ["PII_FREE"],
                "Loads return events and flags returned_at < order_date rows (is_temporal_anomaly=TRUE).",
            ),
            _make_edge(
                "mart.fact_orders", "mart.fact_returns", "return_lead_time_calculation", ["PII_FREE"],
                "Joins returned orders to compute return_lead_days and detect temporal anomalies.",
            ),
            _make_edge(
                "mart.dim_date", "mart.fact_returns", "conformed_dimension_fk_binding", ["PII_FREE"],
                "Binds returned_date_key to conformed dim_date.",
            ),
        ]
    )

    # Facts & Dims -> RFM Mart (2 edges)
    edges.extend(
        [
            _make_edge(
                "mart.fact_orders", "mart.mart_rfm_snapshot", "ntile5_rfm_snapshot_aggregation", ["PSEUDONYMIZED"],
                "Computes Recency, Frequency, Monetary NTILE(5) quintiles on Completed orders per snapshot_date.",
            ),
            _make_edge(
                "mart.dim_customer", "mart.mart_rfm_snapshot", "pseudonymized_customer_cohort_join", ["PSEUDONYMIZED"],
                "Joins pseudonymized customer_key (zero raw PII) into RFM snapshots.",
            ),
        ]
    )

    # Mart -> BI Views (17 edges)
    for view_id, view_name, upstream_nodes, _desc in _BI_VIEWS:
        tx = "dq_audit_aggregation" if view_name == "vw_dq_executive_summary" else "semantic_kpi_view"
        for src in upstream_nodes:
            edges.append(_make_edge(src, view_id, tx, ["PII_FREE"], f"Feeds {view_name} from {src}."))

    # Mart -> Analytics Mining Marts (11 edges)
    for mart_id, mart_name, upstream_nodes, tx_type, _desc in _MINING_MARTS:
        for src in upstream_nodes:
            pii_tag = "PSEUDONYMIZED" if src in ("mart.dim_customer", "mart.mart_rfm_snapshot") else "PII_FREE"
            edges.append(_make_edge(src, mart_id, tx_type, [pii_tag], f"Feeds analytics mining mart {mart_name} from {src}."))

    # Semantic Views & Marts -> Power BI Pages (10 edges)
    for page_id, page_name, upstream_nodes, _img, _desc in _PBI_PAGES:
        for src in upstream_nodes:
            edges.append(
                _make_edge(
                    src, page_id, "powerbi_semantic_consumption", ["PII_FREE"],
                    f"Power BI visual query consuming {src} on {page_name}.",
                )
            )

    return edges


def build_lineage_manifest(
    contract: Dict[str, Any],
    validation_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the complete machine-readable 38-node / 70-edge data lineage DAG."""
    compliance = contract.get("compliance", {})
    prohibited_pii: List[str] = list(compliance.get("mart_prohibited_pii_columns", []))
    tables_spec: Dict[str, Any] = contract["tables"]

    nodes = (
        _raw_nodes(tables_spec, validation_summary)
        + _staging_nodes(tables_spec)
        + _mart_nodes(compliance)
        + _mining_and_pbi_nodes()
    )
    edges = _build_edges(tables_spec)

    layer_counts: Dict[str, int] = {}
    for n in nodes:
        layer = n["layer"]
        layer_counts[layer] = layer_counts.get(layer, 0) + 1

    raw_cust_node = next(n for n in nodes if n["id"] == "raw.customers")
    stg_cust_node = next(n for n in nodes if n["id"] == "stg.stg_customers")
    dim_cust_node = next(n for n in nodes if n["id"] == "mart.dim_customer")
    dim_cust_col_set = set(dim_cust_node.get("columns", []))
    leaked_pii = sorted(c for c in prohibited_pii if c in dim_cust_col_set)

    manifest: Dict[str, Any] = {
        "manifest_version": "1.0.0",
        "contract_version": contract["contract_version"],
        "generated_at": utc_now_iso_z(),
        "project": "viettech-bi-project",
        "pdpl_compliance": {
            "regulation": compliance.get("regulation"),
            "effective_date": compliance.get("effective_date"),
            "raw_customers_pii_restricted": raw_cust_node["pii_restricted_columns"],
            "stg_customers_pii_restricted": stg_cust_node["pii_restricted_columns"],
            "dim_customer_columns": dim_cust_node["columns"],
            "prohibited_pii_columns": prohibited_pii,
            "dim_customer_pii_violations": leaked_pii,
            "compliant": len(leaked_pii) == 0,
        },
        "governance_control_tables": [
            "dq_audit_results",
            "dq_quarantine_records",
            "etl_watermarks",
            "etl_run_audit",
            "retailbi_pii_vault.customer_pii_vault",
        ],
        "summary": {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "node_counts_by_layer": layer_counts,
        },
        "nodes": nodes,
        "edges": edges,
    }

    if validation_summary is not None:
        manifest["pre_ingestion_validation_snapshot"] = {
            "run_id": validation_summary.get("run_id"),
            "checked_at": validation_summary.get("checked_at"),
            "overall_status": validation_summary.get("overall_status"),
            "critical_gate_passed": validation_summary.get("critical_gate_passed"),
            "summary": validation_summary.get("summary"),
        }

    return manifest


def export_lineage_manifest(
    output_path: Path | str,
    contract: Optional[Dict[str, Any] | Path | str] = None,
    validation_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generate and write benchmarks/lineage_manifest.json."""
    contract_dict = contract if isinstance(contract, dict) else load_contract(contract)
    manifest = build_lineage_manifest(contract_dict, validation_summary=validation_summary)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest
