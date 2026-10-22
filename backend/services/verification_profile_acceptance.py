from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Mapping

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.repository_scope import (
    is_excluded_repository_path,
    repository_git_pathspecs,
    verification_source_git_pathspecs,
)
from backend.agents.safe_repo_file import open_repo_file_nofollow
from backend.services.cache_generation_service import GENERATION_SCHEMA_VERSION, GENERATION_SIGNATURE_SCOPE
from backend.services.gmv_refund_models import canonical_payload_sha256
from backend.services.revenue_generation_service import (
    CORE_REVENUE_SIGNATURE_SCHEMA,
    CORE_REVENUE_SOURCE_TABLES,
    CORE_REVENUE_TOKEN_PREFIX,
    REVENUE_SCOPE_CONTRACT_VERSION,
)
from backend.services.revenue_scope_service import REVENUE_SCOPE_LABEL
from backend.services.verification_runtime_paths import VerificationRuntimePaths
from backend.services.verification_runtime_profile import (
    ProfileSourceBinding,
    VerificationRuntimeProfile,
)

VERIFICATION_ACCEPTANCE_SCHEMA = "verification-profile-acceptance-v1"
VERIFICATION_ACCEPTANCE_SCHEMA_V2 = "verification-profile-acceptance-v2"
NBS_PROJECT_ID = "nbs_analytics"
DEFAULT_MAX_PROFILE_AGE = timedelta(hours=24)
_GENERATION_MAX_BYTES = 16 * 1024
_HANDOFF_CONSUMERS = frozenset({"review", "hermes"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class VerificationProfileAcceptanceError(ValueError):
    """Raised when a profile acceptance cannot be formed."""


@dataclass(frozen=True)
class ServiceIdentityEvidence:
    """Bounded evidence that profile services were identified, not just reachable."""

    available: bool
    detail: str = ""


class SourceBindingInspectionError(ValueError):
    """A bounded, path-free source binding validation failure."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class SourceBindingInspection:
    """Current primary-checkout identity and file fingerprints; paths stay local."""

    repository_fingerprint: str
    source_head: str
    database_fingerprint: str
    generation_fingerprint: str
    baseline_registry_fingerprint: str
    database_path: Path
    generation_payload: Mapping[str, object]
    file_state: tuple[object, ...]

    def matches(self, binding: ProfileSourceBinding) -> bool:
        return (
            self.repository_fingerprint == binding.repository_fingerprint
            and self.source_head == binding.source_head
            and self.database_fingerprint == binding.database_fingerprint
            and self.generation_fingerprint == binding.generation_fingerprint
            and self.baseline_registry_fingerprint == binding.baseline_registry_fingerprint
        )


def _has_symlink_component(path: Path) -> bool:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_ctime_ns, value.st_mtime_ns, value.st_size)


def _path_component_state(root: Path, path: Path) -> tuple[tuple[int, int, int, int, int], ...]:
    relative = Path(os.path.abspath(path)).relative_to(Path(os.path.abspath(root)))
    current = Path(root)
    state: list[tuple[int, int, int, int, int]] = []
    for index, part in enumerate(relative.parts):
        current = current / part
        item = current.lstat()
        if stat.S_ISLNK(item.st_mode):
            raise SourceBindingInspectionError("source_binding:source_file_invalid")
        is_leaf = index == len(relative.parts) - 1
        if (is_leaf and not stat.S_ISREG(item.st_mode)) or (not is_leaf and not stat.S_ISDIR(item.st_mode)):
            raise SourceBindingInspectionError("source_binding:source_file_invalid")
        state.append(_stat_identity(item))
    return tuple(state)


def _repo_relative_path(root: Path, path: Path) -> str:
    absolute_root = Path(os.path.abspath(root))
    absolute_path = Path(os.path.abspath(path))
    try:
        return absolute_path.relative_to(absolute_root).as_posix()
    except ValueError:
        raise SourceBindingInspectionError("source_binding:source_file_invalid") from None


def _hash_file_nofollow(root: Path, path: Path) -> tuple[str, tuple[int, int, int, int, int]]:
    relative_path = _repo_relative_path(root, path)
    with open_repo_file_nofollow(root, relative_path) as descriptor:
        before = os.fstat(descriptor)
        before_identity = _stat_identity(before)
        if not stat.S_ISREG(before.st_mode):
            raise SourceBindingInspectionError("source_binding:source_file_invalid")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        after_identity = _stat_identity(after)
        if (
            not stat.S_ISREG(after.st_mode)
            or after_identity != before_identity
        ):
            raise SourceBindingInspectionError("source_binding:source_changed_during_inspection")
        return digest.hexdigest(), after_identity


def _read_generation_nofollow(root: Path, path: Path) -> tuple[dict[str, object], tuple[int, int, int, int, int]]:
    relative_path = _repo_relative_path(root, path)
    with open_repo_file_nofollow(root, relative_path) as descriptor:
        before = os.fstat(descriptor)
        before_identity = _stat_identity(before)
        if not stat.S_ISREG(before.st_mode) or before.st_size > _GENERATION_MAX_BYTES:
            raise SourceBindingInspectionError("source_binding:generation_invalid")
        raw = bytearray()
        while True:
            chunk = os.read(descriptor, min(4096, _GENERATION_MAX_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > _GENERATION_MAX_BYTES:
                raise SourceBindingInspectionError("source_binding:generation_invalid")
        after = os.fstat(descriptor)
        after_identity = _stat_identity(after)
        if after_identity != before_identity:
            raise SourceBindingInspectionError("source_binding:source_changed_during_inspection")
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise SourceBindingInspectionError("source_binding:generation_invalid")
        return value, after_identity


def _repo_identity(root: Path, *, primary: bool) -> tuple[str, str, str]:
    git_marker = root / ".git"
    if _has_symlink_component(root) or git_marker.is_symlink():
        raise SourceBindingInspectionError("source_binding:primary_checkout_invalid")
    if primary and (not git_marker.is_dir() or not stat.S_ISDIR(git_marker.lstat().st_mode)):
        raise SourceBindingInspectionError("source_binding:primary_checkout_invalid")
    if not primary and not (git_marker.is_dir() or git_marker.is_file()):
        raise SourceBindingInspectionError("source_binding:repository_identity_mismatch")
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, check=True,
        )
        common_dir = Path(result.stdout.strip()).resolve(strict=True)
        git_dir_result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--absolute-git-dir"],
            capture_output=True, text=True, check=True,
        )
        git_dir = Path(git_dir_result.stdout.strip()).resolve(strict=True)
        head_result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError, RuntimeError):
        raise SourceBindingInspectionError("source_binding:repository_identity_mismatch") from None
    head = head_result.stdout.strip()
    if re.fullmatch(r"[0-9a-f]{40}", head) is None:
        raise SourceBindingInspectionError("source_binding:source_head_unavailable")

    def _metadata_stat(path: Path, *, directory: bool) -> tuple[int, int, int, int, int, int, int]:
        try:
            item = path.lstat()
        except OSError:
            raise SourceBindingInspectionError("source_binding:repository_identity_mismatch") from None
        if stat.S_ISLNK(item.st_mode) or (directory and not stat.S_ISDIR(item.st_mode)):
            raise SourceBindingInspectionError("source_binding:repository_identity_mismatch")
        if not directory and not stat.S_ISREG(item.st_mode):
            raise SourceBindingInspectionError("source_binding:repository_identity_mismatch")
        return (
            item.st_dev, item.st_ino, item.st_mode, item.st_nlink,
            item.st_size, item.st_mtime_ns, item.st_ctime_ns,
        )

    marker_stat = _metadata_stat(git_marker, directory=git_marker.is_dir())
    git_dir_stat = _metadata_stat(git_dir, directory=True)
    common_dir_stat = _metadata_stat(common_dir, directory=True)
    head_stat = _metadata_stat(git_dir / "HEAD", directory=False)
    metadata_fingerprint = canonical_fingerprint({
        "gitMarker": marker_stat,
        "gitDir": git_dir_stat,
        "gitCommonDir": common_dir_stat,
        "headFile": head_stat,
    })
    return hashlib.sha256(str(common_dir).encode("utf-8")).hexdigest(), head, metadata_fingerprint


def inspect_primary_source(
    project_root: Path,
    source_root: Path,
    *,
    expected_project_id: str,
) -> SourceBindingInspection:
    """Validate one explicit primary checkout and return path-free source identity."""
    if expected_project_id != NBS_PROJECT_ID:
        raise SourceBindingInspectionError("source_binding:project_identity_mismatch")
    project = Path(project_root)
    source = Path(source_root)
    try:
        project_resolved = project.resolve(strict=True)
        source_resolved = source.resolve(strict=True)
    except (OSError, RuntimeError):
        raise SourceBindingInspectionError("source_binding:repository_identity_mismatch") from None
    if project_resolved == source_resolved or not (project / ".git").is_file():
        raise SourceBindingInspectionError("source_binding:primary_checkout_isolation_required")
    source_repo, source_head, source_git_metadata = _repo_identity(source, primary=True)
    code_repo, code_head, code_git_metadata = _repo_identity(project, primary=False)
    if source_repo != code_repo:
        raise SourceBindingInspectionError("source_binding:repository_identity_mismatch")

    database = source / "nbs_marketing_data.db"
    generation_path = source / ".nbs_runtime" / "data_generation.json"
    source_baseline = source / "data" / "monthly_revenue_baselines.json"
    code_baseline = project / "data" / "monthly_revenue_baselines.json"
    try:
        paths = (database, generation_path, source_baseline, code_baseline)
        component_roots = (source, source, source, project)
        before = tuple(_path_component_state(root, path) for root, path in zip(component_roots, paths))
        database_fingerprint, database_identity = _hash_file_nofollow(source, database)
        baseline_fingerprint, source_baseline_identity = _hash_file_nofollow(source, source_baseline)
        code_baseline_fingerprint, code_baseline_identity = _hash_file_nofollow(project, code_baseline)
        if baseline_fingerprint != code_baseline_fingerprint:
            raise SourceBindingInspectionError("source_binding:baseline_source_mismatch")
        generation_payload, generation_identity = _read_generation_nofollow(source, generation_path)
        legacy_keys = {"generation", "operationId", "status", "updatedAt", "dbSignature"}
        v2_keys = legacy_keys | {"schemaVersion", "signatureScope", "coreRevenueSignature"}
        if set(generation_payload) not in (legacy_keys, v2_keys):
            raise SourceBindingInspectionError("source_binding:generation_invalid")
        if (
            not isinstance(generation_payload["generation"], int)
            or isinstance(generation_payload["generation"], bool)
            or generation_payload["generation"] < 0
            or (
                generation_payload["operationId"] is not None
                and (
                    not isinstance(generation_payload["operationId"], str)
                    or len(generation_payload["operationId"]) > 128
                )
            )
            or not isinstance(generation_payload["status"], str)
            or not generation_payload["status"]
            or len(generation_payload["status"]) > 64
            or not isinstance(generation_payload["updatedAt"], str)
            or len(generation_payload["updatedAt"]) > 64
        ):
            raise SourceBindingInspectionError("source_binding:generation_invalid")
        if set(generation_payload) == v2_keys:
            if (
                generation_payload["schemaVersion"] != GENERATION_SCHEMA_VERSION
                or generation_payload["signatureScope"] != GENERATION_SIGNATURE_SCOPE
            ):
                raise SourceBindingInspectionError("source_binding:generation_invalid")
            core_signature = generation_payload["coreRevenueSignature"]
            core_keys = {
                "schemaVersion", "scopeLabel", "scopeContractVersion", "sourceTables",
                "rowCounts", "rawTour", "rawOthers", "formalTour", "formalOthers",
                "sha256", "token",
            }
            if not isinstance(core_signature, dict) or set(core_signature) != core_keys:
                raise SourceBindingInspectionError("source_binding:generation_invalid")
            if (
                core_signature["schemaVersion"] != CORE_REVENUE_SIGNATURE_SCHEMA
                or core_signature["scopeLabel"] != REVENUE_SCOPE_LABEL
                or core_signature["scopeContractVersion"] != REVENUE_SCOPE_CONTRACT_VERSION
                or core_signature["sourceTables"] != list(CORE_REVENUE_SOURCE_TABLES)
            ):
                raise SourceBindingInspectionError("source_binding:generation_invalid")
            row_counts = core_signature["rowCounts"]
            digest_fields = ("rawTour", "rawOthers", "formalTour", "formalOthers")
            if (
                not isinstance(row_counts, dict)
                or set(row_counts) != set(CORE_REVENUE_SOURCE_TABLES)
                or any(
                    not isinstance(count, int) or isinstance(count, bool) or count < 0
                    for count in row_counts.values()
                )
                or any(
                    not isinstance(core_signature[field], str)
                    or _SHA256.fullmatch(core_signature[field]) is None
                    for field in digest_fields
                )
            ):
                raise SourceBindingInspectionError("source_binding:generation_invalid")
            unsigned = {key: core_signature[key] for key in core_keys - {"sha256", "token"}}
            core_digest = canonical_payload_sha256(unsigned)
            if (
                core_signature["sha256"] != core_digest
                or core_signature["token"] != f"{CORE_REVENUE_TOKEN_PREFIX}:{core_digest}"
            ):
                raise SourceBindingInspectionError("source_binding:generation_invalid")
        signature = generation_payload.get("dbSignature")
        if (
            not isinstance(signature, dict)
            or set(signature) != {"sizeBytes", "modifiedNs", "sha256"}
            or not isinstance(signature.get("sizeBytes"), int)
            or isinstance(signature.get("sizeBytes"), bool)
            or signature.get("sizeBytes", -1) < 0
            or not isinstance(signature.get("modifiedNs"), int)
            or isinstance(signature.get("modifiedNs"), bool)
            or signature.get("modifiedNs", -1) < 0
            or not isinstance(signature.get("sha256"), str)
            or _SHA256.fullmatch(signature["sha256"]) is None
        ):
            raise SourceBindingInspectionError("source_binding:generation_invalid")
        if (
            signature["sha256"] != database_fingerprint
            or signature["sizeBytes"] != database_identity[4]
            or signature["modifiedNs"] != database_identity[3]
        ):
            raise SourceBindingInspectionError("source_binding:generation_database_signature_mismatch")

        database_fingerprint_after, database_identity_after = _hash_file_nofollow(source, database)
        generation_payload_after, generation_identity_after = _read_generation_nofollow(source, generation_path)
        baseline_fingerprint_after, source_baseline_identity_after = _hash_file_nofollow(source, source_baseline)
        code_baseline_fingerprint_after, code_baseline_identity_after = _hash_file_nofollow(project, code_baseline)
        after = tuple(_path_component_state(root, path) for root, path in zip(component_roots, paths))
        if (
            before != after
            or database_fingerprint != database_fingerprint_after
            or generation_identity != generation_identity_after
            or canonical_fingerprint(generation_payload) != canonical_fingerprint(generation_payload_after)
            or baseline_fingerprint != baseline_fingerprint_after
            or code_baseline_fingerprint != code_baseline_fingerprint_after
            or database_identity != database_identity_after
            or source_baseline_identity != source_baseline_identity_after
            or code_baseline_identity != code_baseline_identity_after
        ):
            raise SourceBindingInspectionError("source_binding:source_changed_during_inspection")
        source_repo_after, source_head_after, source_git_metadata_after = _repo_identity(source, primary=True)
        code_repo_after, code_head_after, code_git_metadata_after = _repo_identity(project, primary=False)
        if (
            (source_repo_after, source_head_after) != (source_repo, source_head)
            or (code_repo_after, code_head_after) != (code_repo, code_head)
            or source_git_metadata_after != source_git_metadata
            or code_git_metadata_after != code_git_metadata
        ):
            raise SourceBindingInspectionError("source_binding:source_changed_during_inspection")
        file_state = tuple(zip(after, (
            database_fingerprint,
            canonical_fingerprint(generation_payload),
            baseline_fingerprint,
            code_baseline_fingerprint,
        )))
    except SourceBindingInspectionError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        raise SourceBindingInspectionError("source_binding:source_file_invalid") from None

    return SourceBindingInspection(
        repository_fingerprint=source_repo,
        source_head=source_head,
        database_fingerprint=database_fingerprint,
        generation_fingerprint=canonical_fingerprint(generation_payload),
        baseline_registry_fingerprint=baseline_fingerprint,
        database_path=database,
        generation_payload=MappingProxyType(generation_payload),
        file_state=file_state,
    )


@dataclass(frozen=True)
class ProfileAcceptance:
    """Fail-closed acceptance of one immutable verification profile.

    `ready` is true only when every required identity, signature, path,
    service and freshness check passed. All fields are bounded strings,
    ints or booleans; no transaction details, Excel content, secrets or
    absolute runtime paths are ever carried.
    """

    ready: bool
    blocked_reasons: tuple[str, ...]
    profile_id: str
    project_id: str
    git_head: str
    snapshot_fingerprint: str
    source_fingerprint: str
    generation_fingerprint: str
    runtime_dir: str
    service_ports: Mapping[str, int]
    created_at: str
    source_binding: Mapping[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schemaVersion": VERIFICATION_ACCEPTANCE_SCHEMA_V2 if self.source_binding is not None else VERIFICATION_ACCEPTANCE_SCHEMA,
            "status": "ready" if self.ready else "blocked",
            "identity": {
                "profileId": self.profile_id,
                "projectId": self.project_id,
                "gitHead": self.git_head,
                "snapshotFingerprint": self.snapshot_fingerprint,
                "sourceFingerprint": self.source_fingerprint,
                "generationFingerprint": self.generation_fingerprint,
                "runtimeDir": self.runtime_dir,
                "services": dict(self.service_ports),
                "createdAt": self.created_at,
            },
            "blockedReasons": list(self.blocked_reasons),
        }
        if self.source_binding is not None:
            payload["sourceBinding"] = dict(self.source_binding)
        return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_head(project_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    value = result.stdout.strip()
    return value if value else None


def _safe_source_status_path(raw_path: bytes) -> bool:
    try:
        relative = raw_path.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return False
    path = PurePosixPath(relative)
    return bool(
        relative
        and "\\" not in relative
        and not path.is_absolute()
        and str(path) == relative
        and path.parts
        and all(part not in {"", ".", ".."} for part in path.parts)
        and not is_excluded_repository_path(relative)
        and path.parts[0].casefold() != ".nbs_agent_runtime"
    )


def _source_status_is_in_scope(status: bytes) -> bool:
    if not status:
        return True
    if not status.endswith(b"\0"):
        return False
    records = status[:-1].split(b"\0")
    if any(not record for record in records):
        return False
    allowed_states = frozenset(b" M TADRCU?!")
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if len(record) < 4 or record[2:3] != b" ":
            return False
        state = record[:2]
        if any(value not in allowed_states for value in state):
            return False
        paths = [record[3:]]
        if b"R" in state or b"C" in state:
            if index >= len(records):
                return False
            paths.append(records[index])
            index += 1
        if any(not _safe_source_status_path(path) for path in paths):
            return False
    return True


def _worktree_fingerprint(project_root: Path, git_head: str) -> str | None:
    try:
        status = subprocess.run(
            [
                "git", "-C", str(project_root), "status", "--porcelain=v1",
                "--untracked-files=all", "--no-renames", "-z", "--",
                *verification_source_git_pathspecs(),
            ],
            capture_output=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    if not isinstance(status, bytes) or not _source_status_is_in_scope(status):
        return None
    return canonical_fingerprint({
        "head": git_head,
        "statusFingerprint": hashlib.sha256(status).hexdigest(),
    })


def _legacy_worktree_fingerprint(project_root: Path, git_head: str) -> str | None:
    """Retain the original V1 fingerprint contract without querying Summary roots."""
    try:
        status = subprocess.run(
            [
                "git", "-C", str(project_root), "status", "--porcelain", "--",
                *repository_git_pathspecs(),
            ],
            capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return canonical_fingerprint({"head": git_head, "status": status})


def _generation_fingerprint(generation_path: Path) -> str | None:
    try:
        if generation_path.stat().st_size > _GENERATION_MAX_BYTES:
            return None
        value = json.loads(generation_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    return canonical_fingerprint(value)


def _source_binding_evidence(binding: ProfileSourceBinding, *, status: str) -> Mapping[str, object]:
    return MappingProxyType({
        "status": status,
        "sourceRole": "primary-checkout",
        "repositoryFingerprint": binding.repository_fingerprint,
        "sourceHead": binding.source_head,
        "databaseFingerprint": binding.database_fingerprint,
        "generationFingerprint": binding.generation_fingerprint,
        "baselineRegistryFingerprint": binding.baseline_registry_fingerprint,
    })


def _parse_created_at(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def accept_verification_profile(
    profile: VerificationRuntimeProfile,
    paths: VerificationRuntimePaths,
    *,
    project_root: Path,
    expected_git_head: str | None = None,
    expected_project_id: str | None = None,
    max_profile_age: timedelta | None = DEFAULT_MAX_PROFILE_AGE,
    service_identity: ServiceIdentityEvidence | None = None,
    source_root: Path | None = None,
    now: datetime | None = None,
) -> ProfileAcceptance:
    """Run every fail-closed acceptance check and collect blocked reasons.

    A profile is `ready` only when the identity bindings (projectId, gitHead,
    worktree), the DB snapshot / source / generation signatures, the runtime
    path binding, the service identity and the profile freshness all pass.
    Every mismatch is a blocked reason; the function never raises for a
    mismatched profile.
    """
    reasons: list[str] = []
    project = Path(project_root)
    if profile.source_binding is not None:
        expected_project = NBS_PROJECT_ID
        if expected_project_id is not None and expected_project_id != NBS_PROJECT_ID:
            reasons.append("identity_drift:expected_project_id")
    else:
        expected_project = expected_project_id or project.name
    if profile.project_id != expected_project:
        reasons.append("identity_drift:projectId")

    head = expected_git_head or _git_head(project)
    if head is None:
        reasons.append("identity_drift:gitHead_unavailable")
    elif profile.git_head != head:
        reasons.append("identity_drift:gitHead")
    else:
        worktree = (
            _worktree_fingerprint(project, head)
            if profile.source_binding is not None
            else _legacy_worktree_fingerprint(project, head)
        )
        if worktree is None:
            reasons.append("stale_worktree:unavailable")
        elif profile.worktree_fingerprint != worktree:
            reasons.append("stale_worktree:worktree_fingerprint_drift")

    if paths.db_path.is_file():
        if _sha256(paths.db_path) != profile.database.snapshot_fingerprint:
            reasons.append("signature_mismatch:snapshot")
    else:
        reasons.append("signature_mismatch:snapshot_unavailable")

    source_binding_evidence: Mapping[str, object] | None = None
    if profile.source_binding is not None:
        binding = profile.source_binding
        source_binding_ok = True
        if source_root is None:
            source_binding_ok = False
            reasons.append("source_binding:source_root_required")
        else:
            try:
                inspection = inspect_primary_source(
                    project,
                    Path(source_root),
                    expected_project_id=expected_project,
                )
            except SourceBindingInspectionError as exc:
                source_binding_ok = False
                reasons.append(exc.reason_code)
            else:
                if inspection.repository_fingerprint != binding.repository_fingerprint:
                    source_binding_ok = False
                    reasons.append("source_binding:repository_fingerprint_mismatch")
                if inspection.source_head != binding.source_head:
                    source_binding_ok = False
                    reasons.append("source_binding:source_head_mismatch")
                if inspection.database_fingerprint != binding.database_fingerprint:
                    source_binding_ok = False
                    reasons.append("source_binding:database_fingerprint_mismatch")
                if inspection.generation_fingerprint != binding.generation_fingerprint:
                    source_binding_ok = False
                    reasons.append("source_binding:generation_fingerprint_mismatch")
                if inspection.baseline_registry_fingerprint != binding.baseline_registry_fingerprint:
                    source_binding_ok = False
                    reasons.append("source_binding:baseline_fingerprint_mismatch")
        source_binding_evidence = _source_binding_evidence(
            binding,
            status="accepted" if source_binding_ok else "blocked",
        )
    else:
        # V1 never consults source_root; preserve its original project-local
        # source validation even when newer callers pass the optional flag.
        source_db = project / "nbs_marketing_data.db"
        if source_db.is_file():
            if _sha256(source_db) != profile.database.source_fingerprint:
                reasons.append("signature_mismatch:source")
        else:
            reasons.append("signature_mismatch:source_unavailable")

    generation_fingerprint = _generation_fingerprint(paths.generation_path)
    if generation_fingerprint is None:
        reasons.append("signature_mismatch:generation_unavailable")
    elif generation_fingerprint != profile.runtime.generation_fingerprint:
        reasons.append("signature_mismatch:generation")

    runtime_ref = Path(profile.runtime.runtime_dir)
    if runtime_ref.is_absolute() or ".." in runtime_ref.parts or runtime_ref.parts[:2] != ("verification", profile.profile_id):
        reasons.append("runtime_path_mismatch:ref")
    else:
        resolved_runtime = project / ".nbs_agent_runtime" / profile.runtime.runtime_dir
        if resolved_runtime.resolve() != paths.runtime_dir.resolve():
            reasons.append("runtime_path_mismatch:resolved")
    try:
        paths.db_path.resolve(strict=False).relative_to(paths.runtime_dir.resolve())
        paths.generation_path.resolve(strict=False).relative_to(paths.runtime_dir.resolve())
    except ValueError:
        reasons.append("runtime_path_mismatch:artifact_outside_runtime")

    if service_identity is None or not service_identity.available:
        reasons.append("service_identity_unavailable")

    created = _parse_created_at(profile.created_at)
    if created is None:
        reasons.append("stale_profile:createdAt_invalid")
    elif max_profile_age is not None:
        stamp = now if now is not None else datetime.now(timezone.utc)
        created_utc = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
        stamp_utc = stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
        if stamp_utc - created_utc > max_profile_age:
            reasons.append("stale_profile:age")

    return ProfileAcceptance(
        ready=not reasons,
        blocked_reasons=tuple(reasons),
        profile_id=profile.profile_id,
        project_id=profile.project_id,
        git_head=profile.git_head,
        snapshot_fingerprint=profile.database.snapshot_fingerprint,
        source_fingerprint=profile.database.source_fingerprint,
        generation_fingerprint=profile.runtime.generation_fingerprint,
        runtime_dir=profile.runtime.runtime_dir,
        service_ports=profile.services.ports,
        created_at=profile.created_at,
        source_binding=source_binding_evidence,
    )


def accept_profile_file(
    profile_path: Path,
    *,
    project_root: Path,
    expected_git_head: str | None = None,
    expected_project_id: str | None = None,
    max_profile_age: timedelta | None = DEFAULT_MAX_PROFILE_AGE,
    service_identity: ServiceIdentityEvidence | None = None,
    source_root: Path | None = None,
    now: datetime | None = None,
) -> ProfileAcceptance:
    """Load and accept a profile file, converting load/path errors to blocked.

    Missing fields, invalid schema, fingerprint drift and path escapes in the
    profile file itself are all blocked reasons; no profile can claim ready
    when the immutable contract cannot be loaded exactly.
    """
    from backend.services.verification_runtime_paths import load_verification_runtime_profile

    try:
        profile, paths = load_verification_runtime_profile(
            Path(profile_path),
            project_root=Path(project_root),
            expected_git_head=expected_git_head,
        )
    except (ValueError, OSError) as exc:
        return ProfileAcceptance(
            ready=False,
            blocked_reasons=(f"profile_invalid:{type(exc).__name__}",),
            profile_id="",
            project_id="",
            git_head="",
            snapshot_fingerprint="",
            source_fingerprint="",
            generation_fingerprint="",
            runtime_dir="",
            service_ports=MappingProxyType({}),
            created_at="",
        )
    return accept_verification_profile(
        profile,
        paths,
        project_root=Path(project_root),
        expected_git_head=expected_git_head,
        expected_project_id=expected_project_id,
        max_profile_age=max_profile_age,
        service_identity=service_identity,
        source_root=source_root,
        now=now,
    )


def gather_service_identity(project_root: Path, profile_path: Path, *, python_bin: str | None = None) -> ServiceIdentityEvidence:
    """Read profile service identity through system_manager status (read-only).

    A reachable endpoint is never acceptance by itself: the profile services
    must be recorded, alive, ready, owned by the expected worktree, and carry
    the profile identity. Any unavailable evidence is fail-closed.
    """
    py = python_bin or sys.executable
    try:
        result = subprocess.run(
            [py, "scripts/system_manager.py", "status", "--verification-profile", str(profile_path)],
            capture_output=True, text=True, check=True, cwd=str(project_root),
        )
        payload = json.loads(result.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        return ServiceIdentityEvidence(False, f"service_identity_unavailable:{type(exc).__name__}")
    if not isinstance(payload, dict) or payload.get("status") != "ready":
        return ServiceIdentityEvidence(False, "service_identity_unavailable:not_ready")
    services = payload.get("services")
    if not isinstance(services, dict) or not services:
        return ServiceIdentityEvidence(False, "service_identity_unavailable:no_records")
    if not all(isinstance(record, dict) and record.get("identityMatch") is True for record in services.values()):
        return ServiceIdentityEvidence(False, "service_identity_unavailable:identity_mismatch")
    return ServiceIdentityEvidence(True, "service_identity_verified")


def handoff_evidence(acceptance: ProfileAcceptance, *, consumer: str) -> dict[str, object]:
    """Bounded Review/Hermes handoff evidence.

    The output carries only the immutable profile identity, bounded blocked
    reasons and the consumer tag. It never includes transaction details,
    Excel content, secrets, absolute paths, cache inventory contents or
    generation metadata bodies.
    """
    if consumer not in _HANDOFF_CONSUMERS:
        raise VerificationProfileAcceptanceError("consumer must be review or hermes")
    return {**acceptance.to_dict(), "consumer": consumer}
