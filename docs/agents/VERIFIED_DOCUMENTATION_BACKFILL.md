# Verified Documentation Backfill

狀態：active

本程序只適用於已完成、已批准且通過既有 gates 的 verified backfill run。它是 local-only 操作：Obsidian vault 必須由 operator 以 `--obsidian-vault` 明確指定，任何 vault absolute path 都不得進入 serialized workflow/application records。

正式口徑仍為「不含掛賬核銷與TT退款轉團款」，2026-05 baseline 固定為 `HKD 12,057,968`。本程序不寫入 SQLite、baseline、runtime terminal state 或 Git；只在明確 preview 及 target approval 後寫 documentation targets。

## Exact Operator Sequence

以下順序不可省略。`<run-id>` 是 backfill create 回傳的 run ID；`<vault-root>` 是本機 vault 路徑，不要把它寫入任何 artifact、proposal 或 application record。

1. **Backfill create**

   ```bash
   .venv/bin/python scripts/verified_documentation_backfill.py \
     --source-commit HEAD \
     --reason "Documentation Agent verified backfill" \
     --no-notify
   ```

   只接受 `status=completed` 的結果。若回傳 `blocked`，停止並保留原因；不得手動偽造 completed run。

2. **Proposal**

   ```bash
   .venv/bin/python scripts/documentation_agent.py \
     --run-id <run-id> \
     --agent-command "codex" \
     --obsidian-vault "<vault-root>"
   ```

   此步只建立 evidence/proposal/preview sidecars。預期 `status=preview_ready`；runner 缺失、不受批准、輸入或輸出超限時，停止於 blocked outcome。

3. **Preview inspection**

   檢查 `.nbs_agent_runtime/runs/<run-id>/documentation-preview.json`：確認 Brief 是預期 managed-block 更新、System Map 是預期 section、hash 與 target identity 正確，且沒有 ADR 自動套用。此步不得改變 Brief、System Map 或 vault bytes。

4. **Review**

   Review Agent 只做 findings-first、read-only review，檢查 brief、evidence、proposal、preview、實際 diff 與 requirement coverage。Review PASS 不是 apply approval，也不是 Hermes acceptance；Review Agent 不得寫 vault、repo、runtime、SQLite、baseline 或 Git。

5. **Controlled apply**

   Review PASS 後，才執行下列唯一 apply command：

   ```bash
   .venv/bin/python scripts/documentation_agent.py \
     --run-id <run-id> \
     --agent-command "codex" \
     --obsidian-vault "<vault-root>" \
     --apply-brief \
     --approve-target system_map
   ```

   `--apply-brief` 只開啟低風險 Brief apply；`--approve-target system_map` 是 System Map 的必要明確批准。沒有後者時，System Map 必須保持 byte-identical，結果應為 `awaiting_target_approval`。ADR 永遠不因本命令自動寫入。

6. **Hermes**

   ```bash
   .venv/bin/python scripts/hermes_post_change_check.py
   ```

   Hermes 是最後的 read-only acceptance gate，負責 workflow artifacts、documentation sidecar schema/status/cap、runtime、SQLite、baseline、服務與 Git 邊界檢查。Hermes 不執行 Documentation Agent、preview/apply、target approval、backup、prune 或 Obsidian 寫入，也不取代 Review。

## Blocked Outcomes

- create blocked：dirty worktree、非 `main`、source commit 過期、gate 缺失/失敗、review 非 PASS 或 evidence hash 不一致。停止，不建立可 apply 的 run。
- proposal blocked：缺少 approved runner、runner 不在 allowlist、context/output 超限、invalid output 或 timeout。停止，不自行改用其他 runner。
- preview blocked：vault 缺失、target traversal/symlink、protected governance text 變更、stale section 或不合法 proposal。停止，不 apply。
- apply awaiting approval：未提供 `--approve-target system_map` 時，所有未批准高風險 targets 維持原 bytes。
- Hermes failed：回到 Codex 處理 findings；不得以 Review PASS 或已寫入的 bytes 宣稱完成。

## Cleanup Policy

測試只可使用 pytest `tmp_path` 建立 temporary vault，測試結束由 pytest 清理；不得讀寫真實 vault。正式操作完成後，保留 bounded workflow evidence 供 audit，清理只限 operator 明確批准的 temporary vault、暫存檔與 local-only configuration；不得刪除 runtime evidence、backup、quarantine 或 Hermes report 來掩蓋失敗。只提交明確批准的 tracked repository documentation change，永不提交 vault 或 runtime artifacts。
