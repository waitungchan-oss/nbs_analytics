from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from backend.agents.agent_runtime import AgentRunner, AgentRuntime, agent_request_fingerprint
from backend.agents.evidence_models import (
    ALLOWED_CONTEXT_STATUSES,
    ALLOWED_REVIEW_STATUSES,
    EvidenceBundle,
    EvidenceItem,
    canonical_fingerprint,
    estimate_tokens,
)
from backend.agents.memory_hub_integration_models import MemoryHubIntegrationEvidence


REVIEW_EVIDENCE_SCHEMA = "review-evidence-v1"
REVIEW_REPORT_SCHEMA = "review-report-v1"
_REPORT_KEYS = {
    "schemaVersion", "verdict", "findings", "requirementCoverage", "testCoverage",
    "baselineRisk", "residualRisk", "hermesRequiredChecks", "reviewFingerprint",
}
_LIST_FIELDS = (
    "findings", "requirementCoverage", "testCoverage", "residualRisk", "hermesRequiredChecks",
)
_FINDING_KEYS = {
    "severity", "file", "line", "rule", "evidence", "impact", "recommendedAction",
}
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_VERDICT_ORDER = {
    "pass": 0,
    "changes_required": 1,
    "blocked": 2,
    "context_overflow": 3,
    "invalid_bundle": 4,
}
_CONTEXT_KEYS = {
    "schemaVersion", "status", "taskUnderstanding", "systemBoundaries", "relevantFiles",
    "dependencies", "recommendedTests", "risks", "unknowns", "contextFingerprint",
}
_CONTEXT_LIST_FIELDS = (
    "taskUnderstanding", "systemBoundaries", "dependencies", "recommendedTests", "risks", "unknowns",
)
_VERIFICATION_KEYS = {"label", "argv", "exitCode", "stdoutTail", "stderrTail"}
_MEMORY_CONTEXT_KEYS = {
    "schemaVersion", "status", "consumerId", "integrationMode", "authority",
    "evidenceFingerprint", "hintCount", "diagnostics",
}


def _validate_task_contract(task: object) -> dict:
    if not isinstance(task, dict):
        raise ValueError("Review task contract must be an object")
    objective = task.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        raise ValueError("Review task objective must be non-empty")
    for field in ("scope", "forbidden"):
        value = task.get(field)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"Review task {field} must be a list of strings")
    return task


def validate_context_summary(summary: object) -> dict:
    if not isinstance(summary, dict) or set(summary) != _CONTEXT_KEYS:
        raise ValueError("Context summary schema is invalid")
    if summary["schemaVersion"] != "context-summary-v1":
        raise ValueError("Context summary schema is invalid")
    if summary["status"] not in ALLOWED_CONTEXT_STATUSES:
        raise ValueError("Context summary status is invalid")
    for field in _CONTEXT_LIST_FIELDS:
        if not isinstance(summary[field], list) or not all(
            isinstance(item, str) for item in summary[field]
        ):
            raise ValueError(f"Context summary field is invalid: {field}")
    if not isinstance(summary["relevantFiles"], list):
        raise ValueError("Context summary relevantFiles is invalid")
    for item in summary["relevantFiles"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "reason", "symbols"}
            or not isinstance(item["path"], str)
            or not isinstance(item["reason"], str)
            or not isinstance(item["symbols"], list)
            or not all(isinstance(symbol, str) for symbol in item["symbols"])
        ):
            raise ValueError("Context summary relevantFiles is invalid")
    if not isinstance(summary["contextFingerprint"], str) or not summary["contextFingerprint"]:
        raise ValueError("Context summary contextFingerprint must be non-empty")
    return summary


def _validate_verification(verification: object) -> list[dict]:
    if not isinstance(verification, list):
        raise ValueError("Review verification must be a list")
    for command in verification:
        if not isinstance(command, dict) or set(command) != _VERIFICATION_KEYS:
            raise ValueError("Review verification command schema is invalid")
        if not isinstance(command["label"], str) or not command["label"]:
            raise ValueError("Review verification label is invalid")
        if not isinstance(command["argv"], list) or not command["argv"] or not all(
            isinstance(argument, str) and argument for argument in command["argv"]
        ):
            raise ValueError("Review verification argv is invalid")
        if not isinstance(command["exitCode"], int) or isinstance(command["exitCode"], bool):
            raise ValueError("Review verification exitCode is invalid")
        if not isinstance(command["stdoutTail"], str) or not isinstance(command["stderrTail"], str):
            raise ValueError("Review verification output tails are invalid")
    return verification


