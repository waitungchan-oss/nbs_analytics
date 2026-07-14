from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.agents.agent_runtime import AgentRunner, AgentRuntime, agent_request_fingerprint
from backend.agents.evidence_models import (
    ALLOWED_CONTEXT_STATUSES,
    EvidenceBundle,
    EvidenceItem,
    canonical_fingerprint,
    estimate_tokens,
)


CONTEXT_EVIDENCE_SCHEMA = "context-evidence-v1"
CONTEXT_SUMMARY_SCHEMA = "context-summary-v1"
_PUBLIC_KEYS = {
    "schemaVersion", "task", "repository", "guardrails", "documents", "symbols",
    "relatedTests", "recentChanges", "bundleFingerprint",
}
_REPORT_KEYS = {
    "schemaVersion", "status", "taskUnderstanding", "systemBoundaries", "relevantFiles",
    "dependencies", "recommendedTests", "risks", "unknowns", "contextFingerprint",
}
_REPORT_LIST_FIELDS = (
    "taskUnderstanding", "systemBoundaries", "dependencies", "recommendedTests", "risks", "unknowns",
)


def _append_unique(values: list[dict], candidate: dict) -> None:
    if candidate not in values:
        values.append(candidate)


def _semantic_symbol(item: EvidenceItem) -> dict:
    try:
        candidate = json.loads(item.content)
    except (TypeError, json.JSONDecodeError):
        candidate = {"queryId": item.source, "paths": item.content.splitlines()}
    if isinstance(candidate, dict) and isinstance(candidate.get("queryId"), str) and isinstance(candidate.get("paths"), list) and all(isinstance(path, str) for path in candidate["paths"]):
        return {"queryId": candidate["queryId"], "paths": candidate["paths"]}
    return {"queryId": item.source, "paths": item.content.splitlines()}


def build_context_evidence_payload(bundle: EvidenceBundle) -> dict:
    symbols: list[dict] = []
    for item in bundle.commands:
        if item.label.startswith("rg-query-"):
            _append_unique(symbols, {"queryId": item.label, "paths": item.stdout.splitlines()})
    for item in bundle.evidence:
        if item.kind == "symbol":
            _append_unique(symbols, _semantic_symbol(item))
    recent_changes: list[dict] = []
    for item in bundle.commands:
        if item.label == "git-log":
            for line in item.stdout.splitlines():
                _append_unique(recent_changes, {"summary": line})
    for item in bundle.evidence:
        if item.kind == "recent_change":
            _append_unique(recent_changes, {"summary": item.content})
    unsigned = {
        "schemaVersion": CONTEXT_EVIDENCE_SCHEMA,
        "task": bundle.task,
        "repository": bundle.repository,
        "guardrails": bundle.guardrails,
        "documents": [
            item.to_dict() for item in bundle.evidence
            if item.kind == "document" and not item.source.startswith("tests/")
        ],
        "symbols": symbols,
        "relatedTests": [
            item.to_dict() for item in bundle.evidence if item.source.startswith("tests/")
        ],
        "recentChanges": recent_changes,
    }
    return {**unsigned, "bundleFingerprint": canonical_fingerprint(unsigned)}


def context_bundle_from_payload(payload: dict) -> EvidenceBundle:
    if not isinstance(payload, dict):
        raise ValueError("Context evidence payload must be an object")
    if payload.get("schemaVersion") != CONTEXT_EVIDENCE_SCHEMA:
        raise ValueError("Unexpected context evidence schema")
    if set(payload) != _PUBLIC_KEYS:
        raise ValueError("Context evidence payload has unexpected or missing keys")
    _validate_context_payload_shape(payload)
    unsigned = {key: value for key, value in payload.items() if key != "bundleFingerprint"}
    if canonical_fingerprint(unsigned) != payload.get("bundleFingerprint"):
        raise ValueError("Context evidence fingerprint does not match payload")
    documents = [
        EvidenceItem(
            kind=item["kind"], source=item["source"], content=item["content"], metadata=item["metadata"],
        )
        for item in [*payload["documents"], *payload["relatedTests"]]
    ]
    semantic = [
        EvidenceItem(kind="symbol", source=item["queryId"], content=json.dumps(item, ensure_ascii=False))
        for item in payload["symbols"]
    ] + [
        EvidenceItem(kind="recent_change", source="git-log", content=item["summary"])
        for item in payload["recentChanges"]
    ]
    return EvidenceBundle(
        schema_version=CONTEXT_EVIDENCE_SCHEMA,
        task=dict(payload["task"]), repository=dict(payload["repository"]),
        guardrails=dict(payload["guardrails"]), evidence=tuple([*documents, *semantic]),
    )


