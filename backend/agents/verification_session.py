"""Immutable Verification Session contract (`verification-session-v1`).

Task 1 of the strict review verification chain. A session is a frozen
dataclass that seals the source identity (base/head SHA, brief, filtered
worktree and diff fingerprints) together with the policy identity
(contract and policy fingerprints). It never contains SQLite rows, Excel
payloads, full logs or secrets.

The canonical ``source_fingerprint`` is derived only from the source seal
and the policy identity, so it is stable across status transitions and
session bookkeeping and can bind evidence to the same source state.

Persistence is confined to ``.nbs_agent_runtime/verification_sessions/``:
``write_session`` writes a temporary file in the same directory, fsyncs it
and atomically replaces it into place with ``os.replace``; ``read_session``
parses and re-validates the exact schema.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.agents.evidence_models import canonical_fingerprint


SESSION_SCHEMA_VERSION = "verification-session-v1"

SESSION_KEYS = {
    "schemaVersion", "sessionId", "status", "projectId", "baseSha", "headSha",
    "briefPath", "briefFingerprint", "worktreeFingerprint", "diffFingerprint",
    "contractFingerprint", "policyFingerprint", "createdAt", "gates",
}

ALLOWED_SESSION_STATUSES = {
    "created", "sealed", "review_running", "review_passed",
    "full_verification_passed", "hermes_passed", "complete",
    "blocked_runner_capability", "blocked_runner_transport",
    "review_changes_required", "context_overflow", "verification_failed",
    "hermes_failed", "stale_source", "invalid_evidence",
}

# Source-seal and policy-identity fields that define the canonical source
# fingerprint. Deliberately excludes sessionId/status/createdAt/gates so the
# fingerprint stays stable while a session moves through its gates.
_SOURCE_SEAL_KEYS = (
    "baseSha", "headSha", "briefPath", "briefFingerprint",
    "worktreeFingerprint", "diffFingerprint",
    "contractFingerprint", "policyFingerprint",
)

_SESSIONS_ANCHOR = (".nbs_agent_runtime", "verification_sessions")

_SHA_HEX_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_RFC3339_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")


class StaleVerificationSession(ValueError):
    """Raised when the current source state no longer matches the sealed session."""


def _now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _require_non_empty(value: object, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _require_session_id(value: object) -> str:
    if (
        not isinstance(value, str) or not value.strip()
        or any(separator in value for separator in (",", " "))
    ):
        raise ValueError("sessionId must identify exactly one session")
    return value


def _require_sha(value: object, key: str) -> str:
    if not isinstance(value, str) or not _SHA_HEX_RE.fullmatch(value):
        raise ValueError(f"{key} must be a lowercase 40-character SHA hex string")
    return value


def _require_sha256(value: object, key: str) -> str:
    if not isinstance(value, str) or not _SHA256_HEX_RE.fullmatch(value):
        raise ValueError(f"{key} must be a lowercase SHA-256 hex string")
    return value


def _require_rfc3339(value: object, key: str) -> str:
    if not isinstance(value, str) or not _RFC3339_RE.fullmatch(value):
        raise ValueError(f"{key} must be an RFC3339 timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{key} must be an RFC3339 timestamp") from exc
    return value


@dataclass(frozen=True)
class VerificationSession:
    schema_version: str
    session_id: str
    status: str
    project_id: str
    base_sha: str
    head_sha: str
    brief_path: str
    brief_fingerprint: str
    worktree_fingerprint: str
    diff_fingerprint: str
    contract_fingerprint: str
    policy_fingerprint: str
    created_at: str
    gates: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != SESSION_SCHEMA_VERSION:
            raise ValueError("schemaVersion must be verification-session-v1")
        _require_session_id(self.session_id)
        if self.status not in ALLOWED_SESSION_STATUSES:
            raise ValueError(
                f"status is not an allowed verification session status: {self.status}"
            )
        _require_non_empty(self.project_id, "projectId")
        _require_sha(self.base_sha, "baseSha")
        _require_sha(self.head_sha, "headSha")
        _require_non_empty(self.brief_path, "briefPath")
        _require_sha256(self.brief_fingerprint, "briefFingerprint")
        _require_sha256(self.worktree_fingerprint, "worktreeFingerprint")
        _require_sha256(self.diff_fingerprint, "diffFingerprint")
        _require_sha256(self.contract_fingerprint, "contractFingerprint")
        _require_sha256(self.policy_fingerprint, "policyFingerprint")
        _require_rfc3339(self.created_at, "createdAt")
        if not isinstance(self.gates, dict):
            raise ValueError("gates must be an object")

    @classmethod
    def create(
        cls,
        *,
        project_id: str,
        base_sha: str,
        head_sha: str,
        brief_path: str,
        brief_fingerprint: str,
        worktree_fingerprint: str,
        diff_fingerprint: str,
        contract_fingerprint: str,
        policy_fingerprint: str,
        session_id: str | None = None,
        status: str = "sealed",
        created_at: str | None = None,
        gates: dict[str, Any] | None = None,
    ) -> "VerificationSession":
        """Build a sealed session from the approved source seal inputs."""
        return cls(
            schema_version=SESSION_SCHEMA_VERSION,
            session_id=session_id or str(uuid4()),
            status=status,
            project_id=project_id,
            base_sha=base_sha,
            head_sha=head_sha,
            brief_path=brief_path,
            brief_fingerprint=brief_fingerprint,
            worktree_fingerprint=worktree_fingerprint,
            diff_fingerprint=diff_fingerprint,
            contract_fingerprint=contract_fingerprint,
            policy_fingerprint=policy_fingerprint,
            created_at=created_at if created_at is not None else _now_rfc3339(),
            gates=dict(gates or {}),
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "VerificationSession":
        if not isinstance(payload, dict):
            raise ValueError("Verification session must be an object")
        if set(payload) != SESSION_KEYS:
            raise ValueError("Verification session schema keys are invalid")
        return cls(
            schema_version=payload["schemaVersion"],
            session_id=payload["sessionId"],
            status=payload["status"],
            project_id=payload["projectId"],
            base_sha=payload["baseSha"],
            head_sha=payload["headSha"],
            brief_path=payload["briefPath"],
            brief_fingerprint=payload["briefFingerprint"],
            worktree_fingerprint=payload["worktreeFingerprint"],
            diff_fingerprint=payload["diffFingerprint"],
            contract_fingerprint=payload["contractFingerprint"],
            policy_fingerprint=payload["policyFingerprint"],
            created_at=payload["createdAt"],
            gates=dict(payload["gates"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "sessionId": self.session_id,
            "status": self.status,
            "projectId": self.project_id,
            "baseSha": self.base_sha,
            "headSha": self.head_sha,
            "briefPath": self.brief_path,
            "briefFingerprint": self.brief_fingerprint,
            "worktreeFingerprint": self.worktree_fingerprint,
            "diffFingerprint": self.diff_fingerprint,
            "contractFingerprint": self.contract_fingerprint,
            "policyFingerprint": self.policy_fingerprint,
            "createdAt": self.created_at,
            "gates": dict(self.gates),
        }

    @property
    def source_fingerprint(self) -> str:
        """Canonical fingerprint of the source seal and policy identity only."""
        return canonical_fingerprint(
            {key: self.to_dict()[key] for key in _SOURCE_SEAL_KEYS}
        )

    def assert_fresh(
        self,
        *,
        head_sha: str,
        brief_fingerprint: str,
        worktree_fingerprint: str,
        diff_fingerprint: str,
    ) -> None:
        """Raise ``StaleVerificationSession`` if the current source drifted."""
        current = {
            "headSha": _require_sha(head_sha, "headSha"),
            "briefFingerprint": _require_sha256(brief_fingerprint, "briefFingerprint"),
            "worktreeFingerprint": _require_sha256(worktree_fingerprint, "worktreeFingerprint"),
            "diffFingerprint": _require_sha256(diff_fingerprint, "diffFingerprint"),
        }
        sealed = {
            "headSha": self.head_sha,
            "briefFingerprint": self.brief_fingerprint,
            "worktreeFingerprint": self.worktree_fingerprint,
            "diffFingerprint": self.diff_fingerprint,
        }
        for label in current:
            if current[label] != sealed[label]:
                raise StaleVerificationSession(
                    f"verification source is stale: {label} changed"
                )


def _resolve_verification_session_path(path: Path | str) -> Path:
    lexical = Path(os.path.abspath(os.fspath(path)))
    parts = lexical.parts
    anchor: int | None = None
    for index in range(len(parts) - 1):
        if parts[index] == _SESSIONS_ANCHOR[0] and parts[index + 1] == _SESSIONS_ANCHOR[1]:
            anchor = index
            break
    if anchor is None:
        raise PermissionError(
            "Verification session output must stay under "
            ".nbs_agent_runtime/verification_sessions/"
        )
    remainder = parts[anchor + 2:]
    if not remainder:
        raise PermissionError(
            "Verification session output must be a file below "
            ".nbs_agent_runtime/verification_sessions/"
        )
    current = Path(*parts[:anchor])
    for part in (parts[anchor], parts[anchor + 1], *remainder[:-1]):
        current = current / part
        if current.is_symlink():
            raise PermissionError("Verification session parent cannot be a symlink")
    resolved = lexical.resolve()
    root = Path(*parts[: anchor + 2])
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise PermissionError(
            "Verification session output must stay under "
            ".nbs_agent_runtime/verification_sessions/"
        ) from exc
    if resolved.is_symlink():
        raise PermissionError("Verification session output cannot be a symlink")
    return resolved


def write_session(path: Path | str, session: VerificationSession) -> Path:
    """Atomically persist a session manifest below verification_sessions/.

    Writes canonical compact JSON to a temporary file in the same directory,
    fsyncs it, then replaces it into place with ``os.replace``. Raises
    ``PermissionError`` for any path that escapes
    ``.nbs_agent_runtime/verification_sessions/``.
    """
    if not isinstance(session, VerificationSession):
        raise ValueError("write_session requires a VerificationSession")
    resolved = _resolve_verification_session_path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        session.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{resolved.name}.", dir=resolved.parent, text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, resolved)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return resolved


def read_session(path: Path | str) -> VerificationSession:
    """Read and re-validate a session manifest below verification_sessions/."""
    resolved = _resolve_verification_session_path(path)
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Verification session file is not valid JSON") from exc
    return VerificationSession.from_dict(payload)
