from __future__ import annotations

from pathlib import PurePosixPath
import re
from typing import Any, Callable

import streamlit as st


SELECTED_EVIDENCE_KEY = "AGENT_OPERATIONS_SELECTED_EVIDENCE"
SELECTED_CATALOG_KEY = "AGENT_OPERATIONS_SELECTED_CATALOG"
CATALOG_READ_SCHEMA = "governance-graph-owner-dependency-read-v1"
CATALOG_PUBLIC_KEYS = frozenset({"schemaVersion", "status", "snapshotFingerprint", "ownerCatalogFingerprint", "dependencyCatalogFingerprint", "readModelFingerprint", "owners", "dependencies", "coverage", "diagnostics"})
CATALOG_COVERAGE_KEYS = frozenset({"ownerStatus", "dependencyStatus", "ownerEntries", "dependencyEntries", "unknownCount", "missingCount", "staleCount", "blockedCount"})
OWNER_ROLES = frozenset({"context", "review", "implementation", "verification", "hermes", "documentation"})
RELATIONS = frozenset({"requires", "produces", "implements", "reviews", "verifies", "blocks", "derived_from", "committed_as", "documented_by"})
RELATION_KINDS = frozenset({"workflow_edge", "evidence_edge", "governance_edge"})
LINEAGE_INPUT_SCHEMA = "governance-graph-evidence-lineage-input-v1"
LINEAGE_OUTPUT_SCHEMA = "governance-graph-evidence-lineage-v1"
CANONICAL_PATHS = frozenset({"task-gate.json", "terra-diagnosis.json", "protected-incident.json"})
SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
SAFE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+@#%=-]{0,127}$")
MAX_EVIDENCE = 12
SAFE_STATUSES = frozenset({"available", "unavailable", "unknown", "invalid", "blocked", "stale", "fingerprint_mismatch", "missing", "not_started", "ready", "passed", "failed", "skipped", "awaiting_authorization", "completed"})


def _safe_basename(value: Any) -> str | None:
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    path = PurePosixPath(value)
    if len(path.parts) != 1 or path.name != value or value in {".", ".."}:
        return None
    return value


