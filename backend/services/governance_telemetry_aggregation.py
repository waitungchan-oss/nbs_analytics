from __future__ import annotations

from datetime import datetime
from typing import Any


_UNKNOWN = "unknown"
_STAGES = ("context", "implementation", "targeted_verification", "review", "full_verification", "hermes")
MAX_DURATION_MS = 7 * 24 * 60 * 60 * 1000
MAX_REPAIR_LOOPS = 100
MAX_TOKEN_COUNT = 100_000_000
_PASS_STATUSES = frozenset({"pass", "passed", "completed", "applied", "committed", "merged"})
_FAIL_STATUSES = frozenset({"failed", "changes_required", "fail", "rejected"})
_BLOCKED_STATUSES = frozenset({"blocked", "blocked_missing_runner", "awaiting_target_approval"})


def _bounded_int(value: Any, maximum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum


def _metric(*, total: int = 0, observed: int = 0, unknown: int = 0, **extra: Any) -> dict[str, Any]:
    return {"status": "available" if observed else _UNKNOWN, "observedCount": observed, "unknownCount": unknown, "total": total, **extra}


def _canonical_state(item: dict[str, Any], kind: str) -> tuple[str, str | None]:
    evidence = item.get("canonicalEvidence")
    value = evidence.get(kind) if isinstance(evidence, dict) else None
    if not isinstance(value, dict):
        return _UNKNOWN, None
    status = value.get("status")
    state = value.get("state")
    if status not in {"available", "blocked", "unknown", "invalid"} or not isinstance(state, str):
        return "invalid", None
    return status, state


class TelemetryAggregator:
    def __init__(self, reader: Any) -> None:
        self.reader = reader

    def build(self, runs: list[dict[str, Any]], diagnostics: list[dict[str, str]], hard_cap: int) -> dict[str, Any]:
        cycle_times = {stage: {"status": _UNKNOWN, "observedCount": 0, "unknownCount": 0, "totalMs": 0} for stage in _STAGES}
        gate_failures = {key: {"status": _UNKNOWN, "failed": 0, "blocked": 0, "unknownCount": 0} for key in ("specGate", "planGate", "taskGate")}
        gate_failures["taskGate"].update({"observedCount": 0, "blockedCount": 0, "invalidCount": 0})
        luna_total = luna_observed = luna_unknown = stale_count = stale_unknown = graph_observed = 0
        terra_completed = terra_blocked = terra_unknown = terra_invalid = 0
        protected_observed = protected_blocked = protected_unknown = protected_invalid = 0
        usage_totals = {key: 0 for key in ("inputTokens", "outputTokens", "totalTokens")}
        usage_runs = 0
        run_rows: list[dict[str, Any]] = []
        source_times: list[datetime] = []
        unknown_runs = 0
        eligible_runs = [item for item in runs if item.get("retentionState") != "deleted"]
        for item in eligible_runs:
            run_id = item.get("runId")
            run_unknown = False
            if isinstance(item.get("updatedAt"), str):
                try: source_times.append(datetime.fromisoformat(item["updatedAt"]))
                except ValueError: run_unknown = True
            stages = item.get("stages") if isinstance(item.get("stages"), dict) else {}
            for stage in _STAGES:
                detail = stages.get(stage)
                duration = detail.get("durationMs") if isinstance(detail, dict) else None
                if _bounded_int(duration, MAX_DURATION_MS):
                    metric = cycle_times[stage]; metric["status"] = "available"; metric["observedCount"] += 1; metric["totalMs"] += duration
                else: cycle_times[stage]["unknownCount"] += 1
            gate_files = {"specGate": "design-spec-gate.json", "planGate": "plan-gate.json"}
            graph = item.get("governanceGraph")
            if not isinstance(graph, dict) or graph.get("status") != "available":
                stale_unknown += 1; run_unknown = True
                for key in gate_files:
                    gate_failures[key]["unknownCount"] += 1
            else:
                graph_observed += 1
                if graph.get("freshness") == "stale": stale_count += 1
                nodes = graph.get("nodes") or graph.get("nodeStatuses")
                nodes = nodes if isinstance(nodes, list) else []
                node_map = {node.get("nodeId"): node for node in nodes if isinstance(node, dict) and isinstance(node.get("nodeId"), str)}
                for key, node_id in (("specGate", "spec_gate"), ("planGate", "plan_gate")):
                    node = node_map.get(node_id)
                    status = node.get("status") if isinstance(node, dict) else None
                    reason = node.get("reasonCode") if isinstance(node, dict) else None
                    if status in _FAIL_STATUSES or reason == "gate_failed": gate_failures[key]["failed"] += 1; gate_failures[key]["status"] = "available"
                    elif status in _BLOCKED_STATUSES or reason == "blocked": gate_failures[key]["blocked"] += 1; gate_failures[key]["status"] = "available"
                    elif status in _PASS_STATUSES: gate_failures[key]["status"] = "available"
                    else: gate_failures[key]["unknownCount"] += 1; run_unknown = True
            repair_loops = item.get("lunaRepairLoops")
            if _bounded_int(repair_loops, MAX_REPAIR_LOOPS):
                luna_total += repair_loops; luna_observed += 1
            else: luna_unknown += 1; run_unknown = True
            usage = item.get("tokenUsage")
            if isinstance(usage, dict) and all(_bounded_int(usage.get(key), MAX_TOKEN_COUNT) for key in usage_totals):
                for key in usage_totals: usage_totals[key] += usage[key]
                usage_runs += 1
            task_status, task_state = _canonical_state(item, "task_gate")
            task_metric = gate_failures["taskGate"]
            if task_status == "available":
                task_metric["status"] = "available"; task_metric["observedCount"] += 1
                if task_state == "failed": task_metric["failed"] += 1
            elif task_status == "blocked":
                task_metric["status"] = "available"; task_metric["observedCount"] += 1; task_metric["blocked"] += 1; task_metric["blockedCount"] += 1
            elif task_status == "invalid":
                task_metric["invalidCount"] += 1; run_unknown = True
            else:
                task_metric["unknownCount"] += 1; run_unknown = True
            terra_status, terra_state = _canonical_state(item, "terra_diagnosis")
            if terra_status == "available": terra_completed += int(terra_state == "completed")
            elif terra_status == "blocked": terra_blocked += 1
            elif terra_status == "invalid": terra_invalid += 1; run_unknown = True
            else: terra_unknown += 1; run_unknown = True
            incident_status, _incident_state = _canonical_state(item, "protected_incident")
            if incident_status == "available": protected_observed += 1
            elif incident_status == "blocked": protected_blocked += 1
            elif incident_status == "invalid": protected_invalid += 1; run_unknown = True
            else: protected_unknown += 1; run_unknown = True
            run_rows.append({"runId": run_id if isinstance(run_id, str) else "unknown", "briefName": item.get("briefName", "unknown"), "updatedAt": item.get("updatedAt", "unknown"), "status": item.get("status", "unknown"), "unknown": run_unknown})
            unknown_runs += int(run_unknown)
        for metric in cycle_times.values():
            if metric["observedCount"]: metric["averageMs"] = round(metric["totalMs"] / metric["observedCount"]); metric["status"] = "available"
        for metric in gate_failures.values():
            if metric["failed"] or metric["blocked"]: metric["status"] = "available"
        if gate_failures["taskGate"]["invalidCount"]:
            gate_failures["taskGate"]["status"] = "invalid"
        unknown_metric = any(metric["status"] == _UNKNOWN or metric.get("unknownCount", 0) > 0 for group in (cycle_times, gate_failures) for metric in group.values()) or terra_unknown > 0 or protected_unknown > 0 or terra_invalid > 0 or protected_invalid > 0
        invalid = any(item.get("code") in {"invalid_run", "unsafe_artifact", "invalid_archive_summary"} for item in diagnostics if isinstance(item, dict))
        status = "invalid" if not runs and invalid else ("unavailable" if not runs else ("partial" if unknown_runs or diagnostics or unknown_metric else "available"))
        token_usage = {**usage_totals, "runsWithUsage": usage_runs, "runsWithoutUsage": len(eligible_runs) - usage_runs} if usage_runs else None
        source_range = {"earliest": min(source_times).isoformat(), "latest": max(source_times).isoformat()} if source_times else {"earliest": None, "latest": None}
        terra_status = "invalid" if terra_invalid else ("available" if terra_completed or terra_blocked else _UNKNOWN)
        protected_status = "invalid" if protected_invalid else ("available" if protected_observed or protected_blocked else _UNKNOWN)
        return {"schemaVersion": "governance-telemetry-snapshot-v1", "status": status, "generatedAt": datetime.now().astimezone().isoformat(), "sourceGeneratedAt": source_range, "latestRunUpdatedAt": source_range["latest"], "coverage": {"eligibleRunCount": len(eligible_runs), "includedRunCount": len(eligible_runs) - unknown_runs, "unknownRunCount": unknown_runs, "diagnosticCount": len(diagnostics)}, "cycleTimes": cycle_times, "gateFailures": gate_failures, "agentActivity": {"lunaRepair": _metric(total=luna_total, observed=luna_observed, unknown=luna_unknown), "terraDiagnosis": {"status": terra_status, "observedCount": terra_completed + terra_blocked, "completedCount": terra_completed, "blockedCount": terra_blocked, "unknownCount": terra_unknown, "invalidCount": terra_invalid}}, "evidenceHealth": {"stale": _metric(total=stale_count, observed=graph_observed, unknown=stale_unknown)}, "protectedIncidents": {"status": protected_status, "observedCount": protected_observed + protected_blocked, "blockedCount": protected_blocked, "unknownCount": protected_unknown, "invalidCount": protected_invalid}, "tokenUsage": token_usage, "runs": run_rows, "diagnostics": diagnostics[:100]}
