"""
elasticity.py – Price elasticity estimation with seasonal & holiday controls.

Model (log-log OLS with fixed effects):

  ln(units_{i,s,t}) = β·ln(price_{i,s,t})
                    + Σ γ_m · month_m        # seasonal (M2-M12 dummies)
                    + δ · holiday_week        # holiday-week dummy
                    + α_i                    # SKU fixed effect (within-transform)
                    + α_s                    # store fixed effect (within-transform)
                    + ε

β is the price elasticity.  Sign convention: β < 0 means demand falls as price rises.

Fallback: category-level elasticity is used when a SKU has fewer than
MIN_PRICE_POINTS distinct price points (default = 3).
"""

from __future__ import annotations
import math
from collections import defaultdict
from typing import Any

MIN_PRICE_POINTS = 3      # spec requirement
ELASTICITY_ABS_CAP = 15.0 # reject |e| > 15 as unreliable


# ── Pure-Python OLS (normal equations via Gaussian elimination) ──────────────

def _matmul(A: list[list[float]], B: list[list[float]]) -> list[list[float]]:
    n, m, p = len(A), len(A[0]), len(B[0])
    C = [[0.0] * p for _ in range(n)]
    for i in range(n):
        for k in range(m):
            if A[i][k] == 0:
                continue
            for j in range(p):
                C[i][j] += A[i][k] * B[k][j]
    return C


def _transpose(A: list[list[float]]) -> list[list[float]]:
    return [[A[i][j] for i in range(len(A))] for j in range(len(A[0]))]


def _solve(A: list[list[float]], b: list[float]) -> list[float] | None:
    """Solve Ax = b via Gaussian elimination with partial pivoting."""
    n = len(b)
    M = [A[i][:] + [b[i]] for i in range(n)]
    for col in range(n):
        # pivot
        max_row = max(range(col, n), key=lambda r: abs(M[r][col]))
        M[col], M[max_row] = M[max_row], M[col]
        if abs(M[col][col]) < 1e-12:
            return None
        for row in range(col + 1, n):
            f = M[row][col] / M[col][col]
            for j in range(col, n + 1):
                M[row][j] -= f * M[col][j]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = M[i][n]
        for j in range(i + 1, n):
            x[i] -= M[i][j] * x[j]
        x[i] /= M[i][i]
    return x


def _ols(X: list[list[float]], y: list[float]) -> list[float] | None:
    """OLS via normal equations: β = (X'X)^{-1} X'y."""
    n = len(y)
    k = len(X[0])
    Xt = _transpose(X)
    XtX = [[sum(Xt[i][j] * X[j][l] for j in range(n))
             for l in range(k)] for i in range(k)]
    Xty = [sum(Xt[i][j] * y[j] for j in range(n)) for i in range(k)]
    return _solve(XtX, Xty)


# ── Feature builder ──────────────────────────────────────────────────────────

def _build_features(obs: list[dict]) -> tuple[list[list[float]], list[float]]:
    """
    Build design matrix X and target y for a set of observations.
    Each obs has keys: ln_price, is_holiday_week, month, (mean-demeaned).

    X columns: [ln_price, holiday_week, <active month dummies>]
    Only months that actually appear in the data are included as dummies
    (avoids all-zero columns which cause a singular X'X matrix when the
    data does not span all 12 calendar months).
    y: ln_units
    """
    # Find which months (M2..M12) are actually present – skip all-zero cols
    active_months = sorted(
        m for m in range(2, 13)
        if any(o["month"] == m for o in obs)
    )
    X, y = [], []
    for o in obs:
        row = [o["ln_price"], float(o["is_holiday_week"])]
        for m in active_months:
            row.append(1.0 if o["month"] == m else 0.0)
        X.append(row)
        y.append(o["ln_units"])
    return X, y


# ── Within-transformation (removes fixed effects) ─────────────────────────────

