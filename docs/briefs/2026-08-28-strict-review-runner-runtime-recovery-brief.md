# Strict Review Runner Runtime Recovery Brief

## Objective

取得本輪 Review runner/runtime recovery 變更的正式 Strict Review PASS，並讓 Review evidence 能以 bounded、可重現的 verification-v1 provenance 證明同一份 brief、HEAD 與 working tree 已完成驗證。

## In scope

- `backend/agents/review_agent_service.py`
- `backend/agents/review_runner_profile.py`
- `backend/agents/verification_evidence_writer.py`
- `backend/agents/evidence_collector.py`
- `scripts/review_agent.py`
- `scripts/codex_runner_preflight.py`
- 對應 `tests/` 與本 brief

## Acceptance criteria

1. Strict Review 使用本 brief，不得以其他業務或 cold-rebuild brief 代替。
2. verification-v1 必須包含實際執行的 `review-head-fingerprint`、`review-brief-fingerprint`、`review-worktree-fingerprint`，且三者與 Review 啟動時的狀態一致；Review 不得事後合成 provenance。
3. runner preflight 對 executable、model cache schema 與 CLI version floor 失敗時 fail closed，並輸出 bounded diagnostics。
4. review runner timeout、context overflow 與 invalid evidence 不得寫入 active business state。
5. 既有 pytest、Hermes 與 Strict Review 輸出必須保留可追溯 evidence。

## Out of scope

不得修改 SQLite、baseline、revenue scope、business rules、export schema、正式業務資料、Git integration 或 production runtime state。
