from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.agents.governance_graph_models import GovernanceGate


TELEMETRY_SCHEMA = "governance-telemetry-snapshot-v1"
_UNKNOWN = "unknown"
_STAGES = ("context", "implementation", "targeted_verification", "review", "full_verification", "hermes")
MAX_DURATION_MS = 7 * 24 * 60 * 60 * 1000
MAX_REPAIR_LOOPS = 100
MAX_TOKEN_COUNT = 100_000_000
_PASS_STATUSES = frozenset({"pass", "passed", "completed", "applied", "committed", "merged"})
_FAIL_STATUSES = frozenset({"failed", "changes_required", "fail", "rejected"})
_BLOCKED_STATUSES = frozenset({"blocked", "blocked_missing_runner", "awaiting_target_approval"})


def _nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _bounded_int(value: Any, maximum: int) -> bool:
    return _nonnegative_int(value) and value <= maximum


def _metric(*, total: int = 0, observed: int = 0, unknown: int = 0, **extra: Any) -> dict[str, Any]:
    return {
        "status": "available" if observed else _UNKNOWN,
        "observedCount": observed,
        "unknownCount": unknown,
        "total": total,
        **extra,
    }


class GovernanceTelemetryService:
    """Build a bounded, read-only telemetry projection from retained run evidence."""

    def __init__(self, project_root: Path, runtime_root: Path | None = None) -> None:
        from backend.services.agent_operations_service import AgentOperationsService

        self.reader = AgentOperationsService(project_root, runtime_root=runtime_root)

    def build_snapshot(
        self,
        *,
        runs: list[dict[str, Any]] | None = None,
        diagnostics: list[dict[str, str]] | None = None,
        hard_cap: int | None = None,
    ) -> dict[str, Any]:
        if runs is None:
            diagnostics = []
            _retention, policy = self.reader._retention(diagnostics)
            hard_cap = policy.stage_artifact_max_bytes if policy is not None else 5 * 1024 * 1024
            runs = self.reader._load_runs(diagnostics, hard_cap)
        else:
            diagnostics = list(diagnostics or [])
            hard_cap = hard_cap or 5 * 1024 * 1024

        cycle_times = {
            stage: {"status": _UNKNOWN, "observedCount": 0, "unknownCount": 0, "totalMs": 0}
            for stage in _STAGES
        }
        gate_failures = {
            "specGate": {"status": _UNKNOWN, "failed": 0, "blocked": 0, "unknownCount": 0},
            "planGate": {"status": _UNKNOWN, "failed": 0, "blocked": 0, "unknownCount": 0},
            "taskGate": {"status": _UNKNOWN, "failed": 0, "blocked": 0, "unknownCount": 0},
        }
        luna_total = 0
        luna_observed = 0
        luna_unknown = 0
        terra_unknown = 0
        stale_count = 0
        stale_unknown = 0
        graph_observed = 0
        protected_unknown = 0
        usage_totals = {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0}
        usage_runs = 0
        run_rows: list[dict[str, Any]] = []
        source_times: list[datetime] = []
        unknown_runs = 0

        for item in runs:
            run_id = item.get("runId")
            run_path = self.reader.runs_root / run_id if isinstance(run_id, str) else None
            run_unknown = False
            if isinstance(item.get("updatedAt"), str):
                try:
                    source_times.append(datetime.fromisoformat(item["updatedAt"]))
                except ValueError:
                    run_unknown = True

            stages = item.get("stages") if isinstance(item.get("stages"), dict) else {}
            for stage in _STAGES:
                detail = stages.get(stage)
                duration = detail.get("durationMs") if isinstance(detail, dict) else None
                if _bounded_int(duration, MAX_DURATION_MS):
                    cycle_times[stage]["status"] = "available"
                    cycle_times[stage]["observedCount"] += 1
                    cycle_times[stage]["totalMs"] += duration
                else:
                    cycle_times[stage]["unknownCount"] += 1

            gate_observed: set[str] = set()
            gate_files = {"specGate": "design-spec-gate.json", "planGate": "plan-gate.json"}
            if run_path is not None:
                for key, filename in gate_files.items():
                    try:
                        payload = self.reader._read_json(
                            run_path / filename, run_path, hard_cap, optional=True
                        )
                    except (OSError, ValueError, json.JSONDecodeError):
                        payload = None
                    try:
                        gate = GovernanceGate.from_dict(payload) if isinstance(payload, dict) else None
                    except (TypeError, ValueError):
                        gate = None
                    gate_status = gate.status if gate is not None else None
                    if isinstance(gate_status, str) and gate_status in (_PASS_STATUSES | _FAIL_STATUSES | _BLOCKED_STATUSES):
                        gate_observed.add(key)
                        gate_failures[key]["status"] = "available"
                        if gate_status in _FAIL_STATUSES:
                            gate_failures[key]["failed"] += 1
                        elif gate_status in _BLOCKED_STATUSES:
                            gate_failures[key]["blocked"] += 1

            graph = item.get("governanceGraph")
            if not isinstance(graph, dict) or graph.get("status") != "available":
                stale_unknown += 1
                protected_unknown += 1
                run_unknown = True
                for key in gate_files:
                    if key not in gate_observed:
                        gate_failures[key]["unknownCount"] += 1
            else:
                graph_observed += 1
                if graph.get("freshness") == "stale":
                    stale_count += 1
                protected_unknown += 1
                nodes = graph.get("nodes") or graph.get("nodeStatuses")
                if not isinstance(nodes, list):
                    nodes = []
                node_map = {
                    node.get("nodeId"): node
                    for node in nodes
                    if isinstance(node, dict) and isinstance(node.get("nodeId"), str)
                }
                for key, node_id in (("specGate", "spec_gate"), ("planGate", "plan_gate")):
                    if key in gate_observed:
                        continue
                    node = node_map.get(node_id)
                    if not isinstance(node, dict):
                        gate_failures[key]["unknownCount"] += 1
                        run_unknown = True
                        continue
                    status = node.get("status")
                    reason = node.get("reasonCode")
                    if status in _FAIL_STATUSES or reason == "gate_failed":
                        gate_failures[key]["failed"] += 1
                        gate_failures[key]["status"] = "available"
                    elif status in _BLOCKED_STATUSES or reason == "blocked":
                        gate_failures[key]["blocked"] += 1
                        gate_failures[key]["status"] = "available"
                    elif status in _PASS_STATUSES:
                        gate_failures[key]["status"] = "available"
                    else:
                        gate_failures[key]["unknownCount"] += 1
                        run_unknown = True

            implementation = None
            if run_path is not None:
                try:
                    implementation = self.reader._read_stage(run_path, "implementation.json", hard_cap)
                except (OSError, ValueError, json.JSONDecodeError):
                    implementation = None
            if isinstance(implementation, dict) and _bounded_int(implementation.get("repairLoopsUsed"), MAX_REPAIR_LOOPS):
                luna_total += implementation["repairLoopsUsed"]
                luna_observed += 1
            else:
                luna_unknown += 1
                run_unknown = True

            usage = item.get("tokenUsage")
            if isinstance(usage, dict) and all(_bounded_int(usage.get(key), MAX_TOKEN_COUNT) for key in usage_totals):
                for key in usage_totals:
                    usage_totals[key] += usage[key]
                usage_runs += 1

            terra_unknown += 1
            run_rows.append({
                "runId": run_id if isinstance(run_id, str) else "unknown",
                "briefName": item.get("briefName", "unknown"),
                "updatedAt": item.get("updatedAt", "unknown"),
                "status": item.get("status", "unknown"),
                "unknown": run_unknown,
            })
            unknown_runs += int(run_unknown)

        gate_failures["taskGate"]["unknownCount"] = len(runs)

        for metric in cycle_times.values():
            if metric["observedCount"]:
                metric["averageMs"] = round(metric["totalMs"] / metric["observedCount"])
                metric["status"] = "available"
        for metric in gate_failures.values():
            if metric["failed"] or metric["blocked"]:
                metric["status"] = "available"

        unknown_metric = any(
            isinstance(metric, dict)
            and (metric.get("status") == _UNKNOWN or metric.get("unknownCount", 0) > 0)
            for group in (cycle_times, gate_failures)
            for metric in group.values()
        ) or terra_unknown > 0 or protected_unknown > 0
        invalid_diagnostics = any(
            item.get("code") in {"invalid_run", "unsafe_artifact", "invalid_archive_summary"}
            for item in diagnostics
            if isinstance(item, dict)
        )
        status = "invalid" if not runs and invalid_diagnostics else (
            "unavailable" if not runs else ("partial" if unknown_runs or diagnostics or unknown_metric else "available")
        )
        token_usage = None
        if usage_runs:
            token_usage = {**usage_totals, "runsWithUsage": usage_runs, "runsWithoutUsage": len(runs) - usage_runs}

        source_range = {"earliest": None, "latest": None}
        if source_times:
            source_range = {"earliest": min(source_times).isoformat(), "latest": max(source_times).isoformat()}
        return {
            "schemaVersion": TELEMETRY_SCHEMA,
            "status": status,
            "generatedAt": datetime.now().astimezone().isoformat(),
            "sourceGeneratedAt": source_range,
            "latestRunUpdatedAt": source_range["latest"],
            "coverage": {
                "eligibleRunCount": len(runs),
                "includedRunCount": len(runs) - unknown_runs,
                "unknownRunCount": unknown_runs,
                "diagnosticCount": len(diagnostics),
            },
            "cycleTimes": cycle_times,
            "gateFailures": gate_failures,
            "agentActivity": {
                "lunaRepair": _metric(total=luna_total, observed=luna_observed, unknown=luna_unknown),
                "terraDiagnosis": {"status": _UNKNOWN, "observedCount": 0, "unknownCount": terra_unknown},
            },
            "evidenceHealth": {
                "stale": _metric(total=stale_count, observed=graph_observed, unknown=stale_unknown),
            },
            "protectedIncidents": {"status": _UNKNOWN, "observedCount": 0, "unknownCount": protected_unknown},
            "tokenUsage": token_usage,
            "runs": run_rows,
            "diagnostics": diagnostics[:100],
        }
