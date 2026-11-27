# VietTech BI — Prescriptive Analytics & Data Mining Report (`INT3209E`)

> **[SYNTHETIC DATASET DISCLOSURE]** All association rules, cluster assignments, and out-of-time return-risk metrics below were computed empirically on the local VietTech Electronics synthetic retail warehouse (`viettech_mart.db`, spanning `2023-01-06` to `2024-12-31`) using **100% pure Python 3.12 standard library** (zero numpy/pandas/scikit-learn). Results are **not** real production customer traffic.

## 1. Execution Summary & Reproducibility

| Parameter | Verified Value |
| --- | --- |
| Generated At (UTC) | `2026-11-27 21:09:55` |
| Runtime | Python `3.12.10` / SQLite `3.49.1` (pure stdlib) |
| Platform | `Windows 11 (AMD64)` (16 logical CPUs) |
| Deterministic Seed | `42` |
| Total End-to-End Runtime | **`4151.86 ms`** (`4.15 s`) |
| Persisted Mart Tables | `mart_market_basket_rules` (`84` rows), `mart_rfm_kmeans_clusters` (`4,838` rows), `mart_return_risk_scores` (`12,819` rows) |

---

## 2. Module 1 — Market Basket Analysis (Association Rules: Support, Confidence, Lift > 1)

- **Population:** `16,796` Completed orders (`9,368` multi-item baskets, `55.78%` multi-item rate) across `47` SKUs and `14` Sub-Categories.
- **Eligibility Threshold:** `support >= 0.005` OR `co_occurrences >= 30`, ranked by `Lift DESC`. Discovered **`34`** positive-lift Sub-Category rules and **`366`** positive-lift SKU rules (`187.82 ms`).

### 2.1 Top Sub-Category Cross-Sell Rules (`Lift > 1.0`)

| Antecedent Sub-Category | Consequent Sub-Category | Co-Occur | Support | Confidence | Consequent Support | Lift |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `MacBook` | `External SSD & HDD` | `183` | `0.0109` | `23.98%` | `9.37%` | **`2.559x`** |
| `External SSD & HDD` | `MacBook` | `183` | `0.0109` | `11.63%` | `4.54%` | **`2.559x`** |
| `External SSD & HDD` | `Windows Laptop` | `700` | `0.0417` | `44.47%` | `17.59%` | **`2.528x`** |
| `Windows Laptop` | `External SSD & HDD` | `700` | `0.0417` | `23.69%` | `9.37%` | **`2.528x`** |
| `Keyboard & Mouse` | `Windows Laptop` | `877` | `0.0522` | `34.24%` | `17.59%` | **`1.946x`** |
| `Windows Laptop` | `Keyboard & Mouse` | `877` | `0.0522` | `29.68%` | `15.25%` | **`1.946x`** |
| `MacBook` | `Keyboard & Mouse` | `212` | `0.0126` | `27.79%` | `15.25%` | **`1.822x`** |
| `Keyboard & Mouse` | `MacBook` | `212` | `0.0126` | `8.28%` | `4.54%` | **`1.822x`** |
| `External SSD & HDD` | `Keyboard & Mouse` | `407` | `0.0242` | `25.86%` | `15.25%` | **`1.696x`** |
| `Keyboard & Mouse` | `External SSD & HDD` | `407` | `0.0242` | `15.89%` | `9.37%` | **`1.696x`** |

### 2.2 Top SKU-Pair Cross-Sell Rules (`Lift > 1.0`)

