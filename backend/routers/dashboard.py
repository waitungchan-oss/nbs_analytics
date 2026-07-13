from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from config import DB_FILE
from backend.schemas.dashboard import (
    DashboardAnalyticsResponse,
    DashboardContextResponse,
    DashboardFactsResponse,
    DashboardFilters,
    DashboardSummaryResponse,
)
from backend.services.dashboard_analytics_service import build_dashboard_analytics
from backend.services.dashboard_facts_service import build_dashboard_facts_read_model
from backend.services.dashboard_service import _current_rules, build_dashboard_context, build_dashboard_summary
from backend.services.cache_generation_service import load_cache_generation

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/context", response_model=DashboardContextResponse)
def dashboard_context() -> dict:
    return build_dashboard_context()


@router.get("/facts", response_model=DashboardFactsResponse)
def dashboard_facts() -> dict:
    db_path = Path(DB_FILE)
    generation = load_cache_generation(db_path=db_path)
    generation_token = str(generation.get("cacheToken") or "0:missing")
    branch_mapping, target_branches, cruise_depts, sales_reps = _current_rules()
    return build_dashboard_facts_read_model(
        db_path=db_path,
        generation_token=generation_token,
        branch_mapping=branch_mapping,
        target_branches_s3=target_branches,
        cruise_depts=cruise_depts,
        sales_rep_list=sales_reps,
    )


@router.post("/summary", response_model=DashboardSummaryResponse)
def dashboard_summary(filters: DashboardFilters) -> dict:
    return build_dashboard_summary(filters.model_dump())


@router.post("/analytics", response_model=DashboardAnalyticsResponse)
def dashboard_analytics(filters: DashboardFilters) -> dict:
    return build_dashboard_analytics(filters.model_dump())
