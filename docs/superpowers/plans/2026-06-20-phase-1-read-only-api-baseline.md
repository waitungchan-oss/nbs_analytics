# Phase 1 Read-only API Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only Python API baseline for NBS Analytics without changing the existing Streamlit dashboard behavior.

**Architecture:** Add a FastAPI backend under `backend/` with health and dashboard read endpoints. The backend must not import `app.py`, because `app.py` executes Streamlit `main()` at import time; instead, shared read-only business logic must live in backend service modules that call existing safe modules such as `database.py`, `pipeline.py`, and `config.py`.

**Tech Stack:** Python, FastAPI, Pydantic, Uvicorn, pytest, FastAPI TestClient, existing SQLite/Pandas pipeline.

---

## Scope Boundary

Phase 1 is intentionally small.

Implement in Phase 1:

- `GET /api/health`
- `GET /api/dashboard/context`
- `POST /api/dashboard/summary`
- read-only dashboard service
- smoke tests and service tests

Do not implement in Phase 1:

- Vue frontend
- file upload
- rules config writes
- AI Forecast endpoints
- Lazy Export endpoints
- GMV exclusion endpoints
- SQLite migration
- changes to official revenue scope
- changes to Streamlit `app.py` runtime behavior

This project directory is currently not a Git repository. Replace commit steps with checkpoint verification commands and a concise changed-file list.

---

## File Structure

Create:

- `backend/__init__.py`
- `backend/main.py`
- `backend/routers/__init__.py`
- `backend/routers/health.py`
- `backend/routers/dashboard.py`
- `backend/schemas/__init__.py`
- `backend/schemas/dashboard.py`
- `backend/services/__init__.py`
- `backend/services/dashboard_service.py`
- `tests/test_backend_health.py`
- `tests/test_dashboard_service.py`
- `tests/test_dashboard_api.py`

Modify:

- `requirements.txt`

Do not modify:

- `app.py`
- `pipeline.py`
- `database.py`
- `forecasting.py`
- `nbs_marketing_data.db`
- `.nbs_runtime_cache/`
- `rules_config.json`

---

## Task 1: Add Backend Dependencies

**Files:**

- Modify: `requirements.txt`

- [ ] **Step 1: Add FastAPI and test dependencies**

Append these lines to `requirements.txt`:

```text
fastapi>=0.115,<1
uvicorn[standard]>=0.30,<1
pytest>=8,<9
httpx>=0.27,<1
```

- [ ] **Step 2: Install dependencies in the existing virtual environment**

Run:

```bash
.venv/bin/python -m pip install -r requirements.txt
```

Expected:

```text
Successfully installed ...
```

The exact package list may vary because some dependencies may already be installed.

- [ ] **Step 3: Verify imports**

Run:

```bash
.venv/bin/python - <<'PY'
import fastapi
import pydantic
import uvicorn
print("fastapi", fastapi.__version__)
print("pydantic", pydantic.__version__)
print("uvicorn", uvicorn.__version__)
PY
```

Expected:

```text
fastapi <version>
pydantic <version>
uvicorn <version>
```

---

## Task 2: Create FastAPI App Skeleton

**Files:**

- Create: `backend/__init__.py`
- Create: `backend/main.py`
- Create: `backend/routers/__init__.py`
- Create: `backend/routers/health.py`
- Test: `tests/test_backend_health.py`

- [ ] **Step 1: Create package markers**

Create `backend/__init__.py`:

```python
"""FastAPI backend package for NBS Analytics."""
```

Create `backend/routers/__init__.py`:

```python
"""API routers for the NBS Analytics backend."""
```

- [ ] **Step 2: Create health router**

Create `backend/routers/health.py`:

```python
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from config import DB_FILE

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health_check() -> dict:
    db_path = Path(DB_FILE)
    cache_path = Path(".nbs_runtime_cache")
    return {
        "status": "ok",
        "service": "nbs-analytics-api",
        "db": {
            "path": str(db_path),
            "exists": db_path.exists(),
            "sizeBytes": db_path.stat().st_size if db_path.exists() else 0,
        },
        "runtimeCache": {
            "path": str(cache_path),
            "exists": cache_path.exists(),
        },
    }
```

