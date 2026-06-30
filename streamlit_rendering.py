from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st


def _theme_tokens(theme: str | None = None) -> dict[str, str]:
    mode = "dark" if theme == "dark" else "light"
    if mode == "dark":
        return {
            "mode": "dark",
            "page_bg": "#101826",
            "app_overlay": "linear-gradient(90deg, rgba(6,18,34,0.74), rgba(16,24,38,0.96) 34%, rgba(16,24,38,0.98) 100%)",
            "surface": "#172235",
            "surface_soft": "#1D2A3E",
            "surface_panel": "#142033",
            "surface_subtle": "#223149",
            "topbar_bg": "linear-gradient(135deg, rgba(23,34,53,0.98), rgba(29,42,62,0.96))",
            "chip_bg": "#223149",
            "input_bg": "#FFFFFF",
            "border": "#33435C",
            "divider": "#2B3A52",
            "text": "#E7EDF7",
            "text_strong": "#F8FAFC",
            "muted": "#B8C4D6",
            "faint": "#90A0B8",
            "shadow": "0 8px 26px rgba(0, 0, 0, 0.24)",
            "hero_bg": "linear-gradient(135deg, #172235 0%, #1D2A3E 100%)",
            "db_bg": "linear-gradient(135deg, rgba(31,157,85,0.16) 0%, rgba(23,34,53,0.98) 100%)",
            "db_text": "#BDEFCF",
            "db_border": "#2F7F50",
            "export_bg": "#172235",
            "metric_bg": "#172235",
            "dataframe_bg": "#172235",
            "dataframe_filter": "invert(0.92) hue-rotate(180deg) saturate(0.86) contrast(0.98)",
            "image_bg": "#172235",
            "sidebar_bg": "linear-gradient(180deg, #121C2C 0%, #172235 100%)",
            "sidebar_border": "#33435C",
            "sidebar_panel": "rgba(255, 255, 255, 0.055)",
            "sidebar_panel_hover": "rgba(255, 255, 255, 0.095)",
            "sidebar_text": "#D9E3F2",
            "sidebar_text_strong": "#F8FAFC",
            "sidebar_muted": "#9BA9BC",
            "sidebar_input_bg": "#101826",
            "sidebar_input_text": "#F8FAFC",
            "sidebar_chip_bg": "rgba(255, 255, 255, 0.075)",
            "sidebar_chip_text": "#D9E3F2",
            "sidebar_active_bg": "linear-gradient(135deg, rgba(17,141,255,0.18), rgba(47,128,237,0.10))",
            "badge_official_text": "#BDEFCF",
            "badge_official_bg": "rgba(34, 197, 94, 0.14)",
            "badge_official_border": "rgba(125, 211, 160, 0.34)",
            "badge_diagnostic_text": "#BFDBFE",
            "badge_diagnostic_bg": "rgba(59, 130, 246, 0.14)",
            "badge_diagnostic_border": "rgba(147, 197, 253, 0.34)",
            "badge_experimental_text": "#FDE68A",
            "badge_experimental_bg": "rgba(245, 158, 11, 0.14)",
            "badge_experimental_border": "rgba(251, 191, 36, 0.36)",
            "badge_readonly_text": "#D1D5DB",
            "badge_readonly_bg": "rgba(148, 163, 184, 0.12)",
            "badge_readonly_border": "rgba(209, 213, 219, 0.24)",
            "badge_manual_text": "#FBCFE8",
            "badge_manual_bg": "rgba(236, 72, 153, 0.12)",
            "badge_manual_border": "rgba(244, 114, 182, 0.34)",
            "badge_session_text": "#C4B5FD",
            "badge_session_bg": "rgba(139, 92, 246, 0.13)",
            "badge_session_border": "rgba(167, 139, 250, 0.34)",
        }
    return {
        "mode": "light",
        "page_bg": "#F4F7FB",
        "app_overlay": "linear-gradient(90deg, rgba(11,31,58,0.045), rgba(17,141,255,0.028) 26%, rgba(244,247,251,0) 48%)",
        "surface": "#FFFFFF",
        "surface_soft": "#F8FAFD",
        "surface_panel": "#FBFCFE",
        "surface_subtle": "#F8FBFF",
        "topbar_bg": "linear-gradient(135deg, rgba(255,255,255,0.98), rgba(248,251,255,0.96))",
        "chip_bg": "#FFFFFF",
        "input_bg": "#FFFFFF",
        "border": "#D8E0EA",
        "divider": "#E5EAF0",
        "text": "#1F2937",
        "text_strong": "#111827",
        "muted": "#52616F",
        "faint": "#7A8694",
        "shadow": "0 8px 24px rgba(15, 23, 42, 0.08)",
        "hero_bg": "linear-gradient(135deg, #FFFFFF 0%, #F7FBFF 100%)",
        "db_bg": "linear-gradient(135deg, #EAF7EF 0%, #F7FFFA 100%)",
        "db_text": "#14532D",
        "db_border": "#BFE8CC",
        "export_bg": "#F8FBFF",
        "metric_bg": "#FFFFFF",
        "dataframe_bg": "#FFFFFF",
        "dataframe_filter": "none",
        "image_bg": "#FFFFFF",
        "sidebar_bg": "linear-gradient(180deg, #FFFFFF 0%, #F8FAFD 100%)",
        "sidebar_border": "#D8E0EA",
        "sidebar_panel": "#F4F7FB",
        "sidebar_panel_hover": "#EEF5FF",
        "sidebar_text": "#334155",
        "sidebar_text_strong": "#111827",
        "sidebar_muted": "#64748B",
        "sidebar_input_bg": "#FFFFFF",
        "sidebar_input_text": "#111827",
        "sidebar_chip_bg": "#EFF6FF",
        "sidebar_chip_text": "#0B1F3A",
        "sidebar_active_bg": "linear-gradient(135deg, rgba(17,141,255,0.12), rgba(47,128,237,0.07))",
        "badge_official_text": "#0F7A43",
        "badge_official_bg": "#DCFCE7",
        "badge_official_border": "#9AE6B4",
        "badge_diagnostic_text": "#1D5FBF",
        "badge_diagnostic_bg": "#DBEAFE",
        "badge_diagnostic_border": "#93C5FD",
        "badge_experimental_text": "#8A5A00",
        "badge_experimental_bg": "#FEF3C7",
        "badge_experimental_border": "#FBBF24",
        "badge_readonly_text": "#475569",
        "badge_readonly_bg": "#E2E8F0",
        "badge_readonly_border": "#CBD5E1",
        "badge_manual_text": "#BE185D",
        "badge_manual_bg": "#FCE7F3",
        "badge_manual_border": "#F9A8D4",
        "badge_session_text": "#6D28D9",
        "badge_session_bg": "#EDE9FE",
        "badge_session_border": "#C4B5FD",
    }


