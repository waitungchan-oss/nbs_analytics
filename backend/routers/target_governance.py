from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.schemas.target_governance import TargetConfigRequest, TargetConfigResponse
from backend.services.target_governance_service import (
    TargetConfigValidationError,
    load_target_config,
    load_target_history,
    save_target_config,
    validate_target_config,
)


router = APIRouter(prefix="/api/decisions/targets", tags=["decision-targets"])


def _response(config: dict) -> dict:
    return {"config": config, "history": load_target_history()}


@router.get("", response_model=TargetConfigResponse)
def get_target_config() -> dict:
    return _response(load_target_config())


@router.put("", response_model=TargetConfigResponse)
def put_target_config(payload: TargetConfigRequest) -> dict:
    try:
        raw_payload = payload.model_dump()
        validate_target_config(raw_payload)
        config = save_target_config(raw_payload)
    except TargetConfigValidationError as exc:
        raise HTTPException(status_code=422, detail={"status": "invalid", "message": str(exc)}) from exc
    return _response(config)
