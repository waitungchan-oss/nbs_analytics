from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.services.verification_runtime_profile import VerificationRuntimeProfile, VerificationRuntimeProfileError


class VerificationRuntimePathError(ValueError):
    """Raised when a profile cannot be mapped to bounded runtime paths."""


@dataclass(frozen=True)
class VerificationRuntimePaths:
    profile_path: Path
    db_path: Path
    generation_path: Path
    runtime_dir: Path
    cache_path: Path


def _reject_symlink_components(root: Path, target: Path) -> None:
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise VerificationRuntimePathError("verification artifact escaped profile directory") from exc
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise VerificationRuntimePathError("verification artifact path cannot contain symlink")


def resolve_verification_paths(profile: VerificationRuntimeProfile, *, project_root: Path) -> VerificationRuntimePaths:
    project = Path(project_root).resolve()
    profile_dir = project / ".nbs_agent_runtime" / "verification" / profile.profile_id
    profile_path = profile_dir / "profile.json"
    snapshot_ref = Path(profile.database.snapshot_ref)
    generation_ref = Path(profile.runtime.generation_ref)
    runtime_ref = Path(profile.runtime.runtime_dir)
    if (
        snapshot_ref.parts[:2] != ("verification", profile.profile_id)
        or generation_ref.parts[:2] != ("verification", profile.profile_id)
        or runtime_ref.parts[:2] != ("verification", profile.profile_id)
    ):
        raise VerificationRuntimePathError("profile artifact refs do not match profile identity")
    snapshot_relative = snapshot_ref.parts[2:]
    generation_relative = generation_ref.parts[2:]
    if not snapshot_relative or not generation_relative:
        raise VerificationRuntimePathError("profile artifact refs must name files")
    db_path = profile_dir.joinpath(*snapshot_relative)
    generation_path = profile_dir.joinpath(*generation_relative)
    runtime_dir = profile_dir.joinpath(*runtime_ref.parts[2:])
    cache_path = profile_dir / "cache"
    current = profile_dir
    while current != project and current != current.parent:
        if current.is_symlink():
            raise VerificationRuntimePathError("verification profile parent cannot be symlinked")
        current = current.parent
    try:
        for path in (profile_path, db_path, generation_path, runtime_dir, cache_path):
            path.resolve(strict=False).relative_to(profile_dir.resolve())
    except ValueError as exc:
        raise VerificationRuntimePathError("verification profile path escaped profile directory or contains symlink") from exc
    if profile_path.is_symlink() or db_path.is_symlink() or generation_path.is_symlink() or runtime_dir.is_symlink() or cache_path.is_symlink():
        raise VerificationRuntimePathError("verification profile path cannot be symlinked")
    for target in (profile_path, db_path, generation_path, cache_path):
        _reject_symlink_components(profile_dir, target)
    if cache_path.exists():
        for entry in cache_path.rglob("*"):
            if entry.is_symlink():
                raise VerificationRuntimePathError("verification cache cannot contain symlinks")
    if not profile_path.is_file() or not db_path.is_file() or not generation_path.is_file() or not cache_path.is_dir():
        raise VerificationRuntimePathError("verification profile artifacts are missing")
    return VerificationRuntimePaths(profile_path, db_path, generation_path, runtime_dir, cache_path)


def load_verification_runtime_profile(profile_path: Path, *, project_root: Path, expected_git_head: str | None = None) -> tuple[VerificationRuntimeProfile, VerificationRuntimePaths]:
    try:
        profile = VerificationRuntimeProfile.load(profile_path, expected_git_head=expected_git_head)
    except VerificationRuntimeProfileError as exc:
        raise VerificationRuntimePathError(str(exc)) from exc
    paths = resolve_verification_paths(profile, project_root=project_root)
    if paths.profile_path != Path(profile_path).resolve():
        raise VerificationRuntimePathError("profile path does not match profile identity")
    return profile, paths
