# Task 1 Implementation Report

## Status

PASS. Task 1 僅建立 Receipt Exclusion Governance Table UI 所需的純資料 helper、單選 ID helper、preview state matching helper 及 TDD tests。

## RED / GREEN Evidence

RED command:

```bash
.venv/bin/python -m pytest tests/test_receipt_exclusion_rendering.py -q
```

RED output: `3 failed, 3 passed`。三個失敗均為預期的 `AttributeError`，分別指向尚未存在的 `_governance_rows`、`_selected_rule_ids`、`_matching_governance_preview`。中途補上測試遺漏的 `import pandas as pd` 後重跑，確認沒有測試自身的 `NameError`。

GREEN command:

```bash
.venv/bin/python -m pytest tests/test_receipt_exclusion_rendering.py -q
```

GREEN output: `6 passed`。

Focused regression command:

```bash
.venv/bin/python -m pytest tests/test_receipt_exclusion_rendering.py tests/test_receipt_exclusion_matcher.py tests/test_receipt_exclusion_proposal_service.py -q
```

Output: `18 passed`。

## Files

- `receipt_exclusion_rendering.py`
  - Added `GOVERNANCE_TABLE_HEIGHT`, `GOVERNANCE_PREVIEW_STATE_KEY`.
  - Added active/revoked governance column allowlists.
  - Added `_governance_rows`, `_selected_rule_ids`, `_matching_governance_preview`.
- `tests/test_receipt_exclusion_rendering.py`
  - Added the minimum fake Streamlit data editor/error/expander/spinner APIs.
  - Added allowlist, selection, and preview matching tests.

未修改 `app_pages.py`、SQLite、upload、baseline、rollback 或 registry service。

## Commit

- Implementation: `4d9764b feat: add receipt exclusion governance selection state`

## Self-review

- Helper 僅接收記憶體中的 dict/DataFrame，不讀寫 SQLite 或 runtime state。
- Governance table 只投影核准欄位，未帶出 `evidenceHash`、`proposalFingerprint`、`createdOperationId` 等敏感欄位。
- `eventCount` 使用 `or 0`，保留有效的 `0` 語意；選取 helper 對空表及缺欄位安全回傳空清單。
- Preview 必須同時符合 rule ID、registry revision、`revocation_ready` 與非空 fingerprint，否則回傳空 dict。
- `git diff --check` 通過；前輪未提交變更仍保留在 unstaged worktree，未被本 Task commit 帶入。

## Concerns

- Task 1 只提供 pure helpers；治理表格 wiring、preview/revoke 互動與 `app_pages.py` 整合留給 Task 2。
- 尚未執行完整 repo acceptance 或 Hermes，因本 Task brief 明確限制範圍為 focused helper/test 實作，且不得執行 Task 2。