def _chart_theme() -> dict[str, str]:
    tokens = _theme_tokens(st.session_state.get("NBS_UI_THEME", "light"))
    return {
        "mode": tokens["mode"],
        "bg": tokens["surface"],
        "axes_bg": tokens["surface"],
        "text": tokens["text"],
        "muted": tokens["muted"],
        "grid": tokens["border"],
        "edge": tokens["border"],
        "legend_bg": tokens["surface"],
    }


def _render_dynamic_theme_css() -> None:
    t = _theme_tokens(st.session_state.get("NBS_UI_THEME", "light"))
    st.markdown(
        f"""
<style>
    :root {{
        --nbs-page-bg: {t["page_bg"]};
        --nbs-app-overlay: {t["app_overlay"]};
        --nbs-surface: {t["surface"]};
        --nbs-surface-soft: {t["surface_soft"]};
        --nbs-surface-panel: {t["surface_panel"]};
        --nbs-surface-subtle: {t["surface_subtle"]};
        --nbs-topbar-bg: {t["topbar_bg"]};
        --nbs-chip-bg: {t["chip_bg"]};
        --nbs-input-bg: {t["input_bg"]};
        --nbs-border: {t["border"]};
        --nbs-divider: {t["divider"]};
        --nbs-text: {t["text"]};
        --nbs-text-strong: {t["text_strong"]};
        --nbs-muted: {t["muted"]};
        --nbs-faint: {t["faint"]};
        --nbs-shadow: {t["shadow"]};
        --nbs-hero-bg: {t["hero_bg"]};
        --nbs-db-bg: {t["db_bg"]};
        --nbs-db-text: {t["db_text"]};
        --nbs-db-border: {t["db_border"]};
        --nbs-export-bg: {t["export_bg"]};
        --nbs-metric-bg: {t["metric_bg"]};
        --nbs-dataframe-bg: {t["dataframe_bg"]};
        --nbs-dataframe-filter: {t["dataframe_filter"]};
        --nbs-image-bg: {t["image_bg"]};
        --nbs-sidebar-bg: {t["sidebar_bg"]};
        --nbs-sidebar-border: {t["sidebar_border"]};
        --nbs-sidebar-panel: {t["sidebar_panel"]};
        --nbs-sidebar-panel-hover: {t["sidebar_panel_hover"]};
        --nbs-sidebar-text: {t["sidebar_text"]};
        --nbs-sidebar-text-strong: {t["sidebar_text_strong"]};
        --nbs-sidebar-muted: {t["sidebar_muted"]};
        --nbs-sidebar-input-bg: {t["sidebar_input_bg"]};
        --nbs-sidebar-input-text: {t["sidebar_input_text"]};
        --nbs-sidebar-chip-bg: {t["sidebar_chip_bg"]};
        --nbs-sidebar-chip-text: {t["sidebar_chip_text"]};
        --nbs-sidebar-active-bg: {t["sidebar_active_bg"]};
        --nbs-badge-official-text: {t["badge_official_text"]};
        --nbs-badge-official-bg: {t["badge_official_bg"]};
        --nbs-badge-official-border: {t["badge_official_border"]};
        --nbs-badge-diagnostic-text: {t["badge_diagnostic_text"]};
        --nbs-badge-diagnostic-bg: {t["badge_diagnostic_bg"]};
        --nbs-badge-diagnostic-border: {t["badge_diagnostic_border"]};
        --nbs-badge-experimental-text: {t["badge_experimental_text"]};
        --nbs-badge-experimental-bg: {t["badge_experimental_bg"]};
        --nbs-badge-experimental-border: {t["badge_experimental_border"]};
        --nbs-badge-readonly-text: {t["badge_readonly_text"]};
        --nbs-badge-readonly-bg: {t["badge_readonly_bg"]};
        --nbs-badge-readonly-border: {t["badge_readonly_border"]};
        --nbs-badge-manual-text: {t["badge_manual_text"]};
        --nbs-badge-manual-bg: {t["badge_manual_bg"]};
        --nbs-badge-manual-border: {t["badge_manual_border"]};
        --nbs-badge-session-text: {t["badge_session_text"]};
        --nbs-badge-session-bg: {t["badge_session_bg"]};
        --nbs-badge-session-border: {t["badge_session_border"]};
    }}
</style>
""",
        unsafe_allow_html=True,
    )


