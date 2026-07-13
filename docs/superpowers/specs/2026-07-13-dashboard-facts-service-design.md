# P2-1 Dashboard Facts Service Design

## Goal

建立一個共用、可驗證的 Dashboard Facts read model，第一階段只讓 Streamlit 使用，讓後續 FastAPI / Vue 可以在不重算正式口徑的情況下接入同一份結果。

## Scope

- 新增獨立的 `dashboard_facts_service`，負責從指定 `db_path` 讀取現有正式資料並產生標準化 facts。
- Facts 以目前 data generation / cache token 識別，保存必要 metadata，支援同 generation 的重用。
- 第一階段接入 Streamlit dashboard cache；FastAPI、Vue、AI cache、Export cache 不在本次改動。
- 不修改 revenue scope、baseline、SQLite schema、upload、rollback、報表計算或 AI 模型。

## Proposed Architecture

`database.load_all_data_from_db(db_path=...)` → `revenue_scope_service` 現有正式口徑 → `dashboard_facts_service` → Streamlit `PROCESSED_DATA_CACHE`。

Facts service 使用明確 `db_path`，由呼叫端傳入 generation token；輸出包含 `kpi_totals`、`monthly_totals`、`branch_ranking`、`product_totals` 與 `metadata`。既有 Streamlit cache 仍保留完整 DataFrame，以避免一次改動影響既有頁面；Facts 是新增的共用 read model，不取代既有分析 frame。

## Cache Contract

- Cache key 必須包含 generation/cache token 與 service contract version。
- DB generation 改變時不得重用舊 facts。
- Facts cache miss 才重新從正式 SQLite 建立；cache hit 不觸碰寫入路徑。
- 建立失敗時回報明確錯誤，不以空資料或舊資料冒充成功。

## Error Handling

- `db_path` 不存在、generation 不完整或資料欄位不足時，facts service 回傳可定位的例外。
- Streamlit 接入失敗時保留既有 dashboard cache fallback，但顯示 degraded 狀態；不得修改正式 DB 或 baseline。

## Verification

- 先測試 cache key、generation invalidation、facts shape 與 baseline preservation。
- 驗證 2026-01 至 2026-06 月份 baseline；2026-05 必須為 HKD 12,057,968。
- 執行 targeted pytest、完整 pytest、compile、`scripts/system_manager.py acceptance`，並做 Hermes read-only 驗收。

## Alternatives Considered

1. 只在 Streamlit 內加 cache：改動小，但無法形成未來 API/Vue 的共同契約。
2. 同時重構 Streamlit、FastAPI、Vue：一次改動過大，會放大口徑與契約風險。
3. 本設計：先建立共用 read model，再逐個 consumer 接入，風險與可回溯性最佳。