- [ ] **Step 3: Create FastAPI app**

Create `backend/main.py`:

```python
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routers import health


def create_app() -> FastAPI:
    app = FastAPI(
        title="NBS Analytics API",
        version="0.1.0",
        description="Read-only API baseline for the NBS Analytics dashboard.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    return app


app = create_app()
```

- [ ] **Step 4: Write health test**

Create `tests/test_backend_health.py`:

```python
from fastapi.testclient import TestClient

from backend.main import create_app


def test_health_check_returns_runtime_status():
    client = TestClient(create_app())
    response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "nbs-analytics-api"
    assert "db" in payload
    assert "runtimeCache" in payload
```

- [ ] **Step 5: Run the health test**

Run:

```bash
.venv/bin/python -m pytest tests/test_backend_health.py -v
```

Expected:

```text
tests/test_backend_health.py::test_health_check_returns_runtime_status PASSED
```

---

## Task 3: Add Dashboard Schemas

**Files:**

- Create: `backend/schemas/__init__.py`
- Create: `backend/schemas/dashboard.py`

- [ ] **Step 1: Create schema package marker**

Create `backend/schemas/__init__.py`:

```python
"""Pydantic schemas for NBS Analytics API responses."""
```

- [ ] **Step 2: Create dashboard request and response models**

Create `backend/schemas/dashboard.py`:

```python
from __future__ import annotations

from pydantic import BaseModel, Field


class DashboardFilters(BaseModel):
    years: list[int] = Field(default_factory=list)
    months: list[str] = Field(default_factory=list)
    dateRange: list[str] = Field(default_factory=list)
    branch: str = "全部分社"
    salesGroup: str = "全部銷售組"


class DashboardContextResponse(BaseModel):
    hasData: bool
    tourRows: int
    othersRows: int
    maxDate: str | None
    minDate: str | None
    years: list[int]
    months: list[str]
    branches: list[str]
    salesGroups: list[str]
    revenueScope: str


class KpiCard(BaseModel):
    label: str
    value: str
    delta: str
    note: str
    accent: str


class DashboardSummaryResponse(BaseModel):
    appliedFilters: DashboardFilters
    revenueScope: str
    scopeAudit: dict
    kpis: list[KpiCard]
    branchRanking: list[dict]
    productMix: list[dict]
    exportReadiness: dict
```

- [ ] **Step 3: Verify schemas import**

Run:

```bash
.venv/bin/python - <<'PY'
from backend.schemas.dashboard import DashboardFilters, DashboardContextResponse, DashboardSummaryResponse
print(DashboardFilters().model_dump())
print(DashboardContextResponse)
print(DashboardSummaryResponse)
PY
```

Expected:

```text
{'years': [], 'months': [], 'dateRange': [], 'branch': '全部分社', 'salesGroup': '全部銷售組'}
<class 'backend.schemas.dashboard.DashboardContextResponse'>
<class 'backend.schemas.dashboard.DashboardSummaryResponse'>
```

---

## Task 4: Create Read-only Dashboard Service

**Files:**

- Create: `backend/services/__init__.py`
- Create: `backend/services/dashboard_service.py`
- Test: `tests/test_dashboard_service.py`

- [ ] **Step 1: Create service package marker**

Create `backend/services/__init__.py`:

```python
"""Read-only backend services for NBS Analytics."""
```

- [ ] **Step 2: Create dashboard service**

Create `backend/services/dashboard_service.py`:

```python
from __future__ import annotations

from datetime import date
from typing import Iterable

import pandas as pd

from config import (
    COL_BRANCH,
    COL_DATE,
    COL_MONEY,
    COL_ORDER_ID,
    COL_SALESPERSON,
    DEFAULT_RULES,
)
from database import load_all_data_from_db
from pipeline import build_dashboard_data, normalize_runtime_columns

REVENUE_SCOPE_LABEL = "不含掛賬核銷與TT退款轉團款"
EXCLUDED_RECEIPT_TYPES = {"掛賬核銷", "TT 退款轉團款"}


def _money_text(value: float) -> str:
    return f"HKD {float(value):,.0f}"


def _safe_option_list(series: pd.Series) -> list[str]:
    if series is None or series.empty:
        return []
    values = series.dropna().astype(str).str.replace("\u3000", " ", regex=False).str.strip()
    return sorted({value for value in values if value})


def _sum_money(df: pd.DataFrame) -> float:
    if df.empty or COL_MONEY not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[COL_MONEY], errors="coerce").fillna(0).sum())


def _collect_revenue_scope_excluded_ids(frames: Iterable[pd.DataFrame]) -> set[str]:
    excluded_ids: set[str] = set()
    for frame in frames:
        if frame.empty or COL_ORDER_ID not in frame.columns or "收款類型" not in frame.columns:
            continue
        mask = frame["收款類型"].astype(str).str.strip().isin(EXCLUDED_RECEIPT_TYPES)
        ids = frame.loc[mask, COL_ORDER_ID].dropna().astype(str).str.strip()
        excluded_ids.update(value for value in ids if value)
    return excluded_ids


def _drop_revenue_scope_excluded_ids(df: pd.DataFrame, excluded_ids: set[str]) -> pd.DataFrame:
    if df.empty or not excluded_ids or COL_ORDER_ID not in df.columns:
        return df.copy()
    ids = df[COL_ORDER_ID].astype(str).str.strip()
    return df.loc[~ids.isin(excluded_ids)].copy()


def build_revenue_scope_frames(db_tour: pd.DataFrame, db_others: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    raw_tour = normalize_runtime_columns(db_tour)
    raw_others = normalize_runtime_columns(db_others)
    excluded_ids = _collect_revenue_scope_excluded_ids([raw_tour, raw_others])
    analysis_tour = _drop_revenue_scope_excluded_ids(raw_tour, excluded_ids)
    analysis_others = _drop_revenue_scope_excluded_ids(raw_others, excluded_ids)
    audit = {
        "scope_label": REVENUE_SCOPE_LABEL,
        "excluded_order_count": len(excluded_ids),
        "raw_rows": len(raw_tour) + len(raw_others),
        "analysis_rows": len(analysis_tour) + len(analysis_others),
        "excluded_rows": (len(raw_tour) + len(raw_others)) - (len(analysis_tour) + len(analysis_others)),
        "raw_amount": _sum_money(raw_tour) + _sum_money(raw_others),
        "analysis_amount": _sum_money(analysis_tour) + _sum_money(analysis_others),
    }
    audit["excluded_amount"] = audit["raw_amount"] - audit["analysis_amount"]
    return analysis_tour, analysis_others, audit


def _date_pool(*frames: pd.DataFrame) -> pd.Series:
    series_list = []
    for frame in frames:
        if not frame.empty and "統一日期" in frame.columns:
            series_list.append(pd.to_datetime(frame["統一日期"], errors="coerce"))
        elif not frame.empty and COL_DATE in frame.columns:
            series_list.append(pd.to_datetime(frame[COL_DATE], errors="coerce"))
    if not series_list:
        return pd.Series(dtype="datetime64[ns]")
    return pd.concat(series_list, ignore_index=True).dropna()


def _apply_filters(df: pd.DataFrame, date_col: str, years: list[int], months: list[str], date_range: list[str]) -> pd.DataFrame:
    if df.empty or date_col not in df.columns:
        return df.copy()
    work = df.copy()
    dt = pd.to_datetime(work[date_col], errors="coerce")
    mask = pd.Series(True, index=work.index)
    if years:
        mask &= dt.dt.year.isin(years)
    if months:
        mask &= dt.dt.strftime("%Y-%m").isin(months)
    if len(date_range) == 2:
        start = pd.to_datetime(date_range[0], errors="coerce")
        end = pd.to_datetime(date_range[1], errors="coerce")
        if pd.notna(start) and pd.notna(end):
            mask &= dt.between(start, end)
    return work.loc[mask].copy()


def build_dashboard_context() -> dict:
    db_tour, db_others = load_all_data_from_db()
    analysis_tour, analysis_others, _ = build_revenue_scope_frames(db_tour, db_others)
    branch_mapping = DEFAULT_RULES["BRANCH_MAPPING"]
    target_branches = DEFAULT_RULES["TARGET_BRANCHES_S3"]
    cruise_depts = DEFAULT_RULES["CRUISE_DEPTS"]
    sales_reps = DEFAULT_RULES["SALES_REP_LIST"]
    _, s1, _ = build_dashboard_data(
        analysis_tour,
        analysis_others,
        branch_mapping,
        target_branches,
        cruise_depts,
        sales_reps,
        make_workbook=False,
    )
    dates = _date_pool(analysis_tour, analysis_others)
    months = sorted(dates.dt.strftime("%Y-%m").unique().tolist()) if not dates.empty else []
    years = sorted(dates.dt.year.astype(int).unique().tolist()) if not dates.empty else []
    max_date = None if dates.empty else str(dates.max().date())
    min_date = None if dates.empty else str(dates.min().date())
    sales_join = pd.concat(
        [
            analysis_tour.get(COL_SALESPERSON, pd.Series(dtype=str)),
            analysis_others.get(COL_SALESPERSON, pd.Series(dtype=str)),
        ],
        ignore_index=True,
    )
    return {
        "hasData": not db_tour.empty or not db_others.empty,
        "tourRows": int(len(db_tour)),
        "othersRows": int(len(db_others)),
        "maxDate": max_date,
        "minDate": min_date,
        "years": years,
        "months": months,
        "branches": _safe_option_list(s1.get("文本", pd.Series(dtype=str))),
        "salesGroups": _safe_option_list(sales_join),
        "revenueScope": REVENUE_SCOPE_LABEL,
    }


def _kpis(s1: pd.DataFrame, tour: pd.DataFrame, others: pd.DataFrame, filters: dict) -> list[dict]:
    years = filters.get("years", [])
    months = filters.get("months", [])
    date_range = filters.get("dateRange", [])
    branch = filters.get("branch", "全部分社")
    sales_group = filters.get("salesGroup", "全部銷售組")

    s1_f = _apply_filters(s1, "日期", years, months, date_range)
    tour_f = _apply_filters(tour, "統一日期", years, months, date_range)
    others_f = _apply_filters(others, "統一日期", years, months, date_range)

    if branch != "全部分社" and "文本" in s1_f.columns:
        s1_f = s1_f[s1_f["文本"].astype(str).str.strip() == str(branch).strip()].copy()
    if branch != "全部分社" and COL_BRANCH in tour_f.columns:
        tour_f = tour_f[tour_f[COL_BRANCH].astype(str).str.strip() == str(branch).strip()].copy()
    if branch != "全部分社" and COL_BRANCH in others_f.columns:
        others_f = others_f[others_f[COL_BRANCH].astype(str).str.strip() == str(branch).strip()].copy()

    if sales_group != "全部銷售組" and COL_SALESPERSON in tour_f.columns:
        tour_f = tour_f[tour_f[COL_SALESPERSON].astype(str).str.strip() == str(sales_group).strip()].copy()
    if sales_group != "全部銷售組" and COL_SALESPERSON in others_f.columns:
        others_f = others_f[others_f[COL_SALESPERSON].astype(str).str.strip() == str(sales_group).strip()].copy()

    tour_value = float(pd.to_numeric(s1_f.get("旅行團", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    cruise_value = float(pd.to_numeric(s1_f.get("郵輪", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    ticket_value = float(pd.to_numeric(s1_f.get("票務", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    total = tour_value + cruise_value + ticket_value
    branch_count = int(s1_f["文本"].astype(str).nunique()) if "文本" in s1_f.columns else 0
    sales_join = pd.concat(
        [
            tour_f.get(COL_SALESPERSON, pd.Series(dtype=str)),
            others_f.get(COL_SALESPERSON, pd.Series(dtype=str)),
        ],
        ignore_index=True,
    )
    sales_count = int(sales_join.astype(str).str.strip().replace("", pd.NA).dropna().nunique())

    return [
        {
            "label": "淨營收",
            "value": _money_text(total),
            "delta": f"目前視角：{branch} / {sales_group}",
            "note": f"{REVENUE_SCOPE_LABEL}；含旅行團、郵輪與票務",
            "accent": "#118DFF",
        },
        {
            "label": "旅行團營收",
            "value": _money_text(tour_value),
            "delta": f"佔比 {tour_value / total * 100:.1f}%" if total else "佔比 0.0%",
            "note": f"旅行團產品板塊；{REVENUE_SCOPE_LABEL}",
            "accent": "#12239E",
        },
        {
            "label": "郵輪營收",
            "value": _money_text(cruise_value),
            "delta": f"佔比 {cruise_value / total * 100:.1f}%" if total else "佔比 0.0%",
            "note": f"郵輪產品板塊；{REVENUE_SCOPE_LABEL}",
            "accent": "#E66C37",
        },
        {
            "label": "票務營收",
            "value": _money_text(ticket_value),
            "delta": f"佔比 {ticket_value / total * 100:.1f}%" if total else "佔比 0.0%",
            "note": f"票務產品板塊；{REVENUE_SCOPE_LABEL}",
            "accent": "#6B007B",
        },
        {
            "label": "可見分社 / 專員",
            "value": f"{branch_count} / {sales_count}",
            "delta": "以目前篩選條件計算",
            "note": "用來確認當前視角覆蓋範圍",
            "accent": "#197278",
        },
    ]


def build_dashboard_summary(filters: dict) -> dict:
    db_tour, db_others = load_all_data_from_db()
    analysis_tour, analysis_others, scope_audit = build_revenue_scope_frames(db_tour, db_others)
    branch_mapping = DEFAULT_RULES["BRANCH_MAPPING"]
    target_branches = DEFAULT_RULES["TARGET_BRANCHES_S3"]
    cruise_depts = DEFAULT_RULES["CRUISE_DEPTS"]
    sales_reps = DEFAULT_RULES["SALES_REP_LIST"]
    _, s1, s2 = build_dashboard_data(
        analysis_tour,
        analysis_others,
        branch_mapping,
        target_branches,
        cruise_depts,
        sales_reps,
        make_workbook=False,
    )
    ranking = []
    if not s1.empty:
        rank_df = s1.copy()
        for col in ["旅行團", "郵輪", "票務"]:
            rank_df[col] = pd.to_numeric(rank_df.get(col, 0), errors="coerce").fillna(0)
        rank_df["總額"] = rank_df[["旅行團", "郵輪", "票務"]].sum(axis=1)
        ranking = rank_df.sort_values("總額", ascending=False).head(20).to_dict("records")
    product_mix = s2.head(50).to_dict("records") if isinstance(s2, pd.DataFrame) else []
    return {
        "appliedFilters": filters,
        "revenueScope": REVENUE_SCOPE_LABEL,
        "scopeAudit": scope_audit,
        "kpis": _kpis(s1, analysis_tour, analysis_others, filters),
        "branchRanking": ranking,
        "productMix": product_mix,
        "exportReadiness": {
            "lazyExport": True,
            "status": "not_loaded",
            "message": "Phase 1 API does not prepare Excel workbooks.",
        },
    }
```

