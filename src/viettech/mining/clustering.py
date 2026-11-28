"""Module 2: Unsupervised K-Means++ Clustering (log1p + Z-Score) vs. RFM NTILE(5)."""

from __future__ import annotations

from collections import Counter, defaultdict
import math
import random
import sqlite3
import statistics
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from viettech.common.utils import utc_now_iso
from viettech.mining.schema import (
    DEFAULT_SEED,
    DEFAULT_SNAPSHOT_DATE,
    ensure_mining_mart_tables,
)


def log1p_zscore_scale(
    matrix: Sequence[Sequence[float]],
    feature_names: Sequence[str] = ("recency_days", "frequency_orders", "monetary_value"),
) -> Tuple[List[List[float]], List[float], List[float], Dict[str, Any]]:
    """Apply log1p followed by population Z-score standardization per feature."""
    n = len(matrix)
    if n == 0:
        raise ValueError("Cannot scale an empty feature matrix.")
    d = len(matrix[0])

    raw_means = [sum(row[j] for row in matrix) / n for j in range(d)]
    raw_vars = [
        sum((row[j] - raw_means[j]) ** 2 for row in matrix) / n for j in range(d)
    ]
    total_raw_var = sum(raw_vars) or 1.0
    raw_var_share_pct = [(v / total_raw_var) * 100.0 for v in raw_vars]

    logged = [[math.log1p(max(0.0, float(row[j]))) for j in range(d)] for row in matrix]

    means = [sum(row[j] for row in logged) / n for j in range(d)]
    stds: List[float] = []
    for j in range(d):
        var = sum((row[j] - means[j]) ** 2 for row in logged) / n
        stds.append(math.sqrt(var) if var > 1e-12 else 1.0)

    scaled = [[(row[j] - means[j]) / stds[j] for j in range(d)] for row in logged]

    scaled_means = [sum(row[j] for row in scaled) / n for j in range(d)]
    scaled_vars = [
        sum((row[j] - scaled_means[j]) ** 2 for row in scaled) / n for j in range(d)
    ]
    total_scaled_var = sum(scaled_vars) or 1.0
    scaled_var_share_pct = [(v / total_scaled_var) * 100.0 for v in scaled_vars]

    diagnostics = {
        "feature_names": list(feature_names),
        "raw_means": [round(m, 4) for m in raw_means],
        "raw_stds": [round(math.sqrt(v), 4) for v in raw_vars],
        "raw_variance_share_pct": [round(s, 8) for s in raw_var_share_pct],
        "log1p_means": [round(m, 6) for m in means],
        "log1p_stds": [round(s, 6) for s in stds],
        "scaled_means": [round(m, 10) for m in scaled_means],
        "scaled_stds": [round(math.sqrt(v), 10) for v in scaled_vars],
        "scaled_variance_share_pct": [round(s, 4) for s in scaled_var_share_pct],
    }
    return scaled, means, stds, diagnostics


def _sq_dist(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) == 3:
        d0 = a[0] - b[0]
        d1 = a[1] - b[1]
        d2 = a[2] - b[2]
        return d0 * d0 + d1 * d1 + d2 * d2
    total = 0.0
    for x, y in zip(a, b, strict=True):
        diff = x - y
        total += diff * diff
    return total


def _kmeans_plus_plus_init(
    X: Sequence[Sequence[float]],
    k: int,
    rng: random.Random,
) -> List[List[float]]:
    n = len(X)
    d = len(X[0])
    first_idx = rng.randrange(n)
    c0 = list(X[first_idx])
    centroids = [c0]
    if d == 3:
        cx, cy, cz = c0
        min_d2 = [
            (p[0] - cx) ** 2 + (p[1] - cy) ** 2 + (p[2] - cz) ** 2
            for p in X
        ]
    else:
        min_d2 = [_sq_dist(p, c0) for p in X]

    for _ in range(1, k):
        total_d2 = sum(min_d2)
        if total_d2 <= 1e-15:
            next_idx = rng.randrange(n)
        else:
            threshold = rng.random() * total_d2
            cumulative = 0.0
            next_idx = n - 1
            for idx, d2 in enumerate(min_d2):
                cumulative += d2
                if cumulative >= threshold:
                    next_idx = idx
                    break
        new_c = list(X[next_idx])
        centroids.append(new_c)
        if d == 3:
            nx, ny, nz = new_c
            for idx, p in enumerate(X):
                dx = p[0] - nx
                dy = p[1] - ny
                dz = p[2] - nz
                d2 = dx * dx + dy * dy + dz * dz
                if d2 < min_d2[idx]:
                    min_d2[idx] = d2
        else:
            for idx, p in enumerate(X):
                d2 = _sq_dist(p, new_c)
                if d2 < min_d2[idx]:
                    min_d2[idx] = d2
    return centroids