def _render_error(title: str, detail: str) -> None:
    st.error(title)
    with st.expander("查看技術細節", expanded=False):
        st.code(detail, language="python")


def _render_section(title: str, subtitle: str | None = None, icon: str = "") -> None:
    icon_html = f"{escape(icon)} " if icon else ""
    subtitle_html = f'<div class="nbs-section-subtitle">{escape(subtitle)}</div>' if subtitle else ""
    st.markdown(
        f"""
        <div class="nbs-section-title">{icon_html}{escape(title)}</div>
        {subtitle_html}
        """,
        unsafe_allow_html=True,
    )


def _render_anchor(anchor_id: str) -> None:
    st.markdown(f'<div id="{escape(anchor_id)}"></div>', unsafe_allow_html=True)


SIDEBAR_NAV_GROUPS = [
    (
        "Overview",
        [
            ("section-current-context", "▣ 分析脈絡", "正式", "official"),
            ("section-kpi-overview", "▥ KPI 總覽", "正式", "official"),
            ("section-year-summary", "▦ 年度總覽", "正式", "official"),
            ("section-branch-ranking", "▤ 門店排行榜", "正式", "official"),
            ("section-product-drilldown", "◫ 產品下鑽", "正式", "official"),
        ],
    ),
    (
        "Data Quality",
        [
            ("section-data-quality", "◈ Quality Scorecard", "只讀", "readonly"),
            ("section-entity-audit", "◎ Entity Audit", "稽核", "diagnostic"),
            ("section-ai-cleaning", "✦ AI Cleaning", "人工確認", "manual"),
        ],
    ),
    (
        "AI Forecast",
        [
            ("section-ai-forecast", "⌁ Forecast 總覽", "正式", "official"),
            ("section-daily-forecast", "Daily Forecast", "正式", "official"),
            ("section-seven-day-macro", "7-Day Macro", "宏觀", "diagnostic"),
            ("section-month-end-macro", "Month-End Macro", "宏觀", "diagnostic"),
        ],
    ),
    (
        "Governance",
        [
            ("section-model-diagnostics", "◎ Model Diagnostics", "回測", "diagnostic"),
            ("section-forecast-governance", "◉ Forecast Governance", "治理", "readonly"),
            ("section-daily-wape", "◇ Daily WAPE 診斷", "診斷", "diagnostic"),
            ("section-macro-backtest", "◇ Macro Backtest", "診斷", "diagnostic"),
        ],
    ),
    (
        "Advanced Analytics",
        [
            ("section-feature-store", "✧ Feature Store", "只讀", "readonly"),
            ("section-causal-analytics", "⌕ Causal Analytics", "解釋型", "experimental"),
        ],
    ),
    (
        "Exports",
        [
            ("section-data-exports", "⇩ Export Center", "匯出", "session"),
        ],
    ),
]


