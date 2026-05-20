"""
VietTech Retail - Realistic Transactional Dataset Generator
==============================================================
Generates ~5,000 customers, ~30,000 orders across 2023-2024
for a multi-channel technology retail company in Vietnam.

Supports: RFM segmentation, BI dashboards, BigQuery / Power BI / Looker
"""

import random
import math
import csv
import os
from datetime import datetime, date, timedelta
from decimal import Decimal, ROUND_HALF_UP

import numpy as np
import pandas as pd

# ── Reproducibility ─────────────────────────────────────────────────────────
SEED = 42
random.seed(SEED)
np.random.seed(SEED)

# ── Output directory ─────────────────────────────────────────────────────────
OUT_DIR = "C:/Users/ADMIN/Documents/dataset"
os.makedirs(OUT_DIR, exist_ok=True)

# ── Date constants ────────────────────────────────────────────────────────────
START_DATE = date(2023, 1, 1)
END_DATE   = date(2024, 12, 31)
SNAPSHOT   = date(2025, 1, 1)   # RFM reference date

def rand_date(start: date, end: date) -> date:
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, delta))

def rand_datetime(start: date, end: date) -> datetime:
    d = rand_date(start, end)
    h = random.randint(7, 22)
    m = random.randint(0, 59)
    s = random.randint(0, 59)
    return datetime(d.year, d.month, d.day, h, m, s)

def d2dt(d: date, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hour, minute, second)

def fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def fmt_d(d: date) -> str:
    return d.strftime("%Y-%m-%d")

# ─────────────────────────────────────────────────────────────────────────────
# 1. SALES CHANNELS
# ─────────────────────────────────────────────────────────────────────────────
CHANNELS_RAW = [
    (1, "STORE-HN",  "Cửa hàng Hà Nội",        "Physical", date(2020, 3, 15)),
    (2, "STORE-HP",  "Cửa hàng Hải Phòng",      "Physical", date(2020, 6, 1)),
    (3, "STORE-HUE", "Cửa hàng Huế",            "Physical", date(2021, 1, 10)),
    (4, "STORE-DN",  "Cửa hàng Đà Nẵng",        "Physical", date(2020, 9, 20)),
    (5, "STORE-HCM", "Cửa hàng TP. Hồ Chí Minh","Physical", date(2019, 11, 5)),
    (6, "STORE-CT",  "Cửa hàng Cần Thơ",        "Physical", date(2021, 7, 1)),
    (7, "ONLINE-WEB","VietTech Web",             "Web",      date(2020, 1, 1)),
    (8, "ONLINE-APP","VietTech App",             "App",      date(2021, 6, 1)),
]

# Channel weights: Web+App 63%, Physical 37%
CHANNEL_WEIGHTS = {1: 6, 2: 4, 3: 3, 4: 5, 5: 8, 6: 3, 7: 38, 8: 33}

def build_channels():
    rows = []
    for cid, code, name, ctype, opened in CHANNELS_RAW:
        rows.append({
            "id": cid, "channel_code": code, "channel_name": name,
            "channel_type": ctype, "is_active": 1,
            "opened_date": fmt_d(opened)
        })
    return rows

# ─────────────────────────────────────────────────────────────────────────────
# 2. PRODUCT CATEGORIES
# ─────────────────────────────────────────────────────────────────────────────
CATEGORIES_RAW = [
    (1,  "Smartphones",  "Android Phone"),
    (2,  "Smartphones",  "iPhone"),
    (3,  "Laptops",      "Windows Laptop"),
    (4,  "Laptops",      "MacBook"),
    (5,  "Tablets",      "Android Tablet"),
    (6,  "Tablets",      "iPad"),
    (7,  "Accessories",  "Phone Case & Screen Protector"),
    (8,  "Accessories",  "Charger & Cable"),
    (9,  "Accessories",  "Earphones & Headphones"),
    (10, "Accessories",  "Keyboard & Mouse"),
    (11, "Networking",   "WiFi Router"),
    (12, "Networking",   "Network Switch"),
    (13, "Storage",      "External SSD & HDD"),
    (14, "Storage",      "USB Flash Drive"),
]

def build_categories():
    return [{"id": r[0], "category": r[1], "sub_category": r[2]} for r in CATEGORIES_RAW]