def _validate_context_payload_shape(payload: dict) -> None:
    if not isinstance(payload.get("schemaVersion"), str) or not isinstance(payload.get("bundleFingerprint"), str):
        raise ValueError("Context evidence schema and fingerprint must be strings")
    for key in ("task", "repository", "guardrails"):
        if not isinstance(payload[key], dict):
            raise ValueError(f"Context evidence {key} must be an object")
    task = payload["task"]
    if not isinstance(task.get("objective"), str) or not isinstance(task.get("scope"), list) or not isinstance(task.get("forbidden"), list):
        raise ValueError("Context evidence task is incomplete")
    repository = payload["repository"]
    if not isinstance(repository.get("head"), str) or not isinstance(repository.get("dirtyFiles"), list) or not all(isinstance(item, str) for item in repository["dirtyFiles"]):
        raise ValueError("Context evidence repository is incomplete")
    for key in ("documents", "relatedTests", "symbols", "recentChanges"):
        if not isinstance(payload[key], list):
            raise ValueError(f"Context evidence {key} must be a list")
    for item in [*payload["documents"], *payload["relatedTests"]]:
        if not isinstance(item, dict) or set(item) != {"kind", "source", "content", "metadata"}:
            raise ValueError("Context evidence document item is invalid")
        if not all(isinstance(item[field], str) for field in ("kind", "source", "content")) or not isinstance(item["metadata"], dict):
            raise ValueError("Context evidence document item is invalid")
    for item in payload["symbols"]:
        if not isinstance(item, dict) or set(item) != {"queryId", "paths"} or not isinstance(item["queryId"], str) or not isinstance(item["paths"], list) or not all(isinstance(path, str) for path in item["paths"]):
            raise ValueError("Context evidence symbol item is invalid")
    for item in payload["recentChanges"]:
        if not isinstance(item, dict) or set(item) != {"summary"} or not isinstance(item["summary"], str):
            raise ValueError("Context evidence recent change item is invalid")


def _runtime_path(project_root: Path, runtime_root: Path) -> Path:
    raw_runtime = Path(runtime_root)
    if raw_runtime.is_symlink():
        raise PermissionError("Agent runtime root cannot be a symlink")
    expected = (Path(project_root).resolve() / ".nbs_agent_runtime").resolve()
    resolved = raw_runtime.resolve()
    if resolved != expected:
        raise PermissionError(
            f"Agent runtime root must resolve to the project runtime {expected}: {resolved}"
        )
    return resolved


def _validate_report(result: object, expected_fingerprint: str) -> dict:
    if not isinstance(result, dict):
        raise ValueError("Context Agent output must be an object")
    if set(result) != _REPORT_KEYS:
        raise ValueError("Context Agent output schema is invalid")
    if result["schemaVersion"] != CONTEXT_SUMMARY_SCHEMA:
        raise ValueError("Context Agent output schema is invalid")
    if result["status"] not in ALLOWED_CONTEXT_STATUSES:
        raise ValueError("Context Agent output status is invalid")
    for key in _REPORT_LIST_FIELDS:
        if not isinstance(result[key], list) or not all(isinstance(item, str) for item in result[key]):
            raise ValueError(f"Context Agent output field is not a list: {key}")
    if not isinstance(result["relevantFiles"], list):
        raise ValueError("Context Agent output field is not a list: relevantFiles")
    for item in result["relevantFiles"]:
        if not isinstance(item, dict) or set(item) != {"path", "reason", "symbols"} or not isinstance(item["path"], str) or not isinstance(item["reason"], str) or not isinstance(item["symbols"], list) or not all(isinstance(symbol, str) for symbol in item["symbols"]):
            raise ValueError("Context Agent relevantFiles schema is invalid")
    if not isinstance(result["contextFingerprint"], str) or result["contextFingerprint"] != expected_fingerprint:
        raise ValueError("Context Agent context fingerprint does not match")
    return result


