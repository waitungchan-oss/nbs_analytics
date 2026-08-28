from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Iterable

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.verification_session import VerificationSession


_COMMAND_KEYS = {"label", "argv", "exitCode", "stdoutTail", "stderrTail"}
_MAX_TAIL_CHARS = 4000

_GATE_SCHEMA_VERSION = "verification-gate-evidence-v1"
PRODUCER_VERSION = "nbs-verification-evidence-writer-v1"
_ALLOWED_GATES = {"pre_review", "strict_review", "full_pytest", "hermes", "completion"}
_REUSE_REASON_NEW = "new"
_SESSIONS_ANCHOR = (".nbs_agent_runtime", "verification_sessions")


class VerificationEvidenceError(ValueError):
    pass


def sha256_from_output(value: str) -> str:
    """Return the bounded digest token from raw or standard shasum output."""
    token = value.strip().split(maxsplit=1)[0] if value.strip() else ""
    if not re.fullmatch(r"[0-9a-fA-F]{64}", token):
        return ""
    return token.lower()


def _normalize_command(item: dict) -> dict:
    if not isinstance(item, dict):
        raise VerificationEvidenceError("verification command must be an object")
    if set(item) != _COMMAND_KEYS:
        raise VerificationEvidenceError("verification command schema is invalid")
    if not isinstance(item["label"], str) or not item["label"].strip():
        raise VerificationEvidenceError("verification label is invalid")
    if not isinstance(item["argv"], list) or not item["argv"] or not all(
        isinstance(value, str) and value for value in item["argv"]
    ):
        raise VerificationEvidenceError("verification argv is invalid")
    if not isinstance(item["exitCode"], int) or isinstance(item["exitCode"], bool):
        raise VerificationEvidenceError("verification exitCode is invalid")
    if not all(isinstance(item[field], str) for field in ("stdoutTail", "stderrTail")):
        raise VerificationEvidenceError("verification output tails are invalid")
    if len(item["stdoutTail"]) > _MAX_TAIL_CHARS or len(item["stderrTail"]) > _MAX_TAIL_CHARS:
        raise VerificationEvidenceError("verification output tail exceeds bounded limit")
    return {
        "label": item["label"],
        "argv": list(item["argv"]),
        "exitCode": item["exitCode"],
        "stdoutTail": item["stdoutTail"],
        "stderrTail": item["stderrTail"],
    }


def validate_verification_v1(path: Path) -> tuple[dict, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {"commands"} or not isinstance(payload["commands"], list):
        raise VerificationEvidenceError("verification-v1 must contain only commands")
    return tuple(_normalize_command(item) for item in payload["commands"])


def write_verification_v1(commands: Iterable[dict], output: Path) -> Path:
    normalized = [_normalize_command(item) for item in commands]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"commands": normalized}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    validate_verification_v1(output)
    return output


@dataclass(frozen=True)
class GateEvidence:
    """Bounded, session-bound result of writing one gate's evidence."""

    gate: str
    session_id: str
    source_fingerprint: str
    status: str
    verification_path: Path
    metadata_path: Path
    command_fingerprint: str
    evidence_fingerprint: str
    started_at: str
    finished_at: str
    producer: str
    stdout_digest: str
    stderr_digest: str
    reuse_reason: str

    def metadata_dict(self) -> dict:
        return {
            "schemaVersion": _GATE_SCHEMA_VERSION,
            "gate": self.gate,
            "sessionId": self.session_id,
            "sourceFingerprint": self.source_fingerprint,
            "status": self.status,
            "commandFingerprint": self.command_fingerprint,
            "evidenceFingerprint": self.evidence_fingerprint,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "producer": self.producer,
            "stdoutDigest": self.stdout_digest,
            "stderrDigest": self.stderr_digest,
            "reuseReason": self.reuse_reason,
        }


def _now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _resolve_session_output_dir(output_dir: Path | str, session: VerificationSession) -> Path:
    lexical = Path(os.path.abspath(os.fspath(output_dir)))
    parts = lexical.parts
    anchor: int | None = None
    for index in range(len(parts) - 1):
        if parts[index] == _SESSIONS_ANCHOR[0] and parts[index + 1] == _SESSIONS_ANCHOR[1]:
            anchor = index
            break
    if anchor is None:
        raise PermissionError(
            "Verification gate evidence must stay under "
            ".nbs_agent_runtime/verification_sessions/<sessionId>/"
        )
    remainder = parts[anchor + 2:]
    if not remainder or remainder[0] != session.session_id:
        raise PermissionError(
            "Verification gate evidence must stay under the session directory"
        )
    current = Path(*parts[:anchor])
    for part in (parts[anchor], parts[anchor + 1], *remainder):
        current = current / part
        if current.is_symlink():
            raise PermissionError("Verification gate evidence parent cannot be a symlink")
    resolved = lexical.resolve()
    root = Path(*parts[: anchor + 2])
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise PermissionError(
            "Verification gate evidence must stay under "
            ".nbs_agent_runtime/verification_sessions/<sessionId>/"
        ) from exc
    return resolved


