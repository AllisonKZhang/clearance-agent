"""
pdf_export.py – Generate a PDF clearance plan report (returns bytes).
"""

from __future__ import annotations
import io
import unicodedata
from datetime import date
from fpdf import FPDF, XPos, YPos

from .planner import ClearancePlan, monitoring_triggers


def _ascii(text: str) -> str:
    """Transliterate to ASCII-safe string for fpdf2 Helvetica font.

    Chinese / non-latin characters are converted to their closest ASCII
    representation via NFKD decomposition; any remaining non-ASCII bytes
    are dropped so fpdf2 never raises FPDFUnicodeEncodingException.
    """
    nfkd = unicodedata.normalize("NFKD", text)
    return nfkd.encode("ascii", errors="ignore").decode("ascii")

# Palette
BLUE    = (30,  80,  160)
LT_BLUE = (220, 230, 245)
MED_B   = (180, 205, 240)
DGREY   = (50,  50,  50)
MGREY   = (120, 120, 120)
LGREY   = (245, 245, 245)
WHITE   = (255, 255, 255)
GREEN   = (20,  120, 60)
RED     = (170, 30,  30)
AMBER   = (190, 110, 0)


class _PDF(FPDF):
    def header(self):
        self.set_fill_color(*BLUE)
        self.rect(0, 0, 210, 13, "F")
        self.set_text_color(*WHITE)
        self.set_font("Helvetica", "B", 10)
        self.set_xy(10, 3)
        self.cell(0, 7, f"Clearance Plan  |  SKU {self._meta['sku_id']}  -  {self._meta['product_name']}")
        self.set_text_color(*DGREY)
        self.ln()

    def footer(self):
        self.set_y(-11)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*MGREY)
        self.cell(
            0, 5,
            f"Generated {date.today()}  |  "
            f"Elasticity: {self._meta['elasticity']:.2f} ({self._meta['method']})  |  "
            f"Page {self.page_no()}",
            align="C",
        )
        self.set_text_color(*DGREY)

    def sec(self, title: str):
        self.ln(4)
        self.set_fill_color(*BLUE)
        self.set_text_color(*WHITE)
        self.set_font("Helvetica", "B", 9)
        self.cell(0, 7, "  " + title)
        self.ln()
        self.set_text_color(*DGREY)
        self.ln(1)

    def hrow(self, widths, labels):
        self.set_font("Helvetica", "B", 8)
        self.set_fill_color(*MED_B)
        for w, h in zip(widths, labels):
            self.cell(w, 6, " " + h, border=1, fill=True)
        self.ln()

    def drow(self, widths, cells, even: bool, colors=None):
        self.set_fill_color(*(LGREY if even else WHITE))
        colors = colors or [DGREY] * len(cells)
        for w, c, col in zip(widths, cells, colors):
            self.set_text_color(*col)
            self.cell(w, 6, " " + str(c), border=1, fill=even)
        self.set_text_color(*DGREY)
        self.ln()