# ─────────────────────────────────────────────────────────────────────────────
# 3. PRODUCTS
# ─────────────────────────────────────────────────────────────────────────────
PRODUCTS_TEMPLATE = [
    # cat_id, code, name, brand, msrp, cost_pct, stock, demand_w
    (2,"IPHONE-15-PRO-MAX","iPhone 15 Pro Max 256GB","Apple",    34_990_000,.68,180,10),
    (2,"IPHONE-15-PRO",    "iPhone 15 Pro 128GB",    "Apple",    27_990_000,.68,220,14),
    (2,"IPHONE-15",        "iPhone 15 128GB",        "Apple",    22_490_000,.70,300,16),
    (2,"IPHONE-14",        "iPhone 14 128GB",        "Apple",    18_990_000,.70,250,12),
    (2,"IPHONE-13",        "iPhone 13 128GB",        "Apple",    14_490_000,.72,320,10),
    (1,"SAM-S24-ULTRA",    "Samsung Galaxy S24 Ultra","Samsung", 31_990_000,.69,150,9),
    (1,"SAM-S24",          "Samsung Galaxy S24",     "Samsung",  22_990_000,.70,200,11),
    (1,"SAM-A55",          "Samsung Galaxy A55",     "Samsung",  10_490_000,.72,400,14),
    (1,"SAM-A35",          "Samsung Galaxy A35",     "Samsung",   8_490_000,.73,450,13),
    (1,"XIA-14-PRO",       "Xiaomi 14 Pro",          "Xiaomi",   19_990_000,.71,180,8),
    (1,"XIA-REDMI-NOTE13", "Xiaomi Redmi Note 13",   "Xiaomi",    6_990_000,.73,500,12),
    (4,"MACBOOK-PRO-M3",   "MacBook Pro 14\" M3",    "Apple",    52_990_000,.67,80,5),
    (4,"MACBOOK-AIR-M2",   "MacBook Air 13\" M2",    "Apple",    32_990_000,.68,120,7),
    (3,"DELL-XPS-15",      "Dell XPS 15 9530",       "Dell",     42_990_000,.68,70,4),
    (3,"DELL-INS-15",      "Dell Inspiron 15 3520",  "Dell",     16_990_000,.71,200,9),
    (3,"HP-ENVY-X360",     "HP Envy x360 15",        "HP",       25_990_000,.70,150,7),
    (3,"HP-PAVIL-15",      "HP Pavilion 15",         "HP",       13_990_000,.72,250,9),
    (3,"ASUS-ZENBOOK14",   "Asus ZenBook 14 OLED",   "Asus",     23_990_000,.70,170,8),
    (3,"ASUS-VIVOBOOK15",  "Asus VivoBook 15",       "Asus",     11_990_000,.73,300,10),
    (3,"LENOVO-THINKPAD",  "Lenovo ThinkPad E14",    "Lenovo",   19_990_000,.70,160,7),
    (6,"IPAD-PRO-M4",      "iPad Pro 11\" M4 256GB", "Apple",    28_990_000,.68,100,5),
    (6,"IPAD-AIR-M2",      "iPad Air 11\" M2",       "Apple",    18_990_000,.70,130,7),
    (6,"IPAD-10GEN",       "iPad 10th Gen 64GB",     "Apple",    11_990_000,.71,200,8),
    (5,"SAM-TAB-S9",       "Samsung Galaxy Tab S9",  "Samsung",  22_990_000,.70,120,5),
    (5,"SAM-TAB-A9",       "Samsung Galaxy Tab A9",  "Samsung",   7_990_000,.73,250,8),
    (7,"CASE-IPHONE15",    "Ốp lưng iPhone 15 chính hãng","Apple",490_000,.60,2000,20),
    (7,"SCREEN-PROT-UNI",  "Kính cường lực 9H Universal","Logitech",150_000,.55,3000,22),
    (7,"CASE-SAM-S24",     "Ốp lưng Samsung S24 Series","Samsung",390_000,.58,1800,18),
    (8,"CHARGER-APPLE-35W","Apple 35W Dual USB-C Adapter","Apple",1_290_000,.62,1500,20),
    (8,"CABLE-USB-C-1M",   "Cáp USB-C 1m 60W PD",   "Samsung",   190_000,.55,4000,25),
    (8,"CHARGER-SAM-25W",  "Samsung 25W Super Fast Charge","Samsung",590_000,.60,2000,20),
    (8,"CABLE-LIGHTNING",  "Cáp Lightning 1m Apple","Apple",     490_000,.62,2500,18),
    (9,"AIRPODS-PRO2",     "AirPods Pro 2nd Gen",    "Apple",     6_990_000,.68,400,10),
    (9,"SONY-WH1000XM5",   "Sony WH-1000XM5",        "Sony",      8_990_000,.69,200,7),
    (9,"SAM-BUDS2-PRO",    "Samsung Galaxy Buds2 Pro","Samsung",  3_490_000,.68,350,9),
    (9,"XIA-BUDS4-PRO",    "Xiaomi Buds 4 Pro",      "Xiaomi",    1_990_000,.70,600,10),
    (10,"LOG-MX-MASTER3S", "Logitech MX Master 3S",  "Logitech",  2_490_000,.68,500,8),
    (10,"LOG-MX-KEYS-MINI","Logitech MX Keys Mini",  "Logitech",  2_190_000,.68,450,7),
    (10,"LOG-G304",        "Logitech G304 Wireless",  "Logitech",   890_000,.65,800,10),
    (10,"LOG-PEBBLE-KEYS2","Logitech Pebble Keys 2",  "Logitech",   790_000,.65,700,9),
    (11,"ASUS-RT-AX88U",   "Asus RT-AX88U Pro",      "Asus",      4_990_000,.68,300,5),
    (11,"TP-LINK-AX3000",  "TP-Link Archer AX3000",  "TP-Link",   1_590_000,.65,500,7),
    (12,"TP-LINK-SG108E",  "TP-Link TL-SG108E",      "TP-Link",    890_000,.64,400,5),
    (13,"SAMSUNG-T7-1TB",  "Samsung T7 SSD 1TB",     "Samsung",   2_490_000,.67,600,8),
    (13,"WD-PASSPORT-2TB", "WD My Passport 2TB",     "WD",        1_890_000,.66,500,7),
    (14,"SAM-USB-64GB",    "Samsung USB 3.1 64GB",   "Samsung",    290_000,.58,2000,14),
    (14,"XIA-USB-128GB",   "Xiaomi USB 3.2 128GB",   "Xiaomi",     390_000,.57,1800,12),
]

