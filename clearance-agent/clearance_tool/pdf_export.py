"""
pdf_export.py – 生成中文 PDF 清仓计划报告（返回字节流）。
"""

from __future__ import annotations
import io
from datetime import date
from pathlib import Path
from fpdf import FPDF

from .planner import ClearancePlan, monitoring_triggers

# Prefer the bundled font (works on all platforms); fall back to macOS system font
_BUNDLED_FONT = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "NotoSansSC.ttf"
_MACOS_FONT   = Path("/System/Library/Fonts/STHeiti Medium.ttc")
_FONT_PATH    = str(_BUNDLED_FONT if _BUNDLED_FONT.exists() else _MACOS_FONT)

# 调色板
BLUE    = (30,  80,  160)
LT_BLUE = (220, 230, 245)
MED_B   = (180, 205, 240)
DGREY   = (50,  50,  50)
MGREY   = (120, 120, 120)
LGREY   = (245, 245, 245)
WHITE   = (255, 255, 255)
GREEN   = (20,  120, 60)
RED     = (170, 30,  30)


class _PDF(FPDF):
    def header(self):
        self.set_fill_color(*BLUE)
        self.rect(0, 0, 210, 13, "F")
        self.set_text_color(*WHITE)
        self.set_font("Heiti", size=10)
        self.set_xy(10, 3)
        self.cell(0, 7, f"清仓计划  |  SKU {self._meta['sku_id']}  —  {self._meta['product_name']}")
        self.set_text_color(*DGREY)
        self.ln()

    def footer(self):
        self.set_y(-11)
        self.set_font("Heiti", size=7)
        self.set_text_color(*MGREY)
        self.cell(
            0, 5,
            f"生成日期：{date.today()}  |  "
            f"弹性系数：{self._meta['elasticity']:.2f}（{self._meta['method']}）  |  "
            f"第 {self.page_no()} 页",
            align="C",
        )
        self.set_text_color(*DGREY)

    def sec(self, title: str):
        self.ln(4)
        self.set_fill_color(*BLUE)
        self.set_text_color(*WHITE)
        self.set_font("Heiti", size=9)
        self.cell(0, 7, "  " + title, fill=True)
        self.ln()
        self.set_text_color(*DGREY)
        self.ln(1)

    def hrow(self, widths, labels):
        self.set_font("Heiti", size=8)
        self.set_fill_color(*MED_B)
        for w, h in zip(widths, labels):
            self.cell(w, 6, " " + h, border=1, fill=True)
        self.ln()


