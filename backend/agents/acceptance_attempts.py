"""Bounded append-only telemetry for acceptance gate attempts."""

from __future__ import annotations

import json
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

import fcntl


SCHEMA = "verification-attempts-v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GATES = frozenset({
    "pre_review", "strict_review", "full_pytest", "hermes", "completion", "review_batch",
})
_OUTCOMES = frozenset({
    "pass", "blocked_runner_transport", "blocked_runner_capability",
    "blocked_source_probe", "invalid_evidence", "stale_source", "failed",
})
_MAX_REASON_LENGTH = 512
MAX_ATTEMPTS = 128
MAX_TRANSPORT_ATTEMPTS = 2
_TOKEN_KEYS = frozenset({"input", "output", "total"})


def _timestamp(value: str, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be an RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an RFC3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include timezone")
    return parsed


def _fingerprint(value: str | None, field: str) -> None:
    if value is not None and (not isinstance(value, str) or not _SHA256_RE.fullmatch(value)):
        raise ValueError(f"{field} must be a lowercase SHA-256 fingerprint")


@dataclass(frozen=True)
class AcceptanceAttempt:
    """Immutable, bounded representation of one acceptance attempt."""

    attempt_id: str
    gate: str
    source_fingerprint: str
    runner_fingerprint: str | None
    outcome: str
    blocked_reason: str | None
    cache_hit: bool
    started_at: str
    finished_at: str
    token_usage: Mapping[str, int] | None = None
    retry_group: str | None = None
    attempt_number: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.attempt_id, str) or not self.attempt_id or len(self.attempt_id) > 128:
            raise ValueError("attempt_id is invalid")
        if self.gate not in _GATES:
            raise ValueError("gate is invalid")
        if self.outcome not in _OUTCOMES:
            raise ValueError("outcome is invalid")
        _fingerprint(self.source_fingerprint, "source fingerprint")
        _fingerprint(self.runner_fingerprint, "runner fingerprint")
        _fingerprint(self.retry_group, "retry group")
        if self.attempt_number is not None and (
            isinstance(self.attempt_number, bool)
            or not isinstance(self.attempt_number, int)
            or self.attempt_number < 1
        ):
            raise ValueError("attempt_number must be a positive integer")
        if self.blocked_reason is not None:
            if not isinstance(self.blocked_reason, str) or not self.blocked_reason:
                raise ValueError("blocked reason is invalid")
            if len(self.blocked_reason) > _MAX_REASON_LENGTH:
                raise ValueError("blocked reason exceeds 512 characters")
        if not isinstance(self.cache_hit, bool):
            raise ValueError("cache_hit must be a boolean")
        started = _timestamp(self.started_at, "started_at")
        finished = _timestamp(self.finished_at, "finished_at")
        if finished < started:
            raise ValueError("finished_at cannot precede started_at")
        if self.token_usage is not None:
            if not isinstance(self.token_usage, Mapping) or set(self.token_usage) - _TOKEN_KEYS:
                raise ValueError("token_usage is invalid")
            if any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in self.token_usage.values()
            ):
                raise ValueError("token_usage values are invalid")
            object.__setattr__(self, "token_usage", dict(self.token_usage))

    def to_dict(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "attemptId": self.attempt_id,
            "schemaVersion": SCHEMA,
            "gate": self.gate,
            "sourceFingerprint": self.source_fingerprint,
            "outcome": self.outcome,
            "cacheHit": self.cache_hit,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
        }
        if self.runner_fingerprint is not None:
            record["runnerFingerprint"] = self.runner_fingerprint
        if self.blocked_reason is not None:
            record["blockedReason"] = self.blocked_reason
        if self.token_usage is not None:
            record["tokenUsage"] = dict(self.token_usage)
        if self.retry_group is not None:
            record["retryGroup"] = self.retry_group
        if self.attempt_number is not None:
            record["attemptNumber"] = self.attempt_number
        return record

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AcceptanceAttempt":
        """Validate and normalize one persisted attempt record."""
        if not isinstance(payload, Mapping):
            raise ValueError("attempts artifact entry must be an object")
        allowed = {
            "attemptId", "schemaVersion", "gate", "sourceFingerprint",
            "runnerFingerprint", "outcome", "blockedReason", "cacheHit",
            "startedAt", "finishedAt", "tokenUsage", "retryGroup", "attemptNumber",
        }
        if set(payload) - allowed or payload.get("schemaVersion") != SCHEMA:
            raise ValueError("attempts artifact entry schema is invalid")
        required = {
            "attemptId", "schemaVersion", "gate", "sourceFingerprint",
            "outcome", "cacheHit", "startedAt", "finishedAt",
        }
        if not required <= set(payload):
            raise ValueError("attempts artifact entry is missing required fields")
        return cls(
            attempt_id=payload["attemptId"],
            gate=payload["gate"],
            source_fingerprint=payload["sourceFingerprint"],
            runner_fingerprint=payload.get("runnerFingerprint"),
            outcome=payload["outcome"],
            blocked_reason=payload.get("blockedReason"),
            cache_hit=payload["cacheHit"],
            started_at=payload["startedAt"],
            finished_at=payload["finishedAt"],
            token_usage=payload.get("tokenUsage"),
            retry_group=payload.get("retryGroup"),
            attempt_number=payload.get("attemptNumber"),
        )