def _sidebar_navigation_html() -> str:
    groups_html: list[str] = []
    for group_label, items in SIDEBAR_NAV_GROUPS:
        item_html: list[str] = []
        for anchor_id, label, badge, badge_kind in items:
            active_class = " active" if anchor_id == "section-current-context" else ""
            badge_html = (
                f'<span class="nbs-sidebar-nav-badge {escape(badge_kind)}">{escape(badge)}</span>'
                if badge
                else ""
            )
            item_html.append(
                f'<a class="nbs-sidebar-nav-item{active_class}" href="#{escape(anchor_id)}">'
                f'<span class="nbs-sidebar-nav-text">{escape(label)}</span>{badge_html}</a>'
            )
        groups_html.append(
            '<div class="nbs-sidebar-nav-group">'
            f'<div class="nbs-sidebar-nav-group-title">{escape(group_label)}</div>'
            f'<div class="nbs-sidebar-submenu">{"".join(item_html)}</div>'
            "</div>"
        )
    return "".join(groups_html)


def _render_sidebar_navigation() -> None:
    sidebar_nav_html = (
        '<div class="nbs-sidebar-brand">'
        '<div class="nbs-sidebar-logo">NBS</div>'
        "<div>"
        '<div class="nbs-sidebar-title">NBS Analytics</div>'
        '<div class="nbs-sidebar-subtitle">Enterprise Operation Cockpit</div>'
        "</div>"
        "</div>"
        '<div class="nbs-sidebar-zone-label">Navigation</div>'
        '<div class="nbs-sidebar-zone-copy">頁內導覽；點擊跳轉，不刷新頁面。</div>'
        '<div class="nbs-sidebar-nav" aria-label="Cockpit modules">'
        f"{_sidebar_navigation_html()}"
        "</div>"
    )
    st.sidebar.markdown(sidebar_nav_html, unsafe_allow_html=True)


