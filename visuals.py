"""Matplotlib 現代環形圖、排行榜與 AI 預測圖。"""

from __future__ import annotations

try:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    import matplotlib.dates as mdates
    import matplotlib.ticker

    HAS_MATPLOTLIB = True
    matplotlib.rcParams["font.sans-serif"] = [
        "Microsoft JhengHei",
        "PingFang TC",
        "Heiti TC",
        "SimHei",
        "Arial Unicode MS",
        "Noto Sans CJK TC",
        "WenQuanYi Micro Hei",
        "sans-serif",
    ]
    matplotlib.rcParams["axes.unicode_minus"] = False
except ImportError:
    HAS_MATPLOTLIB = False
    Figure = None
    mdates = None
    matplotlib = None

PBI_COLORS = [
    "#118DFF",
    "#1B5DBF",
    "#E66C37",
    "#6B007B",
    "#E044A7",
    "#744EC2",
    "#D9B300",
    "#D64550",
    "#197278",
    "#1AAB40",
]

PBI_BG = "#FFFFFF"
PBI_TEXT = "#1F2937"
PBI_GRID = "#D8E0EA"
PBI_MUTED = "#52616F"


def _chart_tokens(theme=None):
    if isinstance(theme, dict):
        return {
            "bg": theme.get("bg", PBI_BG),
            "axes_bg": theme.get("axes_bg", theme.get("bg", PBI_BG)),
            "text": theme.get("text", PBI_TEXT),
            "muted": theme.get("muted", PBI_MUTED),
            "grid": theme.get("grid", PBI_GRID),
            "edge": theme.get("edge", theme.get("grid", PBI_GRID)),
            "legend_bg": theme.get("legend_bg", theme.get("bg", PBI_BG)),
        }
    if theme == "dark":
        return {
            "bg": "#172235",
            "axes_bg": "#172235",
            "text": "#E7EDF7",
            "muted": "#B8C4D6",
            "grid": "#33435C",
            "edge": "#33435C",
            "legend_bg": "#172235",
        }
    return {
        "bg": PBI_BG,
        "axes_bg": PBI_BG,
        "text": PBI_TEXT,
        "muted": PBI_MUTED,
        "grid": PBI_GRID,
        "edge": PBI_GRID,
        "legend_bg": PBI_BG,
    }


def draw_pie_chart(labels, values, title, theme=None):
    c = _chart_tokens(theme)
    fig = Figure(figsize=(8.2, 4.8), facecolor=c["bg"])
    ax = fig.subplots()
    ax.set_facecolor(c["axes_bg"])

    valid_data = [(label, value) for label, value in zip(labels, values) if value > 0]
    if not valid_data:
        ax.text(0.5, 0.5, "當前篩選條件下\n無有效數據", ha="center", va="center", color=c["muted"], fontsize=11)
        ax.axis("off")
        ax.set_title(title, fontsize=12, fontweight="600", color=c["text"], pad=15)
        fig.tight_layout()
        return fig

    valid_data.sort(key=lambda x: x[1], reverse=True)
    if len(valid_data) > 7:
        major = valid_data[:6]
        minor_sum = sum(v for _, v in valid_data[6:])
        if minor_sum > 0:
            major.append(("其他", minor_sum))
        valid_data = major

    v_labels, v_values = zip(*valid_data)
    total = sum(v_values)
    pct_threshold = 4.0 if len(v_values) <= 6 else 6.0

    wedges, _, _ = ax.pie(
        v_values,
        labels=None,
        autopct=lambda pct: f"{pct:.1f}%" if pct > pct_threshold else "",
        startangle=90,
        counterclock=False,
        colors=PBI_COLORS * (len(v_labels) // len(PBI_COLORS) + 1),
        wedgeprops=dict(width=0.42, edgecolor="white", linewidth=2.0),
        pctdistance=0.75,
        textprops=dict(color="#FFFFFF", fontweight="bold", fontsize=9.5),
    )

    legend_labels = [f"{label}  ({value / total * 100:.1f}%)" for label, value in zip(v_labels, v_values)]
    legend = ax.legend(
        wedges,
        legend_labels,
        title="板塊與佔比",
        loc="center left",
        bbox_to_anchor=(0.98, 0.5),
        fontsize=9.5,
        title_fontsize=10.5,
        frameon=False,
    )
    for text in legend.get_texts():
        text.set_color(c["text"])
    if legend.get_title():
        legend.get_title().set_color(c["text"])
    ax.text(0, 0.04, f"總額\n{total:,.0f}", ha="center", va="center", fontsize=12, color=c["text"], fontweight="bold")
    ax.set_title(title, fontsize=12, fontweight="600", color=c["text"], loc="center")
    fig.subplots_adjust(left=0.0, right=0.47, top=0.86, bottom=0.08)
    return fig


def safe_draw_pie(grp_series, title, theme=None):
    if grp_series is not None and len(grp_series) > 0 and grp_series.sum() > 0:
        return draw_pie_chart(grp_series.index.tolist(), grp_series.tolist(), title, theme=theme)
    return draw_pie_chart([], [], title, theme=theme)


def draw_top10_barh(top_df, value_col="總額", title="Top 10 營業額分社 (HKD)", theme=None):
    c = _chart_tokens(theme)
    row_count = max(len(top_df), 1)
    fig_h = min(max(4.2, 0.42 * row_count + 1.2), 12.5)
    fig = Figure(figsize=(9.2, fig_h), facecolor=c["bg"])
    ax = fig.subplots()
    ax.set_facecolor(c["axes_bg"])

    if top_df.empty:
        ax.text(0.5, 0.5, "當前篩選條件下\n無有效排行數據", ha="center", va="center", color=c["muted"])
        ax.axis("off")
        return fig

    plot_df = top_df.copy()
    if "文本" not in plot_df.columns:
        plot_df = plot_df.reset_index().rename(columns={plot_df.columns[0]: "文本"})
    plot_df = plot_df.sort_values(value_col, ascending=True)
    colors = [PBI_COLORS[i % len(PBI_COLORS)] for i in range(len(plot_df))]
    bars = ax.barh(plot_df["文本"].values, plot_df[value_col].values, color=colors, height=0.62)
    total = float(plot_df[value_col].sum()) if value_col in plot_df.columns else 0.0

    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, p: f"HKD {x:,.0f}"))
    ax.set_title(title, fontweight="700", color=c["text"], pad=12, loc="left")
    ax.tick_params(colors=c["text"], labelsize=9.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)
    for spine in ax.spines.values():
        spine.set_edgecolor(c["edge"])
    ax.grid(axis="x", linestyle=":", color=c["grid"], alpha=0.9)
    ax.set_axisbelow(True)
    ax.margins(y=0.06)

    for bar, value in zip(bars, plot_df[value_col].values):
        pct = (value / total * 100) if total else 0
        ax.text(
            bar.get_width() + (total * 0.008 if total else 1),
            bar.get_y() + bar.get_height() / 2,
            f"{value:,.0f}  ({pct:.1f}%)",
            va="center",
            ha="left",
            fontsize=9.2,
            color=c["text"],
        )

    fig.tight_layout()
    return fig