def _runtime_instructions(instructions: str, *, strict: bool) -> str:
    return (
        f"{instructions}\n\n[review-runtime]\n"
        f"strict={str(strict).lower()}\n"
        "reviewFingerprint must equal the top-level payload.bundleFingerprint exactly; "
        "copy that value verbatim and do not recompute, replace, or omit it."
    )


def split_review_bundle_by_file(
    bundle: EvidenceBundle,
    *,
    patch_token_budget: int,
) -> tuple[EvidenceBundle, ...]:
    if patch_token_budget <= 0:
        raise ValueError("Review patch token budget must be positive")
    patches = [item for item in bundle.evidence if item.kind == "diff"]
    if not patches:
        return (bundle,)
    groups: list[list[EvidenceItem]] = []
    current: list[EvidenceItem] = []
    current_tokens = 0
    for patch in patches:
        patch_tokens = estimate_tokens(patch.content)
        if current and current_tokens + patch_tokens > patch_token_budget:
            groups.append(current)
            current = []
            current_tokens = 0
        current.append(patch)
        current_tokens += patch_tokens
    if current:
        groups.append(current)
    diff_sources = {patch.source for patch in patches}
    dirty_files = bundle.repository.get("dirtyFiles")
    batches: list[EvidenceBundle] = []
    for group in groups:
        repository = dict(bundle.repository)
        if isinstance(dirty_files, list) and all(isinstance(item, str) for item in dirty_files):
            group_sources = {patch.source for patch in group}
            repository["dirtyFiles"] = [
                item for item in dirty_files
                if item in group_sources or item not in diff_sources
            ]
        batches.append(EvidenceBundle(
            schema_version=bundle.schema_version,
            task=bundle.task,
            repository=repository,
            guardrails=bundle.guardrails,
            evidence=tuple(group),
            commands=bundle.commands,
        ))
    return tuple(batches)


def build_review_evidence_payload(
    bundle: EvidenceBundle,
    *,
    context_summary: dict,
    verification: list[dict],
    memory_hub_context: dict | None = None,
) -> dict:
    if bundle.schema_version != REVIEW_EVIDENCE_SCHEMA:
        raise ValueError("Unexpected Review evidence schema")
    _validate_task_contract(bundle.task)
    if context_summary:
        validate_context_summary(context_summary)
    _validate_verification(verification)
    patches = [item for item in bundle.evidence if item.kind == "diff"]
    head_ref = bundle.repository.get("headRef")
    git_diff = {
        "base": bundle.repository.get("baseSha"),
        "head": head_ref if head_ref == "WORKTREE" else bundle.repository.get("headSha"),
        "files": [item.source for item in patches],
        "patches": [item.to_dict() for item in patches],
        "truncated": bool(bundle.repository.get("diffFileLimitExceeded"))
        or any(bool(item.metadata.get("truncated")) for item in patches),
    }
    git_diff["diffFingerprint"] = canonical_fingerprint(git_diff)
    unsigned = {
        "schemaVersion": REVIEW_EVIDENCE_SCHEMA,
        "taskContract": bundle.task,
        "contextSummary": context_summary,
        "gitDiff": git_diff,
        "verification": {"commands": verification},
    }
    if memory_hub_context is not None:
        unsigned["memoryHubContext"] = memory_hub_context
    return {**unsigned, "bundleFingerprint": canonical_fingerprint(unsigned)}


def _memory_hub_observation(payload: object) -> dict:
    base = {
        "schemaVersion": "memory-hub-agent-observation-v1",
        "status": "ignored",
        "consumerId": "context-agent",
        "integrationMode": "gated_context",
        "authority": "non_authoritative_memory",
        "evidenceFingerprint": None,
        "hintCount": 0,
        "diagnostics": [],
    }
    try:
        evidence = MemoryHubIntegrationEvidence.from_dict(payload)
    except (TypeError, ValueError):
        base["diagnostics"] = ["invalid_evidence"]
        return base
    base["evidenceFingerprint"] = evidence.evidence_fingerprint
    if evidence.consumer_id != "context-agent":
        base["diagnostics"] = ["consumer_mismatch"]
        return base
    if evidence.status != "ready":
        base["diagnostics"] = ["evidence_not_ready"]
        return base
    if evidence.integration_mode != "direct_query":
        base["diagnostics"] = ["integration_mode_mismatch"]
        return base
    base["status"] = "ready"
    base["hintCount"] = evidence.hint_count
    return base


