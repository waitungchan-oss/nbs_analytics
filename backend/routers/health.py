from __future__ import annotations

from fastapi import APIRouter
from pathlib import Path

from config import DB_FILE
from backend.services.system_health_service import build_system_health
from backend.services.verification_runtime_paths import load_verification_runtime_profile

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health_check(verification_profile: str | None = None) -> dict:
    if verification_profile:
        project_root = Path(__file__).resolve().parents[2]
        profile_path = Path(verification_profile)
        if not profile_path.is_absolute():
            profile_path = project_root / profile_path
        profile, paths = load_verification_runtime_profile(profile_path, project_root=project_root)
        result = build_system_health(
            db_path=paths.db_path,
            cache_path=paths.cache_path,
            runtime_dir=paths.runtime_dir,
            generation_path=paths.generation_path,
            read_only=True,
        )
        result["verificationProfile"] = {"profileId": profile.profile_id, "profilePath": str(paths.profile_path)}
        return result
    return build_system_health(
        db_path=Path(DB_FILE),
        cache_path=Path(".nbs_runtime_cache"),
        runtime_dir=Path(".nbs_runtime"),
    )
