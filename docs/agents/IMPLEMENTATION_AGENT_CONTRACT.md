# Implementation Agent Contract

版本：v1
狀態：active

## 目的

Implementation Agent 只在已批准的 implementation plan、明確授權與獨立 worktree 中執行一個 allowlisted Task。它消費已完成的 Task contract，產出 final implementation report 與實際 diff，供 Codex 檢查及交給 Review Agent。

本契約所稱 Implementation Agent 專指本專案產品內 `scripts/implementation_agent.py` 定義的 Agent，不包含 Codex Superpowers SDD worker；Task commit 由 Codex 編排流程持有。Codex orchestration 只可在獨立授權後進行 Task commit。

## 必要輸入與回報

- Codex 建立並批准 Task contract，明確列出目標、allowlist、禁止事項與 focused verification。
- Implementation Agent 只可執行該 contract 的一個 Task，不得自行決定下一 Task。
- 完成時回報 status、startHead、endHead、修改檔案、RED/GREEN 結果及 concerns；報告與實際 diff 是 Review Agent handoff 的唯一實作證據。

## 禁止事項

- 產品 Implementation Agent 不得 commit、merge、push。不得管理服務或安裝 dependency。
- 不得修改正式 SQLite、baseline、rollback、revenue、business rules 或 export schema。
- 不得自行進行 full verification 或 Hermes；Review Agent findings 必須交回 Codex 處理。

## Production 執行安全邊界

- Production Implementation Agent 只能經 `scripts/implementation_agent.py` 啟動。Controller 先用 Git index 建立 disposable staging copy；只複製 tracked regular files，不使用 hardlink，並排除 `.git`、`.env`、SQLite、private key、`.nbs_runtime/`、`.nbs_agent_runtime/` 與 secrets。Coding worker 永遠不在正式 worktree 執行。
- Payload 的 `task.approvedWorktree` 及 `execution.worktree` 會指向 staging；原 contract fingerprint 只保留批准身份，不代表 worker 可取得正式 worktree 路徑。Public implementation report 與 telemetry schema 不因此改變。
- macOS `/usr/bin/sandbox-exec` 採 deny-default：不允許 network；file read 只限 staging、resolved executable 所需 runtime 及必要 system runtime；file write 只限 staging 內 `allowedWritePaths` 的 exact targets。Worker 環境不繼承 HOME 或 controller secrets。
- Worker 在獨立 process group 執行；主程序完成、失敗或 timeout 後，Controller 都會終止整個 process group，再驗證 staging 沒有未核准變更。Late child 無法在驗證後改寫正式檔案。
- 只有 response 通過 Implementation response schema、worker 成功退出、staging scope 驗證通過後，可信任 Controller 才會套用檔案。套用使用 actual parent dirfd、private temporary inode 與 atomic `replace`，不會原地 truncate 可能被換成 hardlink 的 target；套用後再次驗證內容、link count 與 parent path，race 時 fail closed。
- allowed target 或 parent 只要是 symlink、逃出 worktree、非 regular file，或 sandbox backend 不存在/平台不支援，即 fail closed。CLI 回 blocked 類 exit code `2`；正式 state fingerprint 仍保留作 defense-in-depth 與 quarantine 證據。
- Phase 1 production local coding worker 明確是 offline worker。需要 network 的模型 transport 必須在另一個受控邊界產生 response，不得把可聯網 CLI、普通 callback 或 service callback 當成本 worker 的 production isolation boundary。
- `ImplementationAgentService.execute(..., callback)` 是測試與內部 adapter，不是 production security boundary；不得用 direct callback 取代 CLI sandbox 執行正式 Implementation Agent。

## 後續流程

Codex 檢查 final implementation report 與實際 diff，啟動 Review Agent，處理 findings，再執行完整驗證及 Hermes。Implementation Agent 不可取代任何後續 gate。
