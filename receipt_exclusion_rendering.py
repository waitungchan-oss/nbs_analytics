from __future__ import annotations

from typing import Callable

import pandas as pd
import streamlit as st


CONFIRMATION_COPY = "我確認永久排除此精確收款單；日後相同 identity 將自動排除。"


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
