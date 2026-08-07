from __future__ import annotations

import json
import os
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
from backend.agents.memory_sidecar_hint_models import MemoryHints, MemorySidecarSchemaError


CONTEXT_EVIDENCE_SCHEMA = "context-evidence-v1"
CONTEXT_SUMMARY_SCHEMA = "context-summary-v1"
_PUBLIC_KEYS = {
    "schemaVersion", "task", "repository", "guardrails", "documents", "symbols",
    "relatedTests", "recentChanges", "bundleFingerprint",
}
_MEMORY_HINTS_KEY = "memoryHints"
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


def _memory_hints_payload(memory_hints: MemoryHints | dict | None) -> dict | None:
    if memory_hints is None:
        return None
    envelope = {
        "authority": "non_authoritative_memory",
        "status": "ignored",
        "hints": [],
    }
    try:
        parsed = memory_hints if isinstance(memory_hints, MemoryHints) else MemoryHints.from_dict(memory_hints)
        if parsed.status != "ready":
            envelope["reason"] = parsed.status
            return envelope
        if any(item.freshness != "fresh" for item in parsed.hints):
            envelope["reason"] = "stale"
            return envelope
        envelope = {"authority": "non_authoritative_memory", **parsed.to_dict()}
        return envelope
    except (MemorySidecarSchemaError, TypeError, ValueError):
        envelope["reason"] = "invalid"
        return envelope


def _validate_memory_hints_payload(payload: object) -> None:
    if not isinstance(payload, dict) or payload.get("authority") != "non_authoritative_memory":
        raise ValueError("Context memory hints authority is invalid")
    status = payload.get("status")
    if status == "ignored":
        if set(payload) != {"authority", "status", "hints", "reason"} or payload.get("hints") != [] or not isinstance(payload.get("reason"), str):
            raise ValueError("Context ignored memory hints are invalid")
        return
    if status not in {"ready", "empty", "timeout", "degraded"}:
        raise ValueError("Context memory hints status is invalid")
    model_payload = {key: value for key, value in payload.items() if key != "authority"}
    try:
        MemoryHints.from_dict(model_payload)
    except (MemorySidecarSchemaError, TypeError, ValueError) as exc:
        raise ValueError("Context memory hints payload is invalid") from exc


def build_context_evidence_payload(
    bundle: EvidenceBundle, *, memory_hints: MemoryHints | dict | None = None,
) -> dict:
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
    # Deliberately fingerprint only canonical collector evidence. Memory hints
    # are an optional, non-authoritative read model appended after hashing.
    result = {**unsigned, "bundleFingerprint": canonical_fingerprint(unsigned)}
    hints_payload = _memory_hints_payload(memory_hints)
    if hints_payload is not None:
        result[_MEMORY_HINTS_KEY] = hints_payload
    return result


def context_bundle_from_payload(payload: dict) -> EvidenceBundle:
    if not isinstance(payload, dict):
        raise ValueError("Context evidence payload must be an object")
    if payload.get("schemaVersion") != CONTEXT_EVIDENCE_SCHEMA:
        raise ValueError("Unexpected context evidence schema")
    keys_without_hints = set(payload) - {_MEMORY_HINTS_KEY}
    if keys_without_hints != _PUBLIC_KEYS:
        raise ValueError("Context evidence payload has unexpected or missing keys")
    _validate_context_payload_shape(payload)
    unsigned = {
        key: value for key, value in payload.items()
        if key not in {"bundleFingerprint", _MEMORY_HINTS_KEY}
    }
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


def context_summary_from_evidence_payload(payload: dict) -> dict:
    """Convert validated collect-only evidence into the strict Review context shape."""
    bundle = context_bundle_from_payload(payload)
    objective = bundle.task.get("objective")
    forbidden = bundle.task.get("forbidden", [])
    boundaries = [
        f"{key}: {value}"
        for key, value in sorted(bundle.guardrails.items())
        if isinstance(key, str) and isinstance(value, (str, int, float, bool))
    ]
    relevant_files = [
        {
            "path": item.source,
            "reason": "Collected read-only context evidence",
            "symbols": [],
        }
        for item in bundle.evidence
        if item.kind == "document"
    ]
    summary = {
        "schemaVersion": CONTEXT_SUMMARY_SCHEMA,
        "status": "ready",
        "taskUnderstanding": [objective] if isinstance(objective, str) and objective else [],
        "systemBoundaries": boundaries,
        "relevantFiles": relevant_files,
        "dependencies": [],
        "recommendedTests": [
            item.source for item in bundle.evidence
            if item.kind == "document" and item.source.startswith("tests/")
        ],
        "risks": list(forbidden) if isinstance(forbidden, list) else [],
        "unknowns": ["Context was collected without LLM summarization."],
        "contextFingerprint": payload["bundleFingerprint"],
    }
    if _MEMORY_HINTS_KEY in payload:
        summary[_MEMORY_HINTS_KEY] = payload[_MEMORY_HINTS_KEY]
    return _validate_report(summary, payload["bundleFingerprint"])


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
    if _MEMORY_HINTS_KEY in payload:
        _validate_memory_hints_payload(payload[_MEMORY_HINTS_KEY])
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