def _attach_memory_observation(report: dict, observation: dict | None) -> dict:
    if observation is None:
        return report
    return {**report, "memoryHubContext": observation}


def _report(
    verdict: str,
    fingerprint: str,
    *,
    findings: list[dict] | None = None,
    requirement_coverage: list[Any] | None = None,
    test_coverage: list[Any] | None = None,
    baseline_risk: str = "none",
    residual_risk: list[Any] | None = None,
    hermes_checks: list[Any] | None = None,
) -> dict:
    return {
        "schemaVersion": REVIEW_REPORT_SCHEMA,
        "verdict": verdict,
        "findings": findings or [],
        "requirementCoverage": requirement_coverage or [],
        "testCoverage": test_coverage or [],
        "baselineRisk": baseline_risk,
        "residualRisk": residual_risk or [],
        "hermesRequiredChecks": hermes_checks or [],
        "reviewFingerprint": fingerprint,
    }


def _runtime_path(project_root: Path, runtime_root: Path) -> Path:
    expected_lexical = Path(os.path.abspath(os.fspath(project_root))) / ".nbs_agent_runtime"
    raw_lexical = Path(os.path.abspath(os.fspath(runtime_root)))
    if raw_lexical != expected_lexical:
        raise PermissionError(
            f"Agent runtime root must be the project runtime {expected_lexical}: {raw_lexical}"
        )
    if expected_lexical.is_symlink():
        raise PermissionError("Agent runtime root cannot be a symlink")
    expected = expected_lexical.resolve()
    resolved = raw_lexical.resolve()
    if resolved != expected:
        raise PermissionError(
            f"Agent runtime root must resolve to the project runtime {expected}: {resolved}"
        )
    return resolved


def _validate_report(result: object, expected_fingerprint: str, *, strict: bool) -> dict:
    if not isinstance(result, dict) or set(result) != _REPORT_KEYS:
        raise ValueError("Review Agent output schema is invalid")
    if result["schemaVersion"] != REVIEW_REPORT_SCHEMA:
        raise ValueError("Review Agent output schema is invalid")
    if result["verdict"] not in ALLOWED_REVIEW_STATUSES:
        raise ValueError("Review Agent verdict is invalid")
    for field in _LIST_FIELDS:
        if not isinstance(result[field], list):
            raise ValueError(f"Review Agent output field is not a list: {field}")
    for field in ("requirementCoverage", "testCoverage", "residualRisk", "hermesRequiredChecks"):
        if not all(isinstance(item, str) for item in result[field]):
            raise ValueError(f"Review Agent output field contains invalid values: {field}")
    if not isinstance(result["baselineRisk"], str):
        raise ValueError("Review Agent baselineRisk must be a string")
    if result["reviewFingerprint"] != expected_fingerprint:
        raise ValueError("Review Agent review fingerprint does not match")
    for finding in result["findings"]:
        if not isinstance(finding, dict) or set(finding) != _FINDING_KEYS:
            raise ValueError("Review Agent finding schema is invalid")
        if finding["severity"] not in _SEVERITY_ORDER:
            raise ValueError("Review Agent finding severity is invalid")
        if not isinstance(finding["file"], str) or not finding["file"]:
            raise ValueError("Review Agent finding file is invalid")
        if not isinstance(finding["line"], int) or isinstance(finding["line"], bool) or finding["line"] < 1:
            raise ValueError("Review Agent finding line is invalid")
        for field in ("rule", "evidence", "impact", "recommendedAction"):
            if not isinstance(finding[field], str) or not finding[field]:
                raise ValueError(f"Review Agent finding field is invalid: {field}")
    if result["verdict"] == "pass":
        if result["findings"]:
            raise ValueError("Review Agent pass cannot contain findings")
        if strict:
            for field in (
                "requirementCoverage", "testCoverage", "residualRisk", "hermesRequiredChecks",
            ):
                if not result[field]:
                    raise ValueError(f"Strict Review Agent pass requires non-empty {field}")
    return result


