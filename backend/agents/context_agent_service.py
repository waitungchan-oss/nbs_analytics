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


def build_context_evidence_payload(bundle: EvidenceBundle) -> dict:
    unsigned = {
        "schemaVersion": CONTEXT_EVIDENCE_SCHEMA,
        "task": bundle.task,
        "repository": bundle.repository,
        "guardrails": bundle.guardrails,
        "documents": [
            item.to_dict() for item in bundle.evidence
            if item.kind == "document" and not item.source.startswith("tests/")
        ],
        "symbols": [
            {"queryId": item.label, "paths": item.stdout.splitlines()}
            for item in bundle.commands if item.label.startswith("rg-query-")
        ],
        "relatedTests": [
            item.to_dict() for item in bundle.evidence if item.source.startswith("tests/")
        ],
        "recentChanges": [
            {"summary": line}
            for item in bundle.commands if item.label == "git-log"
            for line in item.stdout.splitlines()
        ],
    }
    return {**unsigned, "bundleFingerprint": canonical_fingerprint(unsigned)}


def context_bundle_from_payload(payload: dict) -> EvidenceBundle:
    if not isinstance(payload, dict):
        raise ValueError("Context evidence payload must be an object")
    if payload.get("schemaVersion") != CONTEXT_EVIDENCE_SCHEMA:
        raise ValueError("Unexpected context evidence schema")
    if set(payload) != _PUBLIC_KEYS:
        raise ValueError("Context evidence payload has unexpected or missing keys")
    unsigned = {key: value for key, value in payload.items() if key != "bundleFingerprint"}
    if canonical_fingerprint(unsigned) != payload.get("bundleFingerprint"):
        raise ValueError("Context evidence fingerprint does not match payload")
    for key in ("task", "repository", "guardrails"):
        if not isinstance(payload[key], dict):
            raise ValueError(f"Context evidence {key} must be an object")
    if not payload["task"].get("objective") or "scope" not in payload["task"]:
        raise ValueError("Context evidence task is incomplete")
    if not payload["repository"].get("head") or "dirtyFiles" not in payload["repository"]:
        raise ValueError("Context evidence repository is incomplete")
    documents = [
        EvidenceItem(
            kind=str(item.get("kind") or "document"), source=str(item["source"]),
            content=str(item["content"]), metadata=dict(item.get("metadata") or {}),
        )
        for item in [*payload["documents"], *payload["relatedTests"]]
    ]
    semantic = [
        EvidenceItem(kind="symbol", source=str(item["queryId"]), content=json.dumps(item, ensure_ascii=False))
        for item in payload["symbols"]
    ] + [
        EvidenceItem(kind="recent_change", source="git-log", content=str(item["summary"]))
        for item in payload["recentChanges"]
    ]
    return EvidenceBundle(
        schema_version=CONTEXT_EVIDENCE_SCHEMA,
        task=dict(payload["task"]), repository=dict(payload["repository"]),
        guardrails=dict(payload["guardrails"]), evidence=tuple([*documents, *semantic]),
    )


def _runtime_path(runtime_root: Path) -> Path:
    resolved = Path(runtime_root).resolve()
    return resolved if resolved.name == ".nbs_agent_runtime" else resolved / ".nbs_agent_runtime"


def _validate_report(result: object, expected_fingerprint: str) -> dict:
    if not isinstance(result, dict):
        raise ValueError("Context Agent output must be an object")
    if set(result) != _REPORT_KEYS:
        raise ValueError("Context Agent output schema is invalid")
    if result["schemaVersion"] != CONTEXT_SUMMARY_SCHEMA:
        raise ValueError("Context Agent output schema is invalid")
    if result["status"] not in ALLOWED_CONTEXT_STATUSES:
        raise ValueError("Context Agent output status is invalid")
    for key in ("taskUnderstanding", "systemBoundaries", "dependencies", "recommendedTests", "risks", "unknowns"):
        if not isinstance(result[key], list):
            raise ValueError(f"Context Agent output field is not a list: {key}")
    if not isinstance(result["relevantFiles"], list):
        raise ValueError("Context Agent output field is not a list: relevantFiles")
    for item in result["relevantFiles"]:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("reason"), str) or not isinstance(item.get("symbols"), list):
            raise ValueError("Context Agent relevantFiles schema is invalid")
    if result["contextFingerprint"] != expected_fingerprint:
        raise ValueError("Context Agent context fingerprint does not match")
    return result


def build_context_report(
    bundle: EvidenceBundle,
    *,
    runner: AgentRunner | None,
    runtime_root: Path,
    instructions: str,
    collect_only: bool = False,
    input_token_limit: int = 12000,
    output_token_limit: int = 1500,
) -> dict:
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
        _runtime_path(runtime_root),
        input_token_limit=input_token_limit,
        output_token_limit=output_token_limit,
    ).run(
        "context", bundle, runner, output_schema=CONTEXT_SUMMARY_SCHEMA,
        instructions=instructions, evidence_payload=payload,
    )
    if result.get("status") == "context_overflow":
        return result
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