# Cross-sell map: category_id → list of complementary category_ids
CROSSSELL = {
    2: [7, 8, 9],   # iPhone → case, charger, earphones
    1: [7, 8, 9],   # Android → case, charger, earphones
    4: [10, 13],    # MacBook → keyboard/mouse, SSD
    3: [10, 13],    # Win laptop → keyboard/mouse, SSD
    6: [7, 8],      # iPad → case, charger
    5: [7, 8],      # Android tablet → case, charger
}

def build_products():
    rows = []
    for i, t in enumerate(PRODUCTS_TEMPLATE, start=1):
        cat_id, code, name, brand, msrp, cost_pct, stock, _ = t
        cost = round(msrp * cost_pct, -3)  # round to nearest 1000 VND
        rows.append({
            "id": i, "product_code": code, "product_name": name,
            "category_id": cat_id, "brand": brand,
            "cost_price": cost, "msrp": msrp,
            "quantity_in_stock": stock, "is_active": 1,
            "created_at": "2022-12-01 08:00:00",
            "updated_at": "2022-12-01 08:00:00",
        })
    return rows

# Product lookup helpers
def product_by_id(products, pid):
    return next(p for p in products if p["id"] == pid)

def products_by_category(products, cat_id):
    return [p for p in products if p["category_id"] == cat_id]

# ─────────────────────────────────────────────────────────────────────────────
# 4. CUSTOMERS
# ─────────────────────────────────────────────────────────────────────────────
VN_CITIES = [
    "Hà Nội","TP. Hồ Chí Minh","Đà Nẵng","Hải Phòng","Cần Thơ",
    "Huế","Biên Hòa","Nha Trang","Vũng Tàu","Bình Dương",
    "Bắc Ninh","Thái Nguyên","Nam Định","Quy Nhơn","Đà Lạt",
    "Hạ Long","Vinh","Buôn Ma Thuột","Mỹ Tho","Thái Bình",
]
CITY_WEIGHTS = [20,22,10,8,5,4,4,4,4,5,3,2,2,2,2,2,2,2,1,2]

SEGMENTS = ["VIP","Loyal","Regular","Occasional","Churned"]
SEG_DIST  = [0.10, 0.25, 0.30, 0.20, 0.15]
SEG_ORDERS= {"VIP":(28,50),"Loyal":(18,32),"Regular":(8,18),"Occasional":(4,8),"Churned":(1,4)}

VN_FIRST_NAMES_M = [
    "Minh","Tuấn","Hùng","Dũng","Hải","Nam","Hoàng","Đức","Anh","Phong",
    "Tùng","Khoa","Bảo","Trọng","Hưng","Long","Khải","Thiên","Quân","Vy",
    "Hiếu","Lâm","Nhân","Duy","Tâm","Thành","Vĩnh","Sơn","Kiên","Toàn",
]
VN_FIRST_NAMES_F = [
    "Linh","Hương","Lan","Ngọc","Hoa","Mai","Thảo","Phương","Trang","Yến",
    "Nhung","Hằng","Thủy","Thu","Bích","Quỳnh","Trúc","Châu","Vy","Hạnh",
    "Diệu","Khánh","Thanh","Ánh","Nhi","Giang","Mỹ","Ngân","Trinh","Lam",
]
VN_LAST_NAMES = [
    "Nguyễn","Trần","Lê","Phạm","Hoàng","Huỳnh","Phan","Vũ","Võ","Đặng",
    "Bùi","Đỗ","Hồ","Ngô","Dương","Lý","Đinh","Đoàn","Trịnh","Tô",
]
EMAIL_DOMAINS = ["gmail.com","yahoo.com","outlook.com","hotmail.com"]

