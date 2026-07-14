# Decision API Performance Design

## Goal

把 `GET /api/decisions/overview` 的 warm response 從目前約 1.2-1.4 秒降低至 300ms 以內，同時保持正式口徑、baseline、目標治理、Forecast 與決策規則完全不變。

## Current Bottleneck

目前 Decision API 每次都依序建立 Dashboard Facts Read Model、Data Quality、Forecast、System Health 與 Target Config。實測 Data Quality 約 0.99 秒、Dashboard Facts Read Model 約 0.24 秒，而 Decision 組裝接近 0 秒。主要成本來自重複 SQLite/Pandas 計算，不是決策規則本身。

## Chosen Architecture

沿用 `.nbs_runtime_cache` 的本地原子檔案 cache 模式：

- Data Quality cache key 由 `generationToken` 與 `DATA_QUALITY_SERVICE_VERSION` 組成。
- Dashboard Facts Read Model cache key 由既有 Facts `cacheKey` 與 `DASHBOARD_READ_MODEL_VERSION` 組成。
- Cache 使用 JSON wrapper 保存 contract metadata 與 payload，寫入採 temporary file + `os.replace`。
- Cache 缺失、損壞、版本不符或 generation 改變時自動重建。
- Decision API 使用明確 DB path、同一 generation token 取得兩個 read model。
- 不增加 upload 工作，不修改 SQLite，不使用固定 TTL，也不允許跨 generation 沿用結果。

## Alternatives Rejected

1. Process memory cache：命中最快，但 API restart 後消失，多 process 亦不能共用。
2. Upload-time precompute：讀取最快，但擴大 upload critical path，違反本階段只優化 Decision API 的範圍。
3. 只做平行化：仍需等待約 0.99 秒的 Data Quality，無法達成 300ms warm target。

## Interfaces

- `build_data_quality_cached(db_path, generation_token, cache_dir=None) -> dict`
- `build_dashboard_facts_read_model(...) -> dict` 保持現有 public signature，在內部加入 read-model cache。
- Decision provenance 增加 Facts 與 Data Quality cache status，方便驗收與監測。
- `scripts/profile_decision_api.py` 執行 cold/warm timing，warm median 超過 300ms 時回傳非零 exit code。

## Failure Handling

Cache 讀取錯誤視為 miss，立即用正式服務邏輯重建；重建失敗仍由 API 原有錯誤處理回報，不回傳舊 generation 資料。原子寫入避免中途終止留下半份 JSON。

## Verification

- TDD 驗證首次 rebuild、第二次 hit、generation 改變強制 rebuild、損壞 cache 自動修復。
- Decision API contract 驗證所有上游使用同一 generation token。
- Profile script 驗證本機 warm median `< 0.300s`。
- 完整 pytest、Vue verify/build、system acceptance、Hermes 與 2026-05 baseline 驗收。

## Scope Boundary

不修改正式口徑「不含掛賬核銷與TT退款轉團款」、2026-05 baseline `HKD 12,057,968`、monthly baseline、upload、rollback、Forecast 模型、目標值或 Decision 規則。
