"""
data.py – Load, validate, and enrich sales data with seasonal / holiday features.

Expected columns (see template/sales_template.csv):
  date, store_id, store_name, sku_id, product_name,
  buying_price, selling_price, units_sold
"""

from __future__ import annotations
import csv
import io
import math
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

# ── Beijing public-holiday calendar (2023-2027) ──────────────────────────────
# Dates are the *actual* rest days gazetted by the State Council.
# We mark entire Golden-Week blocks so holiday_week catches the run-up.
_HOLIDAY_RANGES: list[tuple[date, date, str]] = [
    # 元旦
    (date(2023,12,30), date(2024, 1, 1), "元旦"),
    (date(2024,12,28), date(2025, 1, 1), "元旦"),
    (date(2025,12,31), date(2026, 1, 2), "元旦"),
    # 春节
    (date(2024, 2, 10), date(2024, 2,17), "春节"),
    (date(2025, 1, 28), date(2025, 2, 4), "春节"),
    (date(2026, 2, 17), date(2026, 2,23), "春节"),
    # 清明节
    (date(2024, 4,  4), date(2024, 4, 6), "清明节"),
    (date(2025, 4,  4), date(2025, 4, 6), "清明节"),
    (date(2026, 4,  4), date(2026, 4, 6), "清明节"),
    # 劳动节
    (date(2024, 5,  1), date(2024, 5, 5), "劳动节"),
    (date(2025, 5,  1), date(2025, 5, 5), "劳动节"),
    (date(2026, 5,  1), date(2026, 5, 5), "劳动节"),
    # 端午节
    (date(2024, 6, 10), date(2024, 6,10), "端午节"),
    (date(2025, 5, 31), date(2025, 6, 2), "端午节"),
    (date(2026, 6, 19), date(2026, 6,21), "端午节"),
    # 中秋节
    (date(2024, 9, 15), date(2024, 9,17), "中秋节"),
    (date(2025,10,  4), date(2025,10, 6), "中秋节"),
    (date(2026, 9, 24), date(2026, 9,26), "中秋节"),
    # 国庆节
    (date(2024,10,  1), date(2024,10, 7), "国庆节"),
    (date(2025,10,  1), date(2025,10, 7), "国庆节"),
    (date(2026,10,  1), date(2026,10, 7), "国庆节"),
]

def _build_holiday_set() -> dict[date, str]:
    """Map every holiday date to its name."""
    mapping: dict[date, str] = {}
    for start, end, name in _HOLIDAY_RANGES:
        d = start
        while d <= end:
            mapping[d] = name
            d += timedelta(days=1)
    return mapping

_HOLIDAY_MAP: dict[date, str] = _build_holiday_set()


def is_holiday(d: date) -> bool:
    return d in _HOLIDAY_MAP


def holiday_name(d: date) -> str:
    return _HOLIDAY_MAP.get(d, "")


def week_has_holiday(d: date) -> bool:
    """True if any day in the ISO week containing *d* is a public holiday."""
    # Move to Monday of that week
    monday = d - timedelta(days=d.weekday())
    return any((monday + timedelta(days=i)) in _HOLIDAY_MAP for i in range(7))


# ── Seasonal indices for ice cream, Beijing ──────────────────────────────────
# Monthly multiplier relative to annual average (1.0).
# Based on typical Northern-China ice-cream consumption patterns:
# peak summer, sharp trough in winter.
SEASONAL_INDEX: dict[int, float] = {
    1: 0.30,  # January  – deep winter
    2: 0.40,  # February – Spring Festival bump, still cold
    3: 0.60,  # March    – early spring
    4: 1.00,  # April    – shoulder season
    5: 1.40,  # May      – warming up, Labour Day boost
    6: 2.00,  # June     – summer begins
    7: 2.50,  # July     – peak
    8: 2.30,  # August   – peak
    9: 1.50,  # September – still warm, National Day
   10: 0.90,  # October  – cooling
   11: 0.50,  # November – autumn chill
   12: 0.30,  # December – winter
}

# Holiday demand lift for ice cream (+fraction on top of seasonal base)
HOLIDAY_LIFT: dict[str, float] = {
    "春节":   0.05,   # 家庭聚会，需求小幅提升
    "劳动节": 0.15,   # 户外活动，需求明显提升
    "端午节": 0.10,
    "中秋节": 0.08,
    "国庆节": 0.20,   # 黄金周，购物需求大幅提升
    "清明节": 0.05,
    "元旦":   0.10,
}


# ── Column aliases ────────────────────────────────────────────────────────────
REQUIRED_COLS = {
    "date":          ["date", "日期", "Date"],
    "store_id":      ["store_id", "门店编号", "Store ID", "StoreID"],
    "store_name":    ["store_name", "门店", "Store Name", "StoreName"],
    "sku_id":        ["sku_id", "商品编码", "SKU ID", "SKUID", "sku"],
    "product_name":  ["product_name", "商品名称", "Product Name", "ProductName"],
    "buying_price":  ["buying_price", "采购单价", "Cost", "cost",
                      "buying price", "未税采购价"],
    "selling_price": ["selling_price", "平均售价", "Selling Price",
                      "selling price", "Price", "price"],
    "units_sold":    ["units_sold", "实销数量", "Units Sold", "units",
                      "quantity", "Quantity"],
}

OPTIONAL_COLS = {
    "revenue":  ["revenue", "实销金额", "未税实销金额", "Revenue"],
    "margin":   ["margin",  "毛利",     "未税基础毛利",  "Margin"],
}


def _map_columns(header: list[str]) -> dict[str, str]:
    """Return {canonical_name: actual_col_name} for every matched column."""
    header_lower = {h.strip().lower(): h for h in header}
    result: dict[str, str] = {}
    for canon, aliases in {**REQUIRED_COLS, **OPTIONAL_COLS}.items():
        for alias in aliases:
            if alias.strip().lower() in header_lower:
                result[canon] = header_lower[alias.strip().lower()]
                break
    return result


