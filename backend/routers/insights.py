from fastapi import APIRouter

from backend.schemas.insights import DataQualityResponse, ForecastResponse
from backend.services.data_quality_service import build_data_quality
from backend.services.forecast_read_service import build_forecast_read_model

router = APIRouter(prefix="/api/insights", tags=["insights"])


@router.get("/data-quality", response_model=DataQualityResponse)
def data_quality() -> dict:
    return build_data_quality()


@router.get("/forecast", response_model=ForecastResponse)
def forecast() -> dict:
    return build_forecast_read_model()