def kmeans_plus_plus(
    X: Sequence[Sequence[float]],
    k: int,
    *,
    seed: int = DEFAULT_SEED,
    n_init: int = 5,
    max_iter: int = 80,
    tol: float = 1e-5,
) -> Dict[str, Any]:
    """Deterministic pure-Python K-Means++ clustering with multiple restarts."""
    n = len(X)
    if n < k or k < 1:
        raise ValueError(f"Invalid k={k} for n={n} samples.")
    d = len(X[0])
    tol_sq = tol * tol

    best_result: Optional[Dict[str, Any]] = None

    for restart in range(n_init):
        rng = random.Random(seed + k * 100 + restart * 17)
        centroids = _kmeans_plus_plus_init(X, k, rng)
        labels = [0] * n
        point_d2 = [0.0] * n
        iters_used = 0

        for it in range(1, max_iter + 1):
            iters_used = it
            changed = False
            counts = [0] * k

            if d == 3:
                sx = [0.0] * k
                sy = [0.0] * k
                sz = [0.0] * k
                cx_list = [c[0] for c in centroids]
                cy_list = [c[1] for c in centroids]
                cz_list = [c[2] for c in centroids]

                for i, p in enumerate(X):
                    px, py, pz = p[0], p[1], p[2]
                    dx = px - cx_list[0]
                    dy = py - cy_list[0]
                    dz = pz - cz_list[0]
                    best_d2 = dx * dx + dy * dy + dz * dz
                    best_c = 0
                    for c_idx in range(1, k):
                        dx = px - cx_list[c_idx]
                        dy = py - cy_list[c_idx]
                        dz = pz - cz_list[c_idx]
                        d2 = dx * dx + dy * dy + dz * dz
                        if d2 < best_d2:
                            best_d2 = d2
                            best_c = c_idx
                    if labels[i] != best_c:
                        labels[i] = best_c
                        changed = True
                    point_d2[i] = best_d2
                    counts[best_c] += 1
                    sx[best_c] += px
                    sy[best_c] += py
                    sz[best_c] += pz

                max_shift_sq = 0.0
                new_centroids: List[List[float]] = []
                for c_idx in range(k):
                    cnt = counts[c_idx]
                    if cnt == 0:
                        farthest_i = max(range(n), key=point_d2.__getitem__)
                        nc = list(X[farthest_i])
                    else:
                        inv = 1.0 / cnt
                        nc = [sx[c_idx] * inv, sy[c_idx] * inv, sz[c_idx] * inv]
                    dx = nc[0] - cx_list[c_idx]
                    dy = nc[1] - cy_list[c_idx]
                    dz = nc[2] - cz_list[c_idx]
                    shift_sq = dx * dx + dy * dy + dz * dz
                    if shift_sq > max_shift_sq:
                        max_shift_sq = shift_sq
                    new_centroids.append(nc)
            else:
                sums = [[0.0] * d for _ in range(k)]
                for i, p in enumerate(X):
                    best_c = 0
                    best_d2 = _sq_dist(p, centroids[0])
                    for c_idx in range(1, k):
                        d2 = _sq_dist(p, centroids[c_idx])
                        if d2 < best_d2:
                            best_d2 = d2
                            best_c = c_idx
                    if labels[i] != best_c:
                        labels[i] = best_c
                        changed = True
                    point_d2[i] = best_d2
                    counts[best_c] += 1
                    s_row = sums[best_c]
                    for j in range(d):
                        s_row[j] += p[j]

                max_shift_sq = 0.0
                new_centroids = []
                for c_idx in range(k):
                    if counts[c_idx] == 0:
                        farthest_i = max(range(n), key=point_d2.__getitem__)
                        nc = list(X[farthest_i])
                    else:
                        inv = 1.0 / counts[c_idx]
                        nc = [sums[c_idx][j] * inv for j in range(d)]
                    shift_sq = _sq_dist(nc, centroids[c_idx])
                    if shift_sq > max_shift_sq:
                        max_shift_sq = shift_sq
                    new_centroids.append(nc)

            centroids = new_centroids
            if not changed or max_shift_sq <= tol_sq:
                break

        wcss = 0.0
        distances = [0.0] * n
        for i in range(n):
            d2 = _sq_dist(X[i], centroids[labels[i]])
            wcss += d2
            distances[i] = math.sqrt(d2)

        if best_result is None or wcss < best_result["wcss"]:
            best_result = {
                "k": k,
                "labels": labels,
                "centroids": centroids,
                "wcss": wcss,
                "iterations": iters_used,
                "distances": distances,
            }

    assert best_result is not None
    return best_result