def generate_pdf(plan: ClearancePlan) -> bytes:
    pdf = _PDF()
    pdf.add_font("Heiti", fname=_FONT_PATH)
    pdf._meta = {
        "sku_id":       str(plan.sku_id),
        "product_name": plan.product_name[:40],
        "elasticity":   plan.elasticity,
        "method":       plan.elasticity_method,
    }
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_left_margin(10)
    pdf.set_right_margin(10)

    margin_pct = (plan.base_price - plan.unit_cost) / plan.base_price * 100
    uplift = plan.total_margin - plan.no_action_margin

    # ── 摘要指标框 ──────────────────────────────────────────────────────────
    pdf.ln(4)
    boxes = [
        ("现有库存",   f"{plan.inventory} 件"),
        ("基准售价",   f"¥{plan.base_price:.2f}"),
        ("单件成本",   f"¥{plan.unit_cost:.2f}"),
        ("起始毛利率", f"{margin_pct:.1f}%"),
        ("预计总毛利", f"¥{plan.total_margin:,}"),
        ("对比不促销", f"+¥{uplift:,}" if uplift >= 0 else f"¥{uplift:,}"),
    ]
    BW = 190 / len(boxes)
    x0, y0 = 10, pdf.get_y()
    for label, val in boxes:
        color = GREEN if label in ("预计总毛利", "对比不促销") else BLUE
        pdf.set_fill_color(*color)
        pdf.rect(x0, y0, BW - 1, 16, "F")
        pdf.set_text_color(*WHITE)
        pdf.set_font("Heiti", size=10)
        pdf.set_xy(x0, y0 + 1)
        pdf.cell(BW - 1, 7, val, align="C")
        pdf.set_font("Heiti", size=6)
        pdf.set_xy(x0, y0 + 8)
        pdf.cell(BW - 1, 5, label, align="C")
        x0 += BW
    pdf.set_xy(10, y0 + 18)
    pdf.set_text_color(*DGREY)

    # ── 基本参数 ────────────────────────────────────────────────────────────
    pdf.sec("基本参数")
    n_weeks = len(plan.weeks)
    inputs = [
        ("SKU",      f"{plan.sku_id}  —  {plan.product_name}"),
        ("基准售价", f"¥{plan.base_price:.2f}（标牌零售价）  |  成本：¥{plan.unit_cost:.2f}  |  毛利率：{margin_pct:.1f}%"),
        ("弹性系数", f"{plan.elasticity:.2f}（{plan.elasticity_method}）"),
        ("现有库存", f"{plan.inventory} 件"),
        ("开始日期", plan.start_date.strftime("%Y年%m月%d日")),
        ("最长周期", f"{n_weeks} 周"),
        ("折扣档位", "0%、10%、20%、30%、40%、50%（每7天一档）"),
        ("优化目标", "在截止日期前清完所有库存，同时最大化毛利"),
        ("季节调整", "北京月度冰淇淋需求季节系数 + 节假日需求提升"),
    ]
    for k, v in inputs:
        pdf.set_font("Heiti", size=8)
        pdf.set_fill_color(*LGREY)
        pdf.cell(28, 6, "  " + k, border=0, fill=True)
        pdf.cell(0,  6, "  " + v, border=0, fill=True)
        pdf.ln()
    pdf.ln(1)

    # ── 逐周清仓方案 ────────────────────────────────────────────────────────
    pdf.sec(f"逐周清仓方案（开始：{plan.start_date.strftime('%Y年%m月%d日')}）")
    sc = [10, 46, 22, 24, 26, 26, 24, 28]
    pdf.hrow(sc, ["周次", "日期区间", "折扣力度", "标牌售价", "预计销量", "累计销量", "剩余库存", "本周毛利"])
    for w in plan.weeks:
        period   = f"{w.start_date.strftime('%m月%d日')}—{w.end_date.strftime('%m月%d日')}"
        disc_lbl = f"{round(w.discount_pct*100):.0f}% 折扣" if w.discount_pct else "原价"
        partial  = "*" if w.is_partial else ""
        row = [
            str(w.week_num), period, disc_lbl,
            f"¥{w.posted_price:.2f}",
            f"{w.proj_units}{partial}",
            str(int(w.cumulative)),
            str(int(w.remaining)),
            f"¥{w.week_margin:,}",
        ]
        pdf.set_font("Heiti", size=8)
        pdf.set_fill_color(*(LT_BLUE if w.remaining == 0 else (LGREY if w.week_num % 2 == 0 else WHITE)))
        for col_w, c in zip(sc, row):
            pdf.cell(col_w, 6, " " + c, border=1, fill=True)
        pdf.ln()
    # 合计行
    pdf.set_font("Heiti", size=8)
    pdf.set_fill_color(*LT_BLUE)
    pdf.set_text_color(*BLUE)
    tot = ["", "合计", "", "", str(plan.total_units), "", "0", f"¥{plan.total_margin:,}"]
    for col_w, c in zip(sc, tot):
        pdf.cell(col_w, 6, " " + c, border=1, fill=True)
    pdf.ln()
    pdf.set_text_color(*MGREY)
    pdf.set_font("Heiti", size=7)
    if any(w.is_partial for w in plan.weeks):
        pdf.cell(0, 5, " * 最后一周：库存在该周期内提前清完。")
        pdf.ln()
    pdf.set_text_color(*DGREY)
    pdf.ln(1)

    # ── 方案对比 ────────────────────────────────────────────────────────────
    pdf.sec("方案对比")
    cc = [82, 36, 28, 44]
    pdf.hrow(cc, ["方案", "总毛利", "未售库存", "对比理想情形差距"])
    cmp_rows = [
        ("理想情形（全部按原价售出，不限时间）",
         f"¥{plan.ideal_margin:,}", "0", "—", DGREY),
        ("推荐清仓方案【最优】",
         f"¥{plan.total_margin:,}", "0",
         f"−¥{plan.ideal_margin - plan.total_margin:,}", GREEN),
        ("不促销（零折扣）",
         f"¥{plan.no_action_margin:,}",
         str(plan.no_action_unsold),
         f"−¥{plan.ideal_margin - plan.no_action_margin:,}", RED),
    ]
    for i, (label, m, u, vs, nc) in enumerate(cmp_rows):
        pdf.set_fill_color(*(LGREY if i % 2 == 0 else WHITE))
        pdf.set_text_color(*DGREY)
        pdf.set_font("Heiti", size=8)
        pdf.cell(cc[0], 6, " " + label, border=1, fill=i % 2 == 0)
        pdf.set_text_color(*nc)
        for c, w in zip([m, u, vs], cc[1:]):
            pdf.cell(w, 6, " " + c, border=1, fill=i % 2 == 0)
        pdf.ln()
    pdf.set_text_color(*DGREY)
    pdf.ln(1)

    # ── 弹性系数敏感性分析 ──────────────────────────────────────────────────
    pdf.sec("弹性系数敏感性分析")
    sv = [28, 34, 128]
    pdf.hrow(sv, ["弹性系数", "预计总毛利", "结果说明"])
    for i, row in enumerate(plan.sensitivity):
        assumed = row["assumed"]
        fill = i % 2 == 0
        pdf.set_fill_color(*(LGREY if fill else WHITE))
        col = BLUE if assumed else DGREY
        pdf.set_font("Heiti", size=8)
        lbl = f"{row['elasticity']:.2f}" + ("  ◀ 当前假设" if assumed else "")
        pdf.set_text_color(*col)
        pdf.cell(sv[0], 6, " " + lbl, border=1, fill=fill)
        pdf.cell(sv[1], 6, f" ¥{row['margin']:,}", border=1, fill=fill)
        pdf.cell(sv[2], 6, " " + row["outcome"], border=1, fill=fill)
        pdf.ln()
    pdf.set_text_color(*DGREY)
    pdf.ln(1)

    # ── 监控预警 ────────────────────────────────────────────────────────────
    pdf.sec("监控预警")
    tv = [34, 60, 96]
    pdf.hrow(tv, ["检查时间", "触发条件", "建议行动"])
    for i, t in enumerate(monitoring_triggers(plan)):
        fill = i % 2 == 0
        pdf.set_fill_color(*(LGREY if fill else WHITE))
        pdf.set_font("Heiti", size=8)
        pdf.set_text_color(*DGREY)
        pdf.cell(tv[0], 6, f" 第{t['week']}周（{t['date']}）", border=1, fill=fill)
        pdf.set_text_color(*RED)
        pdf.cell(tv[1], 6, f" {t['metric']}", border=1, fill=fill)
        pdf.set_text_color(*GREEN)
        pdf.cell(tv[2], 6, f" {t['action']}", border=1, fill=fill)
        pdf.ln()
    pdf.set_text_color(*DGREY)

    # ── 备注 ────────────────────────────────────────────────────────────────
    pdf.ln(3)
    pdf.set_font("Heiti", size=7)
    pdf.set_text_color(*MGREY)
    pdf.multi_cell(
        0, 4,
        "备注：需求模型 Q = 基线销量 × (1 − 折扣率)^弹性系数。"
        "基线销量取最近4周全链周均销量，并根据北京月度冰淇淋需求季节系数"
        "（7月/8月峰值，1月/12月谷底）及节假日需求提升（国庆+20%、劳动节+15%等）进行调整。"
        "弹性系数通过含门店与月份固定效应的混合OLS估算；"
        "当SKU层面数据不足时，自动回退至品类弹性备用值。"
        "所有价格均为标牌零售价。请每周对照实际销量与预测值，及时响应上方监控预警。",
    )

    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()
