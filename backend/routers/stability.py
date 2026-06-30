from __future__ import annotations

from fastapi import APIRouter, Query

from backend.schemas.dashboard import StabilityHistoryResponse
from backend.services.stability_history_service import list_stability_history

router = APIRouter(prefix="/api/stability", tags=["stability"])


@router.get("/history", response_model=StabilityHistoryResponse)
def stability_history(limit: int = Query(default=20, ge=1, le=100)) -> dict:
    items = list_stability_history(limit=limit)
    return {"items": items, "count": len(items)}