- [ ] **Step 3: Write service tests with monkeypatch**

Create `tests/test_dashboard_service.py`:

```python
import pandas as pd

from backend.services import dashboard_service


def _sample_frames():
    tour = pd.DataFrame(
        [
            {
                "來源單據號": "A001",
                "收款時間": "2026-06-01",
                "統一日期": "2026-06-01",
                "收款原幣金額": 1000,
                "收款類型": "正常收款",
                "銷售點": "銅鑼灣分社",
                "銷售員": "YTLAU 刘元太",
                "目的地大類": "旅行團",
                "團負責人部門": "",
                "行程天數": 3,
                "數量": 1,
            },
            {
                "來源單據號": "A002",
                "收款時間": "2026-06-02",
                "統一日期": "2026-06-02",
                "收款原幣金額": 500,
                "收款類型": "掛賬核銷",
                "銷售點": "銅鑼灣分社",
                "銷售員": "YTLAU 刘元太",
                "目的地大類": "旅行團",
                "團負責人部門": "",
                "行程天數": 3,
                "數量": 1,
            },
        ]
    )
    others = pd.DataFrame(
        [
            {
                "來源單據號": "B001",
                "收款時間": "2026-06-03",
                "統一日期": "2026-06-03",
                "收款原幣金額": 300,
                "收款類型": "正常收款",
                "銷售點": "太古分社",
                "銷售員": "ELSA 谢玲玲",
                "目的地大類": "票務",
                "團負責人部門": "",
                "行程天數": 0,
                "數量": 1,
            }
        ]
    )
    return tour, others


def test_revenue_scope_excludes_writeoff_order():
    tour, others = _sample_frames()

    scoped_tour, scoped_others, audit = dashboard_service.build_revenue_scope_frames(tour, others)

    assert len(scoped_tour) == 1
    assert len(scoped_others) == 1
    assert audit["excluded_order_count"] == 1
    assert audit["excluded_rows"] == 1
    assert audit["analysis_amount"] == 1300


def test_dashboard_context_uses_read_only_loaded_frames(monkeypatch):
    tour, others = _sample_frames()
    monkeypatch.setattr(dashboard_service, "load_all_data_from_db", lambda: (tour, others))

    context = dashboard_service.build_dashboard_context()

    assert context["hasData"] is True
    assert context["tourRows"] == 2
    assert context["othersRows"] == 1
    assert context["revenueScope"] == "不含掛賬核銷與TT退款轉團款"
    assert "2026-06" in context["months"]


def test_dashboard_summary_returns_kpis_without_export_generation(monkeypatch):
    tour, others = _sample_frames()
    monkeypatch.setattr(dashboard_service, "load_all_data_from_db", lambda: (tour, others))

    summary = dashboard_service.build_dashboard_summary(
        {
            "years": [2026],
            "months": ["2026-06"],
            "dateRange": ["2026-06-01", "2026-06-30"],
            "branch": "全部分社",
            "salesGroup": "全部銷售組",
        }
    )

    assert summary["revenueScope"] == "不含掛賬核銷與TT退款轉團款"
    assert len(summary["kpis"]) == 5
    assert summary["exportReadiness"]["lazyExport"] is True
    assert summary["exportReadiness"]["status"] == "not_loaded"
```

