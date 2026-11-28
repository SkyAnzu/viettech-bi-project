"""Module 1: Market Basket Analysis (Association Rules: Support, Confidence, Lift > 1)."""

from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from viettech.common.utils import utc_now_iso
from viettech.mining.schema import ensure_mining_mart_tables


def mine_association_rules(
    baskets: Sequence[Iterable[str]],
    *,
    min_support: float = 0.005,
    min_co_occurrences: int = 30,
    labels: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Compute exact 2-itemset association rules from transaction baskets."""
    cleaned_baskets: List[Tuple[str, ...]] = []
    for basket in baskets:
        unique_items = sorted(set(basket))
        if unique_items:
            cleaned_baskets.append(tuple(unique_items))

    total_baskets = len(cleaned_baskets)
    if total_baskets == 0:
        return {
            "total_baskets": 0,
            "multi_item_baskets": 0,
            "multi_item_share_pct": 0.0,
            "distinct_items": 0,
            "candidate_pairs_evaluated": 0,
            "rules_passing_threshold": 0,
            "positive_lift_rules_count": 0,
            "min_support_threshold": min_support,
            "min_co_occurrences_threshold": min_co_occurrences,
            "rules": [],
            "positive_lift_rules": [],
            "popularity_trap_examples": [],
        }

    item_counts: Counter[str] = Counter()
    pair_counts: Counter[Tuple[str, str]] = Counter()
    multi_item_baskets = 0

    for items in cleaned_baskets:
        for item in items:
            item_counts[item] += 1
        if len(items) >= 2:
            multi_item_baskets += 1
            for a, b in combinations(items, 2):
                pair_counts[(a, b)] += 1

    label_map = labels or {}
    all_rules: List[Dict[str, Any]] = []

    for (a, b), co_count in pair_counts.items():
        support_ab = co_count / total_baskets
        if not (support_ab >= min_support or co_count >= min_co_occurrences):
            continue

        count_a = item_counts[a]
        count_b = item_counts[b]
        sup_a = count_a / total_baskets
        sup_b = count_b / total_baskets
        lift_ab = support_ab / (sup_a * sup_b)

        for ant, con, ant_cnt, con_cnt, con_sup in (
            (a, b, count_a, count_b, sup_b),
            (b, a, count_b, count_a, sup_a),
        ):
            confidence = co_count / ant_cnt
            all_rules.append(
                {
                    "antecedent": ant,
                    "antecedent_label": label_map.get(ant, ant),
                    "consequent": con,
                    "consequent_label": label_map.get(con, con),
                    "co_occurrences": co_count,
                    "antecedent_count": ant_cnt,
                    "consequent_count": con_cnt,
                    "total_baskets": total_baskets,
                    "support": round(support_ab, 6),
                    "confidence": round(confidence, 6),
                    "consequent_support": round(con_sup, 6),
                    "lift": round(lift_ab, 6),
                    "is_positive_lift": 1 if lift_ab > 1.0 else 0,
                }
            )

    all_rules.sort(
        key=lambda r: (
            -r["lift"],
            -r["confidence"],
            -r["co_occurrences"],
            r["antecedent"],
            r["consequent"],
        )
    )
    positive_rules = [r for r in all_rules if r["lift"] > 1.0]

    trap_candidates = [r for r in all_rules if r["lift"] <= 1.0]
    trap_candidates.sort(
        key=lambda r: (
            -r["confidence"],
            -r["co_occurrences"],
            r["lift"],
            r["antecedent"],
            r["consequent"],
        )
    )

    return {
        "total_baskets": total_baskets,
        "multi_item_baskets": multi_item_baskets,
        "multi_item_share_pct": round((multi_item_baskets / total_baskets) * 100.0, 2),
        "distinct_items": len(item_counts),
        "candidate_pairs_evaluated": len(pair_counts),
        "rules_passing_threshold": len(all_rules),
        "positive_lift_rules_count": len(positive_rules),
        "min_support_threshold": min_support,
        "min_co_occurrences_threshold": min_co_occurrences,
        "rules": all_rules,
        "positive_lift_rules": positive_rules,
        "popularity_trap_examples": trap_candidates[:5],
    }


def run_market_basket_module(
    conn: sqlite3.Connection,
    *,
    min_support: float = 0.005,
    min_co_occurrences: int = 30,
    top_n_persist: int = 50,
) -> Dict[str, Any]:
    """Mine SKU-level and Sub-Category-level association rules and persist to SQLite."""
    t0 = time.perf_counter()
    ensure_mining_mart_tables(conn)

    rows = conn.execute(
        """
        SELECT DISTINCT
            fo.order_key,
            dp.product_code,
            dp.product_name,
            dp.sub_category
        FROM fact_orders fo
        JOIN fact_order_items foi
          ON fo.order_key = foi.order_key
        JOIN dim_product dp
          ON foi.product_key = dp.product_key
        WHERE fo.status = 'Completed'
        """
    ).fetchall()

    sku_baskets: Dict[str, List[str]] = defaultdict(list)
    sub_baskets: Dict[str, List[str]] = defaultdict(list)
    sku_labels: Dict[str, str] = {}

    for row in rows:
        ok = str(row["order_key"])
        pcode = str(row["product_code"])
        pname = str(row["product_name"])
        sub = str(row["sub_category"])
        sku_baskets[ok].append(pcode)
        sub_baskets[ok].append(sub)
        sku_labels[pcode] = pname

    sku_res = mine_association_rules(
        list(sku_baskets.values()),
        min_support=min_support,
        min_co_occurrences=min_co_occurrences,
        labels=sku_labels,
    )
    sub_res = mine_association_rules(
        list(sub_baskets.values()),
        min_support=min_support,
        min_co_occurrences=min_co_occurrences,
        labels=None,
    )

    computed_at = utc_now_iso()
    sku_persist = sku_res["positive_lift_rules"][:top_n_persist]
    sub_persist = sub_res["positive_lift_rules"][:top_n_persist]

    with conn:
        conn.execute("DELETE FROM mart_market_basket_rules;")
        insert_rows = []
        for gran, rules_list in (("sku", sku_persist), ("sub_category", sub_persist)):
            for r in rules_list:
                insert_rows.append(
                    (
                        gran,
                        r["antecedent"],
                        r["antecedent_label"],
                        r["consequent"],
                        r["consequent_label"],
                        r["co_occurrences"],
                        r["antecedent_count"],
                        r["consequent_count"],
                        r["total_baskets"],
                        r["support"],
                        r["confidence"],
                        r["consequent_support"],
                        r["lift"],
                        r["is_positive_lift"],
                        computed_at,
                    )
                )
        conn.executemany(
            """
            INSERT INTO mart_market_basket_rules (
                granularity, antecedent, antecedent_label, consequent, consequent_label,
                co_occurrences, antecedent_count, consequent_count, total_baskets,
                support, confidence, consequent_support, lift, is_positive_lift, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            insert_rows,
        )

    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    return {
        "duration_ms": elapsed_ms,
        "persisted_rules_total": len(insert_rows),
        "persisted_sku_rules": len(sku_persist),
        "persisted_sub_category_rules": len(sub_persist),
        "sku_level": {
            "total_baskets": sku_res["total_baskets"],
            "multi_item_baskets": sku_res["multi_item_baskets"],
            "multi_item_share_pct": sku_res["multi_item_share_pct"],
            "distinct_items": sku_res["distinct_items"],
            "candidate_pairs_evaluated": sku_res["candidate_pairs_evaluated"],
            "rules_passing_threshold": sku_res["rules_passing_threshold"],
            "positive_lift_rules_count": sku_res["positive_lift_rules_count"],
            "top_rules": sku_res["positive_lift_rules"][:15],
            "popularity_trap_examples": sku_res["popularity_trap_examples"],
        },
        "sub_category_level": {
            "total_baskets": sub_res["total_baskets"],
            "multi_item_baskets": sub_res["multi_item_baskets"],
            "multi_item_share_pct": sub_res["multi_item_share_pct"],
            "distinct_items": sub_res["distinct_items"],
            "candidate_pairs_evaluated": sub_res["candidate_pairs_evaluated"],
            "rules_passing_threshold": sub_res["rules_passing_threshold"],
            "positive_lift_rules_count": sub_res["positive_lift_rules_count"],
            "top_rules": sub_res["positive_lift_rules"][:15],
            "popularity_trap_examples": sub_res["popularity_trap_examples"],
        },
    }
