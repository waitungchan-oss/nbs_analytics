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
