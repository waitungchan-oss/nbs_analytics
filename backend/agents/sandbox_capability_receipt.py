"""Atomic persistence for sandbox capability evidence."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from backend.agents.sandbox_capability_preflight import SandboxCapabilityError, SandboxCapabilityEvidence


def _regular_file(path: Path, *, allow_missing: bool) -> None:
    if not path.is_absolute() or path.is_symlink() or (not allow_missing and not path.is_file()) or (allow_missing and path.exists() and not path.is_file()):
        raise SandboxCapabilityError("evidence path must be a regular file")


def write_capability_evidence(path: Path, evidence: SandboxCapabilityEvidence) -> Path:
    _regular_file(path, allow_missing=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(evidence.to_dict(), handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise SandboxCapabilityError(f"cannot write capability evidence: {exc}") from exc
    return path


def read_capability_evidence(path: Path, *, expected_workspace_fingerprint: str | None = None) -> SandboxCapabilityEvidence:
    _regular_file(path, allow_missing=False)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SandboxCapabilityError("capability evidence is unreadable") from exc
    return SandboxCapabilityEvidence.from_dict(payload, expected_workspace_fingerprint=expected_workspace_fingerprint)