def _load_attempts(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.is_symlink():
        raise ValueError("attempts artifact cannot be a symlink")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("attempts artifact is invalid") from exc
    if not isinstance(payload, dict) or set(payload) != {"schemaVersion", "attempts"}:
        raise ValueError("attempts artifact schema is invalid")
    if payload["schemaVersion"] != SCHEMA or not isinstance(payload["attempts"], list):
        raise ValueError("attempts artifact schema is invalid")
    try:
        return [AcceptanceAttempt.from_dict(item).to_dict() for item in payload["attempts"]]
    except (TypeError, KeyError, ValueError) as exc:
        raise ValueError("attempts artifact entry is invalid") from exc


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent, text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


@contextmanager
def _attempts_lock(path: Path):
    """Serialize cross-process append operations for one attempts artifact."""
    lock_path = path.with_name(f".{path.name}.lock")
    if lock_path.is_symlink():
        raise ValueError("attempts lock cannot be a symlink")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def record_attempt(
    artifact_root: Path,
    *,
    gate: str,
    source_fingerprint: str,
    runner_fingerprint: str | None,
    outcome: str,
    blocked_reason: str | None,
    cache_hit: bool,
    started_at: str,
    finished_at: str,
    token_usage: Mapping[str, int] | None = None,
    retry_group: str | None = None,
    attempt_number: int | None = None,
) -> dict[str, Any]:
    """Append one bounded attempt record and return the record."""
    if not isinstance(artifact_root, Path):
        raise ValueError("artifact_root must be a Path")
    attempt = AcceptanceAttempt(
        attempt_id=str(uuid4()),
        gate=gate,
        source_fingerprint=source_fingerprint,
        runner_fingerprint=runner_fingerprint,
        outcome=outcome,
        blocked_reason=blocked_reason,
        cache_hit=cache_hit,
        started_at=started_at,
        finished_at=finished_at,
        token_usage=token_usage,
        retry_group=retry_group,
        attempt_number=attempt_number,
    )
    record = attempt.to_dict()

    path = artifact_root / "attempts.json"
    with _attempts_lock(path):
        attempts = _load_attempts(path)
        attempts = [*attempts, record][-MAX_ATTEMPTS:]
        _write_atomic(path, {"schemaVersion": SCHEMA, "attempts": attempts})
    return record


def count_transport_attempts(artifact_root: Path, *, gate: str) -> int:
    """Count validated transport failures for one gate in one artifact root."""
    if not isinstance(artifact_root, Path):
        raise ValueError("artifact_root must be a Path")
    if gate not in _GATES:
        raise ValueError("gate is invalid")
    return sum(
        item["gate"] == gate and item["outcome"] == "blocked_runner_transport"
        for item in _load_attempts(artifact_root / "attempts.json")
    )
