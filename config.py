"""Streamlit session state configuration for NBS Analytics."""

from __future__ import annotations

import streamlit as st
from rules import (
    BASE_DIR,
    COL_BRANCH,
    COL_DATE,
    COL_DEPT,
    COL_DEST_CATEGORY,
    COL_DAYS,
    COL_MONEY,
    COL_ORDER_ID,
    COL_QTY,
    COL_RECEIPT_OPERATOR,
    COL_SALESPERSON,
    COL_SOURCE_TAG,
    COLS_TO_FETCH,
    COL_TRANS_TIME,
    COL_TOUR_NAME,
    CONFIG_FILE,
    DATE_COL_R,
    DATE_COL_Y,
    DEFAULT_BRANCH_MAPPING,
    DEFAULT_RULES,
    DB_FILE,
    KEY_COL_1,
    KEY_COL_2,
    MONEY_COLS_1,
    MONEY_COLS_2,
    BRANCH_REASSIGNMENT_OVERRIDES,
    SESSION_RULE_KEYS,
    TARGET_DEPT_FOR_REP,
    _clean_list,
    _clean_text,
    load_business_rules,
    save_business_rules,
)


def init_session_state_config() -> None:
    rules = load_business_rules()
    branch_mapping = rules.get("BRANCH_MAPPING", DEFAULT_BRANCH_MAPPING)
    if not isinstance(branch_mapping, dict):
        branch_mapping = DEFAULT_BRANCH_MAPPING

    normalized = {
        "BRANCH_MAPPING": {
            _clean_text(k).upper(): _clean_text(v)
            for k, v in branch_mapping.items()
            if _clean_text(k) and _clean_text(v)
        },
        "BRANCH_REASSIGNMENT_OVERRIDES": list(rules.get("BRANCH_REASSIGNMENT_OVERRIDES", [])),
        "EXCLUDE_PREFIXES": _clean_list(rules.get("EXCLUDE_PREFIXES", [])),
        "TARGET_BRANCHES_S3": _clean_list(rules.get("TARGET_BRANCHES_S3", [])),
        "SALES_REP_LIST": _clean_list(rules.get("SALES_REP_LIST", [])),
        "CRUISE_DEPTS": _clean_list(rules.get("CRUISE_DEPTS", [])),
    }
    for key, value in normalized.items():
        if key not in st.session_state:
            st.session_state[key] = value
    if "PROCESSED_DATA_CACHE" not in st.session_state:
        st.session_state["PROCESSED_DATA_CACHE"] = None
    if "DB_LOADED_FLAG" not in st.session_state:
        st.session_state["DB_LOADED_FLAG"] = False
