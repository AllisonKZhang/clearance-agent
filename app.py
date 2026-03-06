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
    load_csv_bytes, load_xlsx_bytes, sku_summary, weekly_baseline,
    seasonal_adjusted_baseline, SEASONAL_INDEX,
    WEATHER_INDEX, WEATHER_TEMP_C, WEATHER_PRECIP_MM,
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

    /* Translate file uploader built-in text to Chinese */
    [data-testid="stFileUploaderDropzoneInstructions"] > div > span:nth-child(1) {
        font-size: 0;
    }
    [data-testid="stFileUploaderDropzoneInstructions"] > div > span:nth-child(1)::before {
        content: "拖拽文件至此处";
        font-size: 1rem;
        font-weight: 600;
    }
    [data-testid="stFileUploaderDropzoneInstructions"] > div > span:nth-child(2),
    [data-testid="stFileUploaderDropzoneInstructions"] > div > small {
        font-size: 0;
    }
    [data-testid="stFileUploaderDropzoneInstructions"] > div > span:nth-child(2)::before,
    [data-testid="stFileUploaderDropzoneInstructions"] > div > small::before {
        content: "每个文件限 200MB · 支持 CSV / XLSX 格式";
        font-size: 0.8rem;
    }
    [data-testid="stFileUploaderDropzone"] button {
        font-size: 0 !important;
        color: transparent !important;
    }
    [data-testid="stFileUploaderDropzone"] button::after {
        content: "浏览文件";
        font-size: 0.875rem;
        color: rgb(49, 51, 63);
    }
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
    header = ["日期", "门店编号", "门店", "采购二级类目", "采购三级类目",
              "商品编码", "商品名称", "商品状态", "平均售价", "实销数量",
              "未税实销金额", "未税基础毛利", "未税基础毛利率"]
    samples = [
        ["2026.01.31", "2510", "良乡时光汇店",  "P02-日配食品部", "UJ8-冰淇淋", "816723", "物美精选62gx10生巧雪糕（巧克力味雪糕）", "长期禁下单", 24.9, 1,  21.6018,   5.6460,  0.2614],
        ["2026.01.31", "2510", "良乡时光汇店",  "P02-日配食品部", "UJ8-冰淇淋", "772766", "和路雪迷你梦龙170g松露+卡布基诺味冰淇淋",  "过季商品", 19.9, 1,  17.6106,  -0.9912, -0.0563],
        ["2026.01.31", "2510", "良乡时光汇店",  "P02-日配食品部", "UJ8-冰淇淋", "446145", "伊利65G*6妙趣小雪生雪糕",                "正常商品", 11.8, 7,  71.8850,   5.2079,  0.0724],
        ["2026.01.31", "2510", "良乡时光汇店",  "P02-日配食品部", "UJ8-冰淇淋", "341383", "八喜60G*6六合一牛奶冰淇淋",              "正常商品", 29.9, 2,  52.8938,  -9.9380, -0.1879],
        ["2026.01.31", "2352", "丰台新业广场店", "P02-日配食品部", "UJ8-冰淇淋", "446145", "伊利65G*6妙趣小雪生雪糕",                "正常商品", 11.8, 5,  51.3464,   3.7199,  0.0724],
        ["2026.01.31", "2352", "丰台新业广场店", "P02-日配食品部", "UJ8-冰淇淋", "341383", "八喜60G*6六合一牛奶冰淇淋",              "正常商品", 29.9, 2,  52.8938,  -9.9380, -0.1879],
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
    st.divider()
    st.markdown("### 使用说明")
    st.markdown("""
1. **下载** 数据模板
2. **填写** 历史销售记录
3. **上传** CSV 文件
4. **选择** 需清仓的商品
5. **填写** 库存数量、开始日期及截止日期
6. **生成** 并下载清仓方案
""")
    st.divider()
    st.download_button(
        label="⬇️  下载数据模板（CSV）",
        data=_template_csv(),
        file_name="清仓销售数据模板.csv",
        mime="text/csv",
        use_container_width=True,
    )
    st.markdown('<p class="note">支持最大 200 MB 的 CSV / XLSX 文件</p>',
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
    "上传销售数据文件（请使用左侧边栏提供的模板）",
    type=["csv", "xlsx"],
    label_visibility="collapsed",
)

rows: list[dict] = []
errors: list[str] = []

if uploaded:
    raw = uploaded.read()
    if uploaded.name.lower().endswith(".xlsx"):
        rows, errors = load_xlsx_bytes(raw)
    else:
        rows, errors = load_csv_bytes(raw)
    if errors:
        for e in errors[:5]:
            st.error(e)
        if len(errors) > 5:
            st.warning(f"……另有 {len(errors)-5} 条错误未显示。")
    if rows:
        st.success(
            f"✅  已加载 **{len(rows):,}** 条有效记录，共 "
            f"**{len(set(r['store_id'] for r in rows))}** 家门店、"
            f"**{len(set(str(r['sku_id']) for r in rows))}** 个 SKU。"
            + (f"（已自动过滤 {len(errors)} 条异常行）" if errors else "")
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

col_p, col_c, col_d, col_end = st.columns(4)
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
with col_end:
    deadline_date = st.date_input(
        "清仓截止日期",
        value=date.today() + timedelta(weeks=8),
        min_value=date.today() + timedelta(weeks=1),
        help="所有库存须在此日期前清完",
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

    if deadline_date <= start_date:
        st.error("截止日期须晚于开始日期。")
        st.stop()

    max_weeks = max(1, (deadline_date - start_date).days // 7)

    e_val   = sel_e_info.get("elasticity", cat_e)
    e_meth  = sel_e_info.get("method", "类目弹性备用值")
    base_wk = weekly_baseline(rows, selected_sku)

    if base_wk < 0.1:
        st.warning("该 SKU 近期无销售数据，以每周 1 件作为基准销量。")
        base_wk = 1.0

    baselines = seasonal_adjusted_baseline(
        base_wk, start_date, n_weeks=max_weeks
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
            max_weeks         = max_weeks,
        )

    # Pre-compute download bytes so clicking download never loses the plan
    with st.spinner("正在生成 PDF……"):
        pdf_bytes = generate_pdf(plan)

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

    st.session_state["plan"]      = plan
    st.session_state["baselines"] = baselines
    st.session_state["pdf_bytes"] = pdf_bytes
    st.session_state["csv_bytes"] = csv_buf.getvalue().encode("utf-8-sig")
    st.session_state["plan_sku"]  = selected_sku

# ── 结果展示（持久化，下载不会清除方案）────────────────────────────────────
if "plan" in st.session_state:
    plan      = st.session_state["plan"]
    baselines = st.session_state["baselines"]
    e_val     = plan.elasticity
    e_meth    = plan.elasticity_method

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
                         plan.clears_by.strftime("%Y年%m月%d日") if plan.clears_by else ">计划周期"),
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
            m = w.start_date.month
            s_idx = SEASONAL_INDEX[m]
            hol_name = ""
            for j in range(7):
                h = holiday_name(w.start_date + timedelta(days=j))
                if h:
                    hol_name = h
                    break
            sadj.append({
                "周次":           w.week_num,
                "日期区间":       f"{w.start_date.strftime('%m月%d日')} – {w.end_date.strftime('%m月%d日')}",
                "季节系数":       f"{s_idx:.2f}×",
                "气温 (°C)":      f"{WEATHER_TEMP_C[m]:.0f}°C",
                "降水 (mm)":      f"{WEATHER_PRECIP_MM[m]:.0f} mm",
                "天气系数":       f"{WEATHER_INDEX[m]:.2f}×",
                "节假日":         hol_name if hol_name else "—",
                "调整后基线销量": f"{baselines[i]:.1f} 件",
            })
        st.table(sadj)
        st.caption(
            "季节系数 = 行为/文化季节性分量；天气系数 = 北京月均气温与降水对需求的综合影响（年均=1.0）；"
            "节假日周在两项系数基础上叠加额外需求提升（如国庆 +20%、劳动节 +15%）。"
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
        {"方案": "❌ 不促销（零折扣）",
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
    with dcol1:
        st.download_button(
            label="📄  下载 PDF 报告",
            data=st.session_state["pdf_bytes"],
            file_name=f"清仓计划_{st.session_state['plan_sku']}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
    with dcol2:
        st.download_button(
            label="📊  下载方案 CSV",
            data=st.session_state["csv_bytes"],
            file_name=f"清仓方案_{st.session_state['plan_sku']}.csv",
            mime="text/csv",
            use_container_width=True,
        )
