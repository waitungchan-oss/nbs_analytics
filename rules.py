"""Pure business rules and persisted rule helpers for NBS Analytics."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DB_FILE = os.environ.get("NBS_ANALYTICS_DB_FILE", str(BASE_DIR / "nbs_marketing_data.db"))
CONFIG_FILE = str(BASE_DIR / "rules_config.json")

DEFAULT_BRANCH_MAPPING = {
    "33": "銅鑼灣分社",
    "47": "太古分社",
    "36": "旺角銀行中心分社",
    "31": "將軍澳分社",
    "19": "沙田分社",
    "17": "荃灣綠楊坊分社",
    "27": "屯門市廣場分社",
    "03": "觀塘分社",
    "20": "大埔分社",
    "95": "西九龍站分社",
    "09": "機場服務處",
    "E2": "九龍灣服務點",
    "E3": "沙田服務點",
    "E4": "葵涌服務點",
    "E5": "屯門服務點",
    "E6": "上環服務點",
    "E7": "九龍維景酒店服務點",
    "E8": "筲箕灣服務點",
    "E9": "元朗服務點",
    "0A": "展覽會場專用",
    "0B": "展覽會場專用2",
    "225": "營銷運營中心-專職銷售組",
}

DEFAULT_RULES = {
    "BRANCH_MAPPING": DEFAULT_BRANCH_MAPPING,
    "BRANCH_REASSIGNMENT_OVERRIDES": [
        {
            "month": "2026-06",
            "from_prefix": "E6",
            "from_branch": "上環服務點",
            "to_prefix": "0A",
            "to_branch": "展覽會場專用",
            "scope": ["旅行團", "郵輪", "票務"],
            "reason": "2026年6月E6上環服務點銷售額歸入0A展覽會場專用",
        },
        {
            "month": "2026-06",
            "source_order_id": "E9MF16613172500",
            "from_branch": "上環服務點",
            "to_prefix": "0A",
            "to_branch": "展覽會場專用",
            "scope": ["票務"],
            "reason": "2026年6月指定上環票務訂單歸入0A展覽會場專用",
        }
    ],
    "EXCLUDE_PREFIXES": ["1950506", "1950404", "1950202"],
    "TARGET_BRANCHES_S3": [
        "銅鑼灣分社",
        "太古分社",
        "旺角銀行中心分社",
        "將軍澳分社",
        "沙田分社",
        "荃灣綠楊坊分社",
        "屯門市廣場分社",
        "觀塘分社",
        "大埔分社",
        "西九龍站分社",
        "機場服務處",
        "九龍灣服務點",
        "沙田服務點",
        "葵涌服務點",
        "屯門服務點",
        "上環服務點",
        "九龍維景酒店服務點",
        "筲箕灣服務點",
        "元朗服務點",
        "展覽會場專用",
        "展覽會場專用2",
        "未知",
    ],
    "SALES_REP_LIST": ["YTLAU 刘元太", "ELSA 谢玲玲", "JIA 江嘉韵", "SOGOR 苏清秩"],
    "CRUISE_DEPTS": ["郵輪事業部-郵輪線業務組", "移走-郵輪事業部"],
}

BRANCH_REASSIGNMENT_OVERRIDES = DEFAULT_RULES["BRANCH_REASSIGNMENT_OVERRIDES"]

SESSION_RULE_KEYS = (
    "BRANCH_MAPPING",
    "BRANCH_REASSIGNMENT_OVERRIDES",
    "EXCLUDE_PREFIXES",
    "TARGET_BRANCHES_S3",
    "SALES_REP_LIST",
    "CRUISE_DEPTS",
)

KEY_COL_1 = "來源單據號"
KEY_COL_2 = "交易號碼"
DATE_COL_Y = "交易時間"
DATE_COL_R = "收款時間"
COLS_TO_FETCH = [
    "行程天數",
    "數量",
    "幣種",
    "應收",
    "已收",
    "交易時間",
    "團負責人",
    "團負責人部門",
    "目的地大類",
    "一級目的地",
    "二級目的地",
    "目的地名稱",
    "銷售點",
    "銷售員",
    "團名稱",
    "團代號",
]
MONEY_COLS_1 = ["收款原幣金額", "收款本幣金額"]
MONEY_COLS_2 = ["應收", "已收"]

COL_DATE = "收款時間"
COL_BRANCH = "銷售點"
COL_MONEY = "收款原幣金額"
COL_DEPT = "團負責人部門"
COL_SALESPERSON = "銷售員"
COL_ORDER_ID = "來源單據號"
COL_TRANS_TIME = "交易時間"
COL_DEST_CATEGORY = "目的地大類"
COL_DAYS = "行程天數"
COL_QTY = "數量"
COL_TOUR_NAME = "團名稱"
COL_SOURCE_TAG = "來源報表標籤"
COL_RECEIPT_OPERATOR = "收款操作員"
TARGET_DEPT_FOR_REP = "營銷運營中心-專職銷售組"


def _clean_text(value: Any) -> str:
    return str(value).replace("\u3000", " ").strip()


def _clean_list(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        raw = values.replace("，", ",").split(",")
    else:
        raw = list(values)
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        value = _clean_text(item)
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def load_business_rules(path: str | Path | None = None) -> dict:
    target = Path(path or CONFIG_FILE)
    if not target.exists():
        return DEFAULT_RULES.copy()
    try:
        with target.open("r", encoding="utf-8") as f:
            loaded = json.load(f)
        return {**DEFAULT_RULES, **loaded}
    except Exception:
        return DEFAULT_RULES.copy()


def save_business_rules(new_rules: dict) -> bool:
    try:
        rules = {key: new_rules.get(key, DEFAULT_RULES[key]) for key in SESSION_RULE_KEYS}
        with Path(CONFIG_FILE).open("w", encoding="utf-8") as f:
            json.dump(rules, f, ensure_ascii=False, indent=4)
        return True
    except Exception:
        return False
