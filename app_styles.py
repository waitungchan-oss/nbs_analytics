from __future__ import annotations

import streamlit as st

import streamlit_rendering as rendering_module


def apply_global_styles() -> None:
    rendering_module._render_dynamic_theme_css()
    st.markdown(
        """
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;600;700;800&display=swap');
        :root {
            --nbs-primary-navy: #0B1F3A;
            --nbs-ink-navy: #071426;
            --nbs-sidebar-navy: #061222;
            --nbs-sidebar-panel: rgba(255,255,255,0.07);
            --nbs-sidebar-bg: linear-gradient(180deg, #FFFFFF 0%, #F8FAFD 100%);
            --nbs-sidebar-border: #D8E0EA;
            --nbs-sidebar-panel-hover: #EEF5FF;
            --nbs-sidebar-text: #334155;
            --nbs-sidebar-text-strong: #111827;
            --nbs-sidebar-muted: #64748B;
            --nbs-sidebar-input-bg: #FFFFFF;
            --nbs-sidebar-input-text: #111827;
            --nbs-sidebar-chip-bg: #EFF6FF;
            --nbs-sidebar-chip-text: #0B1F3A;
            --nbs-sidebar-active-bg: linear-gradient(135deg, rgba(17,141,255,0.12), rgba(47,128,237,0.07));
            --nbs-badge-official-text: #0F7A43;
            --nbs-badge-official-bg: #DCFCE7;
            --nbs-badge-official-border: #9AE6B4;
            --nbs-badge-diagnostic-text: #1D5FBF;
            --nbs-badge-diagnostic-bg: #DBEAFE;
            --nbs-badge-diagnostic-border: #93C5FD;
            --nbs-badge-experimental-text: #8A5A00;
            --nbs-badge-experimental-bg: #FEF3C7;
            --nbs-badge-experimental-border: #FBBF24;
            --nbs-badge-readonly-text: #475569;
            --nbs-badge-readonly-bg: #E2E8F0;
            --nbs-badge-readonly-border: #CBD5E1;
            --nbs-badge-manual-text: #BE185D;
            --nbs-badge-manual-bg: #FCE7F3;
            --nbs-badge-manual-border: #F9A8D4;
            --nbs-badge-session-text: #6D28D9;
            --nbs-badge-session-bg: #EDE9FE;
            --nbs-badge-session-border: #C4B5FD;
            --nbs-primary-blue: #118DFF;
            --nbs-active-blue: #2F80ED;
            --nbs-page-bg: #F4F7FB;
            --nbs-surface: #FFFFFF;
            --nbs-surface-soft: #F8FAFD;
            --nbs-surface-panel: #FBFCFE;
            --nbs-border: #D8E0EA;
            --nbs-divider: #E5EAF0;
            --nbs-text: #1F2937;
            --nbs-muted: #52616F;
            --nbs-faint: #7A8694;
            --nbs-success: #1F9D55;
            --nbs-warning: #D97706;
            --nbs-danger: #C2410C;
            --nbs-shadow: 0 8px 24px rgba(15, 23, 42, 0.08);
            --nbs-dataframe-filter: none;
        }
        html, body, [class*="css"] {
            font-family: 'Noto Sans TC', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
        }
        html,
        body,
        [data-testid="stAppViewContainer"],
        [data-testid="stMain"],
        [data-testid="stMainBlockContainer"] {
            background: var(--nbs-page-bg) !important;
            color: var(--nbs-text) !important;
        }
        .stApp {
            background:
                var(--nbs-app-overlay),
                var(--nbs-page-bg) !important;
            color: var(--nbs-text) !important;
        }
        header[data-testid="stHeader"] {
            background: color-mix(in srgb, var(--nbs-page-bg) 92%, transparent) !important;
            border-bottom: 1px solid rgba(216,224,234,0.75);
            backdrop-filter: blur(8px);
        }
        [data-testid="stToolbar"],
        [data-testid="stDecoration"] {
            background: transparent !important;
        }
        .block-container {
            padding-top: 1.15rem;
            padding-bottom: 2.5rem;
        }
        .main-title {
            font-size: clamp(1.8rem, 2.4vw, 2.45rem);
            font-weight: 800;
            color: var(--nbs-text);
            margin-bottom: 0.2rem;
            padding-top: 0.35rem;
            letter-spacing: 0;
        }
        .sub-title {
            font-size: 0.98rem;
            color: var(--nbs-muted);
            margin-bottom: 1rem;
        }
        .nbs-topbar {
            display: flex;
            justify-content: space-between;
            gap: 1rem;
            align-items: center;
            background: var(--nbs-topbar-bg);
            border: 1px solid var(--nbs-border);
            border-radius: 8px;
            padding: 1.05rem 1.2rem;
            box-shadow: 0 1px 2px rgba(15,23,42,0.06);
            margin-bottom: 1rem;
        }
        .nbs-brand-kicker {
            color: var(--nbs-muted);
            font-weight: 700;
            font-size: 0.78rem;
            letter-spacing: .08em;
            text-transform: uppercase;
        }
        .nbs-scope-pill {
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
            color: var(--nbs-text-strong);
            background: var(--nbs-chip-bg);
            border: 1px solid var(--nbs-border);
            border-radius: 999px;
            padding: 0.38rem 0.72rem;
            font-size: 0.86rem;
            font-weight: 800;
            white-space: nowrap;
        }
        .nbs-topbar-status {
            display: flex;
            flex-wrap: wrap;
            justify-content: flex-end;
            gap: 0.45rem;
            max-width: 42rem;
        }
        .nbs-status-chip {
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
            border-radius: 999px;
            padding: 0.28rem 0.62rem;
            font-size: 0.78rem;
            font-weight: 800;
            color: var(--nbs-muted);
            background: var(--nbs-chip-bg);
            border: 1px solid var(--nbs-border);
            white-space: nowrap;
        }
        .nbs-status-chip::before {
            content: "";
            width: 0.45rem;
            height: 0.45rem;
            border-radius: 999px;
            background: var(--nbs-success);
            display: inline-block;
        }
        .section-title,
        .nbs-section-title {
            font-size: 1.2rem;
            font-weight: 800;
            color: var(--nbs-text);
            margin-top: 1rem;
            margin-bottom: 0.25rem;
            letter-spacing: 0;
        }
        .section-subtitle,
        .nbs-section-subtitle {
            font-size: 0.92rem;
            color: var(--nbs-muted);
            margin-bottom: 0.75rem;
            line-height: 1.55;
        }
        .year-header {
            font-size: 1.12rem;
            font-weight: 800;
            color: var(--nbs-text);
            margin-top: 1.1rem;
            margin-bottom: 0.6rem;
            padding-bottom: 0.35rem;
            border-bottom: 2px solid var(--nbs-primary-blue);
        }
        .channel-header {
            font-size: 1.02rem;
            font-weight: 800;
            color: var(--nbs-text);
            margin-top: 1rem;
            margin-bottom: 0.4rem;
            padding-left: 10px;
            border-left: 4px solid var(--nbs-primary-blue);
        }
        .pbi-kpi-card {
            background: var(--nbs-surface);
            border: 1px solid var(--nbs-border);
            border-left: 4px solid var(--accent, #118DFF);
            border-radius: 8px;
            padding: 1rem 1rem 0.9rem 1rem;
            box-shadow: 0 1px 2px rgba(15,23,42,.08);
            min-height: 118px;
            height: auto;
            overflow-wrap: anywhere;
            transition: transform .12s ease, box-shadow .12s ease;
        }
        .pbi-kpi-card:hover {
            transform: translateY(-1px);
            box-shadow: var(--nbs-shadow);
        }
        .pbi-kpi-label {
            font-size: 0.84rem;
            font-weight: 800;
            color: var(--nbs-muted);
            letter-spacing: 0;
            margin-bottom: 0.25rem;
            text-transform: none;
        }
        .pbi-kpi-value {
            font-size: clamp(1.24rem, 1.6vw, 1.55rem);
            line-height: 1.16;
            font-weight: 800;
            color: var(--nbs-text-strong);
            margin-bottom: 0.25rem;
            letter-spacing: 0;
            overflow-wrap: anywhere;
            white-space: normal;
        }
        .pbi-kpi-delta {
            font-size: 0.88rem;
            color: var(--nbs-muted);
            margin-bottom: 0.15rem;
        }
        .pbi-kpi-note {
            font-size: 0.78rem;
            color: var(--nbs-faint);
            line-height: 1.25;
        }
        .pbi-panel {
            background: var(--nbs-surface);
            border: 1px solid var(--nbs-border);
            border-radius: 8px;
            padding: 1rem 1rem 0.9rem 1rem;
            box-shadow: 0 1px 2px rgba(15,23,42,.08);
            margin-top: 0.15rem;
        }
        .pbi-panel-title {
            font-size: 1.02rem;
            font-weight: 800;
            color: var(--nbs-text);
            margin-bottom: 0.15rem;
        }
        .pbi-panel-subtitle {
            font-size: 0.88rem;
            color: var(--nbs-muted);
            margin-bottom: 0.75rem;
        }
        .nbs-panel-header {
            margin: 0.1rem 0 0.7rem 0;
        }
        .nbs-panel-kicker {
            color: var(--nbs-muted);
            font-size: 0.72rem;
            font-weight: 900;
            letter-spacing: .08em;
            text-transform: uppercase;
            margin-bottom: 0.1rem;
        }
        .nbs-panel-title {
            color: var(--nbs-text);
            font-size: 1.04rem;
            font-weight: 900;
            line-height: 1.25;
        }
        .nbs-panel-copy {
            color: var(--nbs-muted);
            font-size: 0.84rem;
            line-height: 1.45;
            margin-top: 0.15rem;
        }
        .nbs-hero-panel {
            background: var(--nbs-hero-bg);
            border: 1px solid var(--nbs-border);
            border-radius: 8px;
            padding: 1.05rem 1.15rem;
            box-shadow: 0 1px 2px rgba(15,23,42,.08);
            margin: 0.65rem 0 1rem 0;
        }
        .nbs-hero-title {
            font-size: 1rem;
            font-weight: 800;
            color: var(--nbs-text);
            margin-bottom: 0.18rem;
        }
        .nbs-hero-copy {
            font-size: 0.9rem;
            color: var(--nbs-muted);
            line-height: 1.5;
        }
        .nbs-db-status-card {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1rem;
            background: var(--nbs-db-bg);
            border: 1px solid var(--nbs-db-border);
            border-left: 4px solid var(--nbs-success);
            border-radius: 8px;
            padding: 0.85rem 1rem;
            color: var(--nbs-db-text);
            box-shadow: 0 1px 2px rgba(15,23,42,.06);
            margin-bottom: 0.9rem;
        }
        .nbs-db-status-title {
            font-size: 0.92rem;
            font-weight: 900;
            margin-bottom: 0.1rem;
        }
        .nbs-db-status-meta {
            color: var(--nbs-db-text);
            font-size: 0.82rem;
            font-weight: 700;
        }
        .nbs-filter-strip {
            display: flex;
            flex-wrap: wrap;
            gap: 0.45rem;
            margin: 0.35rem 0 0.9rem 0;
        }
        .nbs-filter-chip {
            display: inline-flex;
            align-items: center;
            gap: 0.32rem;
            border-radius: 999px;
            padding: 0.34rem 0.65rem;
            background: var(--nbs-chip-bg);
            border: 1px solid var(--nbs-border);
            color: var(--nbs-text);
            font-size: 0.8rem;
            font-weight: 800;
            box-shadow: 0 1px 2px rgba(15,23,42,.04);
            max-width: 100%;
            overflow-wrap: anywhere;
            white-space: normal;
        }
        .nbs-filter-chip span {
            color: var(--nbs-muted);
            font-weight: 800;
        }
        .nbs-executive-band {
            display: flex;
            justify-content: space-between;
            align-items: flex-end;
            gap: 1rem;
            margin: 0.6rem 0 0.6rem 0;
        }
        .nbs-executive-kicker {
            color: var(--nbs-muted);
            font-size: 0.76rem;
            font-weight: 900;
            letter-spacing: .08em;
            text-transform: uppercase;
            margin-bottom: 0.12rem;
        }
        .nbs-executive-title {
            color: var(--nbs-text);
            font-size: 1.28rem;
            font-weight: 900;
            line-height: 1.25;
        }
        .nbs-executive-note {
            color: var(--nbs-muted);
            font-size: 0.86rem;
            line-height: 1.45;
        }
        .nbs-forecast-card,
        .nbs-download-card {
            background: var(--nbs-surface);
            border: 1px solid var(--nbs-border);
            border-radius: 8px;
            padding: 0.95rem;
            box-shadow: 0 1px 2px rgba(15,23,42,.08);
            margin-bottom: 0.75rem;
        }
        .nbs-download-card {
            min-height: 6.1rem;
            height: auto;
            border-left: 4px solid var(--nbs-active-blue);
            overflow-wrap: anywhere;
        }
        .nbs-export-status-card {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 1rem;
            padding: 0.95rem 1rem;
            margin: 0.35rem 0 0.8rem 0;
            background: var(--nbs-export-bg);
            border: 1px solid var(--nbs-border);
            border-radius: 8px;
            box-shadow: 0 1px 2px rgba(15,23,42,.05);
        }
        .nbs-export-status-title {
            color: var(--nbs-text);
            font-weight: 900;
            font-size: 0.98rem;
            margin-bottom: 0.12rem;
        }
        .nbs-export-status-meta {
            color: var(--nbs-muted);
            font-size: 0.82rem;
            line-height: 1.45;
            overflow-wrap: anywhere;
        }
        .nbs-card-title {
            font-weight: 800;
            color: var(--nbs-text);
            font-size: 0.98rem;
            margin-bottom: 0.15rem;
        }
        .nbs-card-note {
            color: var(--nbs-muted);
            font-size: 0.84rem;
            line-height: 1.45;
        }
        .nbs-badge {
            display: inline-flex;
            align-items: center;
            border-radius: 999px;
            padding: 0.22rem 0.58rem;
            font-size: 0.78rem;
            font-weight: 800;
            border: 1px solid transparent;
            white-space: normal;
            line-height: 1.25;
            overflow-wrap: anywhere;
        }
        .nbs-badge-success { color: #146C3A; background: #E8F7EE; border-color: #C7EBD5; }
        .nbs-badge-info { color: #1B5DBF; background: #EAF2FF; border-color: #CFE0F4; }
        .nbs-badge-warning { color: #9A5B00; background: #FFF4DE; border-color: #F6D492; }
        .nbs-badge-danger { color: #A33A12; background: #FFF0E8; border-color: #F5C6B1; }
        .nbs-badge-muted { color: var(--nbs-muted); background: #F1F5F9; border-color: var(--nbs-border); }
        [data-testid="stMetric"] {
            background-color: var(--nbs-metric-bg);
            padding: 16px 18px;
            border-radius: 8px;
            border: 1px solid var(--nbs-border);
            box-shadow: 0 1px 2px rgba(15,23,42,.08);
        }
        [data-testid="stImage"] > img {
            border-radius: 8px;
            box-shadow: 0 1px 2px rgba(15,23,42,.08);
            background-color: var(--nbs-image-bg) !important;
            padding: 8px;
        }
        [data-testid="stDataFrame"] {
            box-shadow: 0 1px 2px rgba(15,23,42,.08);
            border-radius: 8px;
            background-color: var(--nbs-dataframe-bg);
            padding: 5px;
            border: 1px solid var(--nbs-border);
        }
        [data-testid="stDataFrame"] [role="columnheader"] {
            font-weight: 900 !important;
            color: var(--nbs-text) !important;
        }
        [data-testid="stDataFrame"] [role="gridcell"] {
            font-size: 0.82rem !important;
        }
        [data-testid="stDataFrame"] canvas {
            filter: var(--nbs-dataframe-filter) !important;
        }
        div[data-testid="stVerticalBlockBorderWrapper"] {
            border-color: var(--nbs-border) !important;
            border-radius: 8px !important;
            box-shadow: 0 1px 2px rgba(15,23,42,.06);
            background: var(--nbs-surface);
        }
        [data-testid="stTabs"] button[aria-selected="true"] {
            border-bottom: 3px solid var(--nbs-primary-blue) !important;
            font-weight: 800 !important;
        }
        section[data-testid="stSidebar"] {
            border-right: 1px solid var(--nbs-sidebar-border);
            background: var(--nbs-sidebar-bg) !important;
            color: var(--nbs-sidebar-text) !important;
        }
        section[data-testid="stSidebar"] > div,
        section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {
            background: transparent !important;
        }
        section[data-testid="stSidebar"] * {
            color: var(--nbs-sidebar-text);
        }
        section[data-testid="stSidebar"] [data-testid="stCaptionContainer"],
        section[data-testid="stSidebar"] small {
            color: var(--nbs-sidebar-muted) !important;
        }
        section[data-testid="stSidebar"] div[data-baseweb="select"] *,
        section[data-testid="stSidebar"] div[data-baseweb="input"] *,
        section[data-testid="stSidebar"] input,
        section[data-testid="stSidebar"] textarea {
            color: var(--nbs-sidebar-input-text) !important;
        }
        section[data-testid="stSidebar"] div[data-baseweb="select"] > div,
        section[data-testid="stSidebar"] div[data-baseweb="input"],
        section[data-testid="stSidebar"] input,
        section[data-testid="stSidebar"] textarea {
            background: var(--nbs-sidebar-input-bg) !important;
            border-color: var(--nbs-sidebar-border) !important;
            min-height: 2.35rem !important;
            line-height: 1.35 !important;
        }
        section[data-testid="stSidebar"] div[data-baseweb="select"] div[role="button"],
        section[data-testid="stSidebar"] div[data-baseweb="select"] div[data-baseweb="tag"] {
            min-height: 1.6rem !important;
            max-width: 100% !important;
            overflow: visible !important;
        }
        section[data-testid="stSidebar"] div[data-baseweb="select"] div[data-baseweb="tag"] {
            align-items: center !important;
            background: var(--nbs-sidebar-chip-bg) !important;
            color: var(--nbs-sidebar-chip-text) !important;
            padding: 0.18rem 0.42rem !important;
            line-height: 1.25 !important;
            border-radius: 6px !important;
        }
        section[data-testid="stSidebar"] div[data-baseweb="select"] div[data-baseweb="tag"] * {
            color: var(--nbs-sidebar-chip-text) !important;
        }
        section[data-testid="stSidebar"] div[data-baseweb="select"] div[data-baseweb="tag"] span {
            display: inline-block !important;
            max-width: 7.2rem !important;
            overflow: visible !important;
            text-overflow: clip !important;
            white-space: nowrap !important;
            line-height: 1.25 !important;
        }
        section[data-testid="stSidebar"] div[data-baseweb="select"] [class*="value-container"] {
            align-items: flex-start !important;
            flex-wrap: wrap !important;
            row-gap: 0.25rem !important;
            column-gap: 0.25rem !important;
            max-height: 7.5rem !important;
            overflow-y: auto !important;
            overflow-x: hidden !important;
            padding-left: 0.35rem !important;
            padding-right: 0.35rem !important;
        }
        section[data-testid="stSidebar"] label,
        section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {
            color: var(--nbs-sidebar-text-strong) !important;
            font-weight: 800 !important;
        }
        section[data-testid="stSidebar"] button {
            border-radius: 6px !important;
            font-weight: 800 !important;
        }
        section[data-testid="stSidebar"] div.stButton > button,
        section[data-testid="stSidebar"] div.stFormSubmitButton > button {
            background: var(--nbs-sidebar-active-bg) !important;
            border: 1px solid var(--nbs-sidebar-border) !important;
            color: var(--nbs-sidebar-text-strong) !important;
            box-shadow: none !important;
        }
        section[data-testid="stSidebar"] div.stButton > button:hover,
        section[data-testid="stSidebar"] div.stFormSubmitButton > button:hover {
            background: var(--nbs-sidebar-panel-hover) !important;
            border-color: var(--nbs-active-blue) !important;
            color: var(--nbs-sidebar-text-strong) !important;
        }
        section[data-testid="stSidebar"] div.stButton > button:disabled,
        section[data-testid="stSidebar"] div.stFormSubmitButton > button:disabled {
            opacity: 0.68 !important;
        }
        section[data-testid="stSidebar"] hr {
            border-color: var(--nbs-sidebar-border);
            margin: 0.75rem 0;
        }
        section[data-testid="stSidebar"] [data-testid="stSidebarCollapseButton"] {
            position: absolute !important;
            top: 5.1rem !important;
            right: 0.72rem !important;
            z-index: 20 !important;
            width: 1.9rem !important;
            height: 1.9rem !important;
            border-radius: 8px !important;
            background: var(--nbs-sidebar-panel) !important;
            border: 1px solid var(--nbs-sidebar-border) !important;
            box-shadow: none !important;
        }
        section[data-testid="stSidebar"] [data-testid="stSidebarCollapseButton"]:hover {
            background: var(--nbs-sidebar-panel-hover) !important;
        }
        section[data-testid="stSidebar"] [data-testid="stSidebarCollapseButton"] * {
            color: var(--nbs-sidebar-text-strong) !important;
        }
        .nbs-sidebar-brand {
            position: relative;
            display: flex;
            align-items: center;
            gap: 0.65rem;
            min-height: 3rem;
            padding: 0.55rem 2.75rem 0.85rem 0;
            border-bottom: 1px solid var(--nbs-sidebar-border);
            margin-bottom: 0.85rem;
        }
        .nbs-sidebar-logo {
            width: 2.15rem;
            height: 2.15rem;
            border-radius: 8px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            color: var(--nbs-sidebar-text-strong);
            background: var(--nbs-sidebar-chip-bg);
            border: 1px solid var(--nbs-sidebar-border);
            font-weight: 900;
        }
        .nbs-sidebar-title {
            color: var(--nbs-sidebar-text-strong);
            font-weight: 900;
            font-size: 1rem;
            line-height: 1.15;
        }
        .nbs-sidebar-subtitle {
            color: var(--nbs-sidebar-muted);
            font-size: 0.76rem;
            margin-top: 0.08rem;
        }
        .nbs-sidebar-zone-label {
            color: var(--nbs-sidebar-muted);
            text-transform: uppercase;
            letter-spacing: .08em;
            font-size: 0.7rem;
            font-weight: 900;
            margin: 0.78rem 0 0.35rem 0;
        }
        .nbs-sidebar-zone-copy {
            color: var(--nbs-sidebar-muted);
            font-size: 0.73rem;
            line-height: 1.35;
            margin: -0.1rem 0 0.62rem 0;
        }
        .nbs-sidebar-control-header {
            border-top: 1px solid var(--nbs-sidebar-border);
            margin: 0.95rem 0 0.55rem 0;
            padding-top: 0.82rem;
        }
        .nbs-sidebar-control-title {
            color: var(--nbs-sidebar-text-strong);
            font-weight: 900;
            font-size: 0.95rem;
            line-height: 1.2;
        }
        .nbs-sidebar-control-copy {
            color: var(--nbs-sidebar-muted);
            font-size: 0.73rem;
            line-height: 1.35;
            margin-top: 0.18rem;
        }
        .nbs-sidebar-nav {
            display: grid;
            gap: 0.48rem;
            margin: 0.35rem 0 0.85rem 0;
        }
        .nbs-sidebar-nav-group {
            border: 1px solid var(--nbs-sidebar-border);
            border-radius: 8px;
            background: var(--nbs-sidebar-panel);
            overflow: hidden;
        }
        .nbs-sidebar-nav-group-title {
            color: var(--nbs-sidebar-text-strong);
            font-weight: 900;
            font-size: 0.82rem;
            padding: 0.48rem 0.58rem;
            border-left: 3px solid var(--nbs-active-blue);
            background: var(--nbs-sidebar-panel-hover);
        }
        .nbs-sidebar-submenu {
            display: grid;
            gap: 0.18rem;
            padding: 0.28rem 0.34rem 0.42rem 0.34rem;
        }
        .nbs-sidebar-nav-item,
        .nbs-sidebar-nav-item:visited {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            align-items: center;
            gap: 0.42rem;
            color: var(--nbs-sidebar-text);
            border-left: 3px solid transparent;
            border-radius: 6px;
            padding: 0.42rem 0.46rem;
            background: transparent;
            font-weight: 700;
            font-size: 0.78rem;
            line-height: 1.25;
            text-decoration: none !important;
            min-width: 0;
        }
        .nbs-sidebar-nav-text {
            min-width: 0;
            overflow-wrap: anywhere;
        }
        .nbs-sidebar-nav-item:hover,
        .nbs-sidebar-nav-item.active {
            color: var(--nbs-sidebar-text-strong);
            border-left-color: var(--nbs-active-blue);
            background: var(--nbs-sidebar-active-bg);
        }
        body:has(div[id^="section-"]:target) .nbs-sidebar-nav-item.active:not(:hover) {
            color: var(--nbs-sidebar-text);
            border-left-color: transparent;
            background: transparent;
        }
        body:has(#section-current-context:target) .nbs-sidebar-nav-item[href="#section-current-context"],
        body:has(#section-kpi-overview:target) .nbs-sidebar-nav-item[href="#section-kpi-overview"],
        body:has(#section-year-summary:target) .nbs-sidebar-nav-item[href="#section-year-summary"],
        body:has(#section-branch-ranking:target) .nbs-sidebar-nav-item[href="#section-branch-ranking"],
        body:has(#section-product-drilldown:target) .nbs-sidebar-nav-item[href="#section-product-drilldown"],
        body:has(#section-data-quality:target) .nbs-sidebar-nav-item[href="#section-data-quality"],
        body:has(#section-entity-audit:target) .nbs-sidebar-nav-item[href="#section-entity-audit"],
        body:has(#section-ai-cleaning:target) .nbs-sidebar-nav-item[href="#section-ai-cleaning"],
        body:has(#section-ai-forecast:target) .nbs-sidebar-nav-item[href="#section-ai-forecast"],
        body:has(#section-daily-forecast:target) .nbs-sidebar-nav-item[href="#section-daily-forecast"],
        body:has(#section-seven-day-macro:target) .nbs-sidebar-nav-item[href="#section-seven-day-macro"],
        body:has(#section-month-end-macro:target) .nbs-sidebar-nav-item[href="#section-month-end-macro"],
        body:has(#section-model-diagnostics:target) .nbs-sidebar-nav-item[href="#section-model-diagnostics"],
        body:has(#section-forecast-governance:target) .nbs-sidebar-nav-item[href="#section-forecast-governance"],
        body:has(#section-daily-wape:target) .nbs-sidebar-nav-item[href="#section-daily-wape"],
        body:has(#section-macro-backtest:target) .nbs-sidebar-nav-item[href="#section-macro-backtest"],
        body:has(#section-feature-store:target) .nbs-sidebar-nav-item[href="#section-feature-store"],
        body:has(#section-causal-analytics:target) .nbs-sidebar-nav-item[href="#section-causal-analytics"],
        body:has(#section-data-exports:target) .nbs-sidebar-nav-item[href="#section-data-exports"] {
            color: var(--nbs-sidebar-text-strong);
            border-left-color: var(--nbs-active-blue);
            background: var(--nbs-sidebar-active-bg);
        }
        .nbs-sidebar-nav-badge {
            border-radius: 999px;
            border: 1px solid var(--nbs-sidebar-border);
            color: var(--nbs-sidebar-text);
            background: var(--nbs-sidebar-chip-bg);
            font-size: 0.58rem;
            font-weight: 900;
            padding: 0.05rem 0.28rem;
            white-space: nowrap;
            justify-self: end;
        }
        .nbs-sidebar-nav-badge.official {
            color: var(--nbs-badge-official-text);
            border-color: var(--nbs-badge-official-border);
            background: var(--nbs-badge-official-bg);
        }
        .nbs-sidebar-nav-badge.diagnostic {
            color: var(--nbs-badge-diagnostic-text);
            border-color: var(--nbs-badge-diagnostic-border);
            background: var(--nbs-badge-diagnostic-bg);
        }
        .nbs-sidebar-nav-badge.experimental {
            color: var(--nbs-badge-experimental-text);
            border-color: var(--nbs-badge-experimental-border);
            background: var(--nbs-badge-experimental-bg);
        }
        .nbs-sidebar-nav-badge.readonly {
            color: var(--nbs-badge-readonly-text);
            border-color: var(--nbs-badge-readonly-border);
            background: var(--nbs-badge-readonly-bg);
        }
        .nbs-sidebar-nav-badge.manual {
            color: var(--nbs-badge-manual-text);
            border-color: var(--nbs-badge-manual-border);
            background: var(--nbs-badge-manual-bg);
        }
        .nbs-sidebar-nav-badge.session {
            color: var(--nbs-badge-session-text);
            border-color: var(--nbs-badge-session-border);
            background: var(--nbs-badge-session-bg);
        }
        div[id^="section-"] {
            scroll-margin-top: 5rem;
            height: 0;
        }
        section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {
            max-height: 100vh;
            overflow-y: auto;
            padding-bottom: 1.25rem;
        }
        .nbs-sidebar-group-label {
            color: var(--nbs-sidebar-muted);
            text-transform: uppercase;
            letter-spacing: .08em;
            font-size: 0.7rem;
            font-weight: 900;
            margin: 0.75rem 0 0.35rem 0;
        }
        div.stButton > button[kind="primary"],
        div.stDownloadButton > button,
        div.stFormSubmitButton > button {
            border-radius: 6px !important;
            font-weight: 800 !important;
        }
        div.stButton > button,
        div.stDownloadButton > button,
        div.stFormSubmitButton > button {
            background: var(--nbs-chip-bg) !important;
            border: 1px solid var(--nbs-border) !important;
            color: var(--nbs-text-strong) !important;
            box-shadow: none !important;
        }
        div.stButton > button:hover,
        div.stDownloadButton > button:hover,
        div.stFormSubmitButton > button:hover {
            border-color: var(--nbs-active-blue) !important;
            color: var(--nbs-text-strong) !important;
        }
        div.stButton > button:disabled,
        div.stDownloadButton > button:disabled,
        div.stFormSubmitButton > button:disabled {
            background: var(--nbs-surface-panel) !important;
            border-color: var(--nbs-border) !important;
            color: var(--nbs-muted) !important;
            opacity: 0.78 !important;
        }
        [data-testid="stMetric"] label,
        [data-testid="stMetric"] [data-testid="stMetricValue"],
        [data-testid="stMetric"] [data-testid="stMetricDelta"] {
            color: var(--nbs-text) !important;
            overflow-wrap: anywhere !important;
            white-space: normal !important;
        }
        [data-testid="stMetric"] [data-testid="stMetricValue"] {
            font-size: clamp(1.35rem, 2vw, 2.05rem) !important;
            line-height: 1.15 !important;
            overflow: visible !important;
            text-overflow: clip !important;
        }
        [data-testid="stMetric"] [data-testid="stMetricValue"] > div,
        [data-testid="stMetric"] [data-testid="stMetricValue"] p {
            white-space: normal !important;
            overflow: visible !important;
            text-overflow: clip !important;
            line-height: 1.15 !important;
        }
        [data-testid="stMetric"] {
            min-height: 6.4rem;
            height: auto !important;
        }
        [data-testid="stSlider"] label,
        [data-testid="stSlider"] p,
        [data-testid="stCheckbox"] label,
        [data-testid="stCheckbox"] p {
            color: var(--nbs-text) !important;
            opacity: 1 !important;
        }
        [data-testid="stSlider"] [aria-disabled="true"],
        [data-testid="stCheckbox"] [aria-disabled="true"] {
            opacity: 0.82 !important;
        }
        [data-testid="stExpander"] {
            background: var(--nbs-surface) !important;
            border-color: var(--nbs-border) !important;
            color: var(--nbs-text) !important;
        }
        [data-testid="stTabs"] button {
            color: var(--nbs-text) !important;
            white-space: normal !important;
        }
        [data-testid="stDataFrame"] * {
            color: var(--nbs-text);
        }
        [data-testid="stDataFrame"] canvas {
            filter: none;
        }
        div[data-baseweb="popover"],
        div[data-baseweb="popover"] * {
            color: var(--nbs-text) !important;
        }
    </style>
    """,
        unsafe_allow_html=True,
    )

