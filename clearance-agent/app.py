"""
app.py – 物美超值清仓计划工具（采购端）

本地运行：
  streamlit run app.py

云端部署：
  推送至 GitHub，在 share.streamlit.io 一键部署。
"""

from __future__ import annotations
import io
import sys
import csv
from datetime import date, timedelta
from pathlib import Path

import streamlit as st

# ── path setup ───────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from clearance_tool.data import (
    load_csv_bytes, sku_summary, weekly_baseline,
    seasonal_adjusted_baseline, SEASONAL_INDEX,
)
from clearance_tool.elasticity import estimate_elasticity
from clearance_tool.planner import optimise, monitoring_triggers, DISCOUNT_LADDER
from clearance_tool.pdf_export import generate_pdf

# ── page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="清仓计划工具",
    page_icon="🏪",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── colour / style ───────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main-title   { font-size:2rem; font-weight:700; color:#1e50a0; }
    .step-header  { font-size:1.15rem; font-weight:600; color:#1e50a0;
                    border-left:4px solid #1e50a0; padding-left:8px; margin-top:1rem; }
    .metric-box   { background:#f0f4fc; border-radius:8px; padding:12px 16px;
                    text-align:center; }
    .metric-val   { font-size:1.5rem; font-weight:700; color:#1e50a0; }
    .metric-lbl   { font-size:0.78rem; color:#555; margin-top:2px; }
    .green        { color:#147840; }
    .amber        { color:#b87000; }
    .red          { color:#a01e1e; }
    .note         { font-size:0.82rem; color:#666; font-style:italic; }
    footer        { visibility:hidden; }
</style>
""", unsafe_allow_html=True)


# ── helpers ──────────────────────────────────────────────────────────────────

def _metric(label: str, value: str, color: str = "#1e50a0") -> str:
    return f"""
    <div class="metric-box">
      <div class="metric-val" style="color:{color}">{value}</div>
      <div class="metric-lbl">{label}</div>
    </div>"""


def _template_csv() -> bytes:
    """返回 CSV 数据模板。"""
    header = ["date", "store_id", "store_name", "sku_id", "product_name",
              "buying_price", "selling_price", "units_sold"]
    samples = [
        ["2025-09-01", "S001", "良乡时光汇店",  "SKU001", "商品A",   7.50, 14.90, 12],
        ["2025-09-01", "S002", "丰台新广场店",  "SKU001", "商品A",   7.50, 14.90,  9],
        ["2025-09-08", "S001", "良乡时光汇店",  "SKU001", "商品A",   7.50, 13.41, 14],
        ["2025-09-08", "S002", "丰台新广场店",  "SKU001", "商品A",   7.50, 13.41, 11],
        ["2025-09-01", "S001", "良乡时光汇店",  "SKU002", "商品B",   5.20,  9.90, 20],
        ["2025-09-01", "S002", "丰台新广场店",  "SKU002", "商品B",   5.20,  9.90, 17],
    ]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(samples)
    return buf.getvalue().encode("utf-8-sig")


# ── sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    logo_path = ROOT / "logo.png"
    if logo_path.exists():
        st.image(str(logo_path), width=160)
    st.markdown("## 清仓计划工具")
    st.markdown("**商品品类 · 北京**")
    st.divider()
    st.markdown("### 使用说明")
    st.markdown("""
1. **下载** 数据模板
2. **填写** 历史销售记录
3. **上传** CSV 文件
4. **选择** 需清仓的商品
5. **填写** 库存数量及开始日期
6. **生成** 并下载清仓方案
""")
    st.divider()
    st.download_button(
        label="⬇️  下载数据模板（CSV）",
        data=_template_csv(),
        file_name="clearance_sales_template.csv",
        mime="text/csv",
        use_container_width=True,
    )
    st.markdown('<p class="note">支持最大 50 MB 的 CSV 文件</p>',
                unsafe_allow_html=True)


# ── main ─────────────────────────────────────────────────────────────────────
st.markdown('<div class="main-title">清仓计划工具</div>',
            unsafe_allow_html=True)
st.markdown("上传历史销售数据，选择商品，一键生成最优逐周折扣方案。")
st.divider()

# ── 第一步：上传数据 ─────────────────────────────────────────────────────────
st.markdown('<div class="step-header">第一步 — 上传历史销售数据</div>',
            unsafe_allow_html=True)

uploaded = st.file_uploader(
    "上传 CSV 文件（请使用左侧边栏提供的模板）",
    type=["csv"],
    label_visibility="collapsed",
)

rows: list[dict] = []
errors: list[str] = []

if uploaded:
    raw = uploaded.read()
    rows, errors = load_csv_bytes(raw)
    if errors:
        for e in errors[:5]:
            st.error(e)
        if len(errors) > 5:
            st.warning(f"……另有 {len(errors)-5} 条错误未显示。")
    elif rows:
        st.success(
            f"✅  已加载 **{len(rows):,}** 条记录，共 "
            f"**{len(set(r['store_id'] for r in rows))}** 家门店、"
            f"**{len(set(str(r['sku_id']) for r in rows))}** 个 SKU。"
        )

if not rows:
    st.info("👈  请先从左侧下载模板，填写完毕后上传 CSV 文件。")
    st.stop()

# ── 第二步：价格弹性估算 ─────────────────────────────────────────────────────
st.markdown('<div class="step-header">第二步 — 价格弹性估算</div>',
            unsafe_allow_html=True)

with st.spinner("正在估算价格弹性（含季节与节假日控制变量）……"):
    elasticities, cat_e = estimate_elasticity(rows)

summary = sku_summary(rows)

# 构建展示表格
e_table = []
for sid, info in sorted(summary.items(),
                         key=lambda x: -x[1]["total_units"]):
    e_info   = elasticities.get(sid, {})
    e_val    = e_info.get("elasticity", cat_e)
    method   = e_info.get("method", "类目弹性备用值")
    avg_p    = e_info.get("avg_price", info["avg_price"])
    avg_c    = e_info.get("avg_cost",  info["avg_cost"])
    margin   = (avg_p - avg_c) / avg_p * 100 if avg_p else 0
    e_table.append({
        "SKU":      sid,
        "商品名称": info["name"],
        "平均售价": f"¥{avg_p:.2f}",
        "平均成本": f"¥{avg_c:.2f}",
        "毛利率":   f"{margin:.1f}%",
        "弹性系数": f"{e_val:.2f}",
        "估算方法": method,
    })

with st.expander("📊 各 SKU 弹性系数一览", expanded=False):
    st.table(e_table)
    st.caption(
        f"类目弹性备用值：**{cat_e:.2f}**（含季节与节假日控制变量）"
    )

# ── 第三步：选择商品 ─────────────────────────────────────────────────────────
st.markdown('<div class="step-header">第三步 — 选择商品及填写清仓参数</div>',
            unsafe_allow_html=True)

sku_options = {
    f"{sid}  —  {info['name']}": sid
    for sid, info in sorted(summary.items(),
                             key=lambda x: -x[1]["total_units"])
}

col_sel, col_inv = st.columns([3, 1])
with col_sel:
    sku_label = st.selectbox("选择需清仓的商品", list(sku_options.keys()))
    selected_sku = sku_options[sku_label]

with col_inv:
    inventory = st.number_input(
        "现有库存（件）", min_value=1, value=500, step=10,
        help="全链库存总量",
    )

sel_summary = summary[selected_sku]
sel_e_info  = elasticities.get(selected_sku, {})
avg_p = sel_e_info.get("avg_price", sel_summary["avg_price"])
avg_c = sel_e_info.get("avg_cost",  sel_summary["avg_cost"])

col_p, col_c, col_d = st.columns(3)
with col_p:
    base_price = st.number_input(
        "清仓基准售价（元）",
        min_value=0.01,
        value=round(avg_p * 1.05, 1),
        step=0.5,
        help="折扣前的标牌零售价",
    )
with col_c:
    unit_cost = st.number_input(
        "单件进货成本（元）",
        min_value=0.01,
        value=round(avg_c, 2),
        step=0.1,
        help="采购单价",
    )
with col_d:
    start_date = st.date_input(
        "清仓开始日期",
        value=date.today(),
        min_value=date.today(),
    )

# 快速毛利预览
if base_price and unit_cost:
    gm = (base_price - unit_cost) / base_price * 100
    col1, col2, col3 = st.columns(3)
    col1.markdown(_metric("起始毛利率",
                           f"{gm:.1f}%",
                           "#147840" if gm > 0 else "#a01e1e"),
                  unsafe_allow_html=True)
    col2.markdown(_metric("打八折后毛利率",
                           f"{(base_price*0.8-unit_cost)/base_price*100:.1f}%"),
                  unsafe_allow_html=True)
    col3.markdown(_metric("打七折后毛利率",
                           f"{(base_price*0.7-unit_cost)/base_price*100:.1f}%",
                           "#b87000" if (base_price*0.7-unit_cost) > 0 else "#a01e1e"),
                  unsafe_allow_html=True)
    st.markdown("")

# ── 第四步：生成清仓方案 ─────────────────────────────────────────────────────
st.markdown('<div class="step-header">第四步 — 生成清仓方案</div>',
            unsafe_allow_html=True)

if st.button("🚀  生成清仓方案", type="primary", use_container_width=True):
    if unit_cost >= base_price:
        st.error("进货成本须低于基准售价。")
        st.stop()

    e_val   = sel_e_info.get("elasticity", cat_e)
    e_meth  = sel_e_info.get("method", "类目弹性备用值")
    base_wk = weekly_baseline(rows, selected_sku)

    if base_wk < 0.1:
        st.warning("该 SKU 近期无销售数据，以每周 1 件作为基准销量。")
        base_wk = 1.0

    # 按季节调整各周基线销量
    baselines = seasonal_adjusted_baseline(
        base_wk, start_date, n_weeks=8
    )

    with st.spinner("正在优化清仓方案……"):
        plan = optimise(
            sku_id            = selected_sku,
            product_name      = sel_summary["name"],
            base_price        = base_price,
            unit_cost         = unit_cost,
            inventory         = int(inventory),
            elasticity        = e_val,
            elasticity_method = e_meth,
            baseline_rates    = baselines,
            start_date        = start_date,
        )

    # ── 结果展示 ─────────────────────────────────────────────────────────
    st.divider()
    st.markdown("### 📋 清仓方案")

    # 核心指标
    uplift = plan.total_margin - plan.no_action_margin
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.markdown(_metric("预计总毛利", f"¥{plan.total_margin:,}", "#147840"),
                unsafe_allow_html=True)
    c2.markdown(_metric("对比不促销",
                         f"+¥{uplift:,}" if uplift >= 0 else f"¥{uplift:,}",
                         "#147840" if uplift >= 0 else "#a01e1e"),
                unsafe_allow_html=True)
    c3.markdown(_metric("已清库存", f"{plan.total_units} / {plan.inventory}"),
                unsafe_allow_html=True)
    c4.markdown(_metric("预计清仓日期",
                         plan.clears_by.strftime("%Y年%m月%d日") if plan.clears_by else ">8周"),
                unsafe_allow_html=True)
    c5.markdown(_metric("使用弹性系数", f"{e_val:.2f}"), unsafe_allow_html=True)
    st.markdown("")

    # 逐周方案表
    tbl = []
    for w in plan.weeks:
        tbl.append({
            "周次":     w.week_num,
            "日期区间": f"{w.start_date.strftime('%m月%d日')} – {w.end_date.strftime('%m月%d日')}",
            "折扣力度": f"{round(w.discount_pct*100):.0f}% 折扣" if w.discount_pct else "原价",
            "标牌售价": f"¥{w.posted_price:.2f}",
            "预计销量": w.proj_units,
            "累计销量": int(w.cumulative),
            "剩余库存": int(w.remaining),
            "本周毛利": f"¥{w.week_margin:,}",
        })
    st.table(tbl)

    # 弹性说明
    if "fallback" in e_meth.lower() or "备用" in e_meth:
        st.info(
            f"ℹ️  **弹性系数说明：** {e_meth}（e = {e_val:.2f}）。"
            "该 SKU 店内价格变动不足，已自动使用类目弹性备用值。"
            "建议密切关注第一周实际销量，及时调整方案。"
        )

    # 季节与节假日调整明细
    with st.expander("📅 各周季节与节假日调整明细"):
        from clearance_tool.data import holiday_name
        sadj = []
        for i, w in enumerate(plan.weeks):
            s_idx = SEASONAL_INDEX[w.start_date.month]
            hol_name = ""
            for j in range(7):
                h = holiday_name(w.start_date + timedelta(days=j))
                if h:
                    hol_name = h
                    break
            sadj.append({
                "周次":         w.week_num,
                "日期区间":     f"{w.start_date.strftime('%m月%d日')} – {w.end_date.strftime('%m月%d日')}",
                "季节系数":     f"{s_idx:.2f}×",
                "节假日":       hol_name if hol_name else "—",
                "调整后基线销量": f"{baselines[i]:.1f} 件",
            })
        st.table(sadj)
        st.caption(
            "季节系数 = 当月需求量相对全年均值的倍数；"
            "节假日周在季节系数基础上叠加额外需求提升（如国庆 +20%、劳动节 +15%）。"
        )

    # 方案对比
    st.markdown("#### 方案对比")
    cmp_data = [
        {"方案": "理想情形（全部按原价售出，不限时间）",
         "总毛利": f"¥{plan.ideal_margin:,}",
         "未售库存": "0",
         "对比理想情形毛利差": "—"},
        {"方案": "✅ 推荐清仓方案【最优】",
         "总毛利": f"¥{plan.total_margin:,}",
         "未售库存": "0",
         "对比理想情形毛利差": f"−¥{plan.ideal_margin - plan.total_margin:,}"},
        {"方案": "❌ 不促销（零折扣，8周）",
         "总毛利": f"¥{plan.no_action_margin:,}",
         "未售库存": str(plan.no_action_unsold),
         "对比理想情形毛利差": f"−¥{plan.ideal_margin - plan.no_action_margin:,}"},
    ]
    st.table(cmp_data)

    # 敏感性分析
    with st.expander("📈 弹性系数敏感性分析"):
        sens_data = [
            {
                "弹性系数":   ("→ " if s["assumed"] else "  ") + str(s["elasticity"]),
                "预计总毛利": f"¥{s['margin']:,}",
                "结果说明":   s["outcome"],
            }
            for s in plan.sensitivity
        ]
        st.table(sens_data)

    # 监控预警
    st.markdown("#### ⚠️ 监控预警")
    triggers = monitoring_triggers(plan)
    for t in triggers:
        st.markdown(
            f"**第 {t['week']} 周（{t['date']}）** — "
            f"若 *{t['metric']}*，则：**{t['action']}**"
        )

    # ── 下载 ─────────────────────────────────────────────────────────────
    st.divider()
    st.markdown("### ⬇️ 下载")
    dcol1, dcol2 = st.columns(2)

    with st.spinner("正在生成 PDF……"):
        pdf_bytes = generate_pdf(plan)

    with dcol1:
        st.download_button(
            label="📄  下载 PDF 报告",
            data=pdf_bytes,
            file_name=f"clearance_plan_{selected_sku}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

    # CSV 导出
    csv_buf = io.StringIO()
    cw = csv.writer(csv_buf)
    cw.writerow(["周次", "开始日期", "结束日期", "折扣（%）",
                 "标牌售价（元）", "预计销量", "累计销量",
                 "剩余库存", "本周毛利（元）"])
    for w in plan.weeks:
        cw.writerow([
            w.week_num,
            w.start_date.isoformat(),
            w.end_date.isoformat(),
            round(w.discount_pct * 100),
            w.posted_price,
            w.proj_units,
            int(w.cumulative),
            int(w.remaining),
            w.week_margin,
        ])
    with dcol2:
        st.download_button(
            label="📊  下载方案 CSV",
            data=csv_buf.getvalue().encode("utf-8-sig"),
            file_name=f"clearance_schedule_{selected_sku}.csv",
            mime="text/csv",
            use_container_width=True,
        )