def vn_ascii(s: str) -> str:
    """Rough ASCII transliteration for emails."""
    replacements = {
        'à':'a','á':'a','â':'a','ã':'a','ă':'a','ắ':'a','ặ':'a','ầ':'a','ấ':'a','ẩ':'a','ẫ':'a','ậ':'a',
        'è':'e','é':'e','ê':'e','ế':'e','ề':'e','ệ':'e','ể':'e','ễ':'e',
        'ì':'i','í':'i','î':'i','ï':'i',
        'ò':'o','ó':'o','ô':'o','ơ':'o','ổ':'o','ồ':'o','ỗ':'o','ộ':'o','ớ':'o','ờ':'o','ợ':'o','ở':'o','ỡ':'o',
        'ù':'u','ú':'u','ư':'u','ứ':'u','ừ':'u','ự':'u','ữ':'u','ử':'u',
        'ỳ':'y','ý':'y','ỷ':'y','ỹ':'y','ỵ':'y',
        'đ':'d','ñ':'n',
        'À':'A','Á':'A','Â':'A','Ã':'A','Ă':'A','Ắ':'A',
        'È':'E','É':'E','Ê':'E',
        'Ì':'I','Í':'I',
        'Ò':'O','Ó':'O','Ô':'O','Ơ':'O',
        'Ù':'U','Ú':'U','Ư':'U',
        'Ý':'Y','Đ':'D',
    }
    out = []
    for c in s:
        out.append(replacements.get(c, c))
    return "".join(out)

VN_PHONE_PREFIXES = [
    "032","033","034","035","036","037","038","039",
    "086","096","097","098","070","079","077","076","078",
    "089","090","093","058","056","058",
    "091","094","083","084","085","081","082",
]

_used_emails = set()
_used_phones = set()
_customer_counter = [0]

def make_customer(seg: str, cid: int) -> dict:
    gender = random.choice(["Male","Female","Other"])
    if gender == "Male":
        first = random.choice(VN_FIRST_NAMES_M)
    elif gender == "Female":
        first = random.choice(VN_FIRST_NAMES_F)
    else:
        first = random.choice(VN_FIRST_NAMES_M + VN_FIRST_NAMES_F)
    last = random.choice(VN_LAST_NAMES)

    # email
    fn_ascii = vn_ascii(first).lower()
    ln_ascii  = vn_ascii(last).lower()
    for attempt in range(200):
        num = random.randint(1, 9999)
        domain = random.choice(EMAIL_DOMAINS)
        email = f"{fn_ascii}.{ln_ascii}{num}@{domain}"
        if email not in _used_emails:
            _used_emails.add(email)
            break
    else:
        email = f"user{cid}@gmail.com"

    # phone
    for attempt in range(200):
        prefix = random.choice(VN_PHONE_PREFIXES)
        suffix = "".join([str(random.randint(0,9)) for _ in range(7)])
        phone = prefix + suffix
        if phone not in _used_phones:
            _used_phones.add(phone)
            break
    else:
        phone = f"09{cid:08d}"[:10]

    city = random.choices(VN_CITIES, weights=CITY_WEIGHTS)[0]
    cust_seg = random.choices(
        ["Consumer","Corporate","Home Office"], weights=[60,25,15]
    )[0]

    # registered_date based on segment
    if seg == "Churned":
        reg = rand_date(START_DATE, date(2023, 6, 30))
    elif seg == "VIP":
        reg = rand_date(START_DATE, date(2023, 12, 31))
    else:
        reg = rand_date(START_DATE, date(2024, 10, 31))

    # dob: must be 18-65 yrs old AT TIME OF REGISTRATION (not today)
    # max_birth = reg - 18 years, min_birth = reg - 65 years
    max_birth_year = reg.year - 18
    min_birth_year = reg.year - 65
    dob_year = random.randint(min_birth_year, max_birth_year)
    dob_month = random.randint(1, 12)
    dob_day = random.randint(1, 28)
    dob = date(dob_year, dob_month, dob_day)
    # Ensure age >= 18 at registration (handle birthday not yet passed in reg year)
    if (reg - dob).days < 18 * 365:
        dob = dob.replace(year=dob.year - 1)

    now = datetime(2025, 1, 1, 0, 0, 0)
    created_dt = d2dt(reg, 9, 0, 0)

    return {
        "id": cid,
        "customer_code": f"CUST-{cid:05d}",
        "first_name": first,
        "last_name": last,
        "email": email,
        "phone": phone,
        "gender": gender,
        "date_of_birth": fmt_d(dob),
        "city": city,
        "country": "Vietnam",
        "segment": cust_seg,
        "registered_date": fmt_d(reg),
        "is_active": 0 if seg == "Churned" else 1,
        "created_at": fmt_dt(created_dt),
        "updated_at": fmt_dt(created_dt),
        "_seg": seg,          # internal, removed before output
        "_reg_date": reg,     # internal
    }

