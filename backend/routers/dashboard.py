from __future__ import annotations

from fastapi import APIRouter

from backend.schemas.dashboard import (
    DashboardAnalyticsResponse,
    DashboardContextResponse,
    DashboardFilters,
    DashboardSummaryResponse,
)
from backend.services.dashboard_analytics_service import build_dashboard_analytics
from backend.services.dashboard_service import build_dashboard_context, build_dashboard_summary

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/context", response_model=DashboardContextResponse)
def dashboard_context() -> dict:
    return build_dashboard_context()


@router.post("/summary", response_model=DashboardSummaryResponse)
def dashboard_summary(filters: DashboardFilters) -> dict:
    return build_dashboard_summary(filters.model_dump())


@router.post("/analytics", response_model=DashboardAnalyticsResponse)
def dashboard_analytics(filters: DashboardFilters) -> dict:
    return build_dashboard_analytics(filters.model_dump())