def draw_forecast_chart(ts, ar, pr, lgb_trk, ens, lw, up, theme=None):
    c = _chart_tokens(theme)
    fig = Figure(figsize=(10, 4.8), facecolor=c["bg"])
    ax = fig.subplots()
    ax.set_facecolor(c["axes_bg"])

    rh = ts.tail(60)
    ax.plot(rh.index, rh["Revenue"], label="Actual (實際)", color="#A19F9D", marker="o", ms=3.5, lw=1.2)
    ax.plot(ar.index, ar, "--", label="ARIMA", color=PBI_COLORS[0], lw=1.5, alpha=0.8)
    ax.plot(pr.index, pr, "--", label="Prophet", color=PBI_COLORS[2], lw=1.5, alpha=0.8)
    ax.plot(lgb_trk.index, lgb_trk, "--", label="LightGBM", color=PBI_COLORS[3], lw=1.5, alpha=0.8)
    ax.plot(ens.index, ens, "-", label="Consensus (融合預測)", color="#00B7C3", lw=3.0)
    ax.fill_between(ens.index, lw, up, color="#00B7C3", alpha=0.12, label="95% Risk Interval (風險區間)", edgecolor="none")

    ax.set_title("Daily Forecast：逐日波動預測", fontweight="700", color=c["text"], fontsize=13, pad=12)
    ax.tick_params(colors=c["text"])
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for spine in ax.spines.values():
        spine.set_edgecolor(c["edge"])
    legend = ax.legend(loc="upper left", fontsize=9, frameon=True, facecolor=c["legend_bg"], edgecolor=c["edge"])
    for text in legend.get_texts():
        text.set_color(c["text"])
    ax.grid(axis="y", linestyle=":", color=c["grid"], alpha=0.8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    fig.autofmt_xdate(rotation=30)
    return fig


def draw_seven_day_macro_chart(ar, pr, lgb_trk, ens, lw, up, theme=None):
    c = _chart_tokens(theme)
    fig = Figure(figsize=(10, 4.4), facecolor=c["bg"])
    ax = fig.subplots()
    ax.set_facecolor(c["axes_bg"])

    total = float(ens.sum()) if len(ens) else 0.0
    ax.bar(ens.index, ens.values, color="#00B7C3", alpha=0.22, label="_nolegend_")
    ax.plot(ar.index, ar, "--", label="ARIMA", color=PBI_COLORS[0], lw=1.4, alpha=0.82)
    ax.plot(pr.index, pr, "--", label="Prophet", color=PBI_COLORS[2], lw=1.4, alpha=0.82)
    ax.plot(lgb_trk.index, lgb_trk, "--", label="LightGBM", color=PBI_COLORS[3], lw=1.4, alpha=0.82)
    ax.plot(ens.index, ens, "-", label="Consensus", color="#00B7C3", lw=2.8, marker="o", ms=4.5)
    ax.fill_between(ens.index, lw, up, color="#00B7C3", alpha=0.12, label="Lower / Upper", edgecolor="none")

    ax.text(
        0.965,
        0.9,
        f"7-Day Total\nHKD {total:,.0f}",
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=11,
        color=c["text"],
        fontweight="700",
        bbox=dict(boxstyle="round,pad=0.38", facecolor=c["legend_bg"], edgecolor=c["edge"], alpha=0.96),
    )
    ax.set_title("7-Day Macro Forecast：未來 7 日總額（不是自然週）", fontweight="700", color=c["text"], fontsize=13, pad=12)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, p: f"{x / 1000:,.0f}K"))
    ax.tick_params(colors=c["text"])
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for spine in ax.spines.values():
        spine.set_edgecolor(c["edge"])
    legend = ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.18),
        fontsize=8.8,
        frameon=False,
        ncol=5,
        handlelength=2.0,
        columnspacing=1.3,
    )
    for text in legend.get_texts():
        text.set_color(c["text"])
    ax.grid(axis="y", linestyle=":", color=c["grid"], alpha=0.8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    fig.autofmt_xdate(rotation=25)
    fig.subplots_adjust(left=0.07, right=0.985, top=0.86, bottom=0.24)
    return fig


def draw_month_end_macro_chart(ts, consensus, lw, up, theme=None):
    c = _chart_tokens(theme)
    fig = Figure(figsize=(10, 4.4), facecolor=c["bg"])
    ax = fig.subplots()
    ax.set_facecolor(c["axes_bg"])

    forecast_start = consensus.index[0]
    latest_actual = ts.index[-1]
    target_month = forecast_start.to_period("M")
    month_start = target_month.start_time.normalize()
    month_end = target_month.end_time.normalize()
    actual_month = ts["Revenue"].loc[month_start:latest_actual]
    actual_cumulative = actual_month.cumsum()
    mtd_actual = float(actual_cumulative.iloc[-1]) if len(actual_cumulative) else 0.0

    forecast_slice = consensus.loc[forecast_start:month_end]
    lower_slice = lw.loc[forecast_start:month_end]
    upper_slice = up.loc[forecast_start:month_end]
    projected_cumulative = mtd_actual + forecast_slice.cumsum()
    lower_cumulative = mtd_actual + lower_slice.cumsum()
    upper_cumulative = mtd_actual + upper_slice.cumsum()
    month_end_total = float(projected_cumulative.iloc[-1]) if len(projected_cumulative) else mtd_actual

    if len(actual_cumulative):
        ax.plot(actual_cumulative.index, actual_cumulative.values, label="MTD Actual", color="#A19F9D", lw=2.2, marker="o", ms=3.8)
    if len(projected_cumulative):
        ax.plot(projected_cumulative.index, projected_cumulative.values, label="Month-End Consensus", color="#00B7C3", lw=3.0, marker="o", ms=4.0)
        ax.fill_between(projected_cumulative.index, lower_cumulative, upper_cumulative, color="#00B7C3", alpha=0.12, label="Lower / Upper", edgecolor="none")

    ax.axvline(forecast_start, color=c["grid"], lw=1.2, linestyle=":", label="Forecast Start")
    ax.text(
        0.94,
        0.86,
        f"Projected Month-End\nHKD {month_end_total:,.0f}",
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=11,
        color=c["text"],
        fontweight="700",
        bbox=dict(boxstyle="round,pad=0.38", facecolor=c["legend_bg"], edgecolor=c["edge"], alpha=0.96),
    )
    ax.set_title("Month-End Macro Forecast：MTD + 本月剩餘預測", fontweight="700", color=c["text"], fontsize=13, pad=12)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, p: f"{x / 1_000_000:,.1f}M"))
    ax.tick_params(colors=c["text"])
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for spine in ax.spines.values():
        spine.set_edgecolor(c["edge"])
    legend = ax.legend(loc="upper left", fontsize=8.8, frameon=True, facecolor=c["legend_bg"], edgecolor=c["edge"], ncol=2)
    for text in legend.get_texts():
        text.set_color(c["text"])
    ax.grid(axis="y", linestyle=":", color=c["grid"], alpha=0.8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    fig.autofmt_xdate(rotation=25)
    fig.subplots_adjust(left=0.07, right=0.985, top=0.86, bottom=0.18)
    return fig
