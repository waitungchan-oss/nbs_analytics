"""Deterministic manifest-left-join reports for agent memory experiments."""

from __future__ import annotations

import copy
import json
from collections import defaultdict
from typing import Any

from .agent_eval_manifest import planned_slots, validate_manifest
from .agent_eval_statistics import latency_summary, task_usage


SCHEMA = "agent-eval-report-v1"
_VALID_OBSERVATION_ORIGINS = {"real", "synthetic"}


def _key(value: dict) -> tuple:
    if not isinstance(value, dict):
        raise ValueError("invalid_slot_record")
    if "slot" in value:
        slot = value["slot"]
        if not isinstance(slot, dict):
            raise ValueError("invalid_slot_record")
        return slot.get("taskId"), slot.get("repeatIndex"), slot.get("cohort")
    identity = value.get("identity") if isinstance(value.get("identity"), dict) else value
    return identity.get("taskId"), identity.get("repeatIndex"), identity.get("cohort")


def _quality_status(record: dict | None) -> str:
    if not record:
        return "unknown"
    checks = record.get("checks")
    if not isinstance(checks, dict) or not checks:
        return "unknown"
    values = list(checks.values())
    if any(value == "fail" for value in values):
        return "failure"
    if any(value == "unknown" for value in values):
        return "unknown"
    return "success" if all(value == "pass" for value in values) else "unknown"


def _slot_quality(terminal: str, record: dict | None) -> str:
    if terminal in {"failed", "timeout", "budget_exceeded"}:
        return "failure"
    if terminal == "missing" or record is None:
        return "unknown"
    return _quality_status(record)


