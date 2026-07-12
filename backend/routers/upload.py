from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from backend.schemas.dashboard import UploadActionResponse
from backend.services.upload_action_service import run_vue_upload_action
from backend.services.upload_lock_service import UploadBusyError

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