def _demean_within(obs: list[dict], group_key: str) -> list[dict]:
    """Subtract group mean from ln_price and ln_units."""
    groups: dict[Any, list[dict]] = defaultdict(list)
    for o in obs:
        groups[o[group_key]].append(o)
    out = []
    for grp in groups.values():
        mean_lnp = sum(o["ln_price"]  for o in grp) / len(grp)
        mean_lnu = sum(o["ln_units"]  for o in grp) / len(grp)
        mean_hol = sum(o["is_holiday_week"] for o in grp) / len(grp)
        month_means = {m: sum(1.0 if o["month"] == m else 0.0
                              for o in grp) / len(grp) for m in range(2, 13)}
        for o in grp:
            dm = dict(o)
            dm["ln_price"]        = o["ln_price"] - mean_lnp
            dm["ln_units"]        = o["ln_units"] - mean_lnu
            dm["is_holiday_week"] = o["is_holiday_week"] - mean_hol
            # month dummies also demeaned (Mundlak within transform)
            dm["_month_means"]    = month_means
            out.append(dm)
    return out


# ── Aggregate rows to store × month panel ────────────────────────────────────

def _to_panel(rows: list[dict], sku_id: str) -> list[dict]:
    """
    Aggregate raw rows for one SKU to store × month observations.
    Price = revenue / units (actual realized price, per user instruction).
    Outlier filter: keep rows where unit price is within [50%, 150%] of
    the SKU's weighted-median price.
    """
    sku_rows = [r for r in rows if str(r["sku_id"]) == str(sku_id)]
    if not sku_rows:
        return []

    # Compute weighted-median price across all rows for this SKU
    price_units = sorted(
        [(r["selling_price"], r["units_sold"]) for r in sku_rows]
    )
    total_u = sum(u for _, u in price_units)
    cum = 0.0
    wmed = price_units[0][0]
    for p, u in price_units:
        cum += u
        if cum >= total_u / 2:
            wmed = p
            break

    # Aggregate to store × month
    panel: dict[tuple, dict] = defaultdict(lambda: {
        "units": 0.0, "revenue": 0.0, "cost": 0.0,
        "is_holiday_week": 0.0, "n": 0,
    })
    for r in sku_rows:
        p = r["selling_price"]
        if p < 0.5 * wmed or p > 1.5 * wmed:
            continue            # outlier
        key = (str(r["store_id"]), r["month"])
        panel[key]["units"]           += r["units_sold"]
        panel[key]["revenue"]         += r.get("revenue") or p * r["units_sold"]
        panel[key]["cost"]            += r["buying_price"] * r["units_sold"]
        panel[key]["is_holiday_week"] += r.get("is_holiday_week", 0)
        panel[key]["n"]               += 1

    obs = []
    for (store, month), v in panel.items():
        if v["units"] <= 0:
            continue
        realized_price = v["revenue"] / v["units"]
        if realized_price <= 0:
            continue
        obs.append({
            "store":          store,
            "month":          month,
            "ln_price":       math.log(realized_price),
            "ln_units":       math.log(v["units"]),
            "is_holiday_week": v["is_holiday_week"] / v["n"],
            "avg_cost":       v["cost"] / v["units"],
        })
    return obs


# ── Main estimator ────────────────────────────────────────────────────────────

def estimate_elasticity(
    rows: list[dict],
    min_price_points: int = MIN_PRICE_POINTS,
) -> dict[str, dict]:
    """
    Returns a dict: {sku_id: {"elasticity": float, "method": str,
                               "avg_cost": float, "avg_price": float}}

    Method hierarchy:
      1. SKU-level store-FE OLS (if >= min_price_points store×month obs
         with meaningful within-store price variation)
      2. Category-level pooled OLS (store + SKU FE)
    """
    # ── all unique SKUs ──────────────────────────────────────────────────────
    sku_ids = sorted(set(str(r["sku_id"]) for r in rows))

    # ── build per-SKU panels ─────────────────────────────────────────────────
    sku_panels: dict[str, list[dict]] = {
        sid: _to_panel(rows, sid) for sid in sku_ids
    }

    # ── category elasticity (store + SKU FE, pooled) ─────────────────────────
    cat_e = _category_elasticity(sku_panels)

    # ── per-SKU elasticity ───────────────────────────────────────────────────
    results: dict[str, dict] = {}

    for sid in sku_ids:
        obs   = sku_panels[sid]
        panel_rows = [r for r in rows if str(r["sku_id"]) == sid]

        # weighted avg price & cost from raw rows (after outlier filter)
        wmed  = _wmed_price([r["selling_price"] for r in panel_rows],
                             [r["units_sold"]    for r in panel_rows])
        valid = [r for r in panel_rows
                 if 0.5*wmed <= r["selling_price"] <= 1.5*wmed]
        total_u = sum(r["units_sold"]  for r in valid) or 1
        avg_p   = sum(r["selling_price"] * r["units_sold"] for r in valid) / total_u
        avg_c   = sum(r["buying_price"]  * r["units_sold"] for r in valid) / total_u

        sku_e, method = _sku_elasticity(obs, min_price_points)
        if sku_e is None:
            sku_e  = cat_e
            method = "类目弹性备用值"

        results[sid] = {
            "elasticity": sku_e,
            "method":     method,
            "avg_price":  avg_p,
            "avg_cost":   avg_c,
        }

    return results, cat_e


