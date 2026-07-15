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

- Production Implementation Agent 只能經 `scripts/implementation_agent.py` 啟動。CLI 會把已核准 contract 傳給 macOS `/usr/bin/sandbox-exec`，child process 與其後代只可寫入 `allowedWritePaths` 的 canonical exact file targets。
- Sandbox 允許必要的 process、sysctl、Mach、network 與 file read；不允許寫入 worktree 外、HOME、任意 temporary path、Git index、正式 DB/runtime 或未核准 sibling path。
- allowed target 或其 parent 只要是 symlink、逃出 worktree、不是既有目錄，或 backend 不存在/平台不支援，即 fail closed，runner 不會執行，CLI 回 blocked 類 exit code `2`。
- 執行前後 formal-state fingerprint 保留作 defense-in-depth，只偵測遺留污染並 quarantine，不取代 OS sandbox，也不提供 rollback。
- `ImplementationAgentService.execute(..., callback)` 是測試與內部 adapter，不是 production security boundary；不得用 direct callback 取代 CLI sandbox 執行正式 Implementation Agent。

## 後續流程

Codex 檢查 final implementation report 與實際 diff，啟動 Review Agent，處理 findings，再執行完整驗證及 Hermes。Implementation Agent 不可取代任何後續 gate。
