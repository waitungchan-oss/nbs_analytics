from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Mapping

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.repository_scope import verification_source_git_pathspecs
from backend.services.cache_generation_service import GENERATION_SCHEMA_VERSION, GENERATION_SIGNATURE_SCOPE
from backend.services.gmv_refund_models import canonical_payload_sha256
from backend.services.revenue_generation_service import (
    CORE_REVENUE_SIGNATURE_SCHEMA,
    CORE_REVENUE_SOURCE_TABLES,
    CORE_REVENUE_TOKEN_PREFIX,
    REVENUE_SCOPE_CONTRACT_VERSION,
)
from backend.services.revenue_scope_service import REVENUE_SCOPE_LABEL
from backend.services.verification_profile_acceptance import (
    NBS_PROJECT_ID,
    SourceBindingInspectionError,
    inspect_primary_source,
)
from backend.services.verification_runtime_profile import (
    VERIFICATION_PROFILE_SCHEMA_V2,
    ProfileSourceBinding,
    VerificationRuntimeProfile,
)
from backend.services.verification_runtime_snapshot import build_read_only_snapshot


class VerificationRuntimeProfileBuildError(ValueError):
    """Raised when a disposable verification profile cannot be built."""


def _validate_core_revenue_signature(value: object) -> None:
    keys = {
        "schemaVersion", "scopeLabel", "scopeContractVersion", "sourceTables",
        "rowCounts", "rawTour", "rawOthers", "formalTour", "formalOthers",
        "sha256", "token",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise VerificationRuntimeProfileBuildError("runtime core revenue signature keys are invalid")
    if (
        value["schemaVersion"] != CORE_REVENUE_SIGNATURE_SCHEMA
        or value["scopeLabel"] != REVENUE_SCOPE_LABEL
        or value["scopeContractVersion"] != REVENUE_SCOPE_CONTRACT_VERSION
        or value["sourceTables"] != list(CORE_REVENUE_SOURCE_TABLES)
    ):
        raise VerificationRuntimeProfileBuildError("runtime core revenue signature identity is invalid")
    row_counts = value["rowCounts"]
    if (
        not isinstance(row_counts, dict)
        or set(row_counts) != set(CORE_REVENUE_SOURCE_TABLES)
        or any(not isinstance(count, int) or isinstance(count, bool) or count < 0 for count in row_counts.values())
    ):
        raise VerificationRuntimeProfileBuildError("runtime core revenue signature row counts are invalid")
    digest_fields = ("rawTour", "rawOthers", "formalTour", "formalOthers")
    if any(
        not isinstance(value[field], str)
        or len(value[field]) != 64
        or any(character not in "0123456789abcdef" for character in value[field])
        for field in digest_fields
    ):
        raise VerificationRuntimeProfileBuildError("runtime core revenue signature hashes are invalid")
    unsigned = {key: value[key] for key in keys - {"sha256", "token"}}
    digest = canonical_payload_sha256(unsigned)
    if value["sha256"] != digest or value["token"] != f"{CORE_REVENUE_TOKEN_PREFIX}:{digest}":
        raise VerificationRuntimeProfileBuildError("runtime core revenue signature digest is invalid")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _worktree_fingerprint(project_root: Path, git_head: str) -> str:
    try:
        status = subprocess.run(
            [
                "git", "-C", str(project_root), "status", "--porcelain=v1",
                "--untracked-files=all", "--no-renames", "-z", "--",
                *verification_source_git_pathspecs(),
            ],
            capture_output=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise VerificationRuntimeProfileBuildError("project root is not a readable Git worktree") from exc
    return canonical_fingerprint({
        "head": git_head,
        "statusFingerprint": hashlib.sha256(status).hexdigest(),
    })


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


def _validate_generation_payload(value: object) -> dict[str, object]:
    legacy_keys = {"generation", "operationId", "status", "updatedAt", "dbSignature"}
    v2_keys = legacy_keys | {"schemaVersion", "signatureScope", "coreRevenueSignature"}
    if not isinstance(value, dict):
        raise VerificationRuntimeProfileBuildError("runtime generation metadata keys are invalid")
    provided_keys = frozenset(value)
    if provided_keys not in {frozenset(legacy_keys), frozenset(v2_keys)}:
        raise VerificationRuntimeProfileBuildError("runtime generation metadata keys are invalid")
    if provided_keys == frozenset(v2_keys):
        if value["schemaVersion"] != GENERATION_SCHEMA_VERSION or value["signatureScope"] != GENERATION_SIGNATURE_SCOPE:
            raise VerificationRuntimeProfileBuildError("runtime generation metadata schema is invalid")
        _validate_core_revenue_signature(value["coreRevenueSignature"])
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
    if not isinstance(signature["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", signature["sha256"]) is None:
        raise VerificationRuntimeProfileBuildError("runtime dbSignature hash is invalid")
    return value


def _resolve_source_root(
    *,
    source_root: Path | None,
    source_db: Path | None,
    source_runtime: Path | None,
) -> Path:
    if source_root is not None:
        if source_db is not None or source_runtime is not None:
            raise VerificationRuntimeProfileBuildError(
                "source_root cannot be combined with legacy source paths"
            )
        return Path(source_root)
    if source_db is None or source_runtime is None:
        raise VerificationRuntimeProfileBuildError(
            "source_root or both legacy source paths are required"
        )

    database = Path(source_db)
    runtime = Path(source_runtime)
    if database.name != "nbs_marketing_data.db" or runtime.name != ".nbs_runtime":
        raise VerificationRuntimeProfileBuildError("legacy source paths are invalid")
    database_root = Path(os.path.abspath(database.parent))
    runtime_root = Path(os.path.abspath(runtime.parent))
    if database_root != runtime_root:
        raise VerificationRuntimeProfileBuildError(
            "legacy source paths must belong to the same checkout"
        )
    return database_root


def build_verification_profile(
    *, project_root: Path, output_root: Path,
    git_head: str, ports: Mapping[str, int],
    source_root: Path | None = None, source_db: Path | None = None,
    source_runtime: Path | None = None,
) -> Path:
    project = Path(project_root)
    resolved_source_root = _resolve_source_root(
        source_root=source_root,
        source_db=source_db,
        source_runtime=source_runtime,
    )
    try:
        source_before = inspect_primary_source(
            project, resolved_source_root, expected_project_id=NBS_PROJECT_ID,
        )
    except SourceBindingInspectionError as exc:
        raise VerificationRuntimeProfileBuildError(exc.reason_code) from None
    generation_payload = _validate_generation_payload(dict(source_before.generation_payload))
    try:
        actual_head = subprocess.run(
            ["git", "-C", str(project), "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        raise VerificationRuntimeProfileBuildError("project root is not a readable Git worktree") from None
    if actual_head != git_head:
        raise VerificationRuntimeProfileBuildError("git_head does not match the code checkout")
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
    staging_dir = None
    try:
        Path(output_root).mkdir(parents=True, exist_ok=True)
        staging_dir = Path(tempfile.mkdtemp(prefix=f".{profile_id}.staging-", dir=output_root))
        snapshot = build_read_only_snapshot(source_before.database_path, staging_dir / "snapshot.sqlite")
        if snapshot.source_fingerprint != source_before.database_fingerprint:
            raise VerificationRuntimeProfileBuildError("source database changed during snapshot")
        (staging_dir / "cache").mkdir()
        (staging_dir / "generation.json").write_text(json.dumps(generation_payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        source_after = inspect_primary_source(
            project, resolved_source_root, expected_project_id=NBS_PROJECT_ID,
        )
        if source_after != source_before:
            raise VerificationRuntimeProfileBuildError("source changed during profile build")
        source_binding = ProfileSourceBinding(
            source_role="primary-checkout",
            repository_fingerprint=source_before.repository_fingerprint,
            source_head=source_before.source_head,
            database_fingerprint=source_before.database_fingerprint,
            generation_fingerprint=source_before.generation_fingerprint,
            baseline_registry_fingerprint=source_before.baseline_registry_fingerprint,
        )
        unsigned = {
            "schemaVersion": VERIFICATION_PROFILE_SCHEMA_V2,
            "profileId": profile_id,
            "projectId": NBS_PROJECT_ID,
            "gitHead": git_head,
            "worktreeFingerprint": _worktree_fingerprint(project, git_head),
            "sourceBinding": source_binding.to_dict(),
            "database": {
                "snapshotRef": f"verification/{profile_id}/snapshot.sqlite",
                "sourceFingerprint": snapshot.source_fingerprint,
                "snapshotFingerprint": snapshot.snapshot_fingerprint,
                "readOnly": True,
            },
            "baseline": {"registryFingerprint": source_before.baseline_registry_fingerprint, "requiredMay2026Total": "HKD 12,057,968"},
            "runtime": {
                "runtimeDir": f"verification/{profile_id}",
                "generationRef": f"verification/{profile_id}/generation.json",
                "generationFingerprint": canonical_fingerprint(generation_payload),
                "cacheInventory": _cache_inventory(project),
            },
            "services": {"profileNamespace": profile_id, "ports": dict(ports)},
            "createdAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        payload = {**unsigned, "profileFingerprint": canonical_fingerprint(unsigned)}
        VerificationRuntimeProfile.from_dict(
            payload,
            expected_git_head=git_head,
            expected_worktree_fingerprint=unsigned["worktreeFingerprint"],
        )
        profile_path = staging_dir / "profile.json"
        try:
            final_source = inspect_primary_source(
                project, resolved_source_root, expected_project_id=NBS_PROJECT_ID,
            )
        except SourceBindingInspectionError as exc:
            raise VerificationRuntimeProfileBuildError(exc.reason_code) from None
        if final_source != source_before:
            raise VerificationRuntimeProfileBuildError("source changed during profile build")
        profile_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        try:
            published_source = inspect_primary_source(
                project, resolved_source_root, expected_project_id=NBS_PROJECT_ID,
            )
        except SourceBindingInspectionError as exc:
            raise VerificationRuntimeProfileBuildError(exc.reason_code) from None
        if published_source != source_before:
            raise VerificationRuntimeProfileBuildError("source changed during profile build")
        if profile_dir.exists():
            raise VerificationRuntimeProfileBuildError("profile output already exists")
        os.rename(staging_dir, profile_dir)
        staging_dir = None
        return profile_dir / "profile.json"
    except Exception as exc:
        import shutil
        if staging_dir is not None:
            shutil.rmtree(staging_dir, ignore_errors=True)
        if isinstance(exc, VerificationRuntimeProfileBuildError):
            raise
        raise VerificationRuntimeProfileBuildError(f"profile build failed: {type(exc).__name__}") from None


if __name__ == "__main__":
    raise SystemExit("use build_verification_profile from the controlled caller")