def build_customers(n=5000) -> list:
    customers = []
    seg_counts = {s: round(p * n) for s, p in zip(SEGMENTS, SEG_DIST)}
    # adjust to exactly n
    seg_counts["Regular"] += n - sum(seg_counts.values())
    cid = 1
    for seg, cnt in seg_counts.items():
        for _ in range(cnt):
            customers.append(make_customer(seg, cid))
            cid += 1
    random.shuffle(customers)
    # re-assign ids after shuffle
    for i, c in enumerate(customers, start=1):
        c["id"] = i
        c["customer_code"] = f"CUST-{i:05d}"
    return customers

# ─────────────────────────────────────────────────────────────────────────────
# 5. SEASONALITY WEIGHT
# ─────────────────────────────────────────────────────────────────────────────
MONTHLY_WEIGHT = {
    1: 1.7,  # Tết peak
    2: 1.5,
    3: 0.7,
    4: 0.65,
    5: 0.9,
    6: 1.0,
    7: 1.0,
    8: 1.05,
    9: 0.95,
    10: 1.1,
    11: 1.4,
    12: 1.8,  # Year-end peak
}

def seasonal_weight(d: date) -> float:
    return MONTHLY_WEIGHT[d.month]

def weighted_rand_date(start: date, end: date) -> date:
    """Pick a random date biased by seasonal weights."""
    days = [(start + timedelta(days=i)) for i in range((end - start).days + 1)]
    weights = [seasonal_weight(d) for d in days]
    return random.choices(days, weights=weights)[0]

# ─────────────────────────────────────────────────────────────────────────────
# 6. ORDERS + ORDER_DETAILS
# ─────────────────────────────────────────────────────────────────────────────
PAYMENT_BY_CHANNEL = {
    "Physical": {"Cash": 50, "Credit Card": 30, "Bank Transfer": 15, "E-Wallet": 5},
    "Web":      {"Cash": 5,  "Credit Card": 25, "Bank Transfer": 20, "E-Wallet": 50},
    "App":      {"Cash": 3,  "Credit Card": 20, "Bank Transfer": 12, "E-Wallet": 65},
}

def pick_payment(channel_type: str, net_amount: float) -> str:
    weights = PAYMENT_BY_CHANNEL[channel_type].copy()
    if net_amount > 20_000_000:
        weights["Credit Card"] = weights.get("Credit Card", 0) * 2.5
    d = weights
    keys = list(d.keys())
    vals = list(d.values())
    return random.choices(keys, weights=vals)[0]

STATUS_WEIGHTS = [
    ("Completed",  0.78),
    ("Cancelled",  0.08),
    ("Returned",   0.06),
    ("Shipped",    0.04),
    ("Processing", 0.03),
    ("Pending",    0.01),
]

def pick_status() -> str:
    s, w = zip(*STATUS_WEIGHTS)
    return random.choices(s, weights=w)[0]

def campaign_discount(d: date) -> float:
    """Return max extra discount for campaign periods."""
    if (d.month == 1 and d.day <= 20) or (d.month == 2 and d.day <= 15):
        return 0.20   # Tết
    if d.month == 11:
        return 0.15   # 11.11
    if d.month == 12:
        return 0.30   # Year-end
    return 0.05

def pick_discount(cat_id: int, msrp: float, order_date: date) -> float:
    max_disc = campaign_discount(order_date)
    # Accessories get higher discount
    if cat_id in (7, 8, 14):
        base = random.uniform(0.03, min(0.25, max_disc))
    # Premium products minimal discount
    elif msrp > 25_000_000:
        base = random.uniform(0.0, min(0.05, max_disc))
    else:
        base = random.uniform(0.0, min(0.15, max_disc))
    return round(base, 4)

def pick_products_for_order(products, channel_type: str, order_date: date) -> list:
    """
    Return a list of (product, quantity, discount_rate).
    Applies cross-sell logic.
    """
    max_items = 3 if channel_type in ("Web","App") else 4
    n_items = random.choices(
        [1, 2, 3, 4],
        weights=[50, 28, 15, 7] if channel_type in ("Web","App") else [30, 35, 25, 10]
    )[0]
    n_items = min(n_items, max_items)

    # Pick a primary product (weighted by demand)
    demand_w = [t[7] for t in PRODUCTS_TEMPLATE]
    primary_tmpl = random.choices(PRODUCTS_TEMPLATE, weights=demand_w)[0]
    primary_cat = primary_tmpl[0]
    primary_code = primary_tmpl[1]
    primary = next((p for p in products if p["product_code"] == primary_code), None)
    if primary is None:
        primary = random.choice(products)

    chosen = [primary]
    chosen_ids = {primary["id"]}

    # Add cross-sell items
    if n_items > 1 and primary_cat in CROSSSELL:
        complement_cats = CROSSSELL[primary_cat]
        for _ in range(n_items - 1):
            cc = random.choice(complement_cats)
            candidates = [p for p in products if p["category_id"] == cc and p["id"] not in chosen_ids]
            if candidates:
                pick = random.choices(candidates, weights=[1]*len(candidates))[0]
                chosen.append(pick)
                chosen_ids.add(pick["id"])
            if len(chosen) >= n_items:
                break

    # Fill remainder with random products
    while len(chosen) < n_items:
        remaining = [p for p in products if p["id"] not in chosen_ids]
        if not remaining:
            break
        extra = random.choice(remaining)
        chosen.append(extra)
        chosen_ids.add(extra["id"])

    result = []
    for prod in chosen:
        qty = random.choices([1, 2, 3], weights=[70, 20, 10])[0]
        disc = pick_discount(prod["category_id"], prod["msrp"], order_date)
        result.append((prod, qty, disc))
    return result