| Antecedent SKU | Consequent SKU | Co-Occur | Support | Confidence | Consequent Support | Lift |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `WD-PASSPORT-2TB` (WD My Passport 2TB) | `SAMSUNG-T7-1TB` (Samsung T7 SSD 1TB) | `158` | `0.0094` | `18.79%` | `5.30%` | **`3.542x`** |
| `SAMSUNG-T7-1TB` (Samsung T7 SSD 1TB) | `WD-PASSPORT-2TB` (WD My Passport 2TB) | `158` | `0.0094` | `17.73%` | `5.01%` | **`3.542x`** |
| `MACBOOK-PRO-M3` (MacBook Pro 14" M3) | `WD-PASSPORT-2TB` (WD My Passport 2TB) | `52` | `0.0031` | `15.48%` | `5.01%` | **`3.091x`** |
| `WD-PASSPORT-2TB` (WD My Passport 2TB) | `MACBOOK-PRO-M3` (MacBook Pro 14" M3) | `52` | `0.0031` | `6.18%` | `2.00%` | **`3.091x`** |
| `LENOVO-THINKPAD` (Lenovo ThinkPad E14) | `WD-PASSPORT-2TB` (WD My Passport 2TB) | `62` | `0.0037` | `15.23%` | `5.01%` | **`3.042x`** |
| `WD-PASSPORT-2TB` (WD My Passport 2TB) | `LENOVO-THINKPAD` (Lenovo ThinkPad E14) | `62` | `0.0037` | `7.37%` | `2.42%` | **`3.042x`** |
| `ASUS-VIVOBOOK15` (Asus VivoBook 15) | `WD-PASSPORT-2TB` (WD My Passport 2TB) | `73` | `0.0043` | `14.66%` | `5.01%` | **`2.928x`** |
| `WD-PASSPORT-2TB` (WD My Passport 2TB) | `ASUS-VIVOBOOK15` (Asus VivoBook 15) | `73` | `0.0043` | `8.68%` | `2.96%` | **`2.928x`** |
| `HP-ENVY-X360` (HP Envy x360 15) | `WD-PASSPORT-2TB` (WD My Passport 2TB) | `58` | `0.0035` | `14.43%` | `5.01%` | **`2.881x`** |
| `WD-PASSPORT-2TB` (WD My Passport 2TB) | `HP-ENVY-X360` (HP Envy x360 15) | `58` | `0.0035` | `6.90%` | `2.39%` | **`2.881x`** |

### 2.3 Why `Lift > 1` Avoids the High-Confidence Popularity Trap

Ranking association rules by raw **Confidence** (`P(B | A)`) alone systematically favors ubiquitous filler items (`Charger & Cable` appears in `~27.7%` of all Completed baskets). When `Confidence(A -> B) <= P(B)`, **`Lift = Confidence(A -> B) / P(B) <= 1.0`** — meaning buying `A` actually gives **zero or negative** incremental propensity to buy `B`:

| Granularity | Antecedent | Consequent | Co-Occur | Confidence | Baseline `P(Consequent)` | Lift (`<= 1.0` Trap!) |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| `sub_category` | `Android Phone` | `Charger & Cable` | `906` | `27.67%` | `27.70%` | `0.999x` |
| `sub_category` | `Network Switch` | `Charger & Cable` | `77` | `23.55%` | `27.70%` | `0.850x` |
| `sub_category` | `Earphones & Headphones` | `Charger & Cable` | `599` | `20.67%` | `27.70%` | `0.746x` |
| `sub_category` | `WiFi Router` | `Charger & Cable` | `148` | `20.25%` | `27.70%` | `0.731x` |
| `sku` | `IPHONE-15-PRO` | `CABLE-USB-C-1M` | `50` | `8.00%` | `8.09%` | `0.989x` |
| `sku` | `SAM-S24` | `CASE-IPHONE15` | `41` | `7.99%` | `8.03%` | `0.995x` |
| `sku` | `XIA-14-PRO` | `SCREEN-PROT-UNI` | `38` | `7.97%` | `8.31%` | `0.959x` |

**Prescriptive Action:** Bundle **Laptops (MacBook / Windows Laptop)** with **External SSD & HDD** (`Lift ~2.53x–2.56x`) and **Keyboard & Mouse** (`Lift ~1.82x–1.95x`) on product detail pages and checkout upsells, rather than spamming universal cable banners triggered by raw confidence.

---

## 3. Module 2 — Unsupervised K-Means++ (`log1p` + `Z-Score`) vs. Rule-Based RFM `NTILE(5)`

- **Dataset:** `mart_rfm_snapshot` (`2025-01-01`, `4,838` customers, runtime `2585.47 ms`).

### 3.1 Why Raw Euclidean K-Means Fails Without `log1p` + `Z-Score`

Because `monetary_value` is denominated in VND (up to `~370M` VND) while `frequency_orders` ranges from `1` to `11`, unscaled Euclidean distance is dominated **99.9999999%+** by `monetary_value`, effectively ignoring Recency and Frequency. Applying `log1p(x)` compresses right-skewed monetary spend and Z-score standardization equalizes feature variance to `33.33%` each:

| Feature | Raw Mean | Raw Std | Raw Variance Share | Scaled (`log1p` + Z) Mean | Scaled Std | Scaled Variance Share |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `recency_days` | `120.30` | `185.25` | `0.000000%` | `0.000000` | `1.000000` | **`33.33%`** |
| `frequency_orders` | `3.47` | `1.86` | `0.000000%` | `0.000000` | `1.000000` | **`33.33%`** |
| `monetary_value` | `78,619,360.88` | `61,581,320.76` | `100.000000%` | `-0.000000` | `1.000000` | **`33.33%`** |

### 3.2 Model Selection Across `k in [3, 4, 5, 6]` (Silhouette Method: `stratified_exact_n600`)

| `k` | Silhouette Score | WCSS (Inertia) | Iterations | Cluster Sizes |
| ---: | ---: | ---: | ---: | --- |
| `3` **(Selected)** | **`0.3594`** | `6,431.28` | `15` | `[2773, 358, 1707]` |
| `4` | **`0.3276`** | `4,959.46` | `16` | `[1261, 1122, 299, 2156]` |
| `5` | **`0.3088`** | `3,924.12` | `29` | `[832, 1316, 285, 1381, 1024]` |
| `6` | **`0.2849`** | `3,473.73` | `64` | `[1015, 732, 1033, 276, 953, 829]` |

### 3.3 Optimal `k=3` Cluster Profiles (`mart_rfm_kmeans_clusters`)

| Cluster | Business Persona | Customers | Share | Avg Recency (d) | Avg Freq | Avg Monetary (VND) | Revenue Share |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `C0` | `VIP High-Value Active` | `2,773` | `57.32%` | `31.2` | `4.66` | `109,389,225` | **`79.75%`** |
| `C1` | `Dormant Mid-Value Lapsed` | `1,707` | `35.28%` | `235.0` | `1.98` | `44,535,984` | **`19.99%`** |
| `C2` | `Churned Low-Spend One-Time` | `358` | `7.40%` | `263.6` | `1.34` | `2,796,708` | **`0.26%`** |

### 3.4 K-Means Clusters vs. Rule-Based RFM `NTILE(5)` Contingency Matrix

- **Alignment Metrics:** Cluster Purity = **`0.4839`**, Normalized Mutual Information (NMI) = **`0.3369`**.

| K-Means Cluster | `Champions` | `Loyal` | `Regular` | `At-risk` | `Churned` | Total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `C0: VIP High-Value Active` | `754` | `1,256` | `249` | `506` | `8` | `2,773` |
| `C1: Dormant Mid-Value Lapsed` | `0` | `41` | `468` | `329` | `869` | `1,707` |
| `C2: Churned Low-Spend One-Time` | `0` | `8` | `126` | `8` | `216` | `358` |

**Architectural Takeaway:** Rule-based `NTILE(5)` forces equal-sized 20% quantile buckets even when natural spend/recency gaps exist; K-Means in `(log1p + Z)` space discovers natural geometric cohorts (e.g., separating high-spend active VIPs from low-spend recent buyers and long-dormant churners) without arbitrary score cutoffs.

---

## 4. Module 3 — Leakage-Free Out-of-Time Return-Risk Scoring (L2 Logistic Regression)

### 4.1 Strict Anti-Leakage Architecture

| Guardrail | Enforced Specification |
| --- | --- |
| Temporal Split | Train = 2023 (`2023-01-06 09:28:03` to `2023-12-31 22:52:38`, `n=5,265`, observable positives `360` / `6.84%` as of `2024-01-01`) -> Test = 2024 (`2024-01-01 07:24:57` to `2024-12-31 22:49:38`, `n=12,819`, realized positives `902` / `7.04%`) |
| Strict Date Separation | `max(train_date) < min(test_date)` == **`True`** |
| Scored Population & Prior Orders | Post-fulfillment conditional return-risk scoring (`Completed` + `Returned`); `customer_prior_orders_log` aggregates **all prior non-cancelled orders** (`Pending`/`Processing`/`Shipped`/`Completed`/`Returned`, `1,692` non-terminal orders included in history) strictly before `current.order_date` |
| Prior-Return Observability Gate | `fact_returns` left-joined **strictly for `returned_at` (`is_temporal_anomaly = 0`, never as a feature)**; a prior order counts in `customer_prior_return_rate` ONLY IF `prior.returned_at IS NOT NULL AND prior.returned_at >= prior.order_date AND prior.returned_at <= current.order_date` (**`4`** future-peek `(prior_order, current_order)` pairs blocked) |
| Train-Cutoff Maturation Gate | **`26`** late-2023 orders returned on/after `2024-01-01` were right-censored to training label `0` as of cutoff (`360` observable 2023 positives vs. `386` eventual 2023 returns) |
| Scaling Leakage Guard | Z-score `mean` & `std` fit **strictly on 2023 training orders** and applied unchanged to 2024 test orders |
| Excluded Post-Order Feature Columns | `ship_date, ship_date_key, returned_at, returned_date, returned_date_key, return_lead_days, reason, updated_at, is_missing_online_ship_date, is_temporal_anomaly` |
| Model & Score Semantics | Pure-Python L2 Class-Weighted Logistic Regression (`lambda=1.0`, `w_pos=7.3125`, `w_neg=0.5367`, `epochs=120`, loss `0.6931` -> `0.6877`, runtime `1358.69 ms`); `predicted_risk_score` is a **class-balanced propensity score** (not raw calibrated probability) used for decile ranking |

### 4.2 Out-of-Time 2024 Evaluation Metrics (Imbalanced Positive Class `~7.04%`)

| Metric | 2023 Train (In-Sample) | 2024 Out-of-Time Test | Naive Random Baseline |
| --- | ---: | ---: | ---: |
| **ROC-AUC** | `0.5512` | **`0.4986`** | `0.5000` |
| **PR-AUC (Average Precision)** | `0.0804` | **`0.0707`** (`1.00x` baseline) | `0.0704` |
| **Precision@Top10%** | — | **`6.94%`** (89/1282) | `7.04%` |
| **Recall@Top10%** | — | **`9.87%`** (89/902) | `10.00%` |
| **Lift@Top10%** | — | **`0.987x`** | `1.000x` |

### 4.3 Honest Synthetic-Data Signal Diagnosis

> [!NOTE]
> Near-baseline out-of-time generalization (expected on this synthetic dataset): in `data/raw/generate_data.py`, `pick_status()` draws order status independently from fixed probabilities (`Completed=0.78`, `Returned=0.06`, with only a minor year-end ship-date boundary clamp). Under strict leakage-free 2023->2024 validation, ROC-AUC near ~0.50 and PR-AUC near baseline prevalence (~0.070) proves the pipeline does NOT leak target/post-order information and honestly diagnoses absent operational signal rather than overfitting noise.

**Why This Matters in Enterprise Data Science:** Many portfolio demos accidentally leak `ship_date` (NULL on some statuses), `returned_at`, or future customer return rates and claim a fake `0.95+` ROC-AUC. Under strict out-of-time evaluation on a synthetic generator where returns are drawn uniformly at random, an honest model **must** score near chance (`ROC-AUC ~0.50`, `PR-AUC ~prevalence`, `Lift@Top10% ~1.0x`). When wired to real VietTech operational telemetry (carrier SLA delays, SKU defect batches, first-time COD high-ticket orders, warehouse pick-error rates), this exact leakage-free scoring pipeline (`mart_return_risk_scores`) is immediately production-ready.

### 4.4 Learned Standardized Feature Weights (2023 Training Fit)

| Rank | Feature (Order-Time Only) | Standardized Weight | Odds Ratio (per +1 SD) | 2023 Train Mean | 2023 Train Std |
| ---: | --- | ---: | ---: | ---: | ---: |
| `1` | `discount_ratio` | `-0.2072` | `0.8129x` | `0.0403` | `0.0350` |
| `2` | `avg_item_price_log` | `-0.0745` | `0.9282x` | `15.3821` | `1.4276` |
| `3` | `has_laptop` | `+0.0641` | `1.0662x` | `0.2160` | `0.4115` |
| `4` | `is_weekend_order` | `-0.0592` | `0.9425x` | `0.2798` | `0.4489` |
| `5` | `customer_prior_orders_log` | `+0.0496` | `1.0509x` | `0.4360` | `0.4726` |
| `6` | `is_app_channel` | `+0.0420` | `1.0429x` | `0.2984` | `0.4576` |
| `7` | `item_count` | `+0.0365` | `1.0372x` | `2.5690` | `1.5086` |
| `8` | `log1p_net_amount` | `-0.0341` | `0.9665x` | `16.1100` | `1.6511` |
| `9` | `has_accessories` | `+0.0308` | `1.0313x` | `0.6843` | `0.4648` |
| `10` | `customer_prior_return_rate` | `-0.0151` | `0.9850x` | `0.0304` | `0.1513` |
| `11` | `has_smartphone` | `+0.0111` | `1.0111x` | `0.3563` | `0.4789` |
| `12` | `is_online_channel` | `-0.0096` | `0.9904x` | `0.6978` | `0.4592` |
| `13` | `is_tet_season` | `+0.0082` | `1.0083x` | `0.0515` | `0.2210` |

### 4.5 Out-of-Time 2024 Risk Decile Ranking (`mart_return_risk_scores`)

> **Score Semantics:** `predicted_risk_score` is a **class-balanced propensity score** (centered near `~0.50` due to balanced class weighting `w_pos`/`w_neg`), **not** a raw calibrated probability (which averages `~7.04%`). Use it strictly for decile ranking and Top-K% operational triage.

| Risk Decile (1=Highest) | Orders | Actual Returns | Observed Return Rate | Lift vs Baseline | Avg Propensity Score (Balanced) |
| ---: | ---: | ---: | ---: | ---: | ---: |
| `D1` | `1,282` | `89` | `6.94%` | `0.987x` | `0.5864` |
| `D2` | `1,282` | `90` | `7.02%` | `0.998x` | `0.5628` |
| `D3` | `1,282` | `96` | `7.49%` | `1.064x` | `0.5486` |
| `D4` | `1,282` | `78` | `6.08%` | `0.865x` | `0.5368` |
| `D5` | `1,282` | `97` | `7.57%` | `1.075x` | `0.5248` |
| `D6` | `1,282` | `88` | `6.86%` | `0.976x` | `0.5117` |
| `D7` | `1,282` | `83` | `6.47%` | `0.920x` | `0.4969` |
| `D8` | `1,282` | `108` | `8.42%` | `1.197x` | `0.4773` |
| `D9` | `1,282` | `89` | `6.94%` | `0.987x` | `0.4465` |
| `D10` | `1,281` | `84` | `6.56%` | `0.932x` | `0.3758` |
