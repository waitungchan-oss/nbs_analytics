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

## Reviewer Finding Fix: `_matching_governance_preview` fail closed

### Status

PASS. Reviewer Important finding 已修復：malformed preview、缺少或空的
`ruleId`、`registryRevision`、`status`、`previewFingerprint`、invalid selected
rule ID，以及空的 selected registry revision 均回傳 `{}`，不再拋出 integer parsing
例外。

### RED / GREEN Evidence

RED command:

```bash
.venv/bin/python -m pytest tests/test_receipt_exclusion_rendering.py -q
```

RED output: `3 failed, 14 passed`。新增 malformed `ruleId` 測試重現
`ValueError`/`TypeError`，invalid selected rule ID 測試重現 `ValueError`。

GREEN commands:

```bash
.venv/bin/python -m pytest tests/test_receipt_exclusion_rendering.py -q
.venv/bin/python -m pytest tests/test_receipt_exclusion_rendering.py tests/test_receipt_exclusion_matcher.py tests/test_receipt_exclusion_proposal_service.py -q
.venv/bin/python -m py_compile receipt_exclusion_rendering.py tests/test_receipt_exclusion_rendering.py
git diff --check
```

GREEN output: focused rendering `17 passed`；focused regression `29 passed`；
compile 與 diff check 通過。

### Change and Commit

- `receipt_exclusion_rendering.py`: required-field gate、空 registry revision guard、受控 `int()` parsing；任何 malformed selected/preview rule ID 回傳 `{}`。
- `tests/test_receipt_exclusion_rendering.py`: 新增 required fields missing/empty、malformed rule ID、invalid selected rule ID 與 empty registry revision coverage。
- Commit: `fix: fail closed for malformed governance previews` (final hash in the completion report)

### Self-review

- 只 stage 了本 finding 的 implementation/test hunks；既有 confirmation 變更及其他未提交檔案保持 unstaged。
- Function 仍是 pure helper，不讀寫 SQLite、baseline、runtime 或 Git state。
- 合法 preview 的既有 matching、rule mismatch、revision mismatch tests 均維持通過。
- `git diff --cached --check`、focused tests、compile 與 `git diff --check` 均通過。

### Concerns

- 本次未執行完整 repo acceptance 或 Hermes，因 reviewer fix scope 僅限指定 rendering helper/tests。