def _assert_source_fresh(session: VerificationSession, metadata_path: Path) -> None:
    """Refuse to overwrite gate evidence that belongs to a different source seal."""
    if not metadata_path.exists():
        return
    try:
        existing = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise VerificationEvidenceError("existing gate metadata is not valid JSON") from exc
    if not isinstance(existing, dict):
        raise VerificationEvidenceError("existing gate metadata is invalid")
    if existing.get("sessionId") != session.session_id:
        raise VerificationEvidenceError(
            "gate evidence sessionId differs from session (stale source)"
        )
    if existing.get("sourceFingerprint") != session.source_fingerprint:
        raise VerificationEvidenceError(
            "gate evidence source fingerprint differs from session (stale source)"
        )


def _write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent, text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def write_gate_evidence(
    session: VerificationSession,
    gate: str,
    commands: Iterable[dict],
    output_dir: Path | str,
) -> GateEvidence:
    """Write a gate's bounded evidence below its verification session directory.

    The `verification-v1` file keeps the exact ``{"commands": [...]}`` shape
    (validated by the existing ``validate_verification_v1``); the outer
    metadata is stored in ``gate.json`` beside it. A nonzero command is
    recorded as ``failed`` evidence so failures remain auditable. Output is
    confined to ``.nbs_agent_runtime/verification_sessions/<sessionId>/`` and
    existing evidence bound to a different source seal is rejected.
    """
    if not isinstance(session, VerificationSession):
        raise VerificationEvidenceError("gate evidence requires a VerificationSession")
    if gate not in _ALLOWED_GATES:
        raise VerificationEvidenceError(f"gate is not allowlisted for gate evidence: {gate}")

    resolved_dir = _resolve_session_output_dir(output_dir, session)
    normalized = [_normalize_command(item) for item in commands]
    verification_path = resolved_dir / "verification.json"
    metadata_path = resolved_dir / "gate.json"
    for target in (verification_path, metadata_path):
        if target.is_symlink():
            raise PermissionError("Verification gate evidence output cannot be a symlink")
    _assert_source_fresh(session, metadata_path)

    started_at = _now_rfc3339()
    stdout_digest = sha256(
        "\n".join(item["stdoutTail"] for item in normalized).encode("utf-8")
    ).hexdigest()
    stderr_digest = sha256(
        "\n".join(item["stderrTail"] for item in normalized).encode("utf-8")
    ).hexdigest()
    status = "pass" if all(item["exitCode"] == 0 for item in normalized) else "failed"
    command_fingerprint = canonical_fingerprint({"commands": normalized})

    verification_path = write_verification_v1(normalized, verification_path)
    finished_at = _now_rfc3339()
    reuse_reason = _REUSE_REASON_NEW

    evidence_fingerprint = canonical_fingerprint({
        "schemaVersion": _GATE_SCHEMA_VERSION,
        "gate": gate,
        "sessionId": session.session_id,
        "sourceFingerprint": session.source_fingerprint,
        "commandFingerprint": command_fingerprint,
        "status": status,
        "stdoutDigest": stdout_digest,
        "stderrDigest": stderr_digest,
        "startedAt": started_at,
        "finishedAt": finished_at,
        "producer": PRODUCER_VERSION,
        "reuseReason": reuse_reason,
        "commands": normalized,
    })

    evidence = GateEvidence(
        gate=gate,
        session_id=session.session_id,
        source_fingerprint=session.source_fingerprint,
        status=status,
        verification_path=verification_path,
        metadata_path=metadata_path,
        command_fingerprint=command_fingerprint,
        evidence_fingerprint=evidence_fingerprint,
        started_at=started_at,
        finished_at=finished_at,
        producer=PRODUCER_VERSION,
        stdout_digest=stdout_digest,
        stderr_digest=stderr_digest,
        reuse_reason=reuse_reason,
    )
    _write_json_atomic(metadata_path, evidence.metadata_dict())
    return evidence
