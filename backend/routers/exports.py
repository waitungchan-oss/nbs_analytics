from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from backend.schemas.dashboard import DashboardFilters
from backend.services.report_export_service import (
    build_dashboard_report_workbook,
    build_forecast_report_workbook,
    build_quality_report_workbook,
)

router = APIRouter(prefix="/api/exports", tags=["exports"])


def _workbook_response(content: bytes, filename: str) -> StreamingResponse:
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/dashboard.xlsx")
def export_dashboard_report(filters: DashboardFilters) -> StreamingResponse:
    content = build_dashboard_report_workbook(filters.model_dump())
    return _workbook_response(content, "nbs_dashboard_report.xlsx")


@router.get("/quality.xlsx")
def export_quality_report() -> StreamingResponse:
    content = build_quality_report_workbook()
    return _workbook_response(content, "nbs_data_quality_scorecard.xlsx")


@router.get("/forecast.xlsx")
def export_forecast_report() -> StreamingResponse:
    content = build_forecast_report_workbook()
    return _workbook_response(content, "nbs_forecast_report.xlsx")

