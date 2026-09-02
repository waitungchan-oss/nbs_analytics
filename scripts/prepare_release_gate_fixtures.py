"""Build disposable DB/cache fixtures for clean release-gate runners."""

from __future__ import annotations

import io
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import load_all_data_from_db
from backend.services.gmv_export_cache_service import build_gmv_export_cache
from backend.services.gmv_refund_repository import migrate_gmv_schema
from backend.services.gmv_refund_service import RevenueFrames, revenue_state_token
from backend.services.revenue_scope_service import REVENUE_SCOPE_LABEL, build_revenue_scope_frames
from rules import TARGET_DEPT_FOR_REP


EXPECTED_MONTHLY_TOTALS = {
    "2026-01": 10711053.50,
    "2026-02": 9765694.54,
    "2026-03": 14628841.00,
    "2026-04": 10506207.78,
    "2026-05": 12057967.92,
    "2026-06": 9083241.29,
}
ANALYSIS_ROWS = 26640
EXCLUDED_ROWS = 545
VERSION_ID = "release-gate-fixture"
UI_EXCLUDED_ROWS = 1

PHASE2_BRANCH_ALLOCATION = (
    ("荃灣綠楊坊分社", "", 1_705_339.0),
    ("旺角銀行中心分社", "", 1_146_543.0),
    ("沙田分社", "", 737_527.0),
    ("銅鑼灣分社", "", 704_358.0),
    ("屯門市廣場分社", "", 673_995.0),
    ("觀塘分社", "", 500_000.0),
    ("大埔分社", "", 500_000.0),
    ("西九龍站分社", "", 400_000.0),
    ("機場服務處", "", 290_382.0),
)
PHASE2_SPECIALIST_ALLOCATION = (
    (TARGET_DEPT_FOR_REP, "YTLAU 刘元太", 4_421_710.0),
    (TARGET_DEPT_FOR_REP, "SOGOR 苏清秩", 444_608.0),
    (TARGET_DEPT_FOR_REP, "ELSA 谢玲玲", 329_056.0),
    (TARGET_DEPT_FOR_REP, "JIA 江嘉韵", 204_449.92),
)


@dataclass(frozen=True)
class ReleaseGateFixtures:
    db_path: Path
    cache_dir: Path
    active_version_id: str


def _revenue_rows(*, rows_per_month: int, excluded_rows: int, phase2_dimensions: bool = False) -> pd.DataFrame:
    months = list(EXPECTED_MONTHLY_TOTALS)
    rows: list[dict[str, object]] = []
    for month_index, month in enumerate(months):
        for row_index in range(rows_per_month):
            if month == "2026-01" and row_index == 1 and phase2_dimensions:
                day = "01"
                date_month = "2025-01"
            else:
                day = "22" if month == "2026-06" and row_index == rows_per_month - 1 else "01"
                date_month = month
            branch = "銅鑼灣分社"
            salesperson = ""
            amount = EXPECTED_MONTHLY_TOTALS[month] if row_index == 0 else 0.0
            if month == "2026-05" and phase2_dimensions:
                allocation = PHASE2_BRANCH_ALLOCATION + PHASE2_SPECIALIST_ALLOCATION
                if row_index < len(allocation):
                    branch, salesperson, amount = allocation[row_index]
            rows.append(
                {
                    "收款單號": f"fixture-r-{month_index}-{row_index}",
                    "收款單狀態": "正常",
                    "來源單據號": f"33{month_index:02d}{row_index:06d}",
                    "收款原幣金額": amount,
                    "收款類型": "一般",
                    "收款方式": "一般",
                    "收款時間": f"{month}-{day}",
                    "銷售點": branch,
                    "銷售員": salesperson,
                    "交易時間": f"{date_month}-{day}",
                    "團名稱": "release gate fixture",
                    "目的地大類": "",
                    "數量": 1,
                    "行程天數": 0,
                    "已收": 0,
                    "應收": 0,
                    "幣種": "HKD",
                    "原幣幣種": "HKD",
                    "資料來源": "旅行團",
                    "統一日期": f"{date_month}-{day}",
                }
            )
    for row_index in range(excluded_rows):
        rows.append(
            {
                "收款單號": f"fixture-x-{row_index}",
                "收款單狀態": "正常",
                "來源單據號": f"33x{row_index:05d}",
                "收款原幣金額": 0.0,
                "收款類型": "掛賬核銷",
                "收款方式": "一般",
                "收款時間": "2026-05-01",
                    "銷售點": "銅鑼灣分社",
                "銷售員": "",
                "交易時間": "2026-05-01",
                "團名稱": "release gate fixture",
                "目的地大類": "",
                "數量": 1,
                "行程天數": 0,
                "已收": 0,
                "應收": 0,
                "幣種": "HKD",
                "原幣幣種": "HKD",
                "資料來源": "旅行團",
                "統一日期": "2026-05-01",
            }
        )
    return pd.DataFrame(rows)


