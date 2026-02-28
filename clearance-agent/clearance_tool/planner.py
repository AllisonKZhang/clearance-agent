"""
planner.py – Clearance schedule optimiser.

Objective: maximise total margin collected while clearing all `inventory`
units within `max_weeks` weeks.

Algorithm: exhaustive search over all non-decreasing discount sequences
(from the allowed discount ladder) up to max_weeks steps, keeping only
combinations that clear the full inventory.  Each week uses the
season/holiday-adjusted baseline demand.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from datetime import date, timedelta

DISCOUNT_LADDER = [0.0, 0.10, 0.20, 0.30, 0.40, 0.50]
MAX_WEEKS       = 8


@dataclass
class WeekResult:
    week_num:       int
    start_date:     date
    end_date:       date
    discount_pct:   float          # 0.0 – 0.50
    posted_price:   float
    baseline_units: float          # seasonally-adjusted, no-discount
    proj_units:     float          # units expected to sell this week
    cumulative:     float
    remaining:      float
    week_margin:    float
    is_partial:     bool = False   # last week, inventory clears mid-week


@dataclass
class ClearancePlan:
    sku_id:         str
    product_name:   str
    base_price:     float
    unit_cost:      float
    inventory:      int
    elasticity:     float
    elasticity_method: str
    start_date:     date
    weeks:          list[WeekResult] = field(default_factory=list)
    total_margin:   float = 0.0
    total_units:    int   = 0
    clears_by:      date  | None = None

    # Comparison scenarios
    no_action_margin:  float = 0.0
    no_action_unsold:  int   = 0
    ideal_margin:      float = 0.0

    # Sensitivity
    sensitivity: list[dict] = field(default_factory=list)


def _demand(base: float, elasticity: float, discount: float) -> float:
    """Constant-elasticity demand: Q = base * (1 - discount)^elasticity."""
    if discount >= 1.0:
        return 0.0
    return base * math.pow(1.0 - discount, elasticity)


def _margin_per_unit(base_price: float, unit_cost: float, discount: float) -> float:
    return base_price * (1.0 - discount) - unit_cost


def optimise(
    sku_id:       str,
    product_name: str,
    base_price:   float,
    unit_cost:    float,
    inventory:    int,
    elasticity:   float,          # should be negative
    elasticity_method: str,
    baseline_rates: list[float],  # len == max_weeks, season-adjusted
    start_date:   date,
    max_weeks:    int = MAX_WEEKS,
    discount_ladder: list[float] = DISCOUNT_LADDER,
) -> ClearancePlan:
    """
    Find the non-decreasing discount schedule that maximises total margin
    while clearing `inventory` units within `max_weeks` weeks.
    """
    n_weeks = min(max_weeks, len(baseline_rates))

    best_margin  = -1e18
    best_schedule: list[float] = []

    def _simulate(schedule: list[float]) -> float:
        """Return total margin for a given discount schedule; penalise unsold."""
        rem = float(inventory)
        total = 0.0
        for i, d in enumerate(schedule):
            if rem <= 0:
                break
            q = _demand(baseline_rates[i], elasticity, d)
            sold = min(q, rem)
            total += sold * _margin_per_unit(base_price, unit_cost, d)
            rem -= sold
        if rem > 0:
            total -= rem * unit_cost   # sunk cost on unsold units
        return total

    # Enumerate all non-decreasing sequences of length 1..n_weeks
    # over the discount ladder, pruned by feasibility.
    # We represent a sequence as a tuple of (discount_index, weeks_at_this_level).
    # Depth-first with pruning.
    def _search(week: int, min_disc_idx: int, rem: float,
                current: list[float], current_margin: float):
        nonlocal best_margin, best_schedule

        if rem <= 0 or week >= n_weeks:
            m = current_margin
            if rem > 0:
                m -= rem * unit_cost
            if m > best_margin:
                best_margin  = m
                best_schedule = list(current)
            return

        # Prune: even at max discount for remaining weeks, can we clear?
        max_remaining = sum(
            _demand(baseline_rates[week + w], elasticity, discount_ladder[-1])
            for w in range(n_weeks - week)
        )
        if max_remaining < rem * 0.99:
            # Can't clear even with max discount – take what we can
            m = current_margin
            for w in range(n_weeks - week):
                q = _demand(baseline_rates[week + w], elasticity, discount_ladder[-1])
                sold = min(q, rem)
                m += sold * _margin_per_unit(base_price, unit_cost, discount_ladder[-1])
                rem -= sold
            if rem > 0:
                m -= rem * unit_cost
            if m > best_margin:
                best_margin  = m
                best_schedule = list(current) + [discount_ladder[-1]] * (n_weeks - week)
            return

        for di in range(min_disc_idx, len(discount_ladder)):
            d = discount_ladder[di]
            q = _demand(baseline_rates[week], elasticity, d)
            sold = min(q, rem)
            wm   = sold * _margin_per_unit(base_price, unit_cost, d)
            _search(week + 1, di, rem - sold,
                    current + [d], current_margin + wm)

    _search(0, 0, float(inventory), [], 0.0)

    if not best_schedule:
        # Fallback: all at max discount
        best_schedule = [discount_ladder[-1]] * n_weeks

    # ── Build ClearancePlan from best_schedule ──────────────────────────────
    plan = ClearancePlan(
        sku_id=sku_id,
        product_name=product_name,
        base_price=base_price,
        unit_cost=unit_cost,
        inventory=inventory,
        elasticity=elasticity,
        elasticity_method=elasticity_method,
        start_date=start_date,
    )

    rem       = float(inventory)
    cumul     = 0.0
    total_m   = 0.0

    for wk_idx, d in enumerate(best_schedule):
        if rem <= 0:
            break
        wk_num     = wk_idx + 1
        wk_start   = start_date + timedelta(weeks=wk_idx)
        wk_end     = wk_start + timedelta(days=6)
        q_full     = _demand(baseline_rates[wk_idx], elasticity, d)
        sold       = min(q_full, rem)
        wm         = sold * _margin_per_unit(base_price, unit_cost, d)
        is_partial = sold < q_full and rem <= q_full
        cumul     += sold
        rem       -= sold
        total_m   += wm

        plan.weeks.append(WeekResult(
            week_num       = wk_num,
            start_date     = wk_start,
            end_date       = wk_end,
            discount_pct   = d,
            posted_price   = round(base_price * (1.0 - d), 2),
            baseline_units = baseline_rates[wk_idx],
            proj_units     = round(sold),
            cumulative     = round(cumul),
            remaining      = max(0.0, round(rem)),
            week_margin    = round(wm),
            is_partial     = is_partial,
        ))

        if rem <= 0:
            plan.clears_by = wk_end
            break

    plan.total_margin = round(total_m)
    plan.total_units  = round(cumul)

    # ── Comparison scenarios ─────────────────────────────────────────────────
    # Ideal: all inventory sold at full price with current seasonal baseline
    ideal_m = sum(
        min(_demand(baseline_rates[w], elasticity, 0.0), float(inventory))
        * _margin_per_unit(base_price, unit_cost, 0.0)
        for w in range(n_weeks)
    )
    plan.ideal_margin = round(min(ideal_m, inventory * _margin_per_unit(base_price, unit_cost, 0.0)))

    # No action: 0% discount for max_weeks
    no_action_rem = float(inventory)
    no_action_m   = 0.0
    for w in range(n_weeks):
        q    = _demand(baseline_rates[w], elasticity, 0.0)
        sold = min(q, no_action_rem)
        no_action_m   += sold * _margin_per_unit(base_price, unit_cost, 0.0)
        no_action_rem -= sold
    if no_action_rem > 0:
        no_action_m -= no_action_rem * unit_cost
    plan.no_action_margin = round(no_action_m)
    plan.no_action_unsold = round(no_action_rem)

    # ── Sensitivity to elasticity ────────────────────────────────────────────
    for e_test in [-0.5, -1.0, elasticity, -1.5, -2.0, -2.5]:
        test_m  = 0.0
        test_rem= float(inventory)
        wks_used= 0.0
        for w, d in enumerate(best_schedule):
            if test_rem <= 0:
                break
            q    = _demand(baseline_rates[w], e_test, d)
            sold = min(q, test_rem)
            test_m   += sold * _margin_per_unit(base_price, unit_cost, d)
            wks_used += sold / q if q > 0 else 1.0
            test_rem -= sold
        if test_rem > 0:
            test_m   -= test_rem * unit_cost
            outcome   = f"{round(test_rem)} 件未售出（超过 {max_weeks} 周）"
        else:
            outcome   = f"约 {wks_used:.1f} 周内清仓完毕"

        plan.sensitivity.append({
            "elasticity": round(e_test, 2),
            "assumed":    abs(e_test - elasticity) < 0.01,
            "margin":     round(test_m),
            "outcome":    outcome,
        })

    return plan


# ── Monitoring triggers ───────────────────────────────────────────────────────

def monitoring_triggers(plan: ClearancePlan) -> list[dict]:
    """Return a list of checkpoints with thresholds and suggested actions."""
    triggers = []
    for wk in plan.weeks[:5]:
        threshold = round(wk.proj_units * 0.85)
        if wk.week_num == 1:
            action = "提前一周进入下一折扣档位"
        elif wk.discount_pct == 0.0:
            action = "立即启动九折促销"
        else:
            next_d = min(wk.discount_pct + 0.10, 0.50)
            action = f"提前一周升级至 {round(next_d*100):.0f}% 折扣"
        triggers.append({
            "week":      wk.week_num,
            "date":      wk.end_date.strftime("%m月%d日"),
            "threshold": threshold,
            "metric":    f"本周实际销量 < {threshold} 件",
            "action":    action,
        })
    return triggers
