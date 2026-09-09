"""Deterministic manifest-left-join reports for agent memory experiments."""

from __future__ import annotations

import copy
import json
from collections import defaultdict
from typing import Any

from .agent_eval_manifest import planned_slots, validate_manifest
from .agent_eval_statistics import latency_summary, task_usage


SCHEMA = "agent-eval-report-v1"


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


def build_report(manifest: dict, observations: list[dict], ledgers: list[dict], quality: list[dict], diagnostics: list[dict]) -> dict:
    checked = validate_manifest(manifest)
    if not all(isinstance(value, list) for value in (observations, ledgers, quality, diagnostics)):
        raise ValueError("invalid_report_inputs")
    slots = planned_slots(checked["caseIds"], checked["repeatCount"])
    slot_keys = {_key(slot) for slot in slots}
    observation_map = defaultdict(list)
    invalid_slot_input = False
    for value in observations:
        key = _key(value)
        invalid_slot_input = invalid_slot_input or key not in slot_keys
        observation_map[key].append(copy.deepcopy(value))
    duplicate_observation_slot = any(len(values) > 1 for values in observation_map.values())
    ledger_map = {}
    quality_map = {}
    duplicate_slot = False
    sessions = set()
    for value in ledgers:
        if isinstance(value, dict):
            key = _key(value)
            invalid_slot_input = invalid_slot_input or key not in slot_keys
            duplicate_slot = duplicate_slot or key in ledger_map
            session = value.get("sessionId")
            if session is not None:
                if not isinstance(session, str) or not session:
                    raise ValueError("invalid_session_id")
                invalid_slot_input = invalid_slot_input or session in sessions
                sessions.add(session)
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
        slot_rows.append(row)
    terminal = [row["terminalState"] for row in slot_rows]
    q = [row["quality"] for row in slot_rows]
    report_status = "available" if not duplicate_slot and all(
        row["ledgerPresent"] and row["qualityPresent"] and row["usage"]["status"] == "available"
        and row["terminalState"] == "completed" and row["quality"] == "success" for row in slot_rows
    ) else "partial"
    if (duplicate_slot or invalid_slot_input or duplicate_observation_slot
            or any(row["usage"]["status"] == "invalid" for row in slot_rows)):
        report_status = "invalid"
    origins = {item.get("origin") for item in observations if isinstance(item, dict)}
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
               "eligible": sum(row["cohort"] == cohort and row["quality"] == "success"
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
