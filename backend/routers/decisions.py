from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from config import CONFIG_FILE, DB_FILE
from backend.schemas.decisions import DecisionOverviewResponse
from backend.services.application_snapshot_service import (
    ApplicationSnapshotService,
    SnapshotGenerationConflict,
    SnapshotPaths,
)
from backend.services.decision_service import (
    DEFAULT_TARGET_CONFIG_PATH,
    build_decision_overview,
)


router = APIRouter(prefix="/api/decisions", tags=["decisions"])
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATHS = SnapshotPaths(
    db_path=Path(DB_FILE),
    cache_dir=PROJECT_ROOT / ".nbs_runtime_cache",
    runtime_dir=PROJECT_ROOT / ".nbs_runtime",
    rules_config_path=Path(CONFIG_FILE),
    target_config_path=Path(DEFAULT_TARGET_CONFIG_PATH),
)


@router.get("/overview", response_model=DecisionOverviewResponse)
def decision_overview() -> dict:
    try:
        snapshot = ApplicationSnapshotService(SNAPSHOT_PATHS).build()
    except SnapshotGenerationConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="Data generation changed while building the decision overview; retry the request.",
        ) from exc
    return build_decision_overview(
        facts=snapshot.facts,
        forecast=snapshot.forecast,
        quality=snapshot.quality,
        health=snapshot.health,
        target_config=snapshot.targets,
        snapshot_provenance=snapshot.provenance,
    )
