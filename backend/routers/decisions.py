from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from config import DB_FILE
from backend.schemas.decisions import DecisionOverviewResponse
from backend.services.cache_generation_service import load_cache_generation
from backend.services.dashboard_facts_service import build_dashboard_facts_read_model
from backend.services.dashboard_service import _current_rules
from backend.services.data_quality_service import build_data_quality
from backend.services.decision_service import build_decision_overview, load_decision_targets
from backend.services.forecast_read_service import build_forecast_read_model
from backend.services.system_health_service import build_system_health


router = APIRouter(prefix="/api/decisions", tags=["decisions"])


@router.get("/overview", response_model=DecisionOverviewResponse)
def decision_overview() -> dict:
    db_path = Path(DB_FILE)
    generation = load_cache_generation(db_path=db_path)
    generation_token = str(generation.get("cacheToken") or "0:missing")
    branch_mapping, target_branches, cruise_depts, sales_reps = _current_rules()
    facts = build_dashboard_facts_read_model(
        db_path=db_path,
        generation_token=generation_token,
        branch_mapping=branch_mapping,
        target_branches_s3=target_branches,
        cruise_depts=cruise_depts,
        sales_rep_list=sales_reps,
    )
    return build_decision_overview(
        facts=facts,
        forecast=build_forecast_read_model(),
        quality=build_data_quality(),
        health=build_system_health(
            db_path=db_path,
            cache_path=Path(".nbs_runtime_cache"),
            runtime_dir=Path(".nbs_runtime"),
        ),
        target_config=load_decision_targets(),
    )
