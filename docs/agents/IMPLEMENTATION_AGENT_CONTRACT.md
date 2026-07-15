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

## 後續流程

Codex 檢查 final implementation report 與實際 diff，啟動 Review Agent，處理 findings，再執行完整驗證及 Hermes。Implementation Agent 不可取代任何後續 gate。