_order_counter = [0]
_detail_counter = [0]

def build_orders(customers, products, channels):
    orders = []
    details = []
    channel_list = [(c["id"], c["channel_type"]) for c in channels]
    ch_ids   = [c["id"] for c in channels]
    ch_types = {c["id"]: c["channel_type"] for c in channels}
    ch_w     = [CHANNEL_WEIGHTS[cid] for cid in ch_ids]

    for cust in customers:
        seg = cust["_seg"]
        reg = cust["_reg_date"]
        n_orders_min, n_orders_max = SEG_ORDERS[seg]
        n_orders = random.randint(n_orders_min, n_orders_max)

        # Churned: activity only in 2023 H1
        if seg == "Churned":
            order_end = min(END_DATE, date(2023, 8, 31))
        else:
            order_end = END_DATE

        order_start = reg + timedelta(days=1)
        if order_start > order_end:
            continue

        # Generate order dates with realistic gaps (1–6 month spread)
        order_dates = []
        current = order_start
        for _ in range(n_orders):
            if current > order_end:
                break
            d = weighted_rand_date(current, min(order_end, current + timedelta(days=90)))
            order_dates.append(d)
            gap_days = random.randint(14, 90)
            current = d + timedelta(days=gap_days)

        if not order_dates:
            continue

        # Preferred channel (some customers use multiple)
        pref_ch = random.choices(ch_ids, weights=ch_w)[0]
        multi_channel = random.random() < 0.25  # 25% use >1 channel

        for od in order_dates:
            _order_counter[0] += 1
            oid = _order_counter[0]

            # Channel selection
            if multi_channel and random.random() < 0.4:
                ch_id = random.choices(ch_ids, weights=ch_w)[0]
            else:
                ch_id = pref_ch
            ch_type = ch_types[ch_id]

            # Order datetime
            h = random.randint(7, 22)
            m = random.randint(0, 59)
            s = random.randint(0, 59)
            order_dt = datetime(od.year, od.month, od.day, h, m, s)

            # Products
            items = pick_products_for_order(products, ch_type, od)

            total_amount   = 0.0
            total_discount = 0.0
            total_cost     = 0.0
            line_rows      = []

            for prod, qty, disc_rate in items:
                unit_price = float(prod["msrp"])
                unit_cost  = float(prod["cost_price"])
                disc_amt   = round(unit_price * qty * disc_rate, 0)
                line_total = round(unit_price * qty - disc_amt, 0)
                line_cost  = round(unit_cost * qty, 0)
                line_profit= round(line_total - line_cost, 0)

                total_amount   += unit_price * qty
                total_discount += disc_amt
                total_cost     += line_cost

                _detail_counter[0] += 1
                line_rows.append({
                    "id": _detail_counter[0],
                    "order_id": oid,
                    "product_id": prod["id"],
                    "quantity": qty,
                    "unit_price": unit_price,
                    "unit_cost": unit_cost,
                    "discount_rate": disc_rate,
                    "discount_amount": disc_amt,
                    "line_total": line_total,
                    "line_cost": line_cost,
                    "line_profit": line_profit,
                    "created_at": fmt_dt(order_dt),
                })

            net_amount = round(total_amount - total_discount, 0)
            total_amount = round(total_amount, 0)
            total_cost   = round(total_cost, 0)

            status = pick_status()
            payment = pick_payment(ch_type, net_amount)

            ship_date = None
            if status not in ("Pending", "Cancelled", "Processing"):
                ship_days = random.randint(1, 5)
                ship_dt = od + timedelta(days=ship_days)
                if ship_dt <= END_DATE:
                    ship_date = fmt_d(ship_dt)
                else:
                    # If can't fit ship_date, demote Returned to Completed (can't return unshipped)
                    if status == "Returned":
                        status = "Completed"

            created_dt = order_dt
            updated_dt = order_dt + timedelta(hours=random.randint(0, 72))
            end_dt = datetime(END_DATE.year, END_DATE.month, END_DATE.day, 23, 59, 59)
            if updated_dt > end_dt:
                updated_dt = end_dt

            orders.append({
                "id": oid,
                "order_code": f"ORD-{od.year}-{oid:06d}",
                "customer_id": cust["id"],
                "channel_id": ch_id,
                "order_date": fmt_dt(order_dt),
                "ship_date": ship_date or "",
                "status": status,
                "payment_method": payment,
                "total_amount": total_amount,
                "total_discount": total_discount,
                "net_amount": net_amount,
                "total_cost": total_cost,
                "note": "",
                "created_at": fmt_dt(created_dt),
                "updated_at": fmt_dt(updated_dt),
            })
            details.extend(line_rows)

    return orders, details