def compute_silhouette_score(
    X: Sequence[Sequence[float]],
    labels: Sequence[int],
    *,
    sample_size: int = 600,
    seed: int = DEFAULT_SEED,
) -> Tuple[float, int, str]:
    """Compute exact (or deterministic stratified-sampled) Euclidean Silhouette Score."""
    n = len(X)
    by_cluster: Dict[int, List[int]] = defaultdict(list)
    for idx, lab in enumerate(labels):
        by_cluster[lab].append(idx)

    if len(by_cluster) < 2 or n < 3:
        return 0.0, n, "exact"

    if n <= sample_size:
        sampled_indices = list(range(n))
        method = "exact_full"
    else:
        rng = random.Random(seed)
        sampled_indices = []
        cluster_ids = sorted(by_cluster.keys())
        alloc: Dict[int, int] = {}
        for cid in cluster_ids:
            c_len = len(by_cluster[cid])
            alloc[cid] = max(min(2, c_len), int(round(sample_size * (c_len / n))))
        while sum(alloc.values()) > sample_size:
            largest = max(cluster_ids, key=lambda c: alloc[c])
            if alloc[largest] <= 2:
                break
            alloc[largest] -= 1
        while sum(alloc.values()) < sample_size:
            candidates = [c for c in cluster_ids if alloc[c] < len(by_cluster[c])]
            if not candidates:
                break
            chosen = max(candidates, key=lambda c: len(by_cluster[c]) - alloc[c])
            alloc[chosen] += 1

        for cid in cluster_ids:
            indices = list(by_cluster[cid])
            rng.shuffle(indices)
            sampled_indices.extend(indices[: alloc[cid]])
        method = f"stratified_exact_n{len(sampled_indices)}"

    m = len(sampled_indices)
    sub_X = [X[i] for i in sampled_indices]
    sub_labels = [labels[i] for i in sampled_indices]

    sample_clusters: Dict[int, List[int]] = defaultdict(list)
    for pos, lab in enumerate(sub_labels):
        sample_clusters[lab].append(pos)

    if len(sample_clusters) < 2:
        return 0.0, m, method

    dist_mat = [[0.0] * m for _ in range(m)]
    for i in range(m):
        xi = sub_X[i]
        row_i = dist_mat[i]
        for j in range(i + 1, m):
            d_ij = math.sqrt(_sq_dist(xi, sub_X[j]))
            row_i[j] = d_ij
            dist_mat[j][i] = d_ij

    sil_sum = 0.0
    for i in range(m):
        ci = sub_labels[i]
        own_members = sample_clusters[ci]
        if len(own_members) <= 1:
            continue
        row_i = dist_mat[i]
        a_i = sum(row_i[j] for j in own_members if j != i) / (len(own_members) - 1)
        b_i = float("inf")
        for other_c, members in sample_clusters.items():
            if other_c == ci:
                continue
            mean_d = sum(row_i[j] for j in members) / len(members)
            if mean_d < b_i:
                b_i = mean_d
        denom = max(a_i, b_i)
        sil_sum += (b_i - a_i) / denom if denom > 1e-12 else 0.0

    return sil_sum / m, m, method


def _assign_cluster_business_labels(cluster_stats: List[Dict[str, Any]]) -> Dict[int, str]:
    """Deterministically map cluster IDs to clear business persona labels."""
    scored = []
    for st in cluster_stats:
        rz, fz, mz = st["centroid_z"]
        health = (-rz) + fz + mz
        scored.append((st["cluster_id"], rz, fz, mz, health))

    labels_by_id: Dict[int, str] = {}
    used_names: set[str] = set()
    ranked = sorted(scored, key=lambda t: (-t[4], t[0]))
    for rank_idx, (cid, rz, fz, mz, _health) in enumerate(ranked):
        if rank_idx == 0 and fz > 0.3 and mz > 0.3 and rz < 0.0:
            name = "VIP High-Value Active"
        elif rz > 0.5 and mz < -1.0:
            name = "Churned Low-Spend One-Time"
        elif rz > 0.5 and mz >= -1.0:
            name = "Dormant Mid-Value Lapsed"
        elif rz > 0.2 and (fz >= 0.0 or mz >= 0.0):
            name = "Lapsing Mid/High-Value At-Risk"
        elif rz <= 0.0 and fz < 0.0 and mz < 0.0:
            name = "Recent Low-Spend / Emerging"
        elif rz <= 0.0 and (fz >= 0.0 or mz >= 0.0):
            name = "Core Repeat Active"
        elif mz < -0.5:
            name = "Low-Spend Occasional"
        else:
            name = "Mid-Tier Balanced"

        if name in used_names:
            name = f"{name} ({rank_idx + 1})"
        used_names.add(name)
        labels_by_id[cid] = name

    return labels_by_id


