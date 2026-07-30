from __future__ import annotations

from datetime import date, datetime
from typing import Callable, Any

import streamlit as st

from governance_graph_rendering import render_governance_graph_workspace


SNAPSHOT_SCHEMA = "agent-operations-snapshot-v1"
SELECTED_RUN_KEY = "AGENT_OPERATIONS_SELECTED_RUN_ID"


def filter_agent_runs(
    runs: list[dict],
    statuses: set[str],
    date_from: date | None,
    date_to: date | None,
    brief_query: str,
) -> list[dict]:
    query = brief_query.strip().casefold()
    result = []
    for item in runs:
        updated = datetime.fromisoformat(item["updatedAt"]).date()
        if statuses and item["status"] not in statuses:
            continue
        if date_from and updated < date_from:
            continue
        if date_to and updated > date_to:
            continue
        if query and query not in item["briefName"].casefold():
            continue
        result.append(item)
    return result


def token_usage_label(value: dict | None) -> str:
    if not value or not isinstance(value.get("totalTokens"), int):
        return "未提供"
    return f"{value['totalTokens']:,} tokens"


def _date_value(value: Any) -> date | None:
    return value if isinstance(value, date) else None


def _metric_value(summary: dict, key: str) -> int:
    value = summary.get(key, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _run_table(runs: list[dict]) -> list[dict[str, Any]]:
    return [
        {
            "Run ID": item.get("runId", ""),
            "Brief": item.get("briefName", ""),
            "Status": item.get("status", ""),
            "Stage": item.get("stage", ""),
            "Updated": item.get("updatedAt", ""),
            "Duration (ms)": item.get("durationMs"),
        }
        for item in runs
    ]


def _graph_evidence_rows(graph: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    evidence = graph.get("evidence", [])
    if not isinstance(evidence, list):
        return rows
    for item in evidence:
        if not isinstance(item, dict):
            continue
        artifact = item.get("artifact")
        sha256 = item.get("sha256")
        status = item.get("status")
        node_id = item.get("nodeId")
        if all(isinstance(value, str) and value for value in (artifact, sha256, status, node_id)):
            rows.append({
                "Node": node_id,
                "Artifact": artifact,
                "SHA-256": sha256[:8],
                "Status": status,
            })
    return rows


def _graph_node_rows(graph: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    nodes = graph.get("nodeStatuses", graph.get("nodes", []))
    if not isinstance(nodes, list):
        return rows
    for item in nodes:
        if not isinstance(item, dict):
            continue
        node_id = item.get("nodeId")
        status = item.get("status")
        reason = item.get("reasonCode")
        if isinstance(node_id, str) and node_id and isinstance(status, str) and status:
            rows.append({
                "Node": node_id,
                "Status": status,
                "Reason": reason if isinstance(reason, str) else "",
            })
    return rows


def _graph_issue_rows(graph: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for field, label in (("blockers", "Blocker"), ("diagnostics", "Diagnostic")):
        values = graph.get(field, [])
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            code = item.get("code")
            node_id = item.get("nodeId")
            if isinstance(code, str) and code:
                rows.append({
                    "Type": label,
                    "Code": code,
                    "Node": node_id if isinstance(node_id, str) else "",
                })
    return rows


def _render_governance_graph(graph: Any) -> None:
    st.subheader("Governance Graph")
    if not isinstance(graph, dict) or graph.get("status") == "unavailable":
        st.info("尚無已建 Graph snapshot；此頁不會自行建立或更新 snapshot。")
        return

    status = graph.get("status")
    if status != "available":
        safe_status = status if isinstance(status, str) and status else "unknown"
        st.warning(f"Governance Graph 狀態：{safe_status}")
        return

    overall_status = graph.get("overallStatus")
    freshness = graph.get("freshness")
    overall_label = overall_status if isinstance(overall_status, str) and overall_status else "unknown"
    freshness_label = freshness if isinstance(freshness, str) and freshness else "unknown"
    st.write(f"Overall status: {overall_label} · Freshness: {freshness_label}")

    node_rows = _graph_node_rows(graph)
    if node_rows:
        st.dataframe(node_rows, use_container_width=True, hide_index=True)

    issue_rows = _graph_issue_rows(graph)
    if issue_rows:
        codes = ", ".join(row["Code"] for row in issue_rows if row.get("Code"))
        if codes:
            st.caption(f"Graph blockers/diagnostics: {codes}")
        st.dataframe(issue_rows, use_container_width=True, hide_index=True)

    evidence_rows = _graph_evidence_rows(graph)
    if evidence_rows:
        st.dataframe(evidence_rows, use_container_width=True, hide_index=True)


def _render_graph_query(run_id: str, query_graph: Callable[[str, dict[str, str | None]], dict[str, Any]]) -> None:
    st.subheader("Graph Query")
    options = {
        "nodeType": ["", "risk", "spec_gate", "plan_gate", "implementation", "targeted_verification", "review", "full_verification", "hermes", "documentation", "git_integration", "task_gate", "terra_diagnosis", "protected_incident"],
        "nodeStatus": ["", "not_started", "ready", "passed", "failed", "blocked", "skipped", "available", "unknown", "invalid"],
        "artifactKind": ["", "risk", "spec_gate", "plan_gate", "implementation", "targeted_verification", "review", "full_verification", "hermes", "documentation", "git_integration", "task_gate", "terra_diagnosis", "protected_incident"],
        "evidenceStatus": ["", "available", "unknown", "invalid", "blocked"],
        "edgeType": ["", "requires", "produces", "implements", "reviews", "verifies", "blocks", "derived_from", "committed_as", "documented_by"],
    }
    filters = {
        key: (st.selectbox(label, values, key=f"AGENT_GRAPH_QUERY_{key}") or None)
        for key, (label, values) in {
            "nodeType": ("Node type", options["nodeType"]),
            "nodeStatus": ("Node status", options["nodeStatus"]),
            "artifactKind": ("Artifact kind", options["artifactKind"]),
            "evidenceStatus": ("Evidence status", options["evidenceStatus"]),
            "edgeType": ("Edge type", options["edgeType"]),
        }.items()
    }
    filters["nodeId"] = st.text_input("Node ID", key="AGENT_GRAPH_QUERY_nodeId") or None
    result = query_graph(run_id, filters)
    if not isinstance(result, dict):
        st.warning("Graph Query 狀態：invalid")
        return
    status = result.get("status") if isinstance(result.get("status"), str) else "unknown"
    if status != "available":
        st.warning(f"Graph Query 狀態：{status}")
    identity = result.get("snapshotIdentity")
    if isinstance(identity, dict):
        st.caption(
            f"Snapshot: {identity.get('runId', 'unknown')} · "
            f"Fingerprint: {str(identity.get('graphFingerprint', 'unknown'))[:12]}"
        )
    st.write(
        f"Matched nodes: {len(result.get('matchedNodes', [])) if isinstance(result.get('matchedNodes'), list) else 0} · "
        f"Matched edges: {len(result.get('matchedEdges', [])) if isinstance(result.get('matchedEdges'), list) else 0} · "
        f"Unknown: {result.get('unknownCount', 0)} · Invalid: {result.get('invalidCount', 0)} · Blocked: {result.get('blockedCount', 0)}"
    )


def _safe_count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _render_governance_telemetry(telemetry: Any) -> None:
    st.subheader("Governance telemetry")
    if not isinstance(telemetry, dict):
        st.info("尚無可用 Governance telemetry snapshot；此頁不會自行收集 telemetry。")
        return
    status = telemetry.get("status") if isinstance(telemetry.get("status"), str) else "unknown"
    if status == "unavailable":
        st.info("尚無可用 Governance telemetry snapshot；此頁不會自行收集 telemetry。")
    elif status != "available":
        st.warning(f"Governance telemetry 狀態：{status}")

    coverage = telemetry.get("coverage") if isinstance(telemetry.get("coverage"), dict) else {}
    columns = st.columns(4)
    for column, label, key in zip(
        columns,
        ("Eligible runs", "Unknown runs", "Diagnostics", "Stale evidence"),
        ("eligibleRunCount", "unknownRunCount", "diagnosticCount", None),
    ):
        with column:
            if key is not None:
                st.metric(label, _safe_count(coverage.get(key)))
            else:
                evidence = telemetry.get("evidenceHealth")
                stale = evidence.get("stale") if isinstance(evidence, dict) else {}
                st.metric(label, _safe_count(stale.get("total")))

    latest = telemetry.get("latestRunUpdatedAt")
    st.caption(f"Latest run: {latest if isinstance(latest, str) and latest else '未提供'}")

    gate_failures = telemetry.get("gateFailures")
    if isinstance(gate_failures, dict):
        rows = []
        for key, label in (("specGate", "Spec gate"), ("planGate", "Plan gate"), ("taskGate", "Task gate")):
            metric = gate_failures.get(key)
            if not isinstance(metric, dict):
                continue
            rows.append({
                "Gate": label,
                "Status": metric.get("status", "unknown"),
                "Failed": _safe_count(metric.get("failed")),
                "Blocked": _safe_count(metric.get("blocked")),
                "Unknown": _safe_count(metric.get("unknownCount")),
            })
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)

    activity = telemetry.get("agentActivity")
    if isinstance(activity, dict):
        luna = activity.get("lunaRepair")
        if isinstance(luna, dict):
            st.caption(f"Luna repair loops: {_safe_count(luna.get('total'))} · status {luna.get('status', 'unknown')}")
        terra = activity.get("terraDiagnosis")
        if isinstance(terra, dict) and terra.get("status") == "unknown":
            st.caption("Terra diagnosis: unknown（缺少可驗證 evidence）")


def _render_run_details(
    run: dict[str, Any],
    *,
    query_graph: Callable[[str, dict[str, str | None]], dict[str, Any]] | None = None,
    lineage_lookup: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    catalog_lookup: Callable[[str, str], dict[str, Any]] | None = None,
) -> None:
    st.subheader("Selected run")
    st.write(f"**{run.get('briefName', '未提供')}** · {run.get('runId', '未提供')}")
    st.write(
        f"Status: {run.get('status', '未提供')} · Stage: {run.get('stage', '未提供')} · "
        f"Updated: {run.get('updatedAt', '未提供')}"
    )

    stages = run.get("stages", {})
    if isinstance(stages, dict):
        st.caption("Timeline")
        st.dataframe(
            [
                {"Stage": name, "Available": detail.get("available", False), "Duration (ms)": detail.get("durationMs")}
                for name, detail in stages.items()
                if isinstance(detail, dict)
            ],
            use_container_width=True,
            hide_index=True,
        )

    findings = run.get("findings", {})
    verification = run.get("verification", {})
    hermes = run.get("hermes", {})
    st.write(
        f"Findings: {findings.get('count', 0) if isinstance(findings, dict) else 0} · "
        f"Verification: {verification.get('status', 'unavailable') if isinstance(verification, dict) else 'unavailable'} · "
        f"Hermes: {hermes.get('status', 'unavailable') if isinstance(hermes, dict) else 'unavailable'}"
    )
    st.write(f"Token usage: {token_usage_label(run.get('tokenUsage'))}")
    documentation = run.get("documentation", {})
    if isinstance(documentation, dict):
        st.caption(
            "Documentation: "
            f"{documentation.get('status', 'not_requested')} · "
            f"proposals {documentation.get('proposalCount', 0)} · "
            f"applied {documentation.get('appliedTargetCount', 0)} · "
            f"pending approval {documentation.get('pendingApprovalCount', 0)}"
        )
    if query_graph is not None and isinstance(run.get("runId"), str):
        _render_graph_query(run["runId"], query_graph)
    render_governance_graph_workspace(
        run, query_graph=query_graph, lineage_lookup=lineage_lookup, catalog_lookup=catalog_lookup, streamlit_module=st,
    )


def _render_retention_and_diagnostics(snapshot: dict[str, Any]) -> None:
    st.subheader("Retention and diagnostics")
    retention = snapshot.get("retention", {})
    if isinstance(retention, dict) and retention.get("status") == "unavailable":
        st.warning("Retention policy unavailable")
    elif isinstance(retention, dict):
        st.write(
            f"Retain days: {retention.get('retainDays', '未提供')} · "
            f"Latest terminal runs: {retention.get('retainLatestTerminalRuns', '未提供')}"
        )

    diagnostics = snapshot.get("diagnostics", [])
    if diagnostics:
        st.warning(f"Diagnostics: {len(diagnostics)}")
        st.dataframe(diagnostics, use_container_width=True, hide_index=True)
    else:
        st.caption("Diagnostics: 0")


def render_agent_operations(
    snapshot: dict,
    *,
    on_refresh: Callable[[], None],
    query_graph: Callable[[str, dict[str, str | None]], dict[str, Any]] | None = None,
    lineage_lookup: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    catalog_lookup: Callable[[str, str], dict[str, Any]] | None = None,
) -> None:
    if snapshot.get("schemaVersion") != SNAPSHOT_SCHEMA:
        st.warning("Agent operations snapshot schema unavailable")
        return

    st.title("Agent Operations")
    if st.button("Refresh", key="AGENT_OPERATIONS_REFRESH"):
        on_refresh()

    _render_governance_telemetry(snapshot.get("governanceTelemetry"))

    summary = snapshot.get("summary", {})
    metric_columns = st.columns(5)
    metrics = [
        ("Runs", "runCount"),
        ("Active", "activeCount"),
        ("Awaiting authorization", "awaitingAuthorizationCount"),
        ("Completed", "completedCount"),
        ("Blocked / failed", None),
    ]
    for column, (label, key) in zip(metric_columns, metrics):
        with column:
            value = (
                _metric_value(summary, key)
                if key is not None
                else _metric_value(summary, "blockedCount") + _metric_value(summary, "failedCount")
            )
            st.metric(label, value)

    runs = snapshot.get("runs", [])
    runs = runs if isinstance(runs, list) else []
    statuses = sorted({item.get("status") for item in runs if isinstance(item, dict) and item.get("status")})
    selected_statuses = st.multiselect(
        "Status", statuses, default=statuses, key="AGENT_OPERATIONS_STATUS_FILTER"
    )
    filter_columns = st.columns(3)
    with filter_columns[0]:
        date_from = _date_value(
            st.date_input("From", value=None, key="AGENT_OPERATIONS_DATE_FROM")
        )
    with filter_columns[1]:
        date_to = _date_value(
            st.date_input("To", value=None, key="AGENT_OPERATIONS_DATE_TO")
        )
    with filter_columns[2]:
        brief_query = st.text_input("Brief", key="AGENT_OPERATIONS_BRIEF_FILTER")
    filtered = filter_agent_runs(runs, set(selected_statuses), date_from, date_to, brief_query)
    st.dataframe(_run_table(filtered), use_container_width=True, hide_index=True)

    if not runs:
        st.info("尚無 Agent runs")
    elif filtered:
        selected_state = getattr(st, "session_state", None)
        if hasattr(selected_state, "get") and hasattr(selected_state, "pop"):
            selected_value = selected_state.get(SELECTED_RUN_KEY)
            valid_ids = {item.get("runId") for item in filtered if isinstance(item, dict)}
            if selected_value not in valid_ids:
                selected_state.pop(SELECTED_RUN_KEY, None)
        selected_id = st.selectbox(
            "Run",
            [item["runId"] for item in filtered],
            key=SELECTED_RUN_KEY,
        )
        selected = next((item for item in filtered if item.get("runId") == selected_id), filtered[0])
        _render_run_details(selected, query_graph=query_graph, lineage_lookup=lineage_lookup, catalog_lookup=catalog_lookup)
    else:
        st.info("目前篩選條件沒有 Agent runs")

    _render_retention_and_diagnostics(snapshot)
