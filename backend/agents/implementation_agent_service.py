from __future__ import annotations

import os
import stat
import json
from contextlib import contextmanager
from pathlib import Path
import subprocess
from time import perf_counter
from typing import Any, Callable, Protocol
from uuid import uuid4

from backend.agents.agent_runtime import resolve_implementation_runtime_path
from backend.agents.evidence_collector import EvidenceCollector
from backend.agents.evidence_models import EvidenceBundle, EvidenceItem, estimate_tokens
from backend.agents.implementation_guard import (
    GuardDecision,
    WorktreeState,
    capture_worktree_state,
    validate_changes,
    validate_preconditions,
)
from backend.agents.implementation_models import (
    ImplementationRunReport,
    ImplementationTaskContract,
    ValidationResult,
    load_implementation_policy,
)
from backend.agents.validation_runner import CommandRejected, ValidationRunner


_REQUEST_SCHEMA = "implementation-request-v1"
_RESPONSE_SCHEMA = "implementation-response-v1"
_REPORT_SCHEMA = "implementation-run-report-v1"
_RESPONSE_KEYS = {
    "schemaVersion", "status", "summary", "requestedValidationCommandIds",
}
_RESPONSE_STATUSES = {"completed", "needs_repair"}


class ApprovedAgentCommand(Protocol):
    def __call__(self, request: dict[str, Any]) -> object: ...