- [ ] **Step 4: Run service tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_dashboard_service.py -v
```

Expected:

```text
tests/test_dashboard_service.py::test_revenue_scope_excludes_writeoff_order PASSED
tests/test_dashboard_service.py::test_dashboard_context_uses_read_only_loaded_frames PASSED
tests/test_dashboard_service.py::test_dashboard_summary_returns_kpis_without_export_generation PASSED
```

---

## Task 5: Add Dashboard API Router

**Files:**

- Create: `backend/routers/dashboard.py`
- Modify: `backend/main.py`
- Test: `tests/test_dashboard_api.py`

- [ ] **Step 1: Create dashboard router**

Create `backend/routers/dashboard.py`:

```python
from __future__ import annotations

from fastapi import APIRouter

from backend.schemas.dashboard import (
    DashboardContextResponse,
    DashboardFilters,
    DashboardSummaryResponse,
)
from backend.services.dashboard_service import build_dashboard_context, build_dashboard_summary

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/context", response_model=DashboardContextResponse)
def dashboard_context() -> dict:
    return build_dashboard_context()


@router.post("/summary", response_model=DashboardSummaryResponse)
def dashboard_summary(filters: DashboardFilters) -> dict:
    return build_dashboard_summary(filters.model_dump())
```

- [ ] **Step 2: Register dashboard router**

Modify `backend/main.py`:

```python
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routers import dashboard, health


