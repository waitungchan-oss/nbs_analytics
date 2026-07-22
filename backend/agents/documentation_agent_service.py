from __future__ import annotations

import json
import os
import re
import selectors
import shlex
import subprocess
import time
from collections import deque
from hashlib import sha256
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .agent_runtime import AgentRuntime
from .documentation_codex_runner import CodexDocumentationRunner, DocumentationRunnerResult
from .documentation_models import (
    DOCUMENTATION_DRAFT_SCHEMA,
    DOCUMENTATION_PROPOSAL_SCHEMA,
    DocumentationEvidence as ContractDocumentationEvidence,
    DocumentationDraft,
    DocumentationProposal,
    DocumentationSchemaError,
)
from .documentation_evidence import DocumentationEvidence as CollectorDocumentationEvidence
from .documentation_policy import DocumentationImpactClassifier
from .documentation_validator import DocumentationProposalValidator, DocumentationValidationError
from .workflow_models import canonical_sha256


_OUTPUT_MAX_BYTES = 64 * 1024
_STDERR_TAIL_BYTES = 4 * 1024
_TIMEOUT_SECONDS = 120
_MANAGED_BLOCK_START = "<!-- documentation-agent:implementation-evidence:start -->"
_MANAGED_BLOCK_END = "<!-- documentation-agent:implementation-evidence:end -->"
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


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
        process.stdin.write(input_text.encode("utf-8"))
        process.stdin.close()
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        stdout = bytearray()
        stderr_tail = deque(maxlen=_STDERR_TAIL_BYTES)
        timed_out = False
        deadline = time.monotonic() + timeout_seconds
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                process.kill()
                deadline = time.monotonic() + 1
                remaining = 1
            for key, _ in selector.select(min(remaining, 0.1)):
                chunk = os.read(key.fd, 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if key.data == "stdout":
                    if len(stdout) <= max_output_bytes:
                        stdout.extend(chunk[: max_output_bytes + 1 - len(stdout)])
                    if len(stdout) > max_output_bytes:
                        process.kill()
                else:
                    stderr_tail.extend(chunk[-_STDERR_TAIL_BYTES:])
            if process.poll() is not None and not selector.get_map():
                break
        process.wait()
        duration_ms = round((time.perf_counter() - started) * 1000)
        stderr = bytes(stderr_tail).decode("utf-8", errors="replace")
        if timed_out:
            return DocumentationRunnerResult(
                -1, "", stderr, duration_ms,
            )
        return DocumentationRunnerResult(
            0 if len(stdout) > max_output_bytes else process.returncode,
            bytes(stdout).decode("utf-8", errors="replace"),
            stderr,
            duration_ms,
        )


def _set_warnings(proposal: DocumentationProposal, warnings: tuple[str, ...]) -> DocumentationProposal:
    # Task 1's immutable model intentionally excludes diagnostics from the persisted schema.
    object.__setattr__(proposal, "warnings", warnings)
    return proposal


def _proposal(
    evidence: CollectorDocumentationEvidence,
    status: str,
    warnings: tuple[str, ...] = (),
    proposals: tuple[dict[str, Any], ...] = (),
) -> DocumentationProposal:
    contract_evidence = _contract_evidence(evidence)
    unsigned = {
        "schemaVersion": DOCUMENTATION_PROPOSAL_SCHEMA,
        "taskId": evidence.task_id,
        "generatedAt": evidence.generated_at,
        "evidence": contract_evidence.to_dict(),
        "evidenceFingerprint": evidence.documentation_fingerprint,
        "status": status,
        "proposals": [dict(item) for item in proposals],
    }
    fingerprint = canonical_sha256(unsigned)
    result = DocumentationProposal(
        DOCUMENTATION_PROPOSAL_SCHEMA, evidence.task_id, evidence.generated_at,
        contract_evidence, evidence.documentation_fingerprint, status, proposals, fingerprint,
    )
    return _set_warnings(result, warnings)


class DocumentationAgentService:
    def __init__(self, project_root: Path, *, runner=None, runtime=None) -> None:
        self.project_root = Path(project_root).resolve()
        self.runtime = runtime or AgentRuntime(self.project_root / ".nbs_agent_runtime")
        self.runner = runner or CodexDocumentationRunner(project_root=self.project_root)
        self.classifier = DocumentationImpactClassifier()
        self.cache_root = self.project_root / ".nbs_agent_runtime" / "documentation"
        self.telemetry_path = self.project_root / ".nbs_agent_runtime" / "telemetry" / "documentation.jsonl"

    def draft(
        self,
        evidence: CollectorDocumentationEvidence,
        *,
        agent_command: str | None,
    ) -> DocumentationProposal:
        if not isinstance(evidence, CollectorDocumentationEvidence):
            raise TypeError("documentation evidence must be DocumentationEvidence")
        payload = evidence.to_dict()
        payload["evidenceFingerprint"] = payload.pop("documentationFingerprint")
        payload["sources"] = _safe_sources(evidence.sources)
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

        cache_path = self.cache_root / f"{evidence.documentation_fingerprint}.json"
        if cache_path.is_file():
            try:
                cached = DocumentationProposal.from_dict(json.loads(cache_path.read_text(encoding="utf-8")))
                if (
                    cached.evidence_fingerprint == evidence.documentation_fingerprint
                    and cached.evidence.to_dict() == _contract_evidence(evidence).to_dict()
                ):
                    _set_warnings(cached, ())
                    self._telemetry(evidence, payload, cached, cache_hit=True, duration_ms=0)
                    return cached
                cache_path.unlink(missing_ok=True)
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
        changed_paths = tuple(payload.get("changedPaths", ()))
        classification = payload.get("classification")
        policy_input = {
            key: value for key, value in classification.items()
            if key != "requiredTargets"
        } if isinstance(classification, dict) else {}
        result = self.classifier.classify(changed_paths, policy_input)
        return tuple(result["requiredTargets"])

    def _parse_result(
        self,
        evidence: CollectorDocumentationEvidence,
        stdout: str,
        required_targets: tuple[str, ...],
        output_limit: int,
    ) -> DocumentationProposal:
        try:
            payload = json.loads(stdout)
            if not isinstance(payload, dict):
                return _proposal(evidence, "invalid_agent_output", ("invalid_agent_output",))
            if payload.get("evidenceFingerprint") != evidence.documentation_fingerprint:
                return _proposal(evidence, "invalid_agent_output", ("fingerprint_mismatch",))
            if payload.get("schemaVersion") != DOCUMENTATION_DRAFT_SCHEMA:
                return _proposal(evidence, "invalid_agent_output", ("invalid_agent_output",))
            if self._estimate(json.dumps(payload, ensure_ascii=False)) > output_limit:
                return _proposal(evidence, "context_overflow", ("output_over_budget",))
            draft = DocumentationDraft.from_dict(payload)
            return self._normalize_draft(evidence, draft, required_targets)
        except (json.JSONDecodeError, TypeError, ValueError, DocumentationSchemaError):
            return _proposal(evidence, "invalid_agent_output", ("invalid_agent_output",))

    def _normalize_draft(
        self,
        evidence: CollectorDocumentationEvidence,
        draft: DocumentationDraft,
        required_targets: tuple[str, ...],
    ) -> DocumentationProposal:
        if draft.status == "no_documentation_needed":
            if required_targets:
                return _proposal(evidence, "invalid_agent_output", ("target_set_mismatch",))
            return _proposal(evidence, "no_documentation_needed")
        if draft.status == "blocked":
            return _proposal(evidence, "blocked", ("agent_blocked",))
        if draft.status == "context_overflow":
            return _proposal(evidence, "context_overflow", ("agent_context_overflow",))
        if "adr" in required_targets:
            return _proposal(evidence, "blocked", ("adr_draft_normalization_not_implemented",))

        actual_targets = tuple(item["targetKind"] for item in draft.proposals)
        if len(actual_targets) != len(set(actual_targets)) or set(actual_targets) != set(required_targets):
            return _proposal(evidence, "invalid_agent_output", ("target_set_mismatch",))
        try:
            fragments = {
                item["targetKind"]: self._validate_draft_fragment(item["content"])
                for item in draft.proposals
            }
            proposal_items = []
            for target_kind in required_targets:
                if target_kind == "brief_backfill":
                    proposal_items.append(self._brief_proposal(evidence, fragments[target_kind]))
                elif target_kind == "system_map":
                    proposal_items.append(self._system_map_proposal(evidence, fragments[target_kind]))
                else:
                    return _proposal(evidence, "invalid_agent_output", ("unapproved_target",))
        except DocumentationValidationError:
            return _proposal(evidence, "invalid_agent_output", ("unsafe_draft_fragment",))
        except ValueError as exc:
            if str(exc).startswith("draft fragment"):
                return _proposal(evidence, "invalid_agent_output", ("unsafe_draft_fragment",))
            return _proposal(evidence, "blocked", ("target_normalization_failed",))
        except OSError:
            return _proposal(evidence, "blocked", ("target_normalization_failed",))

        proposal = _proposal(evidence, "ready", proposals=tuple(proposal_items))
        try:
            return DocumentationProposal.from_dict(proposal.to_dict())
        except DocumentationSchemaError:
            return _proposal(evidence, "invalid_agent_output", ("normalized_proposal_invalid",))

    def _validate_draft_fragment(self, content: str) -> str:
        fragment = content.strip()
        if not fragment or "\r" in content:
            raise ValueError("draft fragment is empty or malformed")
        if (
            _MANAGED_BLOCK_START in fragment
            or _MANAGED_BLOCK_END in fragment
            or re.search(r"(?m)^#{1,2}\s", fragment)
        ):
            raise ValueError("draft fragment controls document structure")
        if re.search(r"(?m)(?:^|[\s(`])/(?:private|Users|home|tmp|var)/", fragment):
            raise ValueError("draft fragment contains an absolute path")
        DocumentationProposalValidator._check_content(fragment)
        return fragment

    def _brief_proposal(self, evidence: CollectorDocumentationEvidence, fragment: str) -> dict[str, str]:
        identity = self._brief_identity(evidence)
        safe_task = self._safe_task_id(evidence.task_id)
        content = f"## Documentation Backfill: {safe_task}\n\n{fragment}\n"
        return self._proposal_item("brief_backfill", identity, "update_managed_block", content)

    def _system_map_proposal(self, evidence: CollectorDocumentationEvidence, fragment: str) -> dict[str, str]:
        path = self.project_root / "NBS_ANALYTICS_SYSTEM_MAP.md"
        before = path.read_text(encoding="utf-8")
        heading = "## 2A. Agent Evidence Pipeline"
        matches = list(re.finditer(r"^## 2A\. Agent Evidence Pipeline[ \t]*$", before, re.M))
        if len(matches) != 1:
            raise ValueError("system map controlled section is missing or duplicated")
        start = matches[0].start()
        next_heading = re.search(r"^#{1,2}[ \t]+", before[matches[0].end():], re.M)
        end = matches[0].end() + next_heading.start() if next_heading else len(before)
        section = before[start:end].rstrip()
        safe_task = self._safe_task_id(evidence.task_id)
        content = f"{section}\n\n### Documentation Backfill: {safe_task}\n\n{fragment}\n"
        section_hash = sha256(section.encode("utf-8")).hexdigest()
        identity = f"NBS_ANALYTICS_SYSTEM_MAP.md#2A.%20Agent%20Evidence%20Pipeline|sha256={section_hash}"
        return self._proposal_item("system_map", identity, "replace_section", content)

    def _brief_identity(self, evidence: CollectorDocumentationEvidence) -> str:
        candidates = sorted(
            item["path"] for item in _safe_sources(evidence.sources)
            if item["path"].startswith("docs/briefs/") and item["path"].endswith(".md")
        )
        if len(candidates) != 1:
            raise ValueError("documentation evidence must identify exactly one Brief")
        return candidates[0]

    @staticmethod
    def _safe_task_id(value: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
        return safe or "documentation-backfill"

    @staticmethod
    def _proposal_item(target_kind: str, identity: str, operation: str, content: str) -> dict[str, str]:
        return {
            "targetKind": target_kind,
            "targetIdentity": identity,
            "operation": operation,
            "content": content,
            "contentSha256": sha256(content.encode("utf-8")).hexdigest(),
        }

    def _approved_argv(self, command: str) -> tuple[str, ...]:
        argv = tuple(shlex.split(command))
        if not argv:
            raise ValueError("agent command is required")
        if Path(argv[0]).name not in {"codex"}:
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
                evidence, "documentation_fingerprint", evidence.documentation_fingerprint,
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


def _contract_evidence(evidence: CollectorDocumentationEvidence) -> ContractDocumentationEvidence:
    """Adapt collector evidence to the frozen Task3 proposal evidence contract."""
    return ContractDocumentationEvidence.from_dict({
        "schemaVersion": "documentation-evidence-v1",
        "taskId": evidence.task_id,
        "generatedAt": evidence.generated_at,
        "sources": _safe_sources(evidence.sources),
        "guardrails": dict(evidence.guardrails),
        "evidenceFingerprint": evidence.documentation_fingerprint,
    })


def _safe_sources(sources: tuple[dict[str, str], ...]) -> list[dict[str, str]]:
    return [
        {"path": _safe_source_identity(item["path"]), "sha256": item["sha256"]}
        for item in sources
    ]


def _safe_source_identity(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    posix_path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(value.strip())
    segments = normalized.split("/")
    if (
        not normalized
        or _URI_SCHEME.match(normalized)
        or normalized == "~"
        or normalized.startswith("~/")
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or any(segment in {".", ".."} for segment in segments)
    ):
        return "source"
    return posix_path.as_posix()
