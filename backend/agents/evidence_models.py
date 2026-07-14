from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any


ALLOWED_CONTEXT_STATUSES = {
    "ready", "blocked_missing_brief", "blocked_missing_evidence",
    "dirty_worktree", "context_overflow", "invalid_bundle",
}
ALLOWED_REVIEW_STATUSES = {
    "pass", "changes_required", "blocked", "context_overflow", "invalid_bundle",
}
ALLOWED_IMPLEMENTATION_STATUSES = {
    "completed", "changes_required", "blocked_invalid_contract", "blocked_dirty_worktree",
    "blocked_wrong_branch", "blocked_head_mismatch", "blocked_scope", "blocked_high_risk",
    "blocked_diff_limit", "validation_failed", "context_overflow", "invalid_agent_output",
    "runtime_error",
}


def canonical_fingerprint(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def estimate_tokens(text: str) -> int:
    value = str(text or "")
    non_ascii = sum(1 for character in value if ord(character) > 127)
    ascii_count = len(value) - non_ascii
    return non_ascii + ((ascii_count + 3) // 4)


def load_json_config(project_root: Path, relative_path: str) -> dict:
    return json.loads((project_root / relative_path).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class EvidenceItem:
    kind: str
    source: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "source": self.source, "content": self.content, "metadata": self.metadata}


@dataclass(frozen=True)
class CommandEvidence:
    label: str
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    truncated: bool = False

    def to_dict(self) -> dict:
        return {
            "label": self.label, "argv": list(self.argv), "exitCode": self.exit_code,
            "stdout": self.stdout, "stderr": self.stderr, "truncated": self.truncated,
        }


@dataclass(frozen=True)
class EvidenceBundle:
    schema_version: str
    task: dict
    repository: dict
    guardrails: dict
    evidence: tuple[EvidenceItem, ...] = ()
    commands: tuple[CommandEvidence, ...] = ()

    def unsigned_dict(self) -> dict:
        return {
            "schemaVersion": self.schema_version,
            "task": self.task,
            "repository": self.repository,
            "guardrails": self.guardrails,
            "evidence": [item.to_dict() for item in self.evidence],
            "commands": [item.to_dict() for item in self.commands],
        }

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.unsigned_dict())

    def to_dict(self) -> dict:
        return {**self.unsigned_dict(), "bundleFingerprint": self.fingerprint}


@dataclass(frozen=True)
class AgentReportEnvelope:
    schema_version: str
    status: str
    payload: dict

    def __post_init__(self) -> None:
        allowed = ALLOWED_CONTEXT_STATUSES | ALLOWED_REVIEW_STATUSES | ALLOWED_IMPLEMENTATION_STATUSES
        if self.status not in allowed:
            raise ValueError(f"Unsupported agent status: {self.status}")

    def to_dict(self) -> dict:
        return {**self.payload, "schemaVersion": self.schema_version, "status": self.status}
