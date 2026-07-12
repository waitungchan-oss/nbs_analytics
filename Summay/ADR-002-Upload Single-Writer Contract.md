# ADR-002: Upload Single-Writer Contract

狀態：Accepted
日期：2026-07-12
範圍：Streamlit、FastAPI / Vue upload entry point、SQLite write path、preflight、rollback、history、cache generation、Hermes health

---

## 背景

Streamlit 與 FastAPI 曾各自使用 process-local `threading.Lock`。兩者由不同 PID 執行，因此無法避免同時 upload、同時 backup/upsert/gate/history 的競態。Preflight 亦曾暫時改寫 `database.DB_FILE`，使同 process 其他讀取有機會誤讀 temp DB。

## 決策

1. 所有正式 upload 由 `UploadOrchestrator` 執行同一條流程：preflight、正式 upsert、governed gate、rollback、history、cache generation。
2. 兩個入口在讀 Excel 前取得 `.nbs_runtime/upload_coordination.db` 的 SQLite `BEGIN EXCLUSIVE` lease。busy 時不讀檔、不 preflight、不 backup、不寫 history。
3. database、preflight、dashboard gate、monthly gate、history 均接受 explicit `db_path`；不得改寫 module-global `database.DB_FILE`。
4. accepted write 或 verified rollback 以原子 JSON 更新 `data_generation.json`，包含 operation ID、資料庫 SHA-256 與 generation。Streamlit 以 generation token 判斷 session cache 是否失效；FastAPI 僅回報 `invalidated`，不聲稱重建 Streamlit cache。
5. stability history 保存 operation ID、entry point、timings、monthly baseline、cache state/error、generation；operation ID 唯一，Hermes health 會比對 generation 與 history。

## 不採用方案

- FastAPI-only writer：會讓 Streamlit 正式 upload 過度依賴 HTTP service，並放大既有 UI workflow 改動。
- lock file：有 stale lock recovery 問題，亦無法統一 gate/history/cache contract。
- UI 或 export 層補數：不能修復 SQLite write path，也會掩蓋 baseline drift。

## 回滾與復原

正式 upsert 後若 governed blocking gate drift，必須使用該次 hot backup rollback，並以相同 governed gate 二次驗證。rollback failed 不推進 generation；history / cache generation failure 必須回報 degraded，不能包裝成完整成功。

## 守護邊界

正式口徑仍為「不含掛賬核銷與TT退款轉團款」。2026-05 全部分社＋正式四人專職銷售組 baseline 必須保持 `HKD 12,057,968`；2026-01 至 2026-06 monthly baseline 的金額與 monitoring/blocking mode 不得因本 ADR 改變。

## 驗收

- 兩個 process 同時 acquire 時僅一個可寫入；crash 後 lease 可重取。
- preflight 不改寫 `database.DB_FILE`，explicit missing live DB fail closed 且不建立檔案。
- FastAPI / Streamlit 都使用 orchestrator，history / monthly / cache contract 一致。
- Hermes health 可看到 lease、generation、history evidence；generation 有 operation ID 卻缺 history 時必須 degraded。
- upload / DB / baseline tests、full pytest、dry-run、service acceptance、Hermes 均需通過後才建立下一份 rerun Brief。
