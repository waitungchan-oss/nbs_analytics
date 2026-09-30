# Verified Documentation Backfill

狀態：active

本文件保留 legacy v1 verified backfill 流程，並新增 v2 的 handoff/runbook 固定 target 流程。兩者都只適用於已完成、已批准且通過既有 gates 的 run；v1 的 Obsidian vault 必須由 operator 以 `--obsidian-vault` 明確指定，任何 vault absolute path 都不得進入 serialized workflow/application records。v2 repo target 流程不使用 Obsidian vault。

正式口徑仍為「不含掛賬核銷與TT退款轉團款」，2026-05 baseline 固定為 `HKD 12,057,968`。本程序不寫入 SQLite、baseline、runtime terminal state 或 Git；只在明確 preview 及 target approval 後寫 documentation targets。

## v2 Fixed-target Handoff / Runbook Sequence

v2 target identity 只能從本地 catalog 選取；禁止傳入任意檔案路徑。五個 targets 為：

| Target ID | Fixed repo path | Fixed section |
| --- | --- | --- |
| `handoff.current-conclusion` | `NBS_ANALYTICS_HANDOFF.md` | `## 1. 本輪交接結論` |
| `handoff.verification-snapshot` | `NBS_ANALYTICS_HANDOFF.md` | `### 5.3 最近驗證快照` |
| `runbook.pipeline-rollout-gate` | `docs/agents/ACCEPTANCE_PIPELINE_HARDENING_RUNBOOK.md` | `## Rollout handoff gate（Task 6）` |
| `runbook.shard-boundary` | `docs/agents/ACCEPTANCE_SHARD_ROLLOUT_RUNBOOK.md` | `## Boundary` |
| `runbook.parallel-rollout-boundary` | `docs/agents/ACCEPTANCE_PARALLEL_ROLLOUT_RUNBOOK.md` | `## Boundary` |

1. **Confirm the completed run** — 使用通過既有 Review、Full verification 與 Hermes gates 的 completed run ID。缺 gate、run 不完整或 source identity 不一致時停止。
2. **Create preview** — 一次選一個 target ID；runner 必須是明確批准的 Codex runner。

   ```bash
   .venv/bin/python scripts/documentation_agent.py \
     --run-id <completed-run-id> \
     --target-id handoff.current-conclusion \
     --agent-command "codex"
   ```

   這次只產生 `documentation-evidence-v2`、`documentation-proposal-v2` 和 `documentation-preview-v2`，不 apply。Artifacts 位於 `.nbs_agent_runtime/runs/<run-id>/documentation/targets/<target-id>/`，目標路徑與 heading 由 catalog 決定。
3. **Inspect lineage and preview** — 核對 `runId`、`commitSha`、source fingerprint、gate evidence、source hashes、target ID、expected section hash，以及 evidence → proposal → preview fingerprints。任何缺失、malformed、stale 或不一致都停止。
4. **Review** — findings-first Review 對同一 source-bound preview 做 read-only review。Review PASS 不是 apply approval。
5. **Explicitly approve and apply one target** — operator 必須把完全相同的 ID 同時放在兩個參數；只批准該 target，不會批准其他 target。

   ```bash
   .venv/bin/python scripts/documentation_agent.py \
     --run-id <same-run-id> \
     --target-id handoff.current-conclusion \
     --approve-target-id handoff.current-conclusion
   ```

   Controller 會重新收集 evidence 並核對 saved preview、source/section fingerprint 與 commit identity；drift、approval mismatch 或 preview 缺失時必須 `blocked`，不得 apply。Approval 呼叫不重新 dispatch runner。
6. **Hermes read-only inspection** — Hermes bounded-check v1/v2 artifact schema、status、fingerprint、lineage、5 MiB cap 與 symlink/path permissions。它不呼叫 runner、不 preview/apply、不批准 target；`documentation-hermes-report-v1` 不屬於正式 release gate，也不代表內容已回填。Formal release gates 仍需各自 fresh、source-bound evidence。

Failure outcomes 包括 `blocked_missing_runner`、invalid/unknown target、approval mismatch、stale source/section、missing/invalid preview、malformed fingerprint/schema、symlink/path permission violation 與 over-cap artifacts；一律停止並保留 evidence，不可手動改成成功狀態。v1 artifact 與歷史 acceptance reports 不遷移、不重寫。本節只定義流程，不宣稱所列 handoff/runbook 已完成回填。

## Legacy v1 Exact Operator Sequence

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

   此步只建立 evidence、normalized proposal、preview sidecars。Codex runner 內部輸出
   `documentation-draft-v1`，trusted service 會補齊受控 target identity、operation、hash
   與 proposal fingerprint，最後才產生 `documentation-proposal-v1`。預期
   `status=preview_ready`；runner 缺失、不受批准、輸入或輸出超限、draft target 不完整或
   fragment 不安全時，停止於 blocked/invalid outcome。

3. **Preview inspection**

   檢查 `.nbs_agent_runtime/runs/<run-id>/documentation-proposal.json` 和
   `documentation-preview.json`：確認 proposal 已是嚴格 `documentation-proposal-v1`，Brief
   target 位於受控 `docs/briefs/` identity、System Map 是預期 section、hash 與 target
   identity 正確，且沒有 ADR 自動套用。此步不得改變 Brief、System Map 或 vault bytes。

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
