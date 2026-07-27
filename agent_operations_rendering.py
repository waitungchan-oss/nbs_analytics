from __future__ import annotations

from datetime import date, datetime
from typing import Callable, Any

import streamlit as st


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


def _render_run_details(run: dict[str, Any]) -> None:
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
    _render_governance_graph(run.get("governanceGraph"))


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


def render_agent_operations(snapshot: dict, *, on_refresh: Callable[[], None]) -> None:
    if snapshot.get("schemaVersion") != SNAPSHOT_SCHEMA:
        st.warning("Agent operations snapshot schema unavailable")
        return

    st.title("Agent Operations")
    if st.button("Refresh", key="AGENT_OPERATIONS_REFRESH"):
        on_refresh()

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
        selected_id = st.selectbox(
            "Run",
            [item["runId"] for item in filtered],
            key=SELECTED_RUN_KEY,
        )
        selected = next((item for item in filtered if item.get("runId") == selected_id), filtered[0])
        _render_run_details(selected)
    else:
        st.info("目前篩選條件沒有 Agent runs")

    _render_retention_and_diagnostics(snapshot)
