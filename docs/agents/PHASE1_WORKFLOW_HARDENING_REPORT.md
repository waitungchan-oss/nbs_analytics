# Phase 1 Workflow Hardening Report

日期：2026-07-15
範圍：local workflow artifact、subprocess stage output、review evidence 與 approval lock contention。

## Broad Review Resolution

- `WorkflowStore.write_artifact` 在 persistence 前序列化並拒絕超過 `stageArtifactMaxBytes` 的 payload；run 累計首次跨越 `runArtifactSoftCapBytes` 時只寫入 bounded `artifact_size_warning` metadata。
- `SubprocessStageExecutor` 不建立 full stdout spool；stdout 超過 5 MiB 時立即終止 stage process group、保留 bounded tail，並回傳 controlled cap error。cap 以下的 JSON object 維持 parse contract。
- Review evidence 只額外接納已追蹤、project-root 的 Python source diff。process report、sensitive extension、deny pattern、symlink escape、untracked root file 與 traversal 仍被拒絕。
- `approve` 碰到 run lock contention 只回傳 transient `blocked_run_locked` result；不寫入 approval、event 或 stored status，CLI 對應 exit `2`。

## Verification Boundary

- Focused workflow tests、agent test pack、`py_compile` 與 `git diff --check` 是本次 code-level evidence。
- 不執行 upload、SQLite write、baseline promotion、rollback、正式 service acceptance 或 database mutation。
- Hermes 僅 read-only 查看 Phase 1 artifact / retention state；完整責任見 `NBS_HERMES_MONITORING.md`。