def _compute_contingency_and_nmi(
    cluster_labels: Sequence[str],
    rfm_segments: Sequence[str],
) -> Tuple[Dict[str, Dict[str, int]], float, float]:
    """Compute contingency matrix (cluster x rfm_segment), Purity, and NMI."""
    n = len(cluster_labels)
    matrix: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    c_counts: Counter[str] = Counter()
    s_counts: Counter[str] = Counter()

    for c, s in zip(cluster_labels, rfm_segments, strict=True):
        matrix[c][s] += 1
        c_counts[c] += 1
        s_counts[s] += 1

    purity_hits = sum(max(seg_map.values()) for seg_map in matrix.values())
    purity = purity_hits / n if n > 0 else 0.0

    mi = 0.0
    for c, seg_map in matrix.items():
        for s, count in seg_map.items():
            if count > 0:
                p_cs = count / n
                p_c = c_counts[c] / n
                p_s = s_counts[s] / n
                mi += p_cs * math.log(p_cs / (p_c * p_s))

    h_c = -sum((cnt / n) * math.log(cnt / n) for cnt in c_counts.values() if cnt > 0)
    h_s = -sum((cnt / n) * math.log(cnt / n) for cnt in s_counts.values() if cnt > 0)
    nmi = (2.0 * mi / (h_c + h_s)) if (h_c + h_s) > 1e-12 else 0.0

    clean_matrix = {c: dict(sorted(seg_map.items())) for c, seg_map in sorted(matrix.items())}
    return clean_matrix, round(purity, 4), round(nmi, 4)


