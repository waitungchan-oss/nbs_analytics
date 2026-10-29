# 互動盤點工具驗證紀錄

更新日期：2026-10-29。這是指定資料夾的靜態 snapshot refresh，不是正式 NBS release gate。

## 本次唯讀觀察

- Source：乾淨 main worktree `4f287ec76a552826214d558e7954fa28c9036648`。
- Runtime：`/Users/chanwaitung2025/Downloads/nbs_analytics`，branch `codex/beta-export-dedup`；觀察時有未提交檔案，故與 source checkout 分開標示。
- Memory Hub：3 records、3 sources；三個 source expiry 為 `2026-09-17T00:00:00Z`。本次未 query、未改 catalog。
- Workflow：唯讀 `scripts/agent_workflow.py list` 回傳 381 canonical status：370 awaiting_authorization、9 completed、2 blocked。runs 目錄有 393 個直接子目錄，另 12 個不在 canonical list；未推斷用途或狀態。
- Storage：以 `du -sk` 讀取指定目錄：cache 5,597,332 KiB；Codex home 82,236 KiB；runs 21,376 KiB；reports 3,840 KiB；review_inputs 2,204 KiB；system runtime 1,076 KiB；Memory Hub 12 KiB。未做刪除候選判斷。
- Governance Graph：找到 4 份投影檔；未逐一驗 schema/freshness。
- PR #79：已合併；Full pytest、Hermes、UI acceptance、Hermes Governance Graph、macOS sandbox 與 release aggregate 為 PASS。這是 PR CI evidence，並非本次重新執行的驗收。

## 產物檢查

- `snapshot.json` 可解析，並記錄本次 observation timestamp 與 source commit。
- `index.html` 的 `snapshot-data` 與 `snapshot.json` 同步；日期與 source short SHA 已更新。
- `REPORT.md` 中 source links 指向目前 main worktree；舊快照數字已標為歷史或更新為本次唯讀計數。
- `serve.py` 未修改；本機 HTTP smoke check：`/`、`/snapshot.json`、`/REPORT.md` 均回 200，Content-Type 正確且 `Cache-Control: no-store`；HTML 內嵌 snapshot 與 JSON endpoint 完全相同。測試服務已停止。

## 未執行項目

沒有執行 Strict Review、full pytest、Hermes 或 UI release gate；沒有啟動 Agent／LLM／Hub query；沒有修改 Hub、workflow、SQLite、cache、baseline、正式業務資料或 retention policy。頁面仍是人工更新的靜態快照，不具即時監控、批准、刪除或記憶寫入功能。

## 檔案

- `index.html`：自包含互動地圖及同步的 snapshot data。
- `snapshot.json`：結構化唯讀盤點資料。
- `REPORT.md`：繁體中文報告、容量與來源引用。
- `serve.py`：只服務指定靜態附件的本地 HTTP 入口。
- `QA.md`：本驗證紀錄。
