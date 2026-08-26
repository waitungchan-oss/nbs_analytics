from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from backend.agents.evidence_models import canonical_fingerprint
from backend.services.verification_runtime_paths import VerificationRuntimePaths
from backend.services.verification_runtime_profile import VerificationRuntimeProfile

VERIFICATION_ACCEPTANCE_SCHEMA = "verification-profile-acceptance-v1"
DEFAULT_MAX_PROFILE_AGE = timedelta(hours=24)
_GENERATION_MAX_BYTES = 16 * 1024
_HANDOFF_CONSUMERS = frozenset({"review", "hermes"})


class VerificationProfileAcceptanceError(ValueError):
    """Raised when a profile acceptance cannot be formed."""


@dataclass(frozen=True)
class ServiceIdentityEvidence:
    """Bounded evidence that profile services were identified, not just reachable."""

    available: bool
    detail: str = ""


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

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": VERIFICATION_ACCEPTANCE_SCHEMA,
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


def _worktree_fingerprint(project_root: Path, git_head: str) -> str | None:
    try:
        status = subprocess.run(
            ["git", "-C", str(project_root), "status", "--porcelain"],
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
    expected_project = expected_project_id or project.name
    if profile.project_id != expected_project:
        reasons.append("identity_drift:projectId")

    head = expected_git_head or _git_head(project)
    if head is None:
        reasons.append("identity_drift:gitHead_unavailable")
    elif profile.git_head != head:
        reasons.append("identity_drift:gitHead")
    else:
        worktree = _worktree_fingerprint(project, head)
        if worktree is None:
            reasons.append("stale_worktree:unavailable")
        elif profile.worktree_fingerprint != worktree:
            reasons.append("stale_worktree:worktree_fingerprint_drift")

    if paths.db_path.is_file():
        if _sha256(paths.db_path) != profile.database.snapshot_fingerprint:
            reasons.append("signature_mismatch:snapshot")
    else:
        reasons.append("signature_mismatch:snapshot_unavailable")

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
    )


def accept_profile_file(
    profile_path: Path,
    *,
    project_root: Path,
    expected_git_head: str | None = None,
    expected_project_id: str | None = None,
    max_profile_age: timedelta | None = DEFAULT_MAX_PROFILE_AGE,
    service_identity: ServiceIdentityEvidence | None = None,
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
