# Task 1 Findings-Fix Report

## Scope

只修正既有 Task 1 review findings；未修改 Task 2-8、SQLite、runtime、baseline、Obsidian vault 或產品程式。

## Findings

1. **RED**：新增 guardrails 非正式 revenue scope / May baseline 的拒絕測試；初次 focused run 中該測試未拋錯。**GREEN**：`DocumentationEvidence.from_dict()` 嚴格要求 `不含掛賬核銷與TT退款轉團款` 與 `HKD 12,057,968`。
2. **RED**：新增 content SHA-256 與 proposal fingerprint 不一致的拒絕測試；初次 focused run 中兩項均未拋錯。**GREEN**：以 content UTF-8 SHA-256 驗證 `contentSha256`，並以移除自身 `proposalFingerprint` 欄位後的 canonical payload 驗證 proposal fingerprint。
3. **RED**：新增三種 target kind exact policy mapping 與錯誤 mapping 拒絕測試；初次 focused run 中錯誤 mapping 未拋錯。**GREEN**：嚴格驗證 `brief_backfill`、`system_map`、`adr` 的 operation、riskTier、repoRoots/repoPaths、obsidianSubdirectory 與 explicit approval。
4. **RED**：新增 application duplicate `targetIdentity` 拒絕測試；初次 focused run 中未拋錯。**GREEN**：`DocumentationApplication.from_dict()` 拒絕重複 target identity。

## Verification

- RED focused run：5 failed, 15 passed。
- GREEN focused run：`21 passed`。
- Compile：`python -m py_compile backend/agents/documentation_models.py` passed。
- Formatting: `git diff --check` passed。
- 未執行全量 pytest；既有 backend health failure 不屬本 task。

## Commit

- `fix: close documentation model review findings` (final commit hash reported below)

## Concerns

- Worktree 沒有 `.venv/bin/python`；focused tests 使用 parent repo 的 `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python`。系統 `python3` 沒有 pytest。
- 本 task 未改動 tracked policy JSON；model validation 對照 brief 所列 exact policy surface。