def create_app() -> FastAPI:
    app = FastAPI(
        title="NBS Analytics API",
        version="0.1.0",
        description="Read-only API baseline for the NBS Analytics dashboard.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(dashboard.router)
    return app


app = create_app()
```

- [ ] **Step 3: Write dashboard API tests**

Create `tests/test_dashboard_api.py`:

```python
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.routers import dashboard as dashboard_router


def test_dashboard_context_endpoint(monkeypatch):
    monkeypatch.setattr(
        dashboard_router,
        "build_dashboard_context",
        lambda: {
            "hasData": True,
            "tourRows": 10,
            "othersRows": 5,
            "maxDate": "2026-06-15",
            "minDate": "2026-06-01",
            "years": [2026],
            "months": ["2026-06"],
            "branches": ["銅鑼灣分社"],
            "salesGroups": ["YTLAU 刘元太"],
            "revenueScope": "不含掛賬核銷與TT退款轉團款",
        },
    )
    client = TestClient(create_app())

    response = client.get("/api/dashboard/context")

    assert response.status_code == 200
    payload = response.json()
    assert payload["hasData"] is True
    assert payload["maxDate"] == "2026-06-15"


def test_dashboard_summary_endpoint(monkeypatch):
    monkeypatch.setattr(
        dashboard_router,
        "build_dashboard_summary",
        lambda filters: {
            "appliedFilters": filters,
            "revenueScope": "不含掛賬核銷與TT退款轉團款",
            "scopeAudit": {"analysis_rows": 15},
            "kpis": [
                {
                    "label": "淨營收",
                    "value": "HKD 1,300",
                    "delta": "目前視角：全部分社 / 全部銷售組",
                    "note": "不含掛賬核銷與TT退款轉團款；含旅行團、郵輪與票務",
                    "accent": "#118DFF",
                }
            ],
            "branchRanking": [],
            "productMix": [],
            "exportReadiness": {"lazyExport": True, "status": "not_loaded"},
        },
    )
    client = TestClient(create_app())

    response = client.post(
        "/api/dashboard/summary",
        json={
            "years": [2026],
            "months": ["2026-06"],
            "dateRange": ["2026-06-01", "2026-06-30"],
            "branch": "全部分社",
            "salesGroup": "全部銷售組",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["appliedFilters"]["years"] == [2026]
    assert payload["exportReadiness"]["lazyExport"] is True
```

- [ ] **Step 4: Run dashboard API tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_dashboard_api.py -v
```

Expected:

```text
tests/test_dashboard_api.py::test_dashboard_context_endpoint PASSED
tests/test_dashboard_api.py::test_dashboard_summary_endpoint PASSED
```

---

## Task 6: Run Full Phase 1 Verification

**Files:**

- Verify: all files created or modified in Phase 1

- [ ] **Step 1: Compile existing production modules and new backend modules**

Run:

```bash
.venv/bin/python -m py_compile app.py forecasting.py pipeline.py database.py business_calendar.py visuals.py scripts/validate_business_calendar.py scripts/inspect_sqlite_latest.py scripts/prewarm_ai_cache.py backend/main.py backend/routers/health.py backend/routers/dashboard.py backend/services/dashboard_service.py backend/schemas/dashboard.py
```

Expected: no output and exit code `0`.

- [ ] **Step 2: Run existing business calendar validation**

Run:

```bash
.venv/bin/python scripts/validate_business_calendar.py
```

Expected: existing validation passes without changing `data/business_calendar_events.json`.

- [ ] **Step 3: Inspect SQLite latest state**

Run:

```bash
.venv/bin/python scripts/inspect_sqlite_latest.py
```

Expected: read-only SQLite status output. No backup file should be created.

- [ ] **Step 4: Check AI cache status**

Run:

```bash
.venv/bin/python scripts/prewarm_ai_cache.py --status
```

Expected: cache status output only. No forced rebuild should be triggered.

- [ ] **Step 5: Run all new API tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_backend_health.py tests/test_dashboard_service.py tests/test_dashboard_api.py -v
```

Expected:

```text
6 passed
```

- [ ] **Step 6: Start API server for smoke test**

Run:

```bash
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8601
```

Expected:

```text
Uvicorn running on http://127.0.0.1:8601
```

In another terminal, run:

```bash
curl -s http://127.0.0.1:8601/api/health
curl -s http://127.0.0.1:8601/api/dashboard/context
curl -s -X POST http://127.0.0.1:8601/api/dashboard/summary \
  -H 'Content-Type: application/json' \
  -d '{"years":[2026],"months":["2026-06"],"dateRange":["2026-06-01","2026-06-30"],"branch":"全部分社","salesGroup":"全部銷售組"}'
```

Expected:

- health returns `"status":"ok"`
- context returns `hasData`, `years`, `months`, `branches`, `salesGroups`
- summary returns `kpis`, `branchRanking`, `productMix`, `exportReadiness`

- [ ] **Step 7: Verify Streamlit baseline still starts**

Run:

```bash
.venv/bin/python scripts/system_manager.py start --no-browser
.venv/bin/python scripts/system_manager.py acceptance
```

Open:

```text
http://127.0.0.1:8502/
http://127.0.0.1:8502/_stcore/health
http://127.0.0.1:5173/
http://127.0.0.1:8601/docs
```

Expected:

- Streamlit cockpit still loads.
- Sidebar Navigation and Control Center remain available.
- No change to existing tabs.
- Clicking Navigation remains hash-anchor behavior.

---

## Task 7: Checkpoint Summary

**Files:**

- Review: all Phase 1 changes

- [ ] **Step 1: List changed files**

Run:

```bash
find backend tests docs/superpowers/plans -type f | sort
```

Expected includes:

```text
backend/__init__.py
backend/main.py
backend/routers/__init__.py
backend/routers/dashboard.py
backend/routers/health.py
backend/schemas/__init__.py
backend/schemas/dashboard.py
backend/services/__init__.py
backend/services/dashboard_service.py
docs/superpowers/plans/2026-06-20-phase-1-read-only-api-baseline.md
tests/test_backend_health.py
tests/test_dashboard_api.py
tests/test_dashboard_service.py
```

- [ ] **Step 2: Confirm no forbidden files changed**

Run:

```bash
ls -lt nbs_marketing_data.db .nbs_runtime_cache 2>/dev/null | head
```

Expected:

- SQLite database timestamp should not change because Phase 1 is read-only.
- `.nbs_runtime_cache` should not receive new export files from Phase 1 API tests.

- [ ] **Step 3: Document final evidence**

Final handoff should report:

- exact files created or modified
- `pytest` result
- `py_compile` result
- `/api/health` HTTP smoke result
- `/api/dashboard/context` HTTP smoke result
- `/api/dashboard/summary` HTTP smoke result
- Streamlit baseline health URL result

---

## Self-review Notes

Spec coverage:

- Approach A is implemented as read-only API baseline only.
- Vue is intentionally not scaffolded in this phase.
- Upload, export, forecast, GMV and rules write flows remain outside Phase 1.

Ambiguity resolved:

- The backend must not import `app.py`; importing `app.py` would execute Streamlit `main()` because the current file ends with a top-level `try: main()`.
- The official revenue scope is reimplemented in a small backend service because the existing equivalent lives inside `app.py`. This must be tested against sample frames and later compared with Streamlit baseline numbers.

Residual risk:

- `config.py` imports Streamlit for existing rules/session behavior. Phase 1 can tolerate this because Streamlit is already a dependency, but future backend hardening should split pure constants/rules IO into a Streamlit-free module.