def build_report(manifest: dict, observations: list[dict], ledgers: list[dict], quality: list[dict], diagnostics: list[dict], *, observation_artifact_refs: list[dict] | None = None) -> dict:
    checked = validate_manifest(manifest)
    if not all(isinstance(value, list) for value in (observations, ledgers, quality, diagnostics)):
        raise ValueError("invalid_report_inputs")
    if observation_artifact_refs is not None and (not isinstance(observation_artifact_refs, list) or len(observation_artifact_refs) != len(observations)):
        raise ValueError("invalid_artifact_refs")
    slots = planned_slots(checked["caseIds"], checked["repeatCount"])
    slot_keys = {_key(slot) for slot in slots}
    observation_map = defaultdict(list)
    invalid_slot_input = False
    for index, value in enumerate(observations):
        if not isinstance(value, dict):
            raise ValueError("invalid_observation")
        identity = value.get("identity")
        canonical_observation = value.get("schemaVersion") == "agent-eval-observation-v1"
        if not canonical_observation:
            invalid_slot_input = True
        if not isinstance(identity, dict):
            invalid_slot_input = True
            identity = {}
        required = {"projectId", "consumerId", "provider", "model", "settingsFingerprint", "sourceCommit", "dirtyFingerprint", "workloadFingerprint", "catalogFingerprint", "policyFingerprint", "allowedFilesFingerprint", "commandsFingerprint", "taskId", "repeatIndex", "cohort", "sessionId"}
        if not required <= set(identity) or not {"producerId", "sourceSchema", "producerFingerprint", "artifactRef"} <= set(value):
            invalid_slot_input = True
        for field in ("projectId", "consumerId", "provider", "model"):
            if field in identity and identity[field] != checked["identity"][field]:
                invalid_slot_input = True
        for field in ("settingsFingerprint", "sourceCommit", "dirtyFingerprint", "workloadFingerprint", "catalogFingerprint", "policyFingerprint", "allowedFilesFingerprint", "commandsFingerprint"):
            if field in identity and identity[field] != checked["identity"][field]:
                invalid_slot_input = True
        producer_id = value.get("producerId")
        if producer_id is not None:
            producer = checked["producerRegistry"].get(producer_id)
            if producer is None or value.get("sourceSchema") != producer["sourceSchema"] or value.get("producerFingerprint") != producer["producerFingerprint"]:
                invalid_slot_input = True
        if "artifactRef" in value:
            expected_ref = observation_artifact_refs[index] if observation_artifact_refs is not None else None
            if expected_ref is None or value["artifactRef"] != expected_ref:
                invalid_slot_input = True
        key = _key(value)
        invalid_slot_input = invalid_slot_input or key not in slot_keys
        observation_map[key].append(copy.deepcopy(value))
    observation_call_ids = set()
    session_registry = {}
    observation_duplicate = False
    observation_mixed_provenance = False
    origins = set()
    for key, values in observation_map.items():
        local_origins = set()
        for value in values:
            call_id = value.get("callId")
            if call_id in observation_call_ids:
                observation_duplicate = True
            observation_call_ids.add(call_id)
            session_id = value.get("sessionId") or (value.get("identity") or {}).get("sessionId")
            if session_id is not None:
                owner = session_registry.get(session_id)
                if owner is not None and owner[0] != key:
                    invalid_slot_input = True
                session_registry[session_id] = (key, "observation")
            origin = value.get("origin")
            if origin not in _VALID_OBSERVATION_ORIGINS:
                invalid_slot_input = True
            origins.add(origin)
            local_origins.add(origin)
        observation_mixed_provenance = observation_mixed_provenance or len(local_origins) > 1
    observation_mixed_provenance = observation_mixed_provenance or len(origins) > 1
    ledger_map = {}
    quality_map = {}
    duplicate_slot = False
    for value in ledgers:
        if isinstance(value, dict):
            key = _key(value)
            invalid_slot_input = invalid_slot_input or key not in slot_keys
            duplicate_slot = duplicate_slot or key in ledger_map
            session = value.get("sessionId")
            if session is not None:
                if not isinstance(session, str) or not session:
                    raise ValueError("invalid_session_id")
                owner = session_registry.get(session)
                if owner is not None and owner[0] != key:
                    invalid_slot_input = True
                session_registry[session] = (key, "ledger")
            ledger_map[key] = value
        else:
            raise ValueError("invalid_ledger")
    for value in quality:
        if isinstance(value, dict):
            key = _key(value)
            invalid_slot_input = invalid_slot_input or key not in slot_keys
            duplicate_slot = duplicate_slot or key in quality_map
            quality_map[key] = value
        else:
            raise ValueError("invalid_quality")
    slot_rows = []
    for slot in slots:
        key = _key(slot)
        ledger = ledger_map.get(key)
        calls = observation_map.get(key, [])
        expected = ledger.get("expectedCallIds") if isinstance(ledger, dict) else None
        usage = task_usage(calls, expected_call_ids=expected if isinstance(expected, list) else None)
        status = ledger.get("terminalState") if isinstance(ledger, dict) else "missing"
        row = {**slot, "terminalState": status, "usage": usage, "quality": _slot_quality(status, quality_map.get(key)),
               "ledgerPresent": ledger is not None, "qualityPresent": key in quality_map}
        if calls and isinstance(ledger, dict):
            ledger_session = ledger.get("sessionId")
            for call in calls:
                call_session = call.get("sessionId") or (call.get("identity") or {}).get("sessionId")
                if ledger_session is not None and call_session != ledger_session:
                    invalid_slot_input = True
        slot_rows.append(row)
    terminal = [row["terminalState"] for row in slot_rows]
    q = [row["quality"] for row in slot_rows]
    report_status = "available" if not duplicate_slot and all(
        row["ledgerPresent"] and row["qualityPresent"] and row["usage"]["status"] == "available"
        and row["terminalState"] == "completed" and row["quality"] == "success" for row in slot_rows
    ) else "partial"
    if (duplicate_slot or invalid_slot_input or observation_duplicate or observation_mixed_provenance
            or any(row["usage"]["status"] == "invalid" for row in slot_rows)):
        report_status = "invalid"
    if report_status != "invalid" and origins and origins == {"synthetic"}:
        report_status = "synthetic_only"
    by_pair = {(row["taskId"], row["repeatIndex"], row["cohort"]): row for row in slot_rows}
    comparisons = []
    for task_id in checked["caseIds"]:
        for repeat_index in range(checked["repeatCount"]):
            off = by_pair[(task_id, repeat_index, "recall_off")]
            on = by_pair[(task_id, repeat_index, "recall_on")]
            eligible = report_status != "invalid" and all(item["terminalState"] == "completed" and item["quality"] == "success"
                           and item["usage"]["status"] == "available" for item in (off, on))
            comparisons.append({
                "taskId": task_id, "repeatIndex": repeat_index, "eligible": eligible,
                "tokenOff": off["usage"]["taskTotalTokens"] if report_status != "invalid" else None,
                "tokenOn": on["usage"]["taskTotalTokens"] if report_status != "invalid" else None,
                "tokenDelta": (on["usage"]["taskTotalTokens"] - off["usage"]["taskTotalTokens"])
                if eligible else None,
                "qualityOff": off["quality"], "qualityOn": on["quality"],
            })
    strata = [{"cohort": cohort, "planned": sum(row["cohort"] == cohort for row in slot_rows),
               "eligible": sum(row["cohort"] == cohort and row["terminalState"] == "completed"
                               and row["quality"] == "success"
                               and row["usage"]["status"] == "available" and report_status != "invalid"
                               for row in slot_rows)}
              for cohort in ("recall_off", "recall_on")]
    return {
        "schemaVersion": SCHEMA,
        "manifestFingerprint": checked["manifestFingerprint"],
        "origin": "synthetic" if report_status == "synthetic_only" else "offline",
        "status": report_status,
        "coverage": {"planned": len(slot_rows), "observed": sum(row["ledgerPresent"] for row in slot_rows),
                      "missing": terminal.count("missing"), "failed": sum(item in {"failed", "timeout", "budget_exceeded"} for item in terminal),
                      "successCount": q.count("success"), "failureCount": q.count("failure"), "unknownCount": q.count("unknown")},
        "slots": slot_rows,
        "strata": strata,
        "comparisons": comparisons,
        "legacyGateRefs": [],
        "diagnostics": sorted(copy.deepcopy(diagnostics), key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False)),
    }


def render_markdown(report: dict) -> str:
    if not isinstance(report, dict) or report.get("schemaVersion") != SCHEMA:
        raise ValueError("invalid_report")
    coverage = report.get("coverage") or {}
    def display(value: Any) -> str:
        return "未測" if value is None else str(value)
    lines = ["# Agent Memory Evaluation Report", "", f"- Status: `{report.get('status', 'unknown')}`", f"- Origin: `{report.get('origin', 'unknown')}`", "", "## Coverage", "", "| Metric | Value |", "|---|---:|"]
    for name in ("planned", "observed", "missing", "failed", "successCount", "failureCount", "unknownCount"):
        lines.append(f"| {name} | {display(coverage.get(name))} |")
    lines.extend(["", "## Diagnostics", ""])
    for item in report.get("diagnostics", []):
        lines.append(f"- `{item.get('code', 'unknown')}`")
    return "\n".join(lines) + "\n"