class ImplementationAgentService:
    """Executes one explicitly approved implementation task inside its worktree."""

    def __init__(
        self,
        project_root: Path,
        *,
        validation_runner: ValidationRunner | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.validation_runner = validation_runner or ValidationRunner(self.project_root)

    def collect(self, contract: ImplementationTaskContract) -> EvidenceBundle:
        """Collect fresh, compact context; implementation runs never reuse write evidence."""
        validated = self._validated_contract(contract)
        collector = EvidenceCollector(self.project_root)
        include_paths = tuple(
            self.project_root / path
            for path in validated.allowed_write_paths
            if (self.project_root / path).is_file()
        )
        bundle = collector.collect_context(
            self.project_root / "AGENTS.md",
            base_ref=validated.approved_base_sha,
            include_paths=include_paths,
            queries=(validated.task_id, *validated.allowed_write_paths),
        )
        plan_evidence = self._approved_plan_evidence(
            validated,
            max_lines=collector.policy.max_file_lines,
        )
        return EvidenceBundle(
            schema_version=bundle.schema_version,
            task={
                "id": validated.task_id,
                "objective": validated.objective,
                "scope": list(validated.allowed_write_paths),
                "forbidden": list(validated.risk_surfaces),
            },
            repository=bundle.repository,
            guardrails=bundle.guardrails,
            evidence=(*bundle.evidence, plan_evidence),
            commands=bundle.commands,
        )

    def execute(
        self,
        contract: ImplementationTaskContract,
        agent_command: ApprovedAgentCommand,
    ) -> ImplementationRunReport:
        started = perf_counter()
        try:
            validated = self._validated_contract(contract)
        except (TypeError, ValueError) as exc:
            return self._finish(
                contract if isinstance(contract, ImplementationTaskContract) else None,
                status="blocked_invalid_contract",
                finding=self._finding("invalid_contract", str(exc)),
                started=started,
            )

        risk = self._risk_decision(validated)
        if risk is not None:
            return self._finish(validated, status="blocked_high_risk", finding=risk, started=started)

        precondition = validate_preconditions(self.project_root, validated)
        if precondition.status != "allowed":
            return self._finish(
                validated,
                status=precondition.status,
                finding=self._decision_finding(precondition),
                started=started,
            )

        before = capture_worktree_state(self.project_root)
        try:
            bundle = self.collect(validated)
        except (OSError, PermissionError, ValueError) as exc:
            return self._finish(
                validated,
                status="context_overflow",
                finding=self._finding("context_collection", str(exc)),
                before=before,
                started=started,
            )
        if bundle.repository.get("contextOverflow"):
            return self._finish(
                validated,
                status="context_overflow",
                finding=self._finding("context_overflow", "compact context exceeds its token budget"),
                before=before,
                started=started,
            )

        green_evidence: list[ValidationResult] = []
        repair: dict[str, Any] | None = None
        max_repairs = min(
            validated.max_repair_loops,
            int(load_implementation_policy(self.project_root)["limits"]["maxRepairLoops"]),
        )
        for attempt in range(max_repairs + 1):
            request = self._request(validated, bundle, repair=repair)
            if estimate_tokens(json.dumps(request, ensure_ascii=False, sort_keys=True)) > self._implementation_budget("inputTokens"):
                return self._finish(
                    validated,
                    status="context_overflow",
                    finding=self._finding("request_token_limit", "implementation request exceeds its token budget"),
                    before=before,
                    green_evidence=green_evidence,
                    started=started,
                )
            try:
                with self._protect_git_index():
                    response = agent_command(request)
            except Exception as exc:
                decision = self._post_write_decision(validated, before)
                if decision.status != "allowed":
                    return self._finish(
                        validated, status=decision.status, finding=self._decision_finding(decision),
                        before=before, green_evidence=green_evidence, started=started,
                    )
                return self._finish(
                    validated, status="runtime_error", finding=self._finding("runner_error", str(exc)),
                    before=before, green_evidence=green_evidence, started=started,
                )

            decision = self._post_write_decision(validated, before)
            if decision.status != "allowed":
                return self._finish(
                    validated, status=decision.status, finding=self._decision_finding(decision),
                    before=before, changed_files=decision.changed_files,
                    diff_lines=decision.diff_lines, green_evidence=green_evidence, started=started,
                )

            try:
                parsed = self._validated_response(response, validated)
            except ValueError as exc:
                return self._finish(
                    validated, status="invalid_agent_output",
                    finding=self._finding("invalid_agent_output", str(exc)), before=before,
                    changed_files=decision.changed_files, diff_lines=decision.diff_lines,
                    green_evidence=green_evidence, started=started,
                )

            if parsed["status"] == "needs_repair":
                if attempt == max_repairs:
                    return self._finish(
                        validated, status="changes_required",
                        finding=self._finding("repair_limit", parsed["summary"]), before=before,
                        changed_files=decision.changed_files, diff_lines=decision.diff_lines,
                        green_evidence=green_evidence, started=started,
                    )
                repair = {"reason": parsed["summary"], "validation": []}
                continue

            results = self._run_validations(validated)
            green_evidence.extend(results)
            failures = [result for result in results if result.exit_code != 0 or result.timed_out]
            if not failures:
                final = self._post_write_decision(validated, before)
                if final.status != "allowed":
                    return self._finish(
                        validated, status=final.status, finding=self._decision_finding(final),
                        before=before, changed_files=final.changed_files,
                        diff_lines=final.diff_lines, green_evidence=green_evidence, started=started,
                    )
                return self._finish(
                    validated, status="completed", before=before, changed_files=final.changed_files,
                    diff_lines=final.diff_lines, green_evidence=green_evidence, started=started,
                )
            if attempt == max_repairs:
                return self._finish(
                    validated, status="validation_failed",
                    finding=self._finding("validation_failed", "approved validation command failed"),
                    before=before, changed_files=decision.changed_files,
                    diff_lines=decision.diff_lines, green_evidence=green_evidence, started=started,
                )
            repair = {
                "reason": "approved validation command failed",
                "validation": [result.to_dict() for result in failures],
            }

        raise AssertionError("repair loop exhausted without returning")

    @contextmanager
    def _protect_git_index(self):
        index_path = self._git_index_path()
        lock_path = index_path.with_name(f"{index_path.name}.lock")
        if lock_path.exists() or lock_path.is_symlink():
            raise RuntimeError("Git index.lock already exists")

        metadata = index_path.stat()
        lock_path.mkdir()
        try:
            os.chmod(index_path, stat.S_IMODE(metadata.st_mode) & ~0o222)
            yield
        finally:
            try:
                os.rmdir(lock_path)
            finally:
                os.chmod(index_path, stat.S_IMODE(metadata.st_mode))
                os.utime(index_path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))

    def _git_index_path(self) -> Path:
        completed = subprocess.run(
            ["git", "rev-parse", "--git-path", "index"],
            cwd=self.project_root,
            text=True,
            capture_output=True,
            check=True,
            shell=False,
        )
        candidate = Path(completed.stdout.strip())
        return candidate if candidate.is_absolute() else self.project_root / candidate

    def _validated_contract(self, contract: ImplementationTaskContract) -> ImplementationTaskContract:
        if not isinstance(contract, ImplementationTaskContract):
            raise TypeError("contract must be an ImplementationTaskContract")
        return ImplementationTaskContract.from_dict(contract.to_dict())

    def _risk_decision(self, contract: ImplementationTaskContract) -> dict[str, Any] | None:
        denied = set(load_implementation_policy(self.project_root)["deniedRiskSurfaces"])
        blocked = sorted(set(contract.risk_surfaces) & denied)
        if blocked:
            return self._finding("high_risk_surface", "risk surface requires Codex handoff", surfaces=blocked)
        return None

    def _approved_plan_evidence(
        self,
        contract: ImplementationTaskContract,
        *,
        max_lines: int,
    ) -> EvidenceItem:
        raw_path = Path(contract.plan_path)
        if raw_path.is_absolute() or ".." in raw_path.parts:
            raise PermissionError("approved plan path must stay under project root")
        candidate = self.project_root / raw_path
        if candidate.is_symlink() or not candidate.is_file() or candidate.suffix != ".md":
            raise PermissionError("approved plan must be a non-symlink Markdown file")
        resolved = candidate.resolve()
        try:
            relative = resolved.relative_to(self.project_root)
        except ValueError as exc:
            raise PermissionError("approved plan path must stay under project root") from exc
        if not relative.parts or relative.parts[0] not in {".superpowers", "docs"}:
            raise PermissionError("approved plan must be stored under .superpowers or docs")
        lines = resolved.read_text(encoding="utf-8").splitlines()
        selected = lines[:max_lines]
        return EvidenceItem(
            kind="document",
            source=relative.as_posix(),
            content="\n".join(selected),
            metadata={"lineCount": len(lines), "truncated": len(lines) > len(selected)},
        )

    def _request(
        self,
        contract: ImplementationTaskContract,
        bundle: EvidenceBundle,
        *,
        repair: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "schemaVersion": _REQUEST_SCHEMA,
            "contractFingerprint": contract.fingerprint,
            "task": contract.to_dict(),
            "context": bundle.to_dict(),
            "validationCommandIds": list(contract.validation_commands),
            "repair": repair,
        }

    def _validated_response(
        self,
        response: object,
        contract: ImplementationTaskContract,
    ) -> dict[str, Any]:
        if not isinstance(response, dict) or set(response) != _RESPONSE_KEYS:
            raise ValueError("agent response schema keys are invalid")
        if response.get("schemaVersion") != _RESPONSE_SCHEMA:
            raise ValueError("agent response schemaVersion is invalid")
        if response.get("status") not in _RESPONSE_STATUSES:
            raise ValueError("agent response status is invalid")
        if not isinstance(response.get("summary"), str) or not response["summary"].strip():
            raise ValueError("agent response summary is invalid")
        requested = response.get("requestedValidationCommandIds")
        if not isinstance(requested, list) or not all(isinstance(item, str) for item in requested):
            raise ValueError("agent response requested validation commands are invalid")
        if len(set(requested)) != len(requested) or tuple(requested) != contract.validation_commands:
            raise ValueError("agent response cannot change approved validation commands")
        if estimate_tokens(json.dumps(response, ensure_ascii=False, sort_keys=True)) > self._implementation_budget("outputTokens"):
            raise ValueError("agent response exceeds output token budget")
        return response

    def _run_validations(self, contract: ImplementationTaskContract) -> list[ValidationResult]:
        results: list[ValidationResult] = []
        for command_id in contract.validation_commands:
            try:
                results.append(self.validation_runner.run(command_id, self._validation_arguments(command_id, contract)))
            except CommandRejected as exc:
                results.append(
                    ValidationResult(
                        command_id=command_id,
                        argv=(),
                        exit_code=1,
                        stdout="",
                        stderr=str(exc),
                        duration_ms=0,
                    )
                )
        return results

    @staticmethod
    def _validation_arguments(
        command_id: str,
        contract: ImplementationTaskContract,
    ) -> tuple[str, ...]:
        if command_id == "pytest_targeted":
            return tuple(path for path in contract.allowed_write_paths if path.startswith("tests/"))
        if command_id == "py_compile":
            return tuple(path for path in contract.allowed_write_paths if path.endswith(".py"))
        return ()

    def _post_write_decision(
        self,
        contract: ImplementationTaskContract,
        before: WorktreeState,
    ) -> GuardDecision:
        decision = validate_changes(self.project_root, contract, before)
        if decision.status != "allowed":
            return decision
        after = capture_worktree_state(self.project_root)
        if after.head != before.head:
            return GuardDecision(
                "blocked_scope", changed_files=decision.changed_files, diff_lines=decision.diff_lines,
                reason="runner changed Git HEAD",
            )
        return decision

    def _finish(
        self,
        contract: ImplementationTaskContract | None,
        *,
        status: str,
        finding: dict[str, Any] | None = None,
        before: WorktreeState | None = None,
        changed_files: tuple[str, ...] = (),
        diff_lines: int = 0,
        green_evidence: list[ValidationResult] | None = None,
        started: float,
    ) -> ImplementationRunReport:
        current = self._current_state()
        task_id = contract.task_id if contract is not None else "unknown"
        report = ImplementationRunReport(
            schema_version=_REPORT_SCHEMA,
            status=status,
            task_id=task_id,
            contract_fingerprint=contract.fingerprint if contract is not None else "",
            start_head=before.head if before is not None else current.head,
            end_head=current.head,
            changed_files=changed_files,
            diff_stat={"files": len(changed_files), "lines": diff_lines},
            red_evidence=(),
            green_evidence=tuple(green_evidence or ()),
            findings=tuple(() if finding is None else (finding,)),
        )
        self._write_runtime_records(report, started)
        return report

    def _current_state(self) -> WorktreeState:
        try:
            return capture_worktree_state(self.project_root)
        except ValueError:
            return WorktreeState(
                head="", changes={}, diff_lines=0,
                index_fingerprint="", tree_fingerprint="",
            )

    def _write_runtime_records(self, report: ImplementationRunReport, started: float) -> None:
        try:
            report_path = resolve_implementation_runtime_path(
                self.project_root, f"reports/{report.contract_fingerprint or uuid4().hex}.json",
            )
            report_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            telemetry_path = resolve_implementation_runtime_path(self.project_root, "telemetry.jsonl")
            telemetry = {
                "runId": uuid4().hex,
                "agent": "implementation",
                "contractFingerprint": report.contract_fingerprint,
                "taskId": report.task_id,
                "changedFiles": len(report.changed_files),
                "diffLines": report.diff_stat["lines"],
                "validationCommands": len(report.green_evidence),
                "durationMs": round((perf_counter() - started) * 1000, 3),
                "result": report.status,
            }
            with telemetry_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(telemetry, ensure_ascii=False, separators=(",", ":")) + "\n")
        except (OSError, PermissionError):
            # A runtime artifact failure must not hide the deterministic execution result.
            return

    def _implementation_budget(self, key: str) -> int:
        return int(
            json.loads((self.project_root / "agent_config/token_budgets.json").read_text(encoding="utf-8"))
            ["implementation"][key]
        )

    @staticmethod
    def _finding(rule: str, message: str, **extra: Any) -> dict[str, Any]:
        return {"rule": rule, "message": message, **extra}

    def _decision_finding(self, decision: GuardDecision) -> dict[str, Any]:
        return self._finding(
            decision.status,
            decision.reason or decision.status,
            paths=list(decision.changed_files),
            diffLines=decision.diff_lines,
            indexFingerprintChanged=decision.index_fingerprint_changed,
            treeFingerprintChanged=decision.tree_fingerprint_changed,
        )