def _sku_elasticity(
    obs: list[dict],
    min_pts: int,
) -> tuple[float | None, str]:
    """Try store-FE OLS for a single SKU.  Returns (elasticity, method) or (None, '')."""
    if len(obs) < max(min_pts, 6):
        return None, ""

    # Within-store demeaning
    dm = _demean_within(obs, "store")

    # Check meaningful price variation
    lnp_var = sum(o["ln_price"] ** 2 for o in dm)
    if lnp_var < 0.005:
        return None, ""

    X, y = _build_features(dm)
    if len(X) < len(X[0]) + 2:
        return None, ""
    coef = _ols(X, y)
    if coef is None:
        return None, ""

    e = coef[0]  # first coefficient = ln_price
    if math.isnan(e) or math.isinf(e) or e > 0 or abs(e) > ELASTICITY_ABS_CAP:
        return None, ""

    return e, f"SKU门店固定效应估算（{len(obs)}个观测值）"


def _category_elasticity(sku_panels: dict[str, list[dict]]) -> float:
    """
    Pool all SKUs with double-demeaning (store FE + SKU FE).
    Returns a single category elasticity (should be negative).
    """
    all_dm: list[dict] = []

    for sid, obs in sku_panels.items():
        if len(obs) < 2:
            continue
        # SKU-level mean (removes SKU FE)
        mean_lnp = sum(o["ln_price"] for o in obs) / len(obs)
        mean_lnu = sum(o["ln_units"] for o in obs) / len(obs)
        # Store-level demeaning within SKU
        store_groups: dict[str, list[dict]] = defaultdict(list)
        for o in obs:
            store_groups[o["store"]].append(o)
        for grp in store_groups.values():
            sm_lnp = sum(o["ln_price"] for o in grp) / len(grp)
            sm_lnu = sum(o["ln_units"] for o in grp) / len(grp)
            for o in grp:
                all_dm.append({
                    "ln_price":        o["ln_price"]  - sm_lnp - mean_lnp,
                    "ln_units":        o["ln_units"]  - sm_lnu - mean_lnu,
                    "is_holiday_week": o["is_holiday_week"],
                    "month":           o["month"],
                })

    if len(all_dm) < 4:
        return -1.5   # default fallback

    X, y = _build_features(all_dm)
    if len(X) < len(X[0]) + 2:
        # Simple bivariate fallback
        n = len(all_dm)
        mean_x = sum(o["ln_price"] for o in all_dm) / n
        mean_y = sum(o["ln_units"] for o in all_dm) / n
        num = sum((o["ln_price"]-mean_x)*(o["ln_units"]-mean_y) for o in all_dm)
        den = sum((o["ln_price"]-mean_x)**2 for o in all_dm)
        e = num / den if den > 1e-10 else -1.5
        return e if e < 0 else -1.5

    coef = _ols(X, y)
    if coef is None:
        return -1.5
    e = coef[0]
    if math.isnan(e) or math.isinf(e) or e > 0:
        return -1.5
    return max(e, -15.0)


def _wmed_price(prices: list[float], weights: list[float]) -> float:
    pairs = sorted(zip(prices, weights))
    total = sum(weights)
    cum = 0.0
    for p, w in pairs:
        cum += w
        if cum >= total / 2:
            return p
    return pairs[-1][0] if pairs else 1.0
