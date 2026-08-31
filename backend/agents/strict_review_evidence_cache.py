from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from backend.agents.evidence_models import canonical_fingerprint


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DEFAULT_MAX_OUTPUT = 4000


def cache_identity(
    source_fingerprint: str,
    command: list[str],
    policy_fingerprint: str,
    runner_fingerprint: str,
) -> str:
    """Return the identity of one successful deterministic command result."""
    for name, value in (("source", source_fingerprint), ("policy", policy_fingerprint), ("runner", runner_fingerprint)):
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            raise ValueError(f"{name} fingerprint is invalid")
    if not isinstance(command, list) or not all(isinstance(item, str) and item for item in command):
        raise ValueError("command must be a list of non-empty strings")
    return canonical_fingerprint({
        "sourceFingerprint": source_fingerprint,
        "command": command,
        "policyFingerprint": policy_fingerprint,
        "runnerFingerprint": runner_fingerprint,
    })


def _assert_safe_path(path: Path) -> None:
    current = path
    missing: list[Path] = []
    while not current.exists() and current != current.parent:
        missing.append(current)
        current = current.parent
    if current.is_symlink() or any(item.is_symlink() for item in missing):
        raise ValueError("symlink path is not allowed")


def _atomic_json_write(path: Path, payload: dict) -> None:
    _assert_safe_path(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_safe_path(path)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), indent=2).encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class CommandEvidenceCache:
    def __init__(self, *, max_output_chars: int = _DEFAULT_MAX_OUTPUT):
        if isinstance(max_output_chars, bool) or not isinstance(max_output_chars, int) or max_output_chars <= 0:
            raise ValueError("max_output_chars must be a positive integer")
        self.max_output_chars = max_output_chars

    def load(self, cache_path: Path, *, identity: dict) -> dict[str, Any] | None:
        try:
            _assert_safe_path(cache_path)
            if not cache_path.is_file():
                return None
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("identity") != identity:
                return None
            command = payload.get("command")
            if not isinstance(command, dict) or command.get("exitCode") != 0 or command.get("timedOut", False):
                return None
            if not isinstance(command.get("stdoutTail"), str) or not isinstance(command.get("stderrTail"), str):
                return None
            return command
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
            return None

    def store(self, cache_path: Path, *, identity: dict, command: dict) -> None:
        if not isinstance(identity, dict) or not isinstance(command, dict):
            raise ValueError("cache payload is invalid")
        if command.get("exitCode") != 0 or command.get("timedOut", False):
            return
        bounded = dict(command)
        for field in ("stdoutTail", "stderrTail"):
            bounded[field] = str(bounded.get(field, ""))[: self.max_output_chars]
        _atomic_json_write(cache_path, {"schemaVersion": "strict-review-command-cache-v1", "identity": identity, "command": bounded})


def write_preflight_artifacts(session_dir: Path, preflight: dict, verification: dict) -> tuple[Path, Path]:
    """Atomically write bounded preflight and verification artifacts."""
    if not isinstance(session_dir, Path) or session_dir.is_symlink():
        raise ValueError("symlink session directory is not allowed")
    _assert_safe_path(session_dir)
    paths = (session_dir / "preflight.json", session_dir / "verification-v1.json")
    _atomic_json_write(paths[0], preflight)
    _atomic_json_write(paths[1], verification)
    return paths