def _workbook_bytes(label: str) -> bytes:
    output = io.BytesIO()
    pd.DataFrame([{"fixture": label, "scope": REVENUE_SCOPE_LABEL}]).to_excel(output, index=False)
    return output.getvalue()


def _summary_rows(dimension: str, total: float) -> list[dict[str, object]]:
    return [
        {"退款維度": dimension, "指標": "退款明細金額", "數值": 0.0},
        {"退款維度": dimension, "指標": "實際扣減金額", "數值": 0.0},
        {"退款維度": dimension, "指標": "超額退款金額", "數值": 0.0},
        {"退款維度": dimension, "指標": "排除前 GMV", "數值": total},
        {"退款維度": dimension, "指標": "退款扣減後 GMV", "數值": total},
    ]


def build_release_gate_fixtures(root: Path, *, profile: str = "full") -> ReleaseGateFixtures:
    if profile not in {"full", "ui"}:
        raise ValueError(f"unsupported release gate fixture profile: {profile}")
    target = Path(root).resolve()
    target.mkdir(parents=True, exist_ok=True)
    db_path = target / "release_gate_fixture.db"
    cache_dir = target / ".nbs_runtime_cache"
    rows = _revenue_rows(
        rows_per_month=ANALYSIS_ROWS // len(EXPECTED_MONTHLY_TOTALS) if profile == "full" else 1,
        excluded_rows=EXCLUDED_ROWS if profile == "full" else UI_EXCLUDED_ROWS,
        phase2_dimensions=profile == "full",
    )
    with sqlite3.connect(db_path) as connection:
        rows.to_sql("tour_data", connection, index=False)
        rows.head(0).to_sql("others_data", connection, index=False)
    migrate_gmv_schema(db_path)

    # Build the token from the same SQLite round-trip used by the app.  This
    # keeps the disposable fixture cache key source-bound to the actual
    # runtime dtypes/columns instead of the pre-SQLite pandas frame.
    db_tour, db_others = load_all_data_from_db(db_path=db_path, read_only=True)
    formal_tour, formal_others, _ = build_revenue_scope_frames(db_tour, db_others)
    frames = RevenueFrames(db_tour, db_others, formal_tour, formal_others)
    token = revenue_state_token(frames, REVENUE_SCOPE_LABEL)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO gmv_scope_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (VERSION_ID, None, None, token, "d" * 64, REVENUE_SCOPE_LABEL, "e" * 64, "ACTIVE", now, "release-gate-fixture"),
        )
        connection.commit()

    total = sum(EXPECTED_MONTHLY_TOTALS.values())
    workbooks = {
        "ex.xlsx": _workbook_bytes("ex"),
        "ex_no_writeoff.xlsx": _workbook_bytes("ex_no_writeoff"),
        "ex_no_writeoff_refund_transfer.xlsx": _workbook_bytes("official_scope"),
        "audit.xlsx": _workbook_bytes("audit"),
    }
    detail = pd.DataFrame([{"來源單據號": "fixture", "退款狀態": "TOTAL_REFUND", "金額": 0.0}])
    build_gmv_export_cache(
        version_id=VERSION_ID,
        revenue_generation_token=token,
        rule_version=REVENUE_SCOPE_LABEL,
        total_workbooks=workbooks,
        paid_workbooks=workbooks,
        total_detail=detail,
        paid_detail=detail.assign(退款狀態="REFUNDED"),
        summaries=_summary_rows("總退款", total) + _summary_rows("已退款", total),
        cache_dir=cache_dir,
        builder_mode="release_gate_fixture",
        validation_mode="release_gate_fixture",
        shadow_status="NOT_RUN",
        refund_state_sha256="d" * 64,
    )
    return ReleaseGateFixtures(db_path, cache_dir, VERSION_ID)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", choices=("full", "ui"), default="full")
    args = parser.parse_args(argv)
    fixture = build_release_gate_fixtures(args.output, profile=args.profile)
    print(f"db={fixture.db_path}")
    print(f"cache={fixture.cache_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