def build_review_report(
    bundle: EvidenceBundle,
    *,
    project_root: Path,
    context_summary: dict,
    verification: list[dict],
    runner: AgentRunner | None,
    runtime_root: Path,
    instructions: str,
    strict: bool = True,
    input_token_limit: int = 16000,
    output_token_limit: int = 2000,
    memory_hub_evidence: dict | None = None,
) -> dict:
    if input_token_limit <= 0 or output_token_limit <= 0:
        raise ValueError("Review Agent token budgets must be positive")
    validated_runtime_root = _runtime_path(project_root, runtime_root)
    evidence_payload = build_review_evidence_payload(
        bundle, context_summary=context_summary, verification=verification,
        memory_hub_context=(
            _memory_hub_observation(memory_hub_evidence)
            if memory_hub_evidence is not None else None
        ),
    )
    memory_observation = (
        _memory_hub_observation(memory_hub_evidence)
        if memory_hub_evidence is not None else None
    )
    finish = lambda report: _attach_memory_observation(report, memory_observation)
    if strict:
        validate_context_summary(context_summary)
    runtime_instructions = _runtime_instructions(instructions, strict=strict)
    review_fingerprint = agent_request_fingerprint(
        bundle,
        instructions=runtime_instructions,
        output_schema=REVIEW_REPORT_SCHEMA,
        evidence_payload=evidence_payload,
    )
    if strict and context_summary.get("status") != "ready":
        return finish(_report(
            "blocked",
            review_fingerprint,
            residual_risk=["Strict review requires a ready Context Agent summary."],
        ))
    dirty_files = bundle.repository.get("dirtyFiles") or []
    if not isinstance(dirty_files, list) or not all(isinstance(item, str) for item in dirty_files):
        raise ValueError("Review repository dirtyFiles must be a list of strings")
    unattributed_dirty = sorted(set(dirty_files) - set(evidence_payload["gitDiff"]["files"]))
    if strict and unattributed_dirty:
        return finish(_report(
            "blocked",
            review_fingerprint,
            residual_risk=[f"Strict review has unattributed dirty files: {', '.join(unattributed_dirty)}"],
        ))
    if strict and not verification:
        return finish(_report(
            "blocked",
            review_fingerprint,
            residual_risk=["Strict review requires verification evidence."],
        ))
    if strict and any(item["exitCode"] != 0 for item in verification):
        return finish(_report(
            "changes_required",
            review_fingerprint,
            test_coverage=verification,
            residual_risk=["At least one verification command failed."],
        ))
    if evidence_payload["gitDiff"]["truncated"]:
        return finish(_report(
            "context_overflow",
            review_fingerprint,
            residual_risk=["Review diff was truncated; split the task or lower the diff scope."],
        ))
    request_text = json.dumps(
        {"instructions": runtime_instructions, "evidence": evidence_payload},
        ensure_ascii=False,
        sort_keys=True,
    )
    if estimate_tokens(request_text) > input_token_limit:
        return finish(_report(
            "context_overflow",
            review_fingerprint,
            residual_risk=["Collector must split or reduce Review evidence."],
        ))
    if runner is None:
        return finish(_report(
            "blocked",
            review_fingerprint,
            residual_risk=["No AgentRunner was configured; use --collect-only or --agent-command."],
        ))
    result = AgentRuntime(
        validated_runtime_root,
        input_token_limit=input_token_limit,
        output_token_limit=output_token_limit,
    ).run(
        "review",
        bundle,
        runner,
        output_schema=REVIEW_REPORT_SCHEMA,
        instructions=runtime_instructions,
        evidence_payload=evidence_payload,
        output_validator=lambda value: _validate_report(
            value, review_fingerprint, strict=strict,
        ),
    )
    if result.get("status") == "context_overflow":
        return _report(
            "context_overflow",
            review_fingerprint,
            residual_risk=["Collector must split or reduce Review evidence."],
        )
    if estimate_tokens(json.dumps(result, ensure_ascii=False)) > output_token_limit:
        raise ValueError("Review Agent output token budget exceeded")
    return finish(result)


