# NBS Analytics Agent Instructions

正式修改前先讀 `docs/agents/NBS_AGENT_ARCHITECTURE.md` 與 `docs/agents/CODEX_AGENT_DISPATCH.md`。

治理文件：`docs/agents/NBS_AGENT_ARCHITECTURE.md`、`docs/agents/CONTEXT_AGENT_CONTRACT.md`、`docs/agents/REVIEW_AGENT_CONTRACT.md`、`docs/agents/IMPLEMENTATION_AGENT_CONTRACT.md`、`docs/agents/CODEX_AGENT_DISPATCH.md`、`NBS_HERMES_MONITORING.md`。

- 需要 Context Agent 時，先執行 `scripts/context_agent.py --collect-only`，只把 compact bundle 帶入主規劃。
- 每個 implementation Task 完成後，依 `docs/agents/REVIEW_AGENT_CONTRACT.md` 做 findings-first review。
- Review PASS 後仍要跑完整驗證與 `scripts/hermes_post_change_check.py`。
- Context/Review Agent 永遠 read-only，不得修改 SQLite、baseline、runtime、Git 或程式碼。
- Hermes 邊界以 `NBS_HERMES_MONITORING.md` 為準，不與 Review Agent 重複。
- Implementation Agent 只可執行已批准且明確授權的一個 Task，不得自行決定下一 Task，不得 commit 或 merge。
- Implementation Agent 不得修改正式 SQLite、baseline、rollback、revenue、business rules 或 export schema。
- 完成一個 implementation Task 後，final implementation report 與實際 diff 必須交給 Review Agent；Review PASS 後由 Codex 處理 findings、完整驗證與 Hermes。
- Phase 1 workflow 使用 `scripts/agent_workflow.py run`（或 `start`）只收集 Context，必須停在 `awaiting_authorization`；`approve` 每次都要明確帶 run ID、contract、Implementation runner、Review runner，禁止 implicit approval 或保存 runner command。
- `status` / `list` 只讀 artifact；`run` 後 best-effort retention housekeeping 與明確 `prune --apply` 都可依 policy compact 合資格的舊 completed run，`prune --dry-run` 只產生計畫。Hermes 只 read-only 報告 orchestrator artifacts / retention，不執行 prune，也不與 Review 重複。
- Streamlit Agent Operations 是 Phase 2 read-only work；不得成為 approval、dispatch、retention 或任何正式狀態寫入入口。
- Documentation dispatch 只在 Review PASS、full verification PASS、Hermes PASS 後由 Codex 呼叫 `agent_workflow.py document`；deterministic no-doc changes skip，不得由主 Codex LLM 靜默代寫。
- Documentation Agent 只消費 `documentation-evidence-v1` 並輸出 `documentation-proposal-v1`；system map 與 ADR 必須明確 target approval，缺少 approved runner 時回傳 `blocked_missing_runner`。
- Documentation sidecar 與 Agent Operations 永遠 read-only；不得 auto-apply、批准 targets、改變 Hermes/terminal state、修改 SQLite、baseline、runtime 或 Git。

正式口徑固定為「不含掛賬核銷與TT退款轉團款」；2026-05 baseline 固定為 `HKD 12,057,968`。