def _safe_sha(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        return None
    return value


def _safe_value(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 128 or "/" in value or "\\" in value or ".." in value:
        return None
    if re.search(r"(?:sk-[A-Za-z0-9_-]{6,}|ghp_[A-Za-z0-9_-]{6,})", value, re.IGNORECASE):
        return None
    if any(token in value.lower() for token in ("prompt", "secret", "command", "stdout", "stderr")):
        return None
    return value if SAFE_VALUE.fullmatch(value) else None


def _canonical_rows(graph: dict[str, Any]) -> list[dict[str, str]]:
    values = graph.get("evidence")
    nodes = {item.get("nodeId"): item for item in graph.get("nodes", []) if isinstance(item, dict)}
    if not isinstance(values, list):
        return []
    rows: list[dict[str, str]] = []
    for item in values[:MAX_EVIDENCE]:
        if not isinstance(item, dict):
            continue
        artifact = _safe_basename(item.get("artifact"))
        sha256 = _safe_sha(item.get("sha256"))
        node_id = item.get("nodeId")
        if artifact not in CANONICAL_PATHS or not sha256 or _safe_value(node_id) is None:
            continue
        node = nodes.get(node_id)
        if not isinstance(node, dict) or node.get("nodeId") != node_id:
            continue
        rows.append({"nodeId": node_id, "artifact": artifact, "sha256": sha256, "status": str(item.get("status", "unknown"))})
    return rows


def _render_lineage_result(result: Any) -> None:
    if not isinstance(result, dict) or result.get("schemaVersion") != LINEAGE_OUTPUT_SCHEMA:
        st.warning("Evidence lineage 狀態：invalid")
        return
    status = result.get("status") if result.get("status") in SAFE_STATUSES else "invalid"
    if status != "available":
        st.warning(f"Evidence lineage 狀態：{status}")
    evidence = result.get("evidence")
    if isinstance(evidence, list):
        rows = []
        for item in evidence[:MAX_EVIDENCE]:
            if not isinstance(item, dict):
                continue
            path = _safe_basename(item.get("path"))
            artifact_kind = item.get("artifactKind")
            item_status = item.get("status")
            if path in CANONICAL_PATHS and _safe_value(artifact_kind) and item_status in SAFE_STATUSES:
                rows.append({
                    "Artifact": path, "Kind": artifact_kind, "Schema": _safe_value(item.get("schemaVersion")) or "unknown",
                    "Writer": _safe_value(item.get("writer")) or "unknown", "Status": item_status,
                    "Reason": _safe_value(item.get("reasonCode")) or "", "Finalized at": _safe_value(item.get("finalizedAt")) or "未提供",
                    "Fingerprint matched": bool(item.get("fingerprintMatched")),
                })
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
    links = result.get("links")
    if isinstance(links, list) and links:
        safe_links = [{"Relation": _safe_value(item.get("relation")) or "unknown", "Source": _safe_value(item.get("sourceIdentity")) or "unknown"} for item in links[:MAX_EVIDENCE] if isinstance(item, dict)]
        if safe_links:
            st.dataframe(safe_links, use_container_width=True, hide_index=True)
    if isinstance(result.get("diagnostics"), list) and result.get("diagnostics"):
        st.caption("Evidence lineage diagnostics unavailable")


def _render_derived_status(name: str, callback: Callable[..., dict[str, Any]] | None, run: dict[str, Any]) -> None:
    if callback is None:
        st.caption(f"{name}：unavailable；目前沒有可供 UI 消費的 validated read model。")
        return
    try:
        result = callback(run)
    except Exception:
        result = None
    if not isinstance(result, dict) or not isinstance(result.get("status"), str):
        st.caption(f"{name}：unavailable；目前沒有可供 UI 消費的 validated read model。")
        return
    status = result["status"] if result["status"] in SAFE_STATUSES else "invalid"
    st.caption(f"{name}：{status}")
    summary = result.get("summary")
    if isinstance(summary, dict):
        safe = {str(key): (_safe_value(value) if isinstance(value, str) else value) for key, value in summary.items() if isinstance(key, str) and isinstance(value, (str, int, float, bool)) and (not isinstance(value, str) or _safe_value(value) is not None)}
        if safe:
            st.write(safe)


def _render_catalog_lookup(
    run: dict[str, Any],
    snapshot_fingerprint: str,
    callback: Callable[[str, str], dict[str, Any]] | None,
) -> None:
    """Render an externally supplied, validated catalog read model only."""
    state = getattr(st, "session_state", None)
    if callback is None:
        if hasattr(state, "pop"):
            state.pop(SELECTED_CATALOG_KEY, None)
        st.caption("Owner / dependency catalog：unavailable；目前沒有可供 UI 消費的 validated read model。")
        return
    try:
        result = callback(run["runId"], snapshot_fingerprint)
    except Exception:
        result = None
    if not isinstance(result, dict):
        result = None
    if not _valid_catalog_payload(result, snapshot_fingerprint):
        result = None
    status = result.get("status") if result and result.get("status") in SAFE_STATUSES else "invalid"
    if status != "available":
        if hasattr(state, "pop"):
            state.pop(SELECTED_CATALOG_KEY, None)
        st.caption(f"Owner / dependency catalog：{status}")
        return
    if result.get("snapshotFingerprint") != snapshot_fingerprint:
        if hasattr(state, "pop"):
            state.pop(SELECTED_CATALOG_KEY, None)
        st.caption("Owner / dependency catalog：stale")
        return
    owners = result.get("owners")
    dependencies = result.get("dependencies")
    owner_rows = []
    if isinstance(owners, list):
        for item in owners[:MAX_EVIDENCE]:
            if not isinstance(item, dict) or item.get("snapshotFingerprint") != snapshot_fingerprint:
                continue
            subject = item.get("subject")
            owner = item.get("owner")
            if not isinstance(subject, dict) or not isinstance(owner, dict):
                continue
            subject_kind = _safe_value(subject.get("kind"))
            subject_id = _safe_value(subject.get("id"))
            owner_kind = _safe_value(owner.get("kind"))
            owner_id = _safe_value(owner.get("id"))
            if not all((subject_kind, subject_id, owner_kind == "governance_role", owner_id)):
                continue
            owner_rows.append({"Subject": f"{subject_kind}:{subject_id}", "Owner role": owner_id, "Status": item.get("status") if item.get("status") in SAFE_STATUSES else "invalid"})
    dependency_rows = []
    if isinstance(dependencies, list):
        for item in dependencies[:MAX_EVIDENCE]:
            if not isinstance(item, dict) or item.get("snapshotFingerprint") != snapshot_fingerprint:
                continue
            source = item.get("from")
            target = item.get("to")
            relation = _safe_value(item.get("relation"))
            relation_kind = _safe_value(item.get("relationKind"))
            if not isinstance(source, dict) or not isinstance(target, dict) or not relation or not relation_kind:
                continue
            source_kind, source_id = _safe_value(source.get("kind")), _safe_value(source.get("id"))
            target_kind, target_id = _safe_value(target.get("kind")), _safe_value(target.get("id"))
            if not all((source_kind, source_id, target_kind, target_id)):
                continue
            dependency_rows.append({"From": f"{source_kind}:{source_id}", "To": f"{target_kind}:{target_id}", "Relation": relation, "Relation kind": relation_kind, "Status": item.get("status") if item.get("status") in SAFE_STATUSES else "invalid"})
    if hasattr(state, "get") and hasattr(state, "pop"):
        selected = state.get(SELECTED_CATALOG_KEY)
        if isinstance(selected, dict) and (selected.get("runId") != run["runId"] or selected.get("snapshotFingerprint") != snapshot_fingerprint):
            state.pop(SELECTED_CATALOG_KEY, None)
    st.caption("Owner / dependency catalog：available")
    if owner_rows:
        st.dataframe(owner_rows, use_container_width=True, hide_index=True)
    if dependency_rows:
        st.dataframe(dependency_rows, use_container_width=True, hide_index=True)
    identities = [*[("owner", row["Subject"]) for row in owner_rows], *[("dependency", f"{row['From']}->{row['To']}:{row['Relation']}") for row in dependency_rows]]
    if identities:
        label = st.selectbox("Catalog subject / relation", [item[1] for item in identities], key="AGENT_OPERATIONS_CATALOG_SELECT")
        if hasattr(state, "__setitem__"):
            state[SELECTED_CATALOG_KEY] = {"runId": run["runId"], "snapshotFingerprint": snapshot_fingerprint, "kind": next((kind for kind, identity in identities if identity == label), "unknown"), "identity": label}
    elif hasattr(state, "pop"):
        state.pop(SELECTED_CATALOG_KEY, None)


def _valid_catalog_payload(result: dict[str, Any] | None, snapshot_fingerprint: str) -> bool:
    if not isinstance(result, dict) or set(result) != CATALOG_PUBLIC_KEYS or result.get("schemaVersion") != CATALOG_READ_SCHEMA or result.get("status") not in SAFE_STATUSES:
        return False
    status = result["status"]
    if status == "available":
        if result.get("snapshotFingerprint") != snapshot_fingerprint or not _safe_sha(result.get("readModelFingerprint")):
            return False
    elif result.get("snapshotFingerprint") not in (None, snapshot_fingerprint):
        return False
    for key in ("ownerCatalogFingerprint", "dependencyCatalogFingerprint"):
        value = result.get(key)
        if value is not None and not _safe_sha(value):
            return False
    owners, dependencies = result.get("owners"), result.get("dependencies")
    if not isinstance(owners, list) or not isinstance(dependencies, list) or len(owners) > MAX_EVIDENCE or len(dependencies) > MAX_EVIDENCE:
        return False
    for item in owners:
        if not isinstance(item, dict) or set(item) != {"subject", "owner", "source", "snapshotFingerprint", "status"} or item.get("snapshotFingerprint") != snapshot_fingerprint or item.get("status") not in SAFE_STATUSES:
            return False
        subject, owner, source = item["subject"], item["owner"], item["source"]
        if not (isinstance(subject, dict) and set(subject) == {"kind", "id"} and _safe_value(subject.get("kind")) and _safe_value(subject.get("id"))):
            return False
        if not (isinstance(owner, dict) and set(owner) == {"kind", "id"} and owner.get("kind") == "governance_role" and owner.get("id") in OWNER_ROLES):
            return False
        if not _valid_catalog_source(source):
            return False
    for item in dependencies:
        if not isinstance(item, dict) or set(item) != {"from", "to", "relation", "relationKind", "source", "snapshotFingerprint", "status"} or item.get("snapshotFingerprint") != snapshot_fingerprint or item.get("status") not in SAFE_STATUSES:
            return False
        if not (isinstance(item["from"], dict) and isinstance(item["to"], dict) and _valid_subject(item["from"]) and _valid_subject(item["to"])):
            return False
        if item.get("relation") not in RELATIONS or item.get("relationKind") not in RELATION_KINDS or not _valid_catalog_source(item.get("source")):
            return False
    coverage = result.get("coverage")
    if not isinstance(coverage, dict) or set(coverage) != CATALOG_COVERAGE_KEYS:
        return False
    if any(coverage.get(key) not in SAFE_STATUSES for key in ("ownerStatus", "dependencyStatus")):
        return False
    if any(isinstance(coverage.get(key), bool) or not isinstance(coverage.get(key), int) or coverage.get(key) < 0 for key in CATALOG_COVERAGE_KEYS - {"ownerStatus", "dependencyStatus"}):
        return False
    diagnostics = result.get("diagnostics")
    return isinstance(diagnostics, list) and len(diagnostics) <= MAX_EVIDENCE and all(isinstance(item, dict) and _safe_value(item.get("code")) and isinstance(item.get("summary"), str) and _safe_value(item.get("summary")) for item in diagnostics)


def _valid_subject(value: Any) -> bool:
    return set(value) == {"kind", "id"} and bool(_safe_value(value.get("kind"))) and bool(_safe_value(value.get("id")))


def _valid_catalog_source(value: Any) -> bool:
    return isinstance(value, dict) and set(value) == {"kind", "identity", "fingerprint"} and value.get("kind") in {"approved_catalog", "graph_contract", "canonical_evidence"} and bool(_safe_value(value.get("identity"))) and bool(_safe_sha(value.get("fingerprint")))


def render_governance_graph_workspace(
    run: dict[str, Any],
    *,
    query_graph: Callable[..., dict[str, Any]] | None = None,
    lineage_lookup: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    comparison_lookup: Callable[..., dict[str, Any]] | None = None,
    risk_summary_lookup: Callable[..., dict[str, Any]] | None = None,
    impact_lookup: Callable[..., dict[str, Any]] | None = None,
    catalog_lookup: Callable[[str, str], dict[str, Any]] | None = None,
    streamlit_module: Any | None = None,
) -> None:
    global st
    original_streamlit = st
    if streamlit_module is not None:
        st = streamlit_module
    try:
        _render_governance_graph_workspace(
            run,
            query_graph=query_graph,
            lineage_lookup=lineage_lookup,
            comparison_lookup=comparison_lookup,
            risk_summary_lookup=risk_summary_lookup,
            impact_lookup=impact_lookup,
            catalog_lookup=catalog_lookup,
        )
    finally:
        st = original_streamlit


def _render_governance_graph_workspace(
    run: dict[str, Any],
    *,
    query_graph: Callable[..., dict[str, Any]] | None,
    lineage_lookup: Callable[[dict[str, Any]], dict[str, Any]] | None,
    comparison_lookup: Callable[..., dict[str, Any]] | None,
    risk_summary_lookup: Callable[..., dict[str, Any]] | None,
    impact_lookup: Callable[..., dict[str, Any]] | None,
    catalog_lookup: Callable[[str, str], dict[str, Any]] | None,
) -> None:
    graph = run.get("governanceGraph") if isinstance(run, dict) else None
    state = getattr(st, "session_state", None)
    st.subheader("Governance Graph")
    if not isinstance(graph, dict) or graph.get("status") == "unavailable":
        if hasattr(state, "pop"):
            state.pop(SELECTED_EVIDENCE_KEY, None)
            state.pop(SELECTED_CATALOG_KEY, None)
        st.info("尚無已建 Graph snapshot；此頁不會自行建立或更新 snapshot。")
        return
    if graph.get("status") != "available":
        if hasattr(state, "pop"):
            state.pop(SELECTED_EVIDENCE_KEY, None)
            state.pop(SELECTED_CATALOG_KEY, None)
        st.warning("Governance Graph 狀態：invalid")
        return
    run_id = run.get("runId")
    snapshot_fingerprint = graph.get("snapshotFingerprint")
    if not isinstance(run_id, str) or not SAFE_RUN_ID.fullmatch(run_id) or not _safe_sha(snapshot_fingerprint):
        if hasattr(state, "pop"):
            state.pop(SELECTED_EVIDENCE_KEY, None)
            state.pop(SELECTED_CATALOG_KEY, None)
        st.warning("Governance Graph 狀態：invalid")
        return
    st.write(
        f"Overall status: {graph.get('overallStatus', 'unknown')} · "
        f"Freshness: {graph.get('freshness', 'unknown')} · "
        f"Fingerprint: {str(graph.get('snapshotFingerprint', 'unknown'))[:12]}"
    )
    nodes = graph.get("nodes", graph.get("nodeStatuses", []))
    node_rows = [
        {"Node": item.get("nodeId"), "Status": item.get("status"), "Reason": _safe_value(item.get("reasonCode")) or ""}
        for item in nodes[:MAX_EVIDENCE]
        if isinstance(item, dict) and _safe_value(item.get("nodeId")) and item.get("status") in SAFE_STATUSES
    ]
    if node_rows:
        st.dataframe(node_rows, use_container_width=True, hide_index=True)
    evidence_rows = [
        {"Node": item.get("nodeId"), "Artifact": _safe_basename(item.get("artifact")), "SHA-256": str(item.get("sha256"))[:8], "Status": item.get("status")}
        for item in graph.get("evidence", [])[:MAX_EVIDENCE]
        if isinstance(item, dict) and _safe_basename(item.get("artifact")) and _safe_sha(item.get("sha256")) and item.get("status") in SAFE_STATUSES
    ]
    if evidence_rows:
        st.dataframe(evidence_rows, use_container_width=True, hide_index=True)
    blockers = [item for item in graph.get("blockers", [])[:MAX_EVIDENCE] if isinstance(item, dict) and _safe_value(item.get("code"))]
    if blockers:
        st.caption("Graph blockers/diagnostics: " + ", ".join(item["code"] for item in blockers))
    st.subheader("Canonical evidence lineage")
    rows = _canonical_rows(graph)
    if not rows:
        if hasattr(state, "pop"):
            state.pop(SELECTED_EVIDENCE_KEY, None)
        st.info("目前沒有可供 E-1 lineage drill-down 的 canonical evidence node。")
    else:
        existing = state.get(SELECTED_EVIDENCE_KEY) if hasattr(state, "get") else None
        if isinstance(existing, dict) and (
            existing.get("runId") != run_id
            or existing.get("snapshotFingerprint") != snapshot_fingerprint
            or not any(existing.get("nodeId") == row["nodeId"] and existing.get("path") == row["artifact"] and existing.get("sha256") == row["sha256"] for row in rows)
        ) and hasattr(state, "pop"):
            state.pop(SELECTED_EVIDENCE_KEY, None)
        labels = [f"{row['nodeId']} · {row['artifact']}" for row in rows]
        selected_label = st.selectbox("Canonical evidence", labels, key="AGENT_OPERATIONS_EVIDENCE_SELECT")
        selected = rows[labels.index(selected_label)] if selected_label in labels else None
        if selected is not None:
            selected_key = {
                "runId": run.get("runId"), "nodeId": selected["nodeId"], "path": selected["artifact"],
                "sha256": selected["sha256"], "snapshotFingerprint": graph.get("snapshotFingerprint"),
            }
            if hasattr(state, "__setitem__"):
                state[SELECTED_EVIDENCE_KEY] = selected_key
            if lineage_lookup is None:
                st.info("Evidence lineage callback unavailable。")
            else:
                request = {
                    "schemaVersion": LINEAGE_INPUT_SCHEMA,
                    "runId": run.get("runId"),
                    "snapshotFingerprint": graph.get("snapshotFingerprint"),
                    "source": {"kind": "node", "identity": selected["nodeId"]},
                    "evidence": {"path": selected["artifact"], "sha256": selected["sha256"]},
                }
                _render_lineage_result(lineage_lookup(request))
    st.subheader("Derived analysis")
    st.subheader("Owner / dependency catalog")
    _render_catalog_lookup(run, snapshot_fingerprint, catalog_lookup)
    _render_derived_status("Snapshot comparison", comparison_lookup, run)
    _render_derived_status("Risk summary", risk_summary_lookup, run)
    _render_derived_status("Change impact", impact_lookup, run)


__all__ = ["SELECTED_EVIDENCE_KEY", "SELECTED_CATALOG_KEY", "render_governance_graph_workspace"]