# ─────────────────────────────────────────────────────────────────────────────
# 7. ORDER RETURNS
# ─────────────────────────────────────────────────────────────────────────────
RETURN_REASONS = [
    "Sản phẩm bị lỗi kỹ thuật",
    "Không đúng mô tả",
    "Đặt nhầm sản phẩm",
    "Sản phẩm không đúng màu sắc",
    "Hàng giao bị trầy xước",
    "Chất lượng không như mong đợi",
    "Thay đổi quyết định mua hàng",
    "Sản phẩm không tương thích",
]

def build_returns(orders, order_details):
    """
    Tạo return records CHỈ cho các đơn có status='Returned'.
    Đồng thời đảm bảo:
    - Mọi đơn status='Returned' đều có đúng 1 return record
    - return_date >= order_date và nằm trong dataset range (không dùng placeholder 2025-01-01)
    """
    returned_orders = [o for o in orders if o["status"] == "Returned"]
    returns = []
    rid = 0

    for o in returned_orders:
        rid += 1
        order_dt = datetime.strptime(o["order_date"], "%Y-%m-%d %H:%M:%S")
        # return phải sau ngày ship ít nhất 1 ngày, tối đa 30 ngày sau order
        ship_str = o.get("ship_date", "")
        if ship_str:
            base_dt = datetime.strptime(ship_str, "%Y-%m-%d") + timedelta(days=1)
        else:
            base_dt = order_dt + timedelta(days=3)

        max_ret_dt = datetime(END_DATE.year, END_DATE.month, END_DATE.day, 23, 59, 59)
        ret_dt = base_dt + timedelta(days=random.randint(1, 20))
        # Clamp to END_DATE (không dùng 2025-01-01 làm placeholder)
        if ret_dt > max_ret_dt:
            ret_dt = max_ret_dt - timedelta(days=random.randint(1, 5))

        returns.append({
            "id": rid,
            "order_id": o["id"],
            "reason": random.choice(RETURN_REASONS),
            "returned_at": fmt_dt(ret_dt),
        })
    return returns

# ─────────────────────────────────────────────────────────────────────────────
# 8. PRODUCT PRICE HISTORY
# ─────────────────────────────────────────────────────────────────────────────
def build_price_history(products):
    """
    Sinh lịch sử thay đổi giá VÀ cập nhật products[].msrp / products[].cost_price
    về giá cuối cùng (latest) để đảm bảo nhất quán với price_history.
    Sau khi hàm này chạy xong, products[i]["msrp"] và ["cost_price"] phản ánh
    đúng giá hiện tại (tức giá sau lần thay đổi cuối cùng trong history).
    """
    rows = []
    rid = 0
    for prod in products:
        # 30% sản phẩm có ít nhất 1 lần thay đổi giá
        n_changes = random.choices([0, 1, 2, 3], weights=[70, 18, 8, 4])[0]
        msrp = float(prod["msrp"])
        cost = float(prod["cost_price"])
        change_dates = sorted(random.sample(
            [(date(2023, 1, 1) + timedelta(days=i)) for i in range(730)],
            k=min(n_changes, 729)
        ))
        curr_msrp = msrp
        curr_cost = cost
        last_change_date = None

        for cd in change_dates:
            ptype = random.choice(["MSRP", "COST"])
            if ptype == "MSRP":
                factor    = random.uniform(0.90, 1.12)
                new_price = round(curr_msrp * factor, -3)
                old_price = curr_msrp
                curr_msrp = new_price
            else:
                factor    = random.uniform(0.92, 1.08)
                new_price = round(curr_cost * factor, -3)
                old_price = curr_cost
                curr_cost = new_price

            last_change_date = cd
            rid += 1
            rows.append({
                "id":         rid,
                "product_id": prod["id"],
                "price_type": ptype,
                "old_price":  old_price,
                "new_price":  new_price,
                "changed_at": fmt_dt(d2dt(cd, 8, 0, 0)),
            })

        # ── FIX: ghi giá cuối cùng trở lại vào products dict ──────────────
        if last_change_date is not None:
            prod["msrp"]       = curr_msrp
            prod["cost_price"] = curr_cost
            prod["updated_at"] = fmt_dt(d2dt(last_change_date, 8, 0, 0))

    return rows

