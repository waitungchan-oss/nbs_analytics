"""Bounded, source-bound persistence for runner identity metadata."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.runner_identity import RunnerIdentity, RunnerIdentityError


class IdentityEnvelopeError(ValueError):
    """Raised when an identity envelope is unsafe or invalid."""


@dataclass(frozen=True)
class IdentityEnvelope:
    identity: RunnerIdentity
    source_fingerprint: str
    artifact_kind: str
    envelope_fingerprint: str


def _validate_sha256(value: str, label: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise IdentityEnvelopeError(f"{label} must be lowercase sha256")


def _regular_file(path: Path) -> None:
    try:
        if path.is_symlink() or not path.is_file():
            raise IdentityEnvelopeError("identity envelope must be a regular file")
    except OSError as exc:
        raise IdentityEnvelopeError(f"cannot inspect identity envelope: {exc}") from exc


def write_identity_envelope(
    path: Path, identity: RunnerIdentity, *, source_fingerprint: str, artifact_kind: str
) -> Path:
    _validate_sha256(source_fingerprint, "source fingerprint")
    if not isinstance(artifact_kind, str) or not artifact_kind.strip():
        raise IdentityEnvelopeError("artifact kind must be non-empty")
    if path.exists() or path.is_symlink():
        _regular_file(path)
    unsigned = {
        "schemaVersion": "runner-identity-envelope-v1",
        "identity": identity.to_dict(),
        "sourceFingerprint": source_fingerprint,
        "artifactKind": artifact_kind,
    }
    payload = {**unsigned, "envelopeFingerprint": canonical_fingerprint(unsigned)}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise IdentityEnvelopeError(f"cannot write identity envelope: {exc}") from exc
    return path


def read_identity_envelope(path: Path, *, expected_source_fingerprint: str | None = None) -> IdentityEnvelope:
    _regular_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        expected_keys = {"schemaVersion", "identity", "sourceFingerprint", "artifactKind", "envelopeFingerprint"}
        if set(payload) != expected_keys or payload.get("schemaVersion") != "runner-identity-envelope-v1":
            raise IdentityEnvelopeError("unsupported identity envelope schemaVersion")
        unsigned = {key: payload[key] for key in expected_keys if key != "envelopeFingerprint"}
        if payload["envelopeFingerprint"] != canonical_fingerprint(unsigned):
            raise IdentityEnvelopeError("identity envelope fingerprint does not match payload")
        source = payload["sourceFingerprint"]
        _validate_sha256(source, "source fingerprint")
        if expected_source_fingerprint is not None and source != expected_source_fingerprint:
            raise IdentityEnvelopeError("source fingerprint mismatch")
        artifact_kind = payload["artifactKind"]
        if not isinstance(artifact_kind, str) or not artifact_kind.strip():
            raise IdentityEnvelopeError("artifact kind must be non-empty")
        identity = RunnerIdentity.from_dict(payload["identity"])
        return IdentityEnvelope(identity, source, artifact_kind, payload["envelopeFingerprint"])
    except (OSError, KeyError, TypeError, json.JSONDecodeError, RunnerIdentityError) as exc:
        if isinstance(exc, IdentityEnvelopeError):
            raise
        raise IdentityEnvelopeError(f"invalid identity envelope: {exc}") from exc