def run_rfm_kmeans_module(
    conn: sqlite3.Connection,
    *,
    snapshot_date: str = DEFAULT_SNAPSHOT_DATE,
    k_values: Sequence[int] = (3, 4, 5, 6),
    seed: int = DEFAULT_SEED,
    silhouette_sample_size: int = 600,
) -> Dict[str, Any]:
    """Run log1p+Z-score K-Means++ on mart_rfm_snapshot and persist best k."""
    t0 = time.perf_counter()
    ensure_mining_mart_tables(conn)

    rows = conn.execute(
        """
        SELECT
            customer_key, customer_id, recency_days, frequency_orders,
            monetary_value, r_score, f_score, m_score, rfm_score, rfm_segment
        FROM mart_rfm_snapshot
        WHERE snapshot_date = ?
        ORDER BY customer_id ASC
        """,
        (snapshot_date,),
    ).fetchall()

    if not rows:
        raise ValueError(f"No rows found in mart_rfm_snapshot for snapshot_date={snapshot_date}")

    raw_matrix = [
        [float(r["recency_days"]), float(r["frequency_orders"]), float(r["monetary_value"])]
        for r in rows
    ]
    scaled_X, _log_means, _log_stds, scaling_diag = log1p_zscore_scale(raw_matrix)

    k_evaluations: List[Dict[str, Any]] = []
    best_model: Optional[Dict[str, Any]] = None
    best_sil = -2.0
    sil_method_used = ""
    sil_sample_n = 0

    for k in k_values:
        km = kmeans_plus_plus(scaled_X, k=k, seed=seed, n_init=5, max_iter=100)
        sil, s_n, s_method = compute_silhouette_score(
            scaled_X, km["labels"], sample_size=silhouette_sample_size, seed=seed
        )
        sil_method_used = s_method
        sil_sample_n = s_n
        size_counts = Counter(km["labels"])
        k_evaluations.append(
            {
                "k": k,
                "silhouette_score": round(sil, 4),
                "wcss": round(km["wcss"], 2),
                "iterations": km["iterations"],
                "cluster_sizes": [size_counts[c] for c in range(k)],
            }
        )
        if sil > best_sil + 1e-9 or (
            abs(sil - best_sil) <= 1e-9
            and best_model is not None
            and km["wcss"] < best_model["wcss"]
        ):
            best_sil = sil
            best_model = km

    assert best_model is not None
    best_k = int(best_model["k"])
    labels: List[int] = list(best_model["labels"])
    centroids: List[List[float]] = list(best_model["centroids"])
    distances: List[float] = list(best_model["distances"])

    raw_order = sorted(
        range(best_k),
        key=lambda c: (-((-centroids[c][0]) + centroids[c][1] + centroids[c][2]), c),
    )
    remap = {old_c: new_c for new_c, old_c in enumerate(raw_order)}
    labels = [remap[c] for c in labels]
    centroids = [centroids[old_c] for old_c in raw_order]

    total_customers = len(rows)
    total_monetary_all = sum(float(r["monetary_value"]) for r in rows) or 1.0

    cluster_buckets: Dict[int, List[int]] = defaultdict(list)
    for idx, cid in enumerate(labels):
        cluster_buckets[cid].append(idx)

    cluster_profiles: List[Dict[str, Any]] = []
    for cid in range(best_k):
        idxs = cluster_buckets[cid]
        cnt = len(idxs)
        rec_vals = [float(rows[i]["recency_days"]) for i in idxs]
        freq_vals = [float(rows[i]["frequency_orders"]) for i in idxs]
        mon_vals = [float(rows[i]["monetary_value"]) for i in idxs]
        tot_mon = sum(mon_vals)
        cluster_profiles.append(
            {
                "cluster_id": cid,
                "customer_count": cnt,
                "customer_share_pct": round((cnt / total_customers) * 100.0, 2),
                "avg_recency_days": round(statistics.fmean(rec_vals), 1),
                "median_recency_days": round(statistics.median(rec_vals), 1),
                "avg_frequency_orders": round(statistics.fmean(freq_vals), 2),
                "avg_monetary_vnd": round(statistics.fmean(mon_vals), 0),
                "median_monetary_vnd": round(statistics.median(mon_vals), 0),
                "total_monetary_vnd": round(tot_mon, 0),
                "revenue_share_pct": round((tot_mon / total_monetary_all) * 100.0, 2),
                "centroid_z": [round(v, 4) for v in centroids[cid]],
            }
        )

    label_names = _assign_cluster_business_labels(cluster_profiles)
    for prof in cluster_profiles:
        prof["cluster_label"] = label_names[prof["cluster_id"]]

    assigned_label_strings = [f"C{c}: {label_names[c]}" for c in labels]
    rfm_seg_strings = [str(r["rfm_segment"]) for r in rows]
    contingency, purity, nmi = _compute_contingency_and_nmi(
        assigned_label_strings, rfm_seg_strings
    )

    computed_at = utc_now_iso()
    with conn:
        conn.execute(
            "DELETE FROM mart_rfm_kmeans_clusters WHERE snapshot_date = ?;",
            (snapshot_date,),
        )
        conn.executemany(
            """
            INSERT INTO mart_rfm_kmeans_clusters (
                snapshot_date, customer_key, customer_id,
                recency_days, frequency_orders, monetary_value,
                r_log_z, f_log_z, m_log_z,
                rfm_segment, kmeans_k, cluster_id, cluster_label,
                distance_to_centroid, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    snapshot_date,
                    rows[i]["customer_key"],
                    int(rows[i]["customer_id"]),
                    int(rows[i]["recency_days"]),
                    int(rows[i]["frequency_orders"]),
                    float(rows[i]["monetary_value"]),
                    round(scaled_X[i][0], 6),
                    round(scaled_X[i][1], 6),
                    round(scaled_X[i][2], 6),
                    str(rows[i]["rfm_segment"]),
                    best_k,
                    labels[i],
                    label_names[labels[i]],
                    round(distances[i], 6),
                    computed_at,
                )
                for i in range(total_customers)
            ],
        )

    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    return {
        "duration_ms": elapsed_ms,
        "snapshot_date": snapshot_date,
        "customers_clustered": total_customers,
        "seed": seed,
        "silhouette_method": sil_method_used,
        "silhouette_sample_size": sil_sample_n,
        "scaling_diagnostics": scaling_diag,
        "k_evaluations": k_evaluations,
        "selected_k": best_k,
        "selected_silhouette_score": round(best_sil, 4),
        "selected_wcss": round(best_model["wcss"], 2),
        "cluster_profiles": cluster_profiles,
        "rfm_contingency_matrix": contingency,
        "rfm_alignment_purity": purity,
        "rfm_alignment_nmi": nmi,
    }


run_kmeans_module = run_rfm_kmeans_module
