# Verified Documentation Backfill Run Design

日期：2026-07-19
狀態：proposed

## 1. 目的

讓已完成但未使用 Documentation Agent 的正式變更，能以可稽核方式補建一個
`completed` documentation evidence run，再產生 Obsidian Brief 與 System Map 的
documentation proposal。此機制不允許手工把 workflow status 改為 `completed`。

## 2. 範圍與非目標

本設計只處理 commit-bound documentation backfill、獨立 `codex` runner adapter、
Obsidian local-only 設定、proposal/preview/apply 的治理串接。

不處理正式 SQLite、upload、rollback、baseline promotion、revenue/business rules、
Hermes runtime state、Agent Operations 寫入、ADR 自動建立或 Git 自動提交。

正式口徑維持 `不含掛賬核銷與TT退款轉團款`；2026-05 baseline 維持
`HKD 12,057,968`。

## 3. 選定方案

採用 commit-bound backfill run 加獨立 `codex` Documentation runner。

```text
main commit + clean worktree
  -> verified-backfill collector
  -> verification/review/Hermes evidence hashes
  -> completed backfill run
  -> codex documentation runner (proposal only)
  -> validator preview
  -> explicit Brief/System Map approval
  -> trusted Controller atomic apply
```

## 4. Backfill Run 契約

新增 `verified-documentation-backfill-v1` manifest。它至少記錄：

- `sourceCommit`：40 字元 Git SHA，必須是目前 `main` 可達 commit。
- `sourceBranch`：必須為 `main`。
- `dirtyFiles`：必須為空。
- `verification`：pytest、system acceptance、Hermes 三份 JSON 摘要與 SHA-256。
- `review`：Review evidence 摘要與 SHA-256。
- `createdAt`、`createdBy`、`backfillReason`。

Controller 只有在以下條件全部成立時才建立 normal workflow artifacts 並寫入
`completed` status：

1. commit 是 `main` 可達且與目前 HEAD 相同。
2. tracked worktree clean。
3. pytest、system acceptance、Hermes 皆為 PASS。
4. Review evidence verdict 為 PASS。
5. 所有輸入 evidence 的 canonical SHA-256 與 manifest 相符。

任何條件不成立時，回傳 `blocked`，且不得建立 completed run。

## 5. Documentation Runner

runner command 必須以明確設定提供，預設批准 executable 為 `codex`。adapter：

- 僅透過 stdin 接收 `documentation-evidence-v1`。
- 僅接受 stdout 的 `documentation-proposal-v1` JSON。
- 不提供 filesystem、network、Git、SQLite、Obsidian 或 shell tool access。
- 遵守現有 input 8,000 / output 1,500 Token 上限、120 秒 timeout、64 KiB stdout cap。
- 不持久化 runner command、prompt、完整 stderr、絕對路徑或原始資料。

缺少 runner、runner 不是 allowlisted executable、schema/fingerprint 不符時，必須保持
`blocked` 或 `invalid_agent_output`；禁止主 Codex fallback。

## 6. Obsidian 與套用

Obsidian root 的優先順序維持：CLI `--obsidian-vault`、`NBS_OBSIDIAN_VAULT`、
`.nbs_agent_runtime/documentation.local.json`。local config 永不 tracked，序列化
artifact 不可包含 absolute vault path。

首次正式 backfill：

- Brief 僅在明確 `--apply-brief` 時套用。
- System Map 僅在明確 `--approve-target system_map` 時套用。
- ADR 本輪不自動套用，維持 create-only 與明確 approval 邊界。
- Controller 對每個 target 重算 before hash、建立 private backup、同目錄 temporary
  file + fsync + atomic replace，並驗證 after hash。

## 7. Review 與驗收

Review Agent 只讀 backfill manifest、input evidence、proposal、preview、application
record，檢查 commit binding、gate hashes、target scope、absolute-path redaction、
protected governance text 與實際寫入 hash。

驗收最少包括：

1. unit tests：拒絕 non-main/stale/dirty/missing or failed evidence。
2. runner adapter tests：stdin-only、schema/fingerprint、timeout/output cap、無 fallback。
3. temporary vault end-to-end：proposal、preview、Brief apply、System Map explicit approval。
4. full pytest、`scripts/system_manager.py acceptance`、
   `scripts/hermes_post_change_check.py`。
5. 5 月 baseline `HKD 12,057,968` 與正式口徑 matched。

## 8. 風險控制

- 不將歷史 commit 的文字或測試輸出直接當成完成證據；所有 evidence 必須在現有
  clean main 上重新驗證。
- backfill run 不可取代正式 implementation run，也不改變既有 workflow terminal state。
- 若驗收不通過，僅生成 blocked report，不寫 Obsidian/System Map。
- 真實 vault 首次套用後仍需 Hermes read-only report；Hermes 不呼叫 runner 或 apply。
