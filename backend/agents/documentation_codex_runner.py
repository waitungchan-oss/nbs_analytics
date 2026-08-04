from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .documentation_models import DocumentationDraft, DocumentationSchemaError


CODEX_DOCUMENTATION_INSTRUCTION = (
    "Read the documentation-evidence-v1 JSON from stdin. Produce exactly one JSON object "
    "with schemaVersion documentation-draft-v1 and the exact keys schemaVersion, "
    "evidenceFingerprint, status, and proposals. Preserve the evidenceFingerprint. "
    "Each proposals item must contain only targetKind and content. Do not emit target paths, "
    "operations, hashes, evidence, proposal fingerprints, vault paths, or any other keys. "
    "The evidence includes a requiredTargets array; emit exactly those target kinds. "
    "Do not use tools, access files, "
    "network, Git, SQLite, or a vault. Do not include markdown fences, commentary, or any "
    "other output."
)
_STDERR_TAIL_BYTES = 4 * 1024


@dataclass(frozen=True)
class DocumentationRunnerResult:
    exit_code: int
    stdout: str
    stderr_tail: str
    duration_ms: int


class CodexDocumentationRunner:
    """Run the local Codex CLI with a fixed, read-only documentation contract."""

    def __init__(self, subprocess_module: Any = subprocess, *, project_root: Path | None = None) -> None:
        self.subprocess = subprocess_module
        self.project_root = Path(project_root or Path.cwd()).resolve()

    def run(
        self,
        argv: tuple[str, ...],
        *,
        input_text: str,
        timeout_seconds: int,
        max_output_bytes: int,
    ) -> DocumentationRunnerResult:
        started = time.perf_counter()
        try:
            evidence = json.loads(input_text)
        except (TypeError, json.JSONDecodeError):
            return self._failure(started, "invalid evidence JSON")
        if not self._valid_evidence(evidence) or not argv or Path(argv[0]).name != "codex":
            return self._failure(started, "invalid codex documentation input")

        command = (
            "codex", "exec", "--json", "--sandbox", "read-only", "--skip-git-repo-check",
            "--ephemeral", "--ignore-user-config", CODEX_DOCUMENTATION_INSTRUCTION,
        )
        codex_home = self.project_root / ".nbs_agent_runtime" / "codex_home"
        codex_home.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["CODEX_HOME"] = str(codex_home)
        process = self.subprocess.Popen(
            command, cwd=self.project_root, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, env=env,
        )
        try:
            stdout, stderr = process.communicate(
                input=input_text.encode("utf-8"), timeout=timeout_seconds,
            )
        except (TimeoutError, getattr(self.subprocess, "TimeoutExpired", subprocess.TimeoutExpired)):
            process.kill()
            try:
                process.communicate(timeout=1)
            except Exception:
                pass
            return DocumentationRunnerResult(-1, "", "", self._duration(started))

        stdout = bytes(stdout or b"")
        stderr = bytes(stderr or b"")[-_STDERR_TAIL_BYTES:]
        bounded_stdout = stdout[: max_output_bytes + 1]
        output = bounded_stdout.decode("utf-8", errors="replace")
        if len(bounded_stdout) > max_output_bytes or not self._valid_draft(output, evidence):
            return DocumentationRunnerResult(
                -2, output, stderr.decode("utf-8", errors="replace"), self._duration(started),
            )
        # A valid, fingerprint-bound draft is the contract boundary; CLI warnings
        # must not discard an otherwise safe structured result.
        return DocumentationRunnerResult(
            0, output, stderr.decode("utf-8", errors="replace"), self._duration(started),
        )

    @staticmethod
    def _valid_evidence(payload: Any) -> bool:
        return (
            isinstance(payload, dict)
            and payload.get("schemaVersion") == "documentation-evidence-v1"
            and isinstance(payload.get("evidenceFingerprint"), str)
            and len(payload["evidenceFingerprint"]) == 64
            and all(char in "0123456789abcdef" for char in payload["evidenceFingerprint"])
        )

    @staticmethod
    def _valid_draft(output: str, evidence: dict[str, Any]) -> bool:
        try:
            draft = DocumentationDraft.from_dict(json.loads(output))
        except (TypeError, json.JSONDecodeError, DocumentationSchemaError):
            return False
        return draft.evidence_fingerprint == evidence["evidenceFingerprint"]

    @staticmethod
    def _duration(started: float) -> int:
        return round((time.perf_counter() - started) * 1000)

    @classmethod
    def _failure(cls, started: float, message: str) -> DocumentationRunnerResult:
        return DocumentationRunnerResult(-2, "", message, cls._duration(started))


__all__ = ["CODEX_DOCUMENTATION_INSTRUCTION", "CodexDocumentationRunner", "DocumentationRunnerResult"]
