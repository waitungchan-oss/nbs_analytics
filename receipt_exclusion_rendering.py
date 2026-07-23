from __future__ import annotations

from typing import Callable

import pandas as pd
import streamlit as st


CONFIRMATION_COPY = "我確認永久排除此精確收款單；日後相同 identity 將自動排除。"
GOVERNANCE_TABLE_HEIGHT: int = 320
GOVERNANCE_PREVIEW_STATE_KEY: str = "RECEIPT_EXCLUSION_GOVERNANCE_PREVIEW"

ACTIVE_GOVERNANCE_COLUMNS = [
    "選取", "規則 ID", "收款單號", "來源單據號", "排除類型",
    "建立時間", "建立者", "稽核事件數",
]
REVOKED_GOVERNANCE_COLUMNS = [
    "規則 ID", "收款單號", "來源單據號", "排除類型",
    "撤銷時間", "撤銷者", "稽核事件數",
]


def _governance_rows(rules: list[dict], *, revoked: bool = False) -> pd.DataFrame:
    rows = []
    for rule in rules:
        row = {
            "規則 ID": int(rule["id"]),
            "收款單號": str(rule.get("receiptNo") or ""),
            "來源單據號": str(rule.get("sourceOrderNo") or ""),
            "排除類型": str(rule.get("exclusionKind") or ""),
            "稽核事件數": int(rule.get("eventCount") or 0),
        }
        if revoked:
            row.update({
                "撤銷時間": str(rule.get("revokedAt") or ""),
                "撤銷者": str(rule.get("revokedBy") or ""),
            })
        else:
            row = {"選取": False, **row}
            row.update({
                "建立時間": str(rule.get("createdAt") or ""),
                "建立者": str(rule.get("createdBy") or ""),
            })
        rows.append(row)
    columns = REVOKED_GOVERNANCE_COLUMNS if revoked else ACTIVE_GOVERNANCE_COLUMNS
    return pd.DataFrame(rows, columns=columns)


def _selected_rule_ids(edited: pd.DataFrame) -> list[int]:
    if edited.empty or "選取" not in edited.columns or "規則 ID" not in edited.columns:
        return []
    selected = edited.loc[edited["選取"].astype(bool), "規則 ID"].tolist()
    return [int(rule_id) for rule_id in selected]


def _matching_governance_preview(
    preview: dict,
    *,
    rule_id: int | None,
    registry_revision: str,
) -> dict:
    required_fields = ("ruleId", "registryRevision", "status", "previewFingerprint")
    if (
        not isinstance(preview, dict)
        or rule_id is None
        or not registry_revision
        or any(field not in preview or preview[field] in (None, "") for field in required_fields)
        or preview["status"] != "revocation_ready"
    ):
        return {}

    try:
        preview_rule_id = int(preview["ruleId"])
        selected_rule_id = int(rule_id)
    except (TypeError, ValueError):
        return {}

    if (
        preview_rule_id != selected_rule_id
        or preview["registryRevision"] != registry_revision
    ):
        return {}
    return preview


def render_receipt_exclusion_confirmation(
    proposal: dict,
    *,
    confirm_action: Callable[[dict], dict],
) -> None:
    candidates = list(proposal.get("candidates") or [])
    if not candidates:
        return
    rows = [{
        "來源單據號": row.get("sourceOrderNo"), "收款單號": row.get("receiptNo"),
        "排除類型": row.get("exclusionKind"), "觀察金額": row.get("observedAmount"),
        "正式收入影響": row.get("affectedRevenue"),
    } for row in candidates]
    labels = {row["candidateId"]: f"{row.get('receiptNo')} / {row.get('sourceOrderNo')}" for row in candidates}
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    confirmed = st.checkbox(CONFIRMATION_COPY, value=False, key="RECEIPT_EXCLUSION_CONFIRM")
    selected = st.multiselect(
        "選擇要永久排除的精確收款單",
        options=[row["candidateId"] for row in candidates],
        format_func=lambda value: labels[value],
        key="RECEIPT_EXCLUSION_SELECTED",
    )
    if st.button(
        "永久排除並重新預演", type="primary", disabled=not confirmed or not selected,
        key="RECEIPT_EXCLUSION_APPLY",
    ):
        confirm_action({
            "proposalFingerprint": proposal.get("proposalFingerprint"),
            "selectedCandidateIds": selected,
            "confirmedBy": "streamlit-local",
        })


def render_receipt_exclusion_governance(
    snapshot: dict,
    *,
    preview_revoke: Callable[[int], dict],
    confirm_revoke: Callable[[int, str], dict],
) -> None:
    active = list(snapshot.get("active") or [])
    revoked = list(snapshot.get("revoked") or [])
    st.markdown("### Receipt Exclusion Governance")
    st.caption("永久排除規則只影響精確 identity；撤銷前必須在暫存資料庫重播並通過口徑驗收。")
    st.dataframe(pd.DataFrame(active), hide_index=True, width="stretch")
    if revoked:
        st.dataframe(pd.DataFrame(revoked), hide_index=True, width="stretch")
    for rule in active:
        rule_id = int(rule["id"])
        if st.button(f"預覽撤銷 #{rule_id}", key=f"RECEIPT_EXCLUSION_PREVIEW_{rule_id}"):
            preview = preview_revoke(rule_id)
            st.session_state[f"RECEIPT_EXCLUSION_PREVIEW_{rule_id}"] = preview
        preview = st.session_state.get(f"RECEIPT_EXCLUSION_PREVIEW_{rule_id}") or {}
        if preview:
            st.write({key: value for key, value in preview.items() if key != "gate"})
            ready = preview.get("status") == "revocation_ready"
            if st.button(
                f"確認撤銷 #{rule_id}", disabled=not ready,
                key=f"RECEIPT_EXCLUSION_REVOKE_{rule_id}",
            ):
                confirm_revoke(rule_id, str(preview.get("previewFingerprint") or ""))
