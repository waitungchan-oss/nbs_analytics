from __future__ import annotations

import json

import database
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from backend.schemas.dashboard import UploadActionResponse
from backend.schemas.receipt_exclusions import ReceiptExclusionRevocationRequest
from backend.services.receipt_exclusion_governance_service import (
    confirm_receipt_exclusion_revocation,
    preview_receipt_exclusion_revocation,
)
from backend.services.receipt_exclusion_read_model_service import build_receipt_exclusion_read_model
from backend.services.upload_action_service import run_vue_upload_action
from backend.services.upload_lock_service import UploadBusyError, acquire_upload_lease

router = APIRouter(prefix="/api/upload", tags=["upload"])


@router.post("", response_model=UploadActionResponse)
async def upload_monthly_data(
    main_file: UploadFile = File(...),
    tour_file: UploadFile | None = File(default=None),
    other_files: list[UploadFile] | None = File(default=None),
) -> dict:
    try:
        return await run_vue_upload_action(
            main_file=main_file,
            tour_file=tour_file,
            other_files=other_files or [],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UploadBusyError as exc:
        raise HTTPException(status_code=409, detail={"status": "busy", "owner": exc.owner}) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@router.get("/receipt-exclusions")
def list_receipt_exclusions() -> dict:
    return build_receipt_exclusion_read_model(db_path=database.DB_FILE)


@router.post("/receipt-exclusions/confirm", response_model=UploadActionResponse)
async def confirm_receipt_exclusions(
    main_file: UploadFile = File(...),
    proposal_fingerprint: str = Form(...),
    selected_candidate_ids: str = Form(...),
    tour_file: UploadFile | None = File(default=None),
    other_files: list[UploadFile] | None = File(default=None),
) -> dict:
    try:
        selected = json.loads(selected_candidate_ids)
        if not isinstance(selected, list) or not selected or not all(isinstance(item, str) and item for item in selected):
            raise ValueError("selected_candidate_ids must be a non-empty JSON string list")
        return await run_vue_upload_action(
            main_file=main_file,
            tour_file=tour_file,
            other_files=other_files or [],
            receipt_exclusion_confirmation={
                "proposalFingerprint": proposal_fingerprint,
                "selectedCandidateIds": selected,
                "confirmedBy": "vue-local",
            },
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UploadBusyError as exc:
        raise HTTPException(status_code=409, detail={"status": "busy", "owner": exc.owner}) from exc


@router.post("/receipt-exclusions/{rule_id}/revocation-preview")
def preview_receipt_exclusion_revocation_route(rule_id: int) -> dict:
    try:
        with acquire_upload_lease(entry_point="receipt_exclusion_revocation", source_files=[]) as lease:
            return preview_receipt_exclusion_revocation(
                rule_id, operation=lease.operation, live_db_path=database.DB_FILE,
            )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UploadBusyError as exc:
        raise HTTPException(status_code=409, detail={"status": "busy", "owner": exc.owner}) from exc


@router.post("/receipt-exclusions/{rule_id}/revoke")
def revoke_receipt_exclusion_route(rule_id: int, request: ReceiptExclusionRevocationRequest) -> dict:
    try:
        with acquire_upload_lease(entry_point="receipt_exclusion_revocation", source_files=[]) as lease:
            return confirm_receipt_exclusion_revocation(
                rule_id,
                operation=lease.operation,
                submitted_preview_fingerprint=request.previewFingerprint,
                revoked_by=request.confirmedBy,
                live_db_path=database.DB_FILE,
            )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UploadBusyError as exc:
        raise HTTPException(status_code=409, detail={"status": "busy", "owner": exc.owner}) from exc
