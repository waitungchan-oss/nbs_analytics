from __future__ import annotations

from fastapi import APIRouter

from config import DB_FILE
from backend.services.system_health_service import build_system_health

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health_check() -> dict:
    from pathlib import Path

    return build_system_health(
        db_path=Path(DB_FILE),
        cache_path=Path(".nbs_runtime_cache"),
        runtime_dir=Path(".nbs_runtime"),
    )
