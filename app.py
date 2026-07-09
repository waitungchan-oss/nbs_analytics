"""中旅 NBS 企業營銷大盤主入口。"""

from __future__ import annotations

import io
import importlib
import hashlib
import json
import pickle
import threading
import time
from html import escape
from pathlib import Path
import traceback

import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(page_title="中旅NBS營銷數據自動化與AI決策系統", layout="wide", initial_sidebar_state="expanded")

import config as config_module  # noqa: E402
import database as database_module  # noqa: E402
import pipeline as pipeline_module  # noqa: E402
import visuals as visuals_module  # noqa: E402
import forecasting as forecasting_module  # noqa: E402
import streamlit_rendering as rendering_module  # noqa: E402
from backend.services.stability_history_service import record_stability_history  # noqa: E402
from backend.services.stability_service import build_phase2c_stability_gate  # noqa: E402
from backend.services.upload_preflight_service import run_upload_preflight  # noqa: E402
from backend.services.upload_rollback_service import handle_core_drift_rollback  # noqa: E402

config_module = importlib.reload(config_module)
database_module = importlib.reload(database_module)
pipeline_module = importlib.reload(pipeline_module)
visuals_module = importlib.reload(visuals_module)
forecasting_module = importlib.reload(forecasting_module)

COL_BRANCH = config_module.COL_BRANCH
COL_DATE = config_module.COL_DATE
COL_MONEY = config_module.COL_MONEY
COL_ORDER_ID = config_module.COL_ORDER_ID
COL_QTY = config_module.COL_QTY
COL_SALESPERSON = config_module.COL_SALESPERSON
COL_TRANS_TIME = config_module.COL_TRANS_TIME
CONFIG_FILE = config_module.CONFIG_FILE
DB_FILE = config_module.DB_FILE
SESSION_RULE_KEYS = config_module.SESSION_RULE_KEYS
TARGET_DEPT_FOR_REP = config_module.TARGET_DEPT_FOR_REP
init_session_state_config = config_module.init_session_state_config
save_business_rules = config_module.save_business_rules

clear_database = database_module.clear_database
load_all_data_from_db = database_module.load_all_data_from_db
repair_operator_sales_rep_assignments = database_module.repair_operator_sales_rep_assignments
repair_subtable_branch_assignments = database_module.repair_subtable_branch_assignments
restore_database_from_backup = database_module.restore_database_from_backup
upsert_to_db = database_module.upsert_to_db

UPLOAD_OPERATION_LOCK = threading.Lock()

HAS_AI_LIBS = forecasting_module.HAS_AI_LIBS
build_macro_forecast_summary = forecasting_module.build_macro_forecast_summary
run_ai_backtest_report = forecasting_module.run_ai_backtest_report
run_ai_macro_backtest_report = forecasting_module.run_ai_macro_backtest_report
run_ai_prediction_tracks = forecasting_module.run_ai_prediction_tracks

build_dashboard_data = pipeline_module.build_dashboard_data
build_dashboard_data_excluding_receipt_types = pipeline_module.build_dashboard_data_excluding_receipt_types
clean_invoice_number = pipeline_module.clean_invoice_number
map_dest_category = pipeline_module.map_dest_category
map_ticket_category = pipeline_module.map_ticket_category
normalize_runtime_columns = pipeline_module.normalize_runtime_columns

HAS_MATPLOTLIB = visuals_module.HAS_MATPLOTLIB
draw_forecast_chart = visuals_module.draw_forecast_chart
draw_month_end_macro_chart = visuals_module.draw_month_end_macro_chart
draw_seven_day_macro_chart = visuals_module.draw_seven_day_macro_chart
draw_top10_barh = visuals_module.draw_top10_barh
safe_draw_pie = visuals_module.safe_draw_pie

init_session_state_config()
st.session_state.setdefault("PROCESSED_DATA_CACHE", None)
st.session_state.setdefault("DB_LOADED_FLAG", False)
st.session_state.setdefault("NBS_UI_THEME", "light")
if st.session_state.get("OPERATOR_REPAIR_RULE_VERSION") != 1:
    st.session_state["PROCESSED_DATA_CACHE"] = None
    st.session_state["DB_LOADED_FLAG"] = False
    st.session_state["OPERATOR_REPAIR_RULE_VERSION"] = 1
if st.session_state.get("SUBTABLE_BRANCH_REPAIR_RULE_VERSION") != 1:
    st.session_state["PROCESSED_DATA_CACHE"] = None
    st.session_state["DB_LOADED_FLAG"] = False
    st.session_state["SUBTABLE_BRANCH_REPAIR_RULE_VERSION"] = 1
if st.session_state.get("EXPORT_RULE_VERSION") != 5:
    st.session_state["PROCESSED_DATA_CACHE"] = None
    st.session_state["DB_LOADED_FLAG"] = False
    st.session_state["EXPORT_RULE_VERSION"] = 5
if st.session_state.get("REVENUE_SCOPE_RULE_VERSION") != 1:
    st.session_state["PROCESSED_DATA_CACHE"] = None
    st.session_state["DB_LOADED_FLAG"] = False
    st.session_state["REVENUE_SCOPE_RULE_VERSION"] = 1
if st.session_state.get("FORECAST_STRATEGY_RULE_VERSION") != 5:
    st.session_state["PROCESSED_DATA_CACHE"] = None
    st.session_state["DB_LOADED_FLAG"] = False
    st.session_state["FORECAST_STRATEGY_RULE_VERSION"] = 5

REVENUE_SCOPE_LABEL = "不含掛賬核銷與TT退款轉團款"
REVENUE_SCOPE_CAPTION = "收入口徑：不含收款類型「掛賬核銷」；不含收款方式「TT 退款轉團款」。"
REVENUE_SCOPE_EXCLUDED_RECEIPT_TYPES = ("掛賬核銷",)
REVENUE_SCOPE_EXCLUDED_PAYMENT_METHODS = ("TT 退款轉團款",)
AI_CACHE_VERSION = "daily-macro-normal-tight-v1"
EXPORT_CACHE_VERSION = "export-lazy-v3"
AI_CACHE_DIR = Path(__file__).resolve().parent / ".nbs_runtime_cache"

from app_styles import apply_global_styles

apply_global_styles()




from app_pages import main

try:
    _ = main()
except Exception:
    from streamlit_rendering import _render_error
    _render_error("主程式發生未捕獲錯誤，但已攔截避免白屏。", traceback.format_exc())
