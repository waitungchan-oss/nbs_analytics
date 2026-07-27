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
    registry_revision: str | None,
) -> dict:
    if not isinstance(preview, dict):
        return {}
    preview_rule_id = preview.get("ruleId")
    preview_revision = preview.get("registryRevision")
    preview_status = preview.get("status")
    preview_fingerprint = preview.get("previewFingerprint")
    if (
        not isinstance(preview_rule_id, int)
        or isinstance(preview_rule_id, bool)
        or preview_rule_id <= 0
        or not isinstance(rule_id, int)
        or isinstance(rule_id, bool)
        or rule_id <= 0
        or not isinstance(registry_revision, str)
        or not registry_revision.strip()
        or not isinstance(preview_revision, str)
        or not preview_revision.strip()
        or not isinstance(preview_status, str)
        or not preview_status.strip()
        or not isinstance(preview_fingerprint, str)
        or not preview_fingerprint.strip()
        or preview_status != "revocation_ready"
        or preview_rule_id != rule_id
        or preview_revision != registry_revision
    ):
        return {}
    return preview


def _gate_preview_summary(gate: object) -> dict:
    if not isinstance(gate, dict):
        return {}
    return {
        key: gate[key]
        for key in ("status", "matchedChecks", "deltaAmount")
        if key in gate and isinstance(gate[key], (str, int, float, bool))
    }


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
    candidate_ids = [row["candidateId"] for row in candidates]
    proposal_key = str(proposal.get("proposalFingerprint") or "unknown")[:20]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    confirmed = st.checkbox(
        CONFIRMATION_COPY,
        value=False,
        key=f"RECEIPT_EXCLUSION_CONFIRM_{proposal_key}",
    )
    selected = st.multiselect(
        "選擇要永久排除的精確收款單",
        options=candidate_ids,
        default=candidate_ids,
        format_func=lambda value: labels[value],
        key=f"RECEIPT_EXCLUSION_SELECTED_{proposal_key}",
    )
    if st.button(
        "永久排除並重新預演",
        type="primary",
        disabled=not confirmed or set(selected) != set(candidate_ids),
        key=f"RECEIPT_EXCLUSION_APPLY_{proposal_key}",
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
    raw_registry_revision = snapshot.get("registryRevision")
    registry_revision = (
        raw_registry_revision
        if isinstance(raw_registry_revision, str) and raw_registry_revision.strip()
        else None
    )
    revision_token = registry_revision[:20] if registry_revision is not None else "invalid"
    st.markdown("### Receipt Exclusion Governance")
    st.caption("永久排除規則只影響精確 identity；撤銷前必須在暫存資料庫重播並通過口徑驗收。")

    if registry_revision is None:
        st.session_state.pop(GOVERNANCE_PREVIEW_STATE_KEY, None)
        st.error("治理畫面暫時無法操作：registry revision 無效。")

    if not active:
        st.session_state.pop(GOVERNANCE_PREVIEW_STATE_KEY, None)
        st.info("目前沒有生效中的永久排除規則。")
    else:
        edited = st.data_editor(
            _governance_rows(active),
            key=f"RECEIPT_EXCLUSION_GOVERNANCE_EDITOR_{revision_token}",
            hide_index=True,
            width="stretch",
            height=GOVERNANCE_TABLE_HEIGHT,
            num_rows="fixed",
            disabled=[column for column in ACTIVE_GOVERNANCE_COLUMNS if column != "選取"],
            column_config={
                "選取": st.column_config.CheckboxColumn(
                    "選取", help="一次只能預覽與撤銷一條規則。",
                ),
            },
        )
        selected_rule_ids = _selected_rule_ids(edited)
        selected_rule_id = selected_rule_ids[0] if len(selected_rule_ids) == 1 else None
        if len(selected_rule_ids) > 1:
            st.error("一次只能選取一條永久排除規則。")
        if registry_revision is None:
            selected_rule_id = None
        active_rule_ids = {int(rule["id"]) for rule in active}
        if selected_rule_id is not None and selected_rule_id not in active_rule_ids:
            st.session_state.pop(GOVERNANCE_PREVIEW_STATE_KEY, None)
            selected_rule_id = None

        stored_preview = st.session_state.get(GOVERNANCE_PREVIEW_STATE_KEY) or {}
        preview = _matching_governance_preview(
            stored_preview,
            rule_id=selected_rule_id,
            registry_revision=registry_revision,
        )
        if stored_preview and not preview:
            st.session_state.pop(GOVERNANCE_PREVIEW_STATE_KEY, None)

        if st.button(
            "預覽撤銷所選規則",
            disabled=selected_rule_id is None,
            key=f"RECEIPT_EXCLUSION_GOVERNANCE_PREVIEW_{revision_token}",
        ):
            with st.spinner("正在預演撤銷"):
                candidate_preview = preview_revoke(selected_rule_id)
            preview = _matching_governance_preview(
                candidate_preview,
                rule_id=selected_rule_id,
                registry_revision=registry_revision,
            )
            if preview:
                st.session_state[GOVERNANCE_PREVIEW_STATE_KEY] = preview
            else:
                st.session_state.pop(GOVERNANCE_PREVIEW_STATE_KEY, None)
                st.error("撤銷預演結果已失效或未通過口徑驗收。")

        if preview:
            selected_rule = next(rule for rule in active if int(rule["id"]) == selected_rule_id)
            st.write({
                "規則 ID": selected_rule_id,
                "收款單號": selected_rule.get("receiptNo"),
                "來源單據號": selected_rule.get("sourceOrderNo"),
                "排除類型": selected_rule.get("exclusionKind"),
                "預演狀態": preview.get("status"),
                "Gate": _gate_preview_summary(preview.get("gate")),
            })
        if st.button(
            "確認撤銷所選規則",
            type="primary",
            disabled=not bool(preview),
            key=f"RECEIPT_EXCLUSION_GOVERNANCE_CONFIRM_{revision_token}",
        ) and preview:
            with st.spinner("正在確認撤銷"):
                confirm_revoke(selected_rule_id, str(preview["previewFingerprint"]))

    if revoked:
        with st.expander("查看已撤銷規則", expanded=False):
            st.dataframe(
                _governance_rows(revoked, revoked=True),
                hide_index=True,
                width="stretch",
                height=GOVERNANCE_TABLE_HEIGHT,
            )