def _extend_unique(target: list[Any], values: list[Any]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def merge_review_batches(
    reports: list[dict],
    *,
    fingerprint: str,
    output_token_limit: int | None = None,
) -> dict:
    if not reports:
        raise ValueError("Review batch reports cannot be empty")
    verdicts = [
        value if value in _VERDICT_ORDER else "invalid_bundle"
        for value in (report.get("verdict") for report in reports)
    ]
    verdict = max(verdicts, key=_VERDICT_ORDER.__getitem__)
    findings_by_fingerprint: dict[str, dict] = {}
    requirement_coverage: list[Any] = []
    test_coverage: list[Any] = []
    residual_risk: list[Any] = []
    hermes_checks: list[Any] = []
    baseline_risks: list[str] = []
    for report in reports:
        for finding in report.get("findings", []):
            findings_by_fingerprint.setdefault(canonical_fingerprint(finding), finding)
        _extend_unique(requirement_coverage, report.get("requirementCoverage", []))
        _extend_unique(test_coverage, report.get("testCoverage", []))
        _extend_unique(residual_risk, report.get("residualRisk", []))
        _extend_unique(hermes_checks, report.get("hermesRequiredChecks", []))
        risk = report.get("baselineRisk")
        if isinstance(risk, str) and risk != "none" and risk not in baseline_risks:
            baseline_risks.append(risk)
    findings = sorted(
        findings_by_fingerprint.values(),
        key=lambda item: (_SEVERITY_ORDER.get(item.get("severity"), 99), item.get("file", ""), item.get("line", 0)),
    )
    merged = _report(
        verdict,
        fingerprint,
        findings=findings,
        requirement_coverage=requirement_coverage,
        test_coverage=test_coverage,
        baseline_risk="; ".join(baseline_risks) or "none",
        residual_risk=residual_risk,
        hermes_checks=hermes_checks,
    )
    if output_token_limit is None:
        return merged
    if output_token_limit <= 0:
        raise ValueError("Review merged output token budget must be positive")
    if estimate_tokens(json.dumps(merged, ensure_ascii=False)) <= output_token_limit:
        return merged
    high_risk_findings = [
        finding for finding in findings if finding.get("severity") in {"critical", "high"}
    ]
    return _report(
        "context_overflow",
        fingerprint,
        findings=high_risk_findings,
        baseline_risk=merged["baselineRisk"],
        residual_risk=["Merged Review Agent output exceeded the output token budget."],
        hermes_checks=hermes_checks,
    )


def run_review_batches(
    bundle: EvidenceBundle,
    *,
    project_root: Path,
    context_summary: dict,
    verification: list[dict],
    runner: AgentRunner | None,
    runtime_root: Path,
    instructions: str,
    strict: bool = True,
    input_token_limit: int = 16000,
    output_token_limit: int = 2000,
    memory_hub_evidence: dict | None = None,
) -> dict:
    full_payload = build_review_evidence_payload(
        bundle, context_summary=context_summary, verification=verification,
        memory_hub_context=(
            _memory_hub_observation(memory_hub_evidence)
            if memory_hub_evidence is not None else None
        ),
    )
    runtime_root = _runtime_path(project_root, runtime_root)
    runtime_instructions = _runtime_instructions(instructions, strict=strict)
    fingerprint = agent_request_fingerprint(
        bundle,
        instructions=runtime_instructions,
        output_schema=REVIEW_REPORT_SCHEMA,
        evidence_payload=full_payload,
    )
    batches = split_review_bundle_by_file(
        bundle, patch_token_budget=max(1000, input_token_limit // 2),
    )
    reports = [
        build_review_report(
            batch,
            project_root=project_root,
            context_summary=context_summary,
            verification=verification,
            runner=runner,
            runtime_root=runtime_root,
            instructions=instructions,
            strict=strict,
            input_token_limit=input_token_limit,
            output_token_limit=output_token_limit,
            memory_hub_evidence=memory_hub_evidence,
        )
        for batch in batches
    ]
    merged = merge_review_batches(
        reports, fingerprint=fingerprint, output_token_limit=output_token_limit,
    )
    observation = (
        _memory_hub_observation(memory_hub_evidence)
        if memory_hub_evidence is not None else None
    )
    return _attach_memory_observation(merged, observation)


def _markdown_list(values: list[Any]) -> str:
    return "\n".join(f"- {value}" for value in values) or "- None"


def format_review_markdown(report: dict) -> str:
    lines = ["# Review Agent Report", "", f"Verdict: `{report.get('verdict', 'unknown')}`", ""]
    findings = [
        f"[{item.get('severity')}] {item.get('file')}:{item.get('line')} {item.get('evidence')}"
        for item in report.get("findings", [])
    ]
    for title, values in (
        ("Findings", findings),
        ("Requirement Coverage", report.get("requirementCoverage", [])),
        ("Test Coverage", report.get("testCoverage", [])),
        ("Residual Risk", report.get("residualRisk", [])),
        ("Hermes Required Checks", report.get("hermesRequiredChecks", [])),
    ):
        lines.extend([f"## {title}", _markdown_list(values), ""])
    return "\n".join(lines)