def _validate_report(result: object, expected_fingerprint: str) -> dict:
    if not isinstance(result, dict):
        raise ValueError("Context Agent output must be an object")
    if set(result) - {"memoryHints"} != _REPORT_KEYS:
        raise ValueError("Context Agent output schema is invalid")
    if "memoryHints" in result:
        _validate_memory_hints_payload(result["memoryHints"])
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


def _context_overflow_report(expected_fingerprint: str) -> dict:
    return {
        "schemaVersion": CONTEXT_SUMMARY_SCHEMA,
        "status": "context_overflow",
        "taskUnderstanding": [], "systemBoundaries": [], "relevantFiles": [],
        "dependencies": [], "recommendedTests": [], "risks": [],
        "unknowns": ["Collector must reduce evidence before LLM dispatch."],
        "contextFingerprint": expected_fingerprint,
    }


def _finalize_report(result: dict, *, payload: dict, expected_fingerprint: str, output_token_limit: int) -> dict:
    if _MEMORY_HINTS_KEY in payload:
        result = {key: value for key, value in result.items() if key != _MEMORY_HINTS_KEY}
        result[_MEMORY_HINTS_KEY] = payload[_MEMORY_HINTS_KEY]
    if estimate_tokens(json.dumps(result, ensure_ascii=False)) > output_token_limit:
        return _context_overflow_report(expected_fingerprint)
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
    memory_hints: MemoryHints | dict | None = None,
) -> dict:
    runtime_path = _runtime_path(project_root, runtime_root)
    payload = build_context_evidence_payload(bundle, memory_hints=memory_hints)
    if collect_only:
        return payload
    expected_fingerprint = agent_request_fingerprint(
        bundle, instructions=instructions, output_schema=CONTEXT_SUMMARY_SCHEMA,
        evidence_payload=payload,
    )
    request_text = json.dumps({"instructions": instructions, "evidence": payload}, ensure_ascii=False, sort_keys=True)
    if estimate_tokens(request_text) > input_token_limit:
        return _finalize_report(
            _context_overflow_report(expected_fingerprint), payload=payload,
            expected_fingerprint=expected_fingerprint, output_token_limit=output_token_limit,
        )
    if runner is None:
        result = {
            "schemaVersion": CONTEXT_SUMMARY_SCHEMA,
            "status": "blocked_missing_evidence",
            "taskUnderstanding": [], "systemBoundaries": [], "relevantFiles": [],
            "dependencies": [], "recommendedTests": [], "risks": [],
            "unknowns": ["No AgentRunner was configured; use --collect-only or --agent-command."],
            "contextFingerprint": expected_fingerprint,
        }
        return _finalize_report(
            result, payload=payload, expected_fingerprint=expected_fingerprint,
            output_token_limit=output_token_limit,
        )
    result = AgentRuntime(
        runtime_path,
        input_token_limit=input_token_limit,
        output_token_limit=output_token_limit,
    ).run(
        "context", bundle, runner, output_schema=CONTEXT_SUMMARY_SCHEMA,
        instructions=instructions, evidence_payload=payload,
    )
    if isinstance(result, dict) and result.get("status") == "context_overflow":
        return _finalize_report(
            _context_overflow_report(expected_fingerprint), payload=payload,
            expected_fingerprint=expected_fingerprint, output_token_limit=output_token_limit,
        )
    if estimate_tokens(json.dumps(result, ensure_ascii=False)) > output_token_limit:
        raise ValueError("Context Agent output token budget exceeded")
    return _validate_report(
        _finalize_report(
            result, payload=payload, expected_fingerprint=expected_fingerprint,
            output_token_limit=output_token_limit,
        ),
        expected_fingerprint,
    )


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