def _parse_date(raw: Any) -> date | None:
    """Parse a variety of date formats to a date object."""
    if isinstance(raw, date):
        return raw
    s = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d",
                "%d/%m/%Y", "%m/%d/%Y", "%Y%m%d"):
        try:
            return date(*[int(x) for x in
                          __import__("re").split(r"[.\-/]", s)][:3]) \
                if fmt == "%Y.%m.%d" else \
                __import__("datetime").datetime.strptime(s, fmt).date()
        except Exception:
            pass
    return None


def _to_float(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def load_csv_bytes(raw: bytes) -> tuple[list[dict], list[str]]:
    """
    Parse CSV bytes.  Returns (rows, errors).
    Each row is a dict with canonical column names + enriched features.
    """
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return [], ["无法读取 CSV 标题行，请检查文件格式。"]
    col_map = _map_columns(list(reader.fieldnames))
    missing = [c for c in REQUIRED_COLS if c not in col_map]
    if missing:
        return [], [f"缺少必要列：{', '.join(missing)}。"
                    f"请使用左侧边栏提供的数据模板。"]
    rows, errors = [], []
    for i, raw_row in enumerate(reader, start=2):
        row: dict[str, Any] = {}
        ok = True
        for canon, actual in col_map.items():
            row[canon] = raw_row.get(actual, "").strip()

        # date
        d = _parse_date(row["date"])
        if d is None:
            errors.append(f"第 {i} 行：无法解析日期\"{row['date']}\"")
            ok = False
        else:
            row["date"] = d
            row["year"]  = d.year
            row["month"] = d.month
            row["week"]  = d.isocalendar()[1]
            row["season"] = _season(d.month)
            row["is_holiday_week"] = 1 if week_has_holiday(d) else 0
            row["holiday_name"]    = holiday_name(d)
            row["seasonal_index"]  = SEASONAL_INDEX[d.month]

        # numeric
        for col in ("buying_price", "selling_price", "units_sold"):
            v = _to_float(row[col])
            if v is None:
                errors.append(f"第 {i} 行：无法解析字段\"{col}\"的值\"{row[col]}\"")
                ok = False
            else:
                row[col] = v

        # optional
        for col in ("revenue", "margin"):
            v = _to_float(row.get(col, ""))
            row[col] = v  # may be None

        # derived: if revenue missing, approximate from selling_price × units
        if ok and row.get("revenue") is None and \
                row.get("selling_price") and row.get("units_sold"):
            row["revenue"] = row["selling_price"] * row["units_sold"]

        if ok:
            rows.append(row)

    return rows, errors


def _season(month: int) -> str:
    if month in (3, 4, 5):    return "Spring"
    if month in (6, 7, 8):    return "Summer"
    if month in (9, 10, 11):  return "Autumn"
    return "Winter"


def sku_summary(rows: list[dict]) -> dict[str, dict]:
    """
    Return per-SKU summary: name, total_units, avg_price, avg_cost,
    n_price_points, months_present.
    """
    agg: dict[str, dict] = defaultdict(lambda: {
        "name": "", "units": 0.0, "revenue": 0.0, "cost_total": 0.0,
        "prices": set(), "months": set(),
    })
    for r in rows:
        sid = str(r["sku_id"])
        agg[sid]["name"]       = r["product_name"]
        agg[sid]["units"]     += r["units_sold"]
        agg[sid]["revenue"]   += r.get("revenue") or \
                                  r["selling_price"] * r["units_sold"]
        agg[sid]["cost_total"] += r["buying_price"] * r["units_sold"]
        agg[sid]["prices"].add(round(r["selling_price"], 2))
        agg[sid]["months"].add(r["month"])

    out: dict[str, dict] = {}
    for sid, v in agg.items():
        u = v["units"]
        out[sid] = {
            "name":           v["name"],
            "total_units":    u,
            "avg_price":      v["revenue"] / u if u else 0,
            "avg_cost":       v["cost_total"] / u if u else 0,
            "n_price_points": len(v["prices"]),
            "months_present": sorted(v["months"]),
        }
    return out


def weekly_baseline(rows: list[dict], sku_id: str) -> float:
    """
    Compute the chain-wide weekly baseline sales rate for a SKU,
    using the most-recent 4-week window available.
    """
    sku_rows = [r for r in rows if str(r["sku_id"]) == str(sku_id)]
    if not sku_rows:
        return 0.0
    # group by ISO week
    week_units: dict[tuple, float] = defaultdict(float)
    for r in sku_rows:
        key = (r["year"], r["week"])
        week_units[key] += r["units_sold"]
    if not week_units:
        return 0.0
    sorted_weeks = sorted(week_units.keys())
    last4 = sorted_weeks[-4:]
    avg = sum(week_units[w] for w in last4) / len(last4)
    return avg


def seasonal_adjusted_baseline(
    base_rate: float,
    clearance_start: date,
    n_weeks: int,
) -> list[float]:
    """
    Return a list of week-level demand baselines adjusted for seasonality
    and holidays.  Length = n_weeks.
    """
    baselines = []
    for w in range(n_weeks):
        d = clearance_start + timedelta(weeks=w)
        s_idx = SEASONAL_INDEX[d.month]
        # Holiday lift
        h_lift = 0.0
        for i in range(7):
            day = d + timedelta(days=i)
            hname = holiday_name(day)
            if hname:
                h_lift = max(h_lift, HOLIDAY_LIFT.get(hname, 0.0))
        adjusted = base_rate * s_idx * (1 + h_lift)
        baselines.append(adjusted)
    return baselines
