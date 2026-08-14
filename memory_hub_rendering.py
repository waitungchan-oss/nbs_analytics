from __future__ import annotations

from typing import Any, Callable

import streamlit as st

from backend.agents.memory_hub_ui_service import MemoryHubUiReadModel


def _catalog_status_rows(model: MemoryHubUiReadModel) -> list[dict[str, object]]:
    catalog = model.catalog
    status = {
        "catalog_missing": "missing",
        "invalid_catalog": "invalid",
        "query_invalid": "blocked",
    }.get(model.status, model.status if model.status in {"ready", "empty", "timeout", "degraded", "blocked", "invalid", "missing"} else "blocked")
    return [{
        "Status": status,
        "Catalog fingerprint": catalog.get("catalogFingerprint", "未提供"),
        "Built from Git head": catalog.get("builtFromHead", "未提供"),
        "Sources": catalog.get("sourceCount", 0),
        "Records": catalog.get("recordCount", 0),
        "Policy fingerprint": catalog.get("policyFingerprint", "未提供"),
        "Policy version": catalog.get("policyVersion", "未提供"),
        "Freshness": catalog.get("freshnessSummary", "未提供"),
    }]


def _record_rows(model: MemoryHubUiReadModel) -> list[dict[str, object]]:
    return [
        {
            "Memory ID": row.get("memoryId", ""),
            "Kind": row.get("memoryKind", ""),
            "Summary": row.get("summary", ""),
            "Owner": row.get("owner", ""),
            "Scope": row.get("scope", ""),
            "Freshness": row.get("freshness", ""),
            "Status": row.get("status", ""),
            "Sources": row.get("sourceCount", 0),
        }
        for row in model.records
    ]


def _decision_rows(model: MemoryHubUiReadModel) -> list[dict[str, object]]:
    return [
        {
            "Consumer": row.get("consumerId", ""),
            "Requested scope": row.get("requestedScope", ""),
            "Record scope": row.get("recordScope", ""),
            "Decision": row.get("decision", ""),
            "Reason": row.get("reason", ""),
        }
        for row in model.decisions
    ]


def _source_rows(model: MemoryHubUiReadModel) -> list[dict[str, object]]:
    if not model.source:
        return []
    source = model.source
    return [{
        "Source ID": source.get("sourceId", ""),
        "Source kind": source.get("sourceKind", ""),
        "Artifact ref": source.get("artifactRef", ""),
        "Artifact SHA-256": source.get("artifactSha256", ""),
        "Run ID": source.get("runId"),
        "Git head": source.get("gitHead"),
        "Generated": source.get("generatedAt", ""),
        "Expires": source.get("expiresAt", ""),
        "Source fingerprint": source.get("sourceFingerprint", ""),
        "Status": source.get("status", ""),
    }]


def _render_query_controls(model: MemoryHubUiReadModel, *, query_callback: Callable[..., MemoryHubUiReadModel] | None, source_callback: Callable[..., MemoryHubUiReadModel] | None, st_module: Any) -> None:
    if query_callback is None or not hasattr(st_module, "text_input"):
        return
    query = st_module.text_input("Query", key="MEMORY_HUB_QUERY", max_chars=512)
    scope = st_module.selectbox("Scope", ["project", "agent", "team"], key="MEMORY_HUB_SCOPE") if hasattr(st_module, "selectbox") else "project"
    kinds = st_module.multiselect("Memory kind", ["governance", "evidence", "skill"], default=["governance"], key="MEMORY_HUB_KINDS") if hasattr(st_module, "multiselect") else ["governance"]
    consumer = st_module.text_input("Consumer identity", key="MEMORY_HUB_CONSUMER", max_chars=128)
    team = st_module.text_input("Team identity (optional)", key="MEMORY_HUB_TEAM", max_chars=128)
    st_module.caption("固定 query budget：最多 3 筆、6,000 bytes、800 ms；此頁不會自行建立或更新 catalog。")
    if hasattr(st_module, "button") and st_module.button("Query Memory Hub", key="MEMORY_HUB_QUERY_SUBMIT"):
        result = query_callback(query=query, consumer_id=consumer, scope=scope, memory_kinds=tuple(kinds), team_id=team or None)
        st_module.info(f"Query status: {result.status}") if result.status in {"ready", "empty"} else st_module.warning(", ".join(result.diagnostics or (result.status,)))
        if result.status == "ready" and result.records:
            st_module.dataframe(_record_rows(result), use_container_width=True, hide_index=True)
        if result.decisions:
            st_module.dataframe(_decision_rows(result), use_container_width=True, hide_index=True)
        if result.diagnostics:
            st_module.warning("; ".join(result.diagnostics))
        if result.status == "ready" and result.records and source_callback is not None and hasattr(st_module, "selectbox"):
            options = [str(row.get("memoryId", "")) for row in result.records]
            selected_id = st_module.selectbox("Memory record", options, key="MEMORY_HUB_RECORD")
            selected = next((row for row in result.records if row.get("memoryId") == selected_id), result.records[0])
            source_ids = tuple(selected.get("sourceIds", ()))[:3]
            if source_ids:
                source_id = st_module.selectbox("Source evidence", list(source_ids), key="MEMORY_HUB_SOURCE")
                source_result = source_callback(source_id=source_id, consumer_id=consumer, team_id=team or None)
                if source_result.source:
                    st_module.dataframe(_source_rows(source_result), use_container_width=True, hide_index=True)
                else:
                    st_module.warning("; ".join(source_result.diagnostics or (source_result.status,)))


def render_memory_hub(
    model: MemoryHubUiReadModel,
    *,
    query_callback: Callable[..., MemoryHubUiReadModel] | None,
    source_callback: Callable[..., MemoryHubUiReadModel] | None,
    st_module: Any = st,
) -> None:
    st_module.title("Memory Hub")
    st_module.caption("Memory Hub 是 non-authoritative read-only memory；canonical artifacts 與正式 context 仍是真相來源。")
    st_module.dataframe(_catalog_status_rows(model), use_container_width=True, hide_index=True)

    if model.status == "catalog_missing":
        st_module.info("尚無已建 Memory Hub catalog；此頁不會自行建立或更新 catalog。")
        return
    if model.status in {"invalid_catalog", "blocked", "query_invalid", "empty", "timeout", "degraded"}:
        st_module.warning("Memory Hub 目前無法提供 ready records：" + ", ".join(model.diagnostics or (model.status,)))
        if model.decisions:
            st_module.dataframe(_decision_rows(model), use_container_width=True, hide_index=True)
        return

    _render_query_controls(model, query_callback=query_callback, source_callback=source_callback, st_module=st_module)
    if model.records:
        st_module.dataframe(_record_rows(model), use_container_width=True, hide_index=True)
    else:
        st_module.caption("目前沒有符合條件的 Memory records。")
    if model.decisions:
        st_module.dataframe(_decision_rows(model), use_container_width=True, hide_index=True)
    if model.source:
        st_module.dataframe(_source_rows(model), use_container_width=True, hide_index=True)