def _render_sidebar_control_header() -> None:
    st.sidebar.markdown(
        """
        <div class="nbs-sidebar-control-header">
            <div class="nbs-sidebar-zone-label">Control Center</div>
            <div class="nbs-sidebar-control-title">控制中心</div>
            <div class="nbs-sidebar-control-copy">提交式篩選；只影響目前分析視角，不負責頁內導航。</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_info_panel(title: str, subtitle: str) -> None:
    st.markdown(
        f"""
        <div class="nbs-hero-panel">
            <div class="nbs-hero-title">{escape(title)}</div>
            <div class="nbs-hero-copy">{escape(subtitle)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _date_range_text(date_rng) -> str:
    if isinstance(date_rng, (tuple, list)) and len(date_rng) >= 2:
        start_dt = pd.to_datetime(date_rng[0], errors="coerce")
        end_dt = pd.to_datetime(date_rng[1], errors="coerce")
        if pd.notna(start_dt) and pd.notna(end_dt):
            return f"{start_dt.strftime('%Y-%m-%d')} ~ {end_dt.strftime('%Y-%m-%d')}"
    one_dt = pd.to_datetime(date_rng, errors="coerce")
    if pd.notna(one_dt):
        return one_dt.strftime("%Y-%m-%d")
    return "全部日期"


def _compact_selection_text(values: list | tuple, all_label: str = "全部", limit: int = 3) -> str:
    clean = [str(v) for v in values if str(v).strip()] if isinstance(values, (list, tuple, set)) else []
    if not clean:
        return all_label
    if len(clean) <= limit:
        return ", ".join(clean)
    return f"{', '.join(clean[:limit])} +{len(clean) - limit}"


def _render_database_status_card(db_tour: pd.DataFrame, db_others: pd.DataFrame) -> None:
    st.markdown(
        f"""
        <div class="nbs-db-status-card">
            <div>
                <div class="nbs-db-status-title">SQLite 歷史資料已連線</div>
                <div class="nbs-db-status-meta">目前累積 {len(db_tour):,} 筆旅行團 與 {len(db_others):,} 筆票務歷史數據</div>
            </div>
            <div class="nbs-badge nbs-badge-success">Local Ready</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_applied_filter_chips(year_sel: list[int], month_sel: list[str], date_rng, branch_sel: str, sales_sel: str) -> None:
    chips = [
        ("年份", _compact_selection_text(year_sel)),
        ("月份", _compact_selection_text(month_sel)),
        ("日期", _date_range_text(date_rng)),
        ("分社", branch_sel or "全部分社"),
        ("專職", sales_sel or "全部銷售組"),
    ]
    chip_html = "".join(
        f'<div class="nbs-filter-chip"><span>{escape(label)}</span>{escape(value)}</div>'
        for label, value in chips
    )
    st.markdown(f'<div class="nbs-filter-strip">{chip_html}</div>', unsafe_allow_html=True)


def _render_executive_summary_band(revenue_scope_label: str) -> None:
    st.markdown(
        f"""
        <div class="nbs-executive-band">
            <div>
                <div class="nbs-executive-kicker">Executive Dashboard</div>
                <div class="nbs-executive-title">營運總覽與管理層 KPI</div>
            </div>
            <div class="nbs-executive-note">首屏聚焦淨營收、產品板塊與可見通路範圍；{escape(revenue_scope_label)}。</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_panel_header(kicker: str, title: str, copy: str | None = None) -> None:
    copy_html = f'<div class="nbs-panel-copy">{escape(copy)}</div>' if copy else ""
    st.markdown(
        f"""
        <div class="nbs-panel-header">
            <div class="nbs-panel-kicker">{escape(kicker)}</div>
            <div class="nbs-panel-title">{escape(title)}</div>
            {copy_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _health_badge_class(label: str) -> str:
    if label == "優秀":
        return "nbs-badge-success"
    if label == "可接受":
        return "nbs-badge-info"
    if label == "可參考":
        return "nbs-badge-warning"
    if label == "需謹慎":
        return "nbs-badge-danger"
    return "nbs-badge-muted"


def _render_forecast_panel_header(kicker: str, title: str, copy: str, health: str | None = None, detail: str | None = None) -> None:
    health_html = ""
    if health:
        badge_class = _health_badge_class(health)
        detail_html = f'<div class="nbs-panel-copy">{escape(detail)}</div>' if detail else ""
        health_html = f'<div style="text-align:right"><div class="nbs-badge {badge_class}">{escape(health)}</div>{detail_html}</div>'
    st.markdown(
        f"""
        <div class="nbs-panel-header" style="display:flex;justify-content:space-between;gap:1rem;align-items:flex-start">
            <div>
                <div class="nbs-panel-kicker">{escape(kicker)}</div>
                <div class="nbs-panel-title">{escape(title)}</div>
                <div class="nbs-panel-copy">{escape(copy)}</div>
            </div>
            {health_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_role_badges(items: list[tuple[str, str, str]]) -> None:
    badge_html = "".join(
        f'<span class="nbs-badge {escape(css_class)}" style="margin-right:0.35rem;margin-bottom:0.35rem">{escape(label)}：{escape(text)}</span>'
        for label, text, css_class in items
    )
    st.markdown(f'<div style="margin:0.25rem 0 0.65rem 0">{badge_html}</div>', unsafe_allow_html=True)


def _render_export_status_card(cache: dict, export_loaded: bool) -> None:
    status = str(cache.get("export_cache_status", "not_prepared") or "not_prepared")
    path = str(cache.get("export_cache_path", "") or "")
    if export_loaded:
        label = "Loaded"
        badge_class = "nbs-badge-success"
        title = "Export workbooks 已載入，可直接下載"
        note = "三份 Excel bytes 已載入目前 session；下載檔名、sheet 與正式口徑保持不變。"
    elif status == "ready":
        label = "Cache Ready"
        badge_class = "nbs-badge-info"
        title = "Export cache 已準備，等待按需載入"
        note = "為加快首屏，系統暫不把大型 workbook bytes 放入頁面；點擊載入後才顯示下載按鈕。"
    else:
        label = "Not Prepared"
        badge_class = "nbs-badge-warning"
        title = "Export workbooks 尚未準備"
        note = "需要下載時才生成三份大型 Excel，完成後會寫入本地 export cache。"
    path_note = f"Cache path：{path}" if path else "Cache path：尚未建立"
    st.markdown(
        f'<div class="nbs-export-status-card"><div><div class="nbs-export-status-title">{escape(title)}</div><div class="nbs-export-status-meta">{escape(note)}<br>{escape(path_note)}</div></div><div class="nbs-badge {badge_class}">{escape(label)}</div></div>',
        unsafe_allow_html=True,
    )


def _render_forecast_card(title: str, subtitle: str, health: str | None = None, detail: str | None = None) -> None:
    with st.container(border=True):
        if health:
            title_col, health_col = st.columns([4, 1])
            title_col.markdown(f"###### {title}")
            title_col.caption(subtitle)
            health_col.metric("健康", health, detail or "")
        else:
            st.markdown(f"###### {title}")
            st.caption(subtitle)
            if detail:
                st.caption(detail)


def _render_kpi_strip(cards: list[dict]) -> None:
    cols = st.columns(len(cards))
    for col, card in zip(cols, cards):
        with col:
            st.markdown(
                f"""
                <div class="pbi-kpi-card" style="--accent:{card.get('accent', '#118DFF')}">
                    <div class="pbi-kpi-label">{escape(str(card.get('label', '')))}</div>
                    <div class="pbi-kpi-value">{escape(str(card.get('value', '')))}</div>
                    <div class="pbi-kpi-delta">{escape(str(card.get('delta', '')))}</div>
                    <div class="pbi-kpi-note">{escape(str(card.get('note', '')))}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


__all__ = [name for name in globals() if not name.startswith("__")]