def build_context_report(
    bundle: EvidenceBundle,
    *,
    runner: AgentRunner | None,
    project_root: Path,
    runtime_root: Path,
    instructions: str,
    collect_only: bool = False,
    input_token_limit: int = 12000,
    output_token_limit: int = 1500,
) -> dict:
    runtime_path = _runtime_path(project_root, runtime_root)
    payload = build_context_evidence_payload(bundle)
    if collect_only:
        return payload
    expected_fingerprint = agent_request_fingerprint(
        bundle, instructions=instructions, output_schema=CONTEXT_SUMMARY_SCHEMA,
        evidence_payload=payload,
    )
    request_text = json.dumps({"instructions": instructions, "evidence": payload}, ensure_ascii=False, sort_keys=True)
    if estimate_tokens(request_text) > input_token_limit:
        return {
            "schemaVersion": CONTEXT_SUMMARY_SCHEMA,
            "status": "context_overflow",
            "taskUnderstanding": [], "systemBoundaries": [], "relevantFiles": [],
            "dependencies": [], "recommendedTests": [], "risks": [],
            "unknowns": ["Collector must reduce evidence before LLM dispatch."],
            "contextFingerprint": expected_fingerprint,
        }
    if runner is None:
        return {
            "schemaVersion": CONTEXT_SUMMARY_SCHEMA,
            "status": "blocked_missing_evidence",
            "taskUnderstanding": [], "systemBoundaries": [], "relevantFiles": [],
            "dependencies": [], "recommendedTests": [], "risks": [],
            "unknowns": ["No AgentRunner was configured; use --collect-only or --agent-command."],
            "contextFingerprint": expected_fingerprint,
        }
    result = AgentRuntime(
        runtime_path,
        input_token_limit=input_token_limit,
        output_token_limit=output_token_limit,
    ).run(
        "context", bundle, runner, output_schema=CONTEXT_SUMMARY_SCHEMA,
        instructions=instructions, evidence_payload=payload,
    )
    if isinstance(result, dict) and result.get("status") == "context_overflow":
        return {
            "schemaVersion": CONTEXT_SUMMARY_SCHEMA,
            "status": "context_overflow",
            "taskUnderstanding": [], "systemBoundaries": [], "relevantFiles": [],
            "dependencies": [], "recommendedTests": [], "risks": [],
            "unknowns": ["Collector must reduce evidence before LLM dispatch."],
            "contextFingerprint": expected_fingerprint,
        }
    if estimate_tokens(json.dumps(result, ensure_ascii=False)) > output_token_limit:
        raise ValueError("Context Agent output token budget exceeded")
    return _validate_report(result, expected_fingerprint)


def _markdown_list(values: list[Any]) -> str:
    return "\n".join(f"- {value}" for value in values) or "- None"


def format_context_markdown(report: dict) -> str:
    lines = [f"# Context Agent Report", "", f"Status: `{report.get('status', 'unknown')}`", ""]
    sections = (
        ("Task Understanding", report.get("taskUnderstanding", [])),
        ("System Boundaries", report.get("systemBoundaries", [])),
        ("Relevant Files", [f"{item.get('path')}: {item.get('reason')}" for item in report.get("relevantFiles", [])]),
        ("Recommended Tests", report.get("recommendedTests", [])),
        ("Risks", report.get("risks", [])),
        ("Unknowns", report.get("unknowns", [])),
    )
    for title, values in sections:
        lines.extend([f"## {title}", _markdown_list(values), ""])
    return "\n".join(lines)
