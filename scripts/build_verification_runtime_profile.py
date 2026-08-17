from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Mapping

from backend.agents.evidence_models import canonical_fingerprint
from backend.services.verification_runtime_profile import VERIFICATION_PROFILE_SCHEMA, VerificationRuntimeProfile
from backend.services.verification_runtime_snapshot import build_read_only_snapshot


class VerificationRuntimeProfileBuildError(ValueError):
    """Raised when a disposable verification profile cannot be built."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _worktree_fingerprint(project_root: Path, git_head: str) -> str:
    try:
        status = subprocess.run(
            ["git", "-C", str(project_root), "status", "--porcelain"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise VerificationRuntimeProfileBuildError("project root is not a readable Git worktree") from exc
    return canonical_fingerprint({"head": git_head, "status": status})


def _cache_inventory(project_root: Path) -> dict[str, object]:
    cache_root = project_root / ".nbs_runtime_cache"
    entries: list[dict[str, object]] = []
    if cache_root.exists():
        if cache_root.is_symlink():
            raise VerificationRuntimeProfileBuildError("runtime cache root cannot be symlinked")
        for path in sorted(cache_root.rglob("*")):
            if path.is_symlink():
                raise VerificationRuntimeProfileBuildError("runtime cache entry cannot be symlinked")
            if path.is_file():
                entries.append({"path": path.relative_to(cache_root).as_posix(), "bytes": path.stat().st_size, "sha256": _sha256(path)})
    return {
        "fileCount": len(entries),
        "totalBytes": sum(int(item["bytes"]) for item in entries),
        "fingerprint": canonical_fingerprint(entries),
    }


def _bounded_generation(path: Path) -> dict[str, object]:
    if path.stat().st_size > 16 * 1024:
        raise VerificationRuntimeProfileBuildError("runtime generation metadata exceeds bounded size")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationRuntimeProfileBuildError("runtime generation metadata is invalid") from exc
    if not isinstance(value, dict) or set(value) != {"generation", "operationId", "status", "updatedAt", "dbSignature"}:
        raise VerificationRuntimeProfileBuildError("runtime generation metadata keys are invalid")
    if not isinstance(value["generation"], int) or isinstance(value["generation"], bool) or value["generation"] < 0:
        raise VerificationRuntimeProfileBuildError("runtime generation value is invalid")
    if value["operationId"] is not None and (not isinstance(value["operationId"], str) or len(value["operationId"]) > 128):
        raise VerificationRuntimeProfileBuildError("runtime operationId is invalid")
    if not isinstance(value["status"], str) or not value["status"] or len(value["status"]) > 64:
        raise VerificationRuntimeProfileBuildError("runtime status is invalid")
    if not isinstance(value["updatedAt"], str) or len(value["updatedAt"]) > 64:
        raise VerificationRuntimeProfileBuildError("runtime updatedAt is invalid")
    signature = value["dbSignature"]
    if not isinstance(signature, dict) or set(signature) != {"sizeBytes", "modifiedNs", "sha256"}:
        raise VerificationRuntimeProfileBuildError("runtime dbSignature is invalid")
    if not all(isinstance(signature[key], int) and not isinstance(signature[key], bool) and signature[key] >= 0 for key in ("sizeBytes", "modifiedNs")):
        raise VerificationRuntimeProfileBuildError("runtime dbSignature numbers are invalid")
    if not isinstance(signature["sha256"], str) or len(signature["sha256"]) != 64:
        raise VerificationRuntimeProfileBuildError("runtime dbSignature hash is invalid")
    return value


def build_verification_profile(
    *, project_root: Path, source_db: Path, source_runtime: Path, output_root: Path,
    git_head: str, ports: Mapping[str, int],
) -> Path:
    project = Path(project_root)
    source = Path(source_db)
    runtime = Path(source_runtime)
    if not source.is_file() or source.is_symlink():
        raise VerificationRuntimeProfileBuildError("source database is missing or symlinked")
    generation = runtime / "data_generation.json"
    if not generation.is_file() or generation.is_symlink():
        raise VerificationRuntimeProfileBuildError("runtime generation metadata is missing or symlinked")
    registry = project / "data" / "monthly_revenue_baselines.json"
    if not registry.is_file() or registry.is_symlink():
        raise VerificationRuntimeProfileBuildError("monthly baseline registry is missing or symlinked")
    if set(ports) != {"api", "streamlit", "vue"} or any(not isinstance(v, int) or isinstance(v, bool) or not 1024 <= v <= 65535 for v in ports.values()) or len(set(ports.values())) != 3:
        raise VerificationRuntimeProfileBuildError("profile ports must be allocated positive integers")
    allowed_root = (project / ".nbs_agent_runtime" / "verification").resolve()
    requested_root = Path(output_root)
    if requested_root.is_symlink() or ".." in requested_root.parts:
        raise VerificationRuntimeProfileBuildError("output root must stay under verification runtime")
    try:
        requested_root.resolve(strict=False).relative_to(allowed_root)
    except ValueError as exc:
        raise VerificationRuntimeProfileBuildError("output root must stay under verification runtime") from exc
    if requested_root.resolve(strict=False) != allowed_root:
        raise VerificationRuntimeProfileBuildError("output root must be the verification runtime root")
    profile_id = f"profile-{git_head[:12]}"
    profile_dir = Path(output_root) / profile_id
    if profile_dir.exists():
        raise VerificationRuntimeProfileBuildError("profile output already exists")
    try:
        snapshot = build_read_only_snapshot(source, profile_dir / "snapshot.sqlite")
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "cache").mkdir()
        generation_payload = _bounded_generation(generation)
        (profile_dir / "generation.json").write_text(json.dumps(generation_payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        baseline_fingerprint = _sha256(registry)
        unsigned = {
            "schemaVersion": VERIFICATION_PROFILE_SCHEMA,
            "profileId": profile_id,
            "projectId": project.name,
            "gitHead": git_head,
            "worktreeFingerprint": _worktree_fingerprint(project, git_head),
            "database": {
                "snapshotRef": f"verification/{profile_id}/snapshot.sqlite",
                "sourceFingerprint": snapshot.source_fingerprint,
                "snapshotFingerprint": snapshot.snapshot_fingerprint,
                "readOnly": True,
            },
            "baseline": {"registryFingerprint": baseline_fingerprint, "requiredMay2026Total": "HKD 12,057,968"},
            "runtime": {"generationRef": f"verification/{profile_id}/generation.json", "cacheInventory": _cache_inventory(project)},
            "services": {"profileNamespace": profile_id, "ports": dict(ports)},
            "createdAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        payload = {**unsigned, "profileFingerprint": canonical_fingerprint(unsigned)}
        VerificationRuntimeProfile.from_dict(
            payload,
            expected_git_head=git_head,
            expected_worktree_fingerprint=unsigned["worktreeFingerprint"],
        )
        profile_path = profile_dir / "profile.json"
        profile_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return profile_path
    except Exception as exc:
        import shutil
        shutil.rmtree(profile_dir, ignore_errors=True)
        if isinstance(exc, VerificationRuntimeProfileBuildError):
            raise
        raise VerificationRuntimeProfileBuildError(str(exc)) from exc


if __name__ == "__main__":
    raise SystemExit("use build_verification_profile from the controlled caller")