def generate_pdf(plan: ClearancePlan) -> bytes:
    pdf = _PDF()
    pdf._meta = {
        "sku_id":       _ascii(str(plan.sku_id)),
        "product_name": _ascii(plan.product_name[:60]),
        "elasticity":   plan.elasticity,
        "method":       _ascii(plan.elasticity_method),
    }
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_left_margin(10)
    pdf.set_right_margin(10)

    margin_pct = (plan.base_price - plan.unit_cost) / plan.base_price * 100

    # ── SUMMARY BOXES ──────────────────────────────────────────────────────
    pdf.ln(4)
    boxes = [
        ("Units on Hand",  str(plan.inventory)),
        ("Base Price",     f"CNY {plan.base_price:.2f}"),
        ("Unit Cost",      f"CNY {plan.unit_cost:.2f}"),
        ("Start Margin",   f"{margin_pct:.1f}%"),
        ("Exp. Margin",    f"CNY {plan.total_margin:,}"),
        ("vs. No Action",  f"+CNY {plan.total_margin - plan.no_action_margin:,}"),
    ]
    BW = 190 / len(boxes)
    x0, y0 = 10, pdf.get_y()
    for label, val in boxes:
        color = GREEN if "vs." in label or "Exp." in label else BLUE
        pdf.set_fill_color(*color)
        pdf.rect(x0, y0, BW - 1, 16, "F")
        pdf.set_text_color(*WHITE)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_xy(x0, y0 + 1)
        pdf.cell(BW - 1, 7, val, align="C")
        pdf.set_font("Helvetica", "", 6.5)
        pdf.set_xy(x0, y0 + 8)
        pdf.cell(BW - 1, 5, label, align="C")
        x0 += BW
    pdf.set_xy(10, y0 + 18)
    pdf.set_text_color(*DGREY)

    # ── INPUTS ────────────────────────────────────────────────────────────
    pdf.sec("Model Inputs")
    inputs = [
        ("SKU",           f"{_ascii(str(plan.sku_id))}  -  {_ascii(plan.product_name)}"),
        ("Base price",    f"CNY {plan.base_price:.2f} (posted retail)   |   Cost: CNY {plan.unit_cost:.2f}   |   Margin: {margin_pct:.1f}%"),
        ("Elasticity",    f"{plan.elasticity:.2f}  ({plan.elasticity_method})"),
        ("Inventory",     f"{plan.inventory} units on hand"),
        ("Clearance from",plan.start_date.strftime("%d %b %Y")),
        ("Max duration",  "8 weeks"),
        ("Discount steps","0%  10%  20%  30%  40%  50%  (7-day intervals)"),
        ("Objective",     "Maximise margin while clearing all inventory within 8 weeks"),
        ("Seasonal adj.", "Monthly seasonal demand indices (Beijing) + public-holiday lifts applied"),
    ]
    for k, v in inputs:
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_fill_color(*LGREY)
        pdf.cell(54, 6, "  " + k, border=0, fill=True)
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(0, 6, "  " + v, border=0, fill=True)
        pdf.ln()
    pdf.ln(1)

    # ── WEEK-BY-WEEK SCHEDULE ──────────────────────────────────────────────
    pdf.sec(f"Week-by-Week Clearance Schedule  (Start: {plan.start_date.strftime('%d %b %Y')})")
    sc = [10, 44, 22, 24, 28, 26, 24, 28]
    pdf.hrow(sc, ["Wk", "Period", "Discount", "Price", "Proj. Sales", "Cumulative", "Remaining", "Wk Margin"])
    for w in plan.weeks:
        period  = f"{w.start_date.strftime('%-d %b')} - {w.end_date.strftime('%-d %b %Y')}"
        disc_lbl= f"{round(w.discount_pct*100):.0f}% off" if w.discount_pct else "Full price"
        partial = "*" if w.is_partial else ""
        row = [
            str(w.week_num), period, disc_lbl,
            f"CNY {w.posted_price:.2f}",
            f"{w.proj_units}{partial}",
            str(int(w.cumulative)),
            str(int(w.remaining)),
            f"CNY {w.week_margin:,}",
        ]
        fill = w.week_num % 2 == 0
        pdf.set_fill_color(*(LT_BLUE if w.remaining == 0 else (LGREY if fill else WHITE)))
        for col_w, c in zip(sc, row):
            pdf.cell(col_w, 6, " " + c, border=1, fill=True)
        pdf.ln()
    # totals
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(*LT_BLUE)
    pdf.set_text_color(*BLUE)
    tot = ["", "TOTAL", "", "", str(plan.total_units), "", "0", f"CNY {plan.total_margin:,}"]
    for col_w, c in zip(sc, tot):
        pdf.cell(col_w, 6, " " + c, border=1, fill=True)
    pdf.ln()
    pdf.set_text_color(*MGREY)
    pdf.set_font("Helvetica", "I", 7.5)
    if any(w.is_partial for w in plan.weeks):
        pdf.cell(0, 5, " * Partial week - inventory clears before period end.")
        pdf.ln()
    pdf.set_text_color(*DGREY)
    pdf.ln(1)

    # ── OUTCOME COMPARISON ────────────────────────────────────────────────
    pdf.sec("Outcome Comparison")
    cc = [82, 36, 28, 44]
    pdf.hrow(cc, ["Scenario", "Total Margin", "Unsold", "Margin Loss vs Ideal"])
    cmp = [
        ("Ideal  (all inventory at full price, unlimited time)",
         f"CNY {plan.ideal_margin:,}", "0", "-", DGREY),
        ("Recommended clearance plan  [OPTIMAL]",
         f"CNY {plan.total_margin:,}", "0", f"- CNY {plan.ideal_margin - plan.total_margin:,}", GREEN),
        ("No action  (0% discount, 8 weeks)",
         f"CNY {plan.no_action_margin:,}",
         str(plan.no_action_unsold),
         f"- CNY {plan.ideal_margin - plan.no_action_margin:,}", RED),
    ]
    for i, (label, m, u, vs, nc) in enumerate(cmp):
        pdf.data_row = lambda *a, **kw: None   # suppress – use drow
        pdf.set_fill_color(*(LGREY if i % 2 == 0 else WHITE))
        pdf.set_text_color(*DGREY)
        pdf.set_font("Helvetica", "B" if i == 1 else "", 8)
        pdf.cell(cc[0], 6, " " + label, border=1, fill=i%2==0)
        pdf.set_text_color(*nc)
        pdf.set_font("Helvetica", "", 8)
        for j, (c, w) in enumerate(zip([m, u, vs], cc[1:])):
            pdf.cell(w, 6, " " + c, border=1, fill=i%2==0)
        pdf.ln()
    pdf.set_text_color(*DGREY)
    pdf.ln(1)

    # ── SENSITIVITY ───────────────────────────────────────────────────────
    pdf.sec("Sensitivity to Elasticity Assumption")
    sv = [28, 34, 128]
    pdf.hrow(sv, ["Elasticity", "Exp. Margin", "Implication"])
    for i, row in enumerate(plan.sensitivity):
        assumed = row["assumed"]
        fill = i % 2 == 0
        pdf.set_fill_color(*(LGREY if fill else WHITE))
        col = BLUE if assumed else DGREY
        pdf.set_font("Helvetica", "B" if assumed else "", 8)
        lbl = f"{row['elasticity']:.2f}" + ("  <--" if assumed else "")
        pdf.set_text_color(*col)
        pdf.cell(sv[0], 6, " " + lbl, border=1, fill=fill)
        pdf.cell(sv[1], 6, f" CNY {row['margin']:,}", border=1, fill=fill)
        pdf.set_font("Helvetica", "I" if assumed else "", 8)
        pdf.cell(sv[2], 6, " " + row["outcome"], border=1, fill=fill)
        pdf.ln()
    pdf.set_text_color(*DGREY)
    pdf.ln(1)

    # ── MONITORING TRIGGERS ───────────────────────────────────────────────
    pdf.sec("Monitoring Triggers")
    tv = [34, 58, 98]
    pdf.hrow(tv, ["Checkpoint", "Trigger Condition", "Recommended Action"])
    for i, t in enumerate(monitoring_triggers(plan)):
        fill = i % 2 == 0
        pdf.set_fill_color(*(LGREY if fill else WHITE))
        pdf.set_text_color(*DGREY)
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(tv[0], 6, f" Wk {t['week']} ({t['date']})", border=1, fill=fill)
        pdf.set_text_color(*RED)
        pdf.cell(tv[1], 6, f" {t['metric']}", border=1, fill=fill)
        pdf.set_text_color(*GREEN)
        pdf.cell(tv[2], 6, f" {t['action']}", border=1, fill=fill)
        pdf.ln()
    pdf.set_text_color(*DGREY)

    # ── FOOTNOTES ─────────────────────────────────────────────────────────
    pdf.ln(3)
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(*MGREY)
    pdf.multi_cell(
        0, 4,
        "Notes: Demand model Q = baseline x (1 - discount)^elasticity.  "
        "Baseline is the chain-wide weekly run rate from the most recent 4 weeks, adjusted for Beijing seasonal "
        "seasonal demand indices (peak Jul/Aug, trough Jan/Dec) and public-holiday demand lifts "
        "(National Day +20%, Labour Day +15%, etc.).  "
        "Elasticity estimated via pooled fixed-effects OLS with store and month controls; "
        "category fallback applied when SKU-level data is insufficient.  "
        "All prices are posted retail.  Monitor weekly actuals against projections and act on triggers above.",
    )

    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()
