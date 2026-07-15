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

正式口徑固定為「不含掛賬核銷與TT退款轉團款」；2026-05 baseline 固定為 `HKD 12,057,968`。