# ─────────────────────────────────────────────────────────────────────────────
# 9. CSV WRITERS
# ─────────────────────────────────────────────────────────────────────────────
def strip_internal(rows: list, keys_to_remove: list) -> list:
    out = []
    for r in rows:
        row = {k: v for k, v in r.items() if k not in keys_to_remove}
        out.append(row)
    return out

def write_csv(filename: str, rows: list):
    if not rows:
        return
    path = os.path.join(OUT_DIR, filename)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"  ✅ {filename:40s}  {len(rows):>7,} rows")

# ─────────────────────────────────────────────────────────────────────────────
# 10. SQL WRITER
# ─────────────────────────────────────────────────────────────────────────────
def esc(v) -> str:
    if v is None or v == "":
        return "NULL"
    if isinstance(v, (int, float)):
        return str(v)
    # escape single quotes
    return "'" + str(v).replace("'", "''") + "'"

def rows_to_insert(table: str, rows: list, batch=500) -> str:
    if not rows:
        return ""
    lines = [f"-- {table}\n"]
    cols = list(rows[0].keys())
    col_str = "(`" + "`, `".join(cols) + "`)"
    for i in range(0, len(rows), batch):
        chunk = rows[i:i+batch]
        vals = ",\n  ".join(
            "(" + ", ".join(esc(r[c]) for c in cols) + ")"
            for r in chunk
        )
        lines.append(f"INSERT INTO `{table}` {col_str} VALUES\n  {vals};\n")
    return "\n".join(lines)

def write_sql(all_tables: dict):
    path = os.path.join(OUT_DIR, "data.sql")
    header = """-- =============================================================
-- VietTech Retail OLTP Data
-- Generated: 2023-01-01 to 2024-12-31
-- =============================================================
USE `retailbi_oltp`;
SET FOREIGN_KEY_CHECKS=0;
SET UNIQUE_CHECKS=0;

"""
    footer = "\nSET FOREIGN_KEY_CHECKS=1;\nSET UNIQUE_CHECKS=1;\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(header)
        for table, rows in all_tables.items():
            f.write(rows_to_insert(table, rows))
            f.write("\n")
        f.write(footer)
    size_mb = os.path.getsize(path) / 1_048_576
    print(f"  ✅ {'data.sql':40s}  {size_mb:>6.1f} MB")

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("\n🏪  VietTech Dataset Generator — starting...\n")

    print("► Building channels...")
    channels = build_channels()

    print("► Building categories...")
    categories = build_categories()

    print("► Building products...")
    products = build_products()

    print("► Building customers (5 000)...")
    customers = build_customers(5000)

    print("► Building orders + details (~30 000 orders)...")
    orders, details = build_orders(customers, products, channels)

    print("► Building returns...")
    returns = build_returns(orders, details)

    print("► Building price history...")
    price_history = build_price_history(products)

    # Strip internal fields from customers before output
    customers_out = strip_internal(customers, ["_seg", "_reg_date"])

    # ── CSV ──────────────────────────────────────────────────────────────────
    print(f"\n📂 Writing CSV files to {OUT_DIR}/\n")
    write_csv("sales_channels.csv",       channels)
    write_csv("product_categories.csv",   categories)
    write_csv("products.csv",             products)
    write_csv("customers.csv",            customers_out)
    write_csv("orders.csv",               orders)
    write_csv("order_details.csv",        details)
    write_csv("order_returns.csv",        returns)
    write_csv("product_price_history.csv",price_history)

    # ── SQL ──────────────────────────────────────────────────────────────────
    print(f"\n📄 Writing data.sql...\n")
    write_sql({
        "sales_channels":       channels,
        "product_categories":   categories,
        "products":             products,
        "customers":            customers_out,
        "orders":               orders,
        "order_details":        details,
        "order_returns":        returns,
        "product_price_history":price_history,
    })

    # ── Summary ───────────────────────────────────────────────────────────────
    total_revenue = sum(float(o["net_amount"]) for o in orders if o["status"] == "Completed")
    completed_orders = sum(1 for o in orders if o["status"] == "Completed")
    print(f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊  DATASET SUMMARY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Customers        : {len(customers):>8,}
  Products         : {len(products):>8,}
  Total Orders     : {len(orders):>8,}
  Completed Orders : {completed_orders:>8,}
  Order Details    : {len(details):>8,}
  Returns          : {len(returns):>8,}
  Price Changes    : {len(price_history):>8,}
  Total Revenue    : {total_revenue/1_000_000:>10.1f}M VND
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅  Done!  All files saved to: {OUT_DIR}
""")

if __name__ == "__main__":
    main()