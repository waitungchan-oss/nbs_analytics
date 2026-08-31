# Strict Review Evidence Current-Round Brief

## Objective

取得本輪 Strict Review 的 fresh, provenance-bound PASS。Review 必須只評估本輪
Strict Review evidence preflight、source binding、runner diagnostic preservation
與 changed-surface verification，並保留既有 fail-closed 行為。

## In scope (current delta from base `e53b191`)

- `backend/agents/review_agent_service.py`
- `backend/agents/evidence_collector.py`
- `scripts/review_agent.py`
- `scripts/codex_usage_report.py` (untracked local source surface; compile-only
  attribution, no business/runtime scope)
- 對應本輪 Strict Review evidence tests與 strict provenance artifact

The preflight modules (`strict_review_preflight_models.py`,
`strict_review_preflight.py`, `strict_review_evidence_service.py`,
`strict_review_evidence_cache.py`, Graph/Memory adapters, CLI and verification
chain changes) are already part of the sealed base `e53b191`. They are covered
by the targeted regression command, but are not current changed files for this
Review request.

## Evidence identity contract

本輪使用兩個有意義且不同的 identity：

1. runner payload 的 outer `bundleFingerprint` 是本次 Review request bundle 的
   identity；Review report 的 `reviewFingerprint` 必須逐字等於它。
2. nested `evidence.bundleFingerprint` 是 source evidence bundle 的 identity，
   用來追溯 HEAD、brief、worktree 與 deterministic verification；它不要求、也
   不應該等於 outer request identity。

這兩個 fingerprint 的差異是 contract 設計，不是 evidence mismatch。只有
`reviewFingerprint` 未複製 outer request `bundleFingerprint`，或 source evidence
與同一 session 的 source seal 不一致時，才應判定 invalid evidence。

## Required fresh evidence

`verification-v1` 必須由本輪實際執行的 command 產生，且包含：

- `review-head-fingerprint`: current `git rev-parse HEAD`
- `review-brief-fingerprint`: current SHA-256 of this brief
- `review-worktree-fingerprint`: exact bounded worktree status hash，排除
  `docs/superpowers` 與 `.superpowers`
- changed Python surface 的 `python-compile`
- changed Strict Review/preflight tests 的 `targeted-tests`
- `review-diff-check`: current `git diff --check`

每個 command item 僅使用既有 `verification-v1` exact fields：`label`、`argv`、
`exitCode`、`stdoutTail`、`stderrTail`；output tail bounded，不保存業務明細、
secret、完整 prompt 或完整 logs。

## Dirty-path boundary

本輪不歸屬於 Strict Review implementation 的使用者檔案必須保留並明確排除：

- `AGENTS.md`
- `docs/agents/CODEX_CREDIT_SAVING_RUNBOOK.md`
- `scripts/codex_usage_report.py`
- `scripts/codex_usage_weekly.sh`
- `docs/superpowers/specs/2026-08-31-strict-review-evidence-preflight-design.md`
- `docs/superpowers/plans/2026-08-31-strict-review-evidence-preflight.md`

`docs/superpowers/**` 已由 worktree provenance command 排除；其餘 preserved paths
只作為 Review launch boundary，不得改寫、加入 production scope 或影響 verdict。

## Acceptance criteria

1. Fresh session source seal、brief、HEAD、worktree 與 verification-v1 provenance
   完全一致。
2. 所有本輪 changed Python surface 均有 compile evidence；對應 tests 有
   targeted evidence。
3. `verification-v1` 通過既有 exact schema 與 freshness validator。
4. runner capability 使用 `gpt-5.4` compatible local CLI/cache profile；若
   runtime transport 失敗，Strict Review 必須回傳 bounded blocker，而不是偽造
   PASS。
5. Strict Review 不修改 SQLite、baseline、revenue、business rules、export
   schema、正式 cache、Git integration 或 application runtime state。
6. 本輪 Strict Review 報告的 gate status 是可追溯的 `PASS` 或明確的
   `invalid_evidence`/`changes_required`/`blocked`，不得以舊 session 報告代替。

## Out of scope

- production 業務資料與正式 SQLite
- dashboard、GMV、退款、baseline 或 export schema
- commit、push、PR、merge
- Governance Graph、Memory Hub、Memory Sidecar 的 write path
- 自動修復 Review finding 或放寬 strict gate
