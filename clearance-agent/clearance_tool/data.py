"""
data.py – Load, validate, and enrich sales data with seasonal / holiday features.

Expected columns (see template/sales_template.csv):
  date, store_id, store_name, sku_id, product_name,
  buying_price (or computable from margin columns), selling_price, units_sold
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


# ── Beijing monthly weather data ──────────────────────────────────────────────
# Average temperature (°C) and precipitation (mm) for Beijing by month.
WEATHER_TEMP_C: dict[int, float] = {
    1: -3.0, 2:  0.0, 3:  7.0, 4: 14.0, 5: 21.0, 6: 26.0,
    7: 29.0, 8: 27.0, 9: 22.0, 10: 14.0, 11:  5.0, 12: -2.0,
}

WEATHER_PRECIP_MM: dict[int, float] = {
    1:   3.0, 2:   5.0, 3:   8.0, 4:  17.0, 5:  33.0, 6:  63.0,
    7: 173.0, 8: 128.0, 9:  50.0, 10: 17.0, 11:   6.0, 12:   3.0,
}


def _build_weather_index() -> dict[int, float]:
    """Temperature-based demand index normalised so the annual mean = 1.0."""
    raw = {m: max(0.0, WEATHER_TEMP_C[m] + 5) for m in range(1, 13)}
    mean = sum(raw.values()) / 12
    if mean == 0:
        return {m: 1.0 for m in range(1, 13)}
    return {m: round(v / mean, 2) for m, v in raw.items()}


WEATHER_INDEX: dict[int, float] = _build_weather_index()


# ── Column aliases ────────────────────────────────────────────────────────────
REQUIRED_COLS = {
    "date":          ["date", "日期", "Date"],
    "store_id":      ["store_id", "门店编号", "Store ID", "StoreID"],
    "store_name":    ["store_name", "门店", "Store Name", "StoreName"],
    "sku_id":        ["sku_id", "商品编码", "SKU ID", "SKUID", "sku"],
    "product_name":  ["product_name", "商品名称", "Product Name", "ProductName"],
    "selling_price": ["selling_price", "平均售价", "Selling Price",
                      "selling price", "Price", "price"],
    "units_sold":    ["units_sold", "实销数量", "Units Sold", "units",
                      "quantity", "Quantity"],
}

OPTIONAL_COLS = {
    # Direct cost column (preferred)
    "buying_price":  ["buying_price", "采购单价", "Cost", "cost",
                      "buying price", "未税采购价"],
    # Financial columns to derive cost when buying_price absent
    "revenue":       ["revenue", "实销金额", "未税实销金额", "Revenue"],
    "margin":        ["margin",  "毛利",     "未税基础毛利",  "Margin"],
    "margin_rate":   ["margin_rate", "毛利率", "未税基础毛利率", "Margin Rate"],
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
        missing_cn = [REQUIRED_COLS[c][1] for c in missing]
        return [], [f"缺少必要列：{', '.join(missing_cn)}。"
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

        # numeric required
        for col in ("selling_price", "units_sold"):
            v = _to_float(row.get(col, ""))
            if v is None:
                errors.append(f"第 {i} 行：无法解析字段\"{col}\"的值\"{row.get(col, '')}\"")
                ok = False
            else:
                row[col] = v

        # optional numeric
        for col in ("buying_price", "revenue", "margin", "margin_rate"):
            v = _to_float(row.get(col, ""))
            row[col] = v  # may be None

        # derive buying_price if absent
        if ok and row.get("buying_price") is None:
            rev  = row.get("revenue")
            mgn  = row.get("margin")
            rate = row.get("margin_rate")
            u    = row.get("units_sold")
            if rev is not None and mgn is not None and u:
                row["buying_price"] = (rev - mgn) / u
            elif rev is not None and rate is not None and u:
                row["buying_price"] = rev * (1.0 - rate) / u
            else:
                errors.append(
                    f"第 {i} 行：无法确定进货价，请提供 buying_price 列，"
                    "或同时提供 未税实销金额 和 未税基础毛利。")
                ok = False

        # derived: if revenue missing, approximate from selling_price × units
        if ok and row.get("revenue") is None and \
                row.get("selling_price") and row.get("units_sold"):
            row["revenue"] = row["selling_price"] * row["units_sold"]

        if ok:
            rows.append(row)

    return rows, errors


def load_xlsx_bytes(raw: bytes) -> tuple[list[dict], list[str]]:
    """
    Parse Excel (.xlsx) bytes.  Returns (rows, errors).
    Re-uses the same validation / enrichment logic as load_csv_bytes.
    Requires openpyxl (listed in requirements.txt).
    """
    try:
        import openpyxl
    except ImportError:
        return [], ["openpyxl 未安装，无法读取 XLSX 文件。请安装：pip install openpyxl"]

    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        ws = wb.active
        rows_raw = list(ws.iter_rows(values_only=True))
    except Exception as e:
        return [], [f"无法读取 XLSX 文件：{e}"]

    if not rows_raw:
        return [], ["XLSX 文件为空。"]

    # Convert to CSV-like text so we can reuse load_csv_bytes
    buf = io.StringIO()
    w = csv.writer(buf)
    for row in rows_raw:
        w.writerow([("" if v is None else str(v)) for v in row])
    return load_csv_bytes(buf.getvalue().encode("utf-8"))


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
