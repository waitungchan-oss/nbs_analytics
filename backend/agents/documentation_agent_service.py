from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent_runtime import AgentRuntime
from .documentation_models import (
    DOCUMENTATION_PROPOSAL_SCHEMA,
    DocumentationEvidence,
    DocumentationProposal,
    DocumentationSchemaError,
)
from .documentation_policy import DocumentationImpactClassifier
from .workflow_models import canonical_sha256


_OUTPUT_MAX_BYTES = 64 * 1024
_STDERR_TAIL_BYTES = 4 * 1024
_TIMEOUT_SECONDS = 120
_ALLOWED_EXECUTABLES = frozenset({"codex", "claude"})


@dataclass(frozen=True)
class DocumentationRunnerResult:
    exit_code: int
    stdout: str
    stderr_tail: str
    duration_ms: int


class _SubprocessDocumentationRunner:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root

    def run(
        self,
        argv: tuple[str, ...],
        *,
        input_text: str,
        timeout_seconds: int,
        max_output_bytes: int,
    ) -> DocumentationRunnerResult:
        started = time.perf_counter()
        process = subprocess.Popen(
            list(argv), cwd=self.project_root, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
        )
        try:
            stdout, stderr = process.communicate(
                input=input_text.encode("utf-8"), timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            return DocumentationRunnerResult(
                -1, "", "", round((time.perf_counter() - started) * 1000),
            )
        duration_ms = round((time.perf_counter() - started) * 1000)
        if len(stdout) > max_output_bytes:
            return DocumentationRunnerResult(
                process.returncode, "", "", duration_ms,
            )
        return DocumentationRunnerResult(
            process.returncode,
            stdout.decode("utf-8", errors="replace"),
            stderr[-_STDERR_TAIL_BYTES:].decode("utf-8", errors="replace"),
            duration_ms,
        )


def _set_warnings(proposal: DocumentationProposal, warnings: tuple[str, ...]) -> DocumentationProposal:
    # Task 1's immutable model intentionally excludes diagnostics from the persisted schema.
    object.__setattr__(proposal, "warnings", warnings)
    return proposal


def _proposal(
    evidence: DocumentationEvidence,
    status: str,
    warnings: tuple[str, ...] = (),
    proposals: tuple[dict[str, Any], ...] = (),
) -> DocumentationProposal:
    unsigned = {
        "schemaVersion": DOCUMENTATION_PROPOSAL_SCHEMA,
        "taskId": evidence.task_id,
        "generatedAt": evidence.generated_at,
        "evidence": evidence.to_dict(),
        "evidenceFingerprint": evidence.evidence_fingerprint,
        "status": status,
        "proposals": [dict(item) for item in proposals],
    }
    fingerprint = canonical_sha256(unsigned)
    result = DocumentationProposal(
        DOCUMENTATION_PROPOSAL_SCHEMA, evidence.task_id, evidence.generated_at,
        evidence, evidence.evidence_fingerprint, status, proposals, fingerprint,
    )
    return _set_warnings(result, warnings)


class DocumentationAgentService:
    def __init__(self, project_root: Path, *, runner=None, runtime=None) -> None:
        self.project_root = Path(project_root).resolve()
        self.runtime = runtime or AgentRuntime(self.project_root / ".nbs_agent_runtime")
        self.runner = runner or _SubprocessDocumentationRunner(self.project_root)
        self.classifier = DocumentationImpactClassifier()
        self.cache_root = self.project_root / ".nbs_agent_runtime" / "documentation"
        self.telemetry_path = self.project_root / ".nbs_agent_runtime" / "telemetry" / "documentation.jsonl"

    def draft(
        self,
        evidence: DocumentationEvidence,
        *,
        agent_command: str | None,
    ) -> DocumentationProposal:
        if not isinstance(evidence, DocumentationEvidence):
            raise TypeError("documentation evidence must be DocumentationEvidence")
        payload = evidence.to_dict()
        required_targets = self._required_targets(payload)
        if not required_targets:
            proposal = _proposal(evidence, "no_documentation_needed")
            self._telemetry(evidence, payload, proposal, cache_hit=False, duration_ms=0)
            return proposal
        if agent_command is None or not agent_command.strip():
            proposal = _proposal(evidence, "blocked", ("blocked_missing_runner",))
            self._telemetry(evidence, payload, proposal, cache_hit=False, duration_ms=0)
            return proposal

        input_limit, output_limit = self._budget()
        input_text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if self._estimate(input_text) > input_limit:
            proposal = _proposal(evidence, "context_overflow", ("input_over_budget",))
            self._telemetry(evidence, payload, proposal, cache_hit=False, duration_ms=0)
            return proposal

        cache_path = self.cache_root / f"{evidence.evidence_fingerprint}.json"
        if cache_path.is_file():
            try:
                cached = DocumentationProposal.from_dict(json.loads(cache_path.read_text(encoding="utf-8")))
                if cached.evidence_fingerprint == evidence.evidence_fingerprint:
                    _set_warnings(cached, ())
                    self._telemetry(evidence, payload, cached, cache_hit=True, duration_ms=0)
                    return cached
            except (OSError, ValueError, DocumentationSchemaError, json.JSONDecodeError):
                cache_path.unlink(missing_ok=True)

        try:
            argv = self._approved_argv(agent_command)
        except (ValueError, PermissionError):
            proposal = _proposal(evidence, "blocked", ("blocked_unapproved_runner",))
            self._telemetry(evidence, payload, proposal, cache_hit=False, duration_ms=0)
            return proposal

        started = time.perf_counter()
        result = self.runner.run(
            argv, input_text=input_text, timeout_seconds=_TIMEOUT_SECONDS,
            max_output_bytes=_OUTPUT_MAX_BYTES,
        )
        duration_ms = result.duration_ms or round((time.perf_counter() - started) * 1000)
        if result.exit_code == -1 or result.duration_ms >= _TIMEOUT_SECONDS * 1000:
            proposal = _proposal(evidence, "blocked", ("runner_timeout",))
        elif result.exit_code != 0:
            proposal = _proposal(evidence, "blocked", ("runner_nonzero_exit",))
        elif not result.stdout or len(result.stdout.encode("utf-8")) > _OUTPUT_MAX_BYTES:
            proposal = _proposal(evidence, "context_overflow", ("output_over_budget",))
        else:
            proposal = self._parse_result(evidence, result.stdout, required_targets, output_limit)
        if proposal.status in {"ready", "no_documentation_needed"}:
            self._write_cache(cache_path, proposal)
        self._telemetry(evidence, payload, proposal, cache_hit=False, duration_ms=duration_ms)
        return proposal

    def _required_targets(self, payload: dict[str, Any]) -> tuple[str, ...]:
        classification = payload.get("classification")
        if isinstance(classification, dict) and isinstance(classification.get("requiredTargets"), list):
            return tuple(str(value) for value in classification["requiredTargets"])
        changed_paths = tuple(payload.get("changedPaths", ()))
        if changed_paths:
            result = self.classifier.classify(changed_paths, classification or {})
            return tuple(result["requiredTargets"])
        return ("brief_backfill",)

    def _parse_result(
        self,
        evidence: DocumentationEvidence,
        stdout: str,
        required_targets: tuple[str, ...],
        output_limit: int,
    ) -> DocumentationProposal:
        try:
            payload = json.loads(stdout)
            if (
                isinstance(payload, dict)
                and payload.get("schemaVersion") == DOCUMENTATION_PROPOSAL_SCHEMA
                and payload.get("evidenceFingerprint") != evidence.evidence_fingerprint
            ):
                return _proposal(evidence, "invalid_agent_output", ("fingerprint_mismatch",))
            proposal = DocumentationProposal.from_dict(payload)
            if proposal.schema_version != DOCUMENTATION_PROPOSAL_SCHEMA:
                raise DocumentationSchemaError("invalid schema")
            if proposal.evidence_fingerprint != evidence.evidence_fingerprint:
                return _proposal(evidence, "invalid_agent_output", ("fingerprint_mismatch",))
            if any(item["targetKind"] not in required_targets for item in proposal.proposals):
                return _proposal(evidence, "invalid_agent_output", ("unapproved_target",))
            if self._estimate(json.dumps(payload, ensure_ascii=False)) > output_limit:
                return _proposal(evidence, "context_overflow", ("output_over_budget",))
            _set_warnings(proposal, ())
            return proposal
        except (json.JSONDecodeError, TypeError, ValueError, DocumentationSchemaError):
            return _proposal(evidence, "invalid_agent_output", ("invalid_agent_output",))

    def _approved_argv(self, command: str) -> tuple[str, ...]:
        argv = tuple(shlex.split(command))
        if not argv:
            raise ValueError("agent command is required")
        if Path(argv[0]).name not in _ALLOWED_EXECUTABLES:
            raise PermissionError("agent command is not allowlisted")
        return argv

    def _budget(self) -> tuple[int, int]:
        if hasattr(self.runtime, "configured_budget"):
            return self.runtime.configured_budget("documentation")
        return 8_000, 1_500

    @staticmethod
    def _estimate(value: str) -> int:
        return max(1, (len(value.encode("utf-8")) + 3) // 4)

    @staticmethod
    def _write_cache(path: Path, proposal: DocumentationProposal) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(proposal.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)

    def _telemetry(
        self, evidence: DocumentationEvidence, payload: dict, proposal: DocumentationProposal,
        *, cache_hit: bool, duration_ms: int,
    ) -> None:
        record = {
            "schemaVersion": "documentation-telemetry-v1",
            "runId": getattr(evidence, "run_id", evidence.task_id),
            "documentationFingerprint": getattr(
                evidence, "documentation_fingerprint", evidence.evidence_fingerprint,
            ),
            "inputCharacters": len(json.dumps(payload, ensure_ascii=False, sort_keys=True)),
            "estimatedInputTokens": self._estimate(json.dumps(payload, ensure_ascii=False, sort_keys=True)),
            "outputTokens": self._estimate(json.dumps(proposal.to_dict(), ensure_ascii=False)),
            "proposalCount": len(proposal.proposals),
            "cacheHit": cache_hit,
            "durationMs": duration_ms,
            "result": proposal.status,
        }
        self.telemetry_path.parent.mkdir(parents=True, exist_ok=True)
        with self.telemetry_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


__all__ = ["DocumentationAgentService", "DocumentationRunnerResult"]
