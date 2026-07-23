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

## Reviewer High Finding Fix Round 2: strict fail-closed preview validation

### RED / GREEN Evidence

RED command:

```bash
.venv/bin/python -m pytest tests/test_receipt_exclusion_rendering.py -q
```

RED output: `10 failed, 39 passed`。新增案例確認既有 `int()` coercion 會接受
`ruleId=4.0`、selected `rule_id=4.0`/`"4"`，且既有 truthiness gate 會接受
`previewFingerprint=0`、`True`、容器值與空白字串。

GREEN commands:

```bash
.venv/bin/python -m pytest tests/test_receipt_exclusion_rendering.py -q
.venv/bin/python -m pytest tests/test_receipt_exclusion_rendering.py tests/test_receipt_exclusion_matcher.py tests/test_receipt_exclusion_proposal_service.py -q
.venv/bin/python -m py_compile receipt_exclusion_rendering.py tests/test_receipt_exclusion_rendering.py
git diff --check
git diff --cached --check
```

GREEN output: rendering `49 passed`；focused regression `61 passed`；compile、working-tree
diff check 與 staged diff check 均通過。

### Change and Commit

- `_matching_governance_preview` 現在要求 preview `ruleId` 與 selected `rule_id` 都是非-bool 的正整數；`registryRevision`、`status`、`previewFingerprint` 與輸入 `registry_revision` 都必須是 strip 後非空字串。缺欄位、`0`、`True`、浮點數、字串或其他 malformed 值一律回傳 `{}`。
- Regression tests 覆蓋 bool、float、字串、零值、負值、空白及非字串 governance fields。
- Commit: `4514b79 fix: strictly validate governance preview state`

### Self-review

- 只 stage 本輪 `_matching_governance_preview` implementation 與 strict regression test hunks；前輪 confirmation 變更及其他未提交檔案保持 unstaged。
- Helper 仍為 pure function，不讀寫 SQLite、baseline、runtime 或 Git state；合法 preview、rule mismatch、revision mismatch 既有 tests 均維持通過。
- commit 後未重新修改已提交的兩個 code/test 檔案；report 本身依要求追加且保持 unstaged。

### Concerns

- 未執行完整 repo acceptance 或 Hermes；本輪僅修復指定 High finding，且 brief 限制修改範圍。

## Controller Reconciliation

本報告的 Task 1 scope 記錄是在 Task 1 review 時建立，當時 Task 2 尚未開始，
因此其中「治理表格 wiring 留給 Task 2」是當時的時間點狀態，不是本輪最終狀態。
Task 2 後續已由 `receipt_exclusion_rendering.py` 與
`tests/test_receipt_exclusion_rendering.py` 完成，實作與驗證證據記錄於
`.superpowers/sdd/task-2-rendering-report.md`。
