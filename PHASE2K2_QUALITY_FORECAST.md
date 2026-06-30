# Phase 2K-2 Data Quality 與 Forecast 唯讀對齊

## API

```text
GET /api/insights/data-quality
GET /api/insights/forecast
```

兩個 endpoint 都是 read-only。Data Quality 從 SQLite 計算；Forecast 只讀
最新有效 `.nbs_runtime_cache/ai_*.pkl`，API request 不會重新訓練模型、
改權重或生成 Export。

## Data Quality 驗收

- Overall Score：`97.14`
- Health：`優秀`
- Latest Date：`2026-06-24`
- Date Coverage：`99.67`
- Field Completeness：`96.73`
- Entity Resolution：`93.22`
- Official Scope Health：`96.07`
- Amount Health：`100.00`

## Forecast 驗收

- Cache version：`daily-macro-normal-tight-v1`
- Cache modified：`2026-06-25 10:39:29 +08:00`
- Daily forecast：30 日，Vue 預覽前 7 日
- 7-Day Consensus：`HKD 2,669,496.45`
- Month-End Consensus：`HKD 9,998,849.80`
- 7-Day WAPE：`14.66%`，可接受
- Month-End WAPE：`10.53%`，可接受

## 保護規則

- Vue 不重算 Forecast。
- API 不執行模型訓練。
- 若 Streamlit 上傳後先把 AI cache 標成 deferred，仍由 Streamlit 的「補算 AI」手動入口完成完整重算；這不屬於 Vue read-only endpoint 的責任。
- cache 缺失或版本不符時回傳 `not_ready`。
- Data Quality 與其他看板不受 Forecast cache 錯誤影響。
- 正式收入仍排除掛賬核銷與 TT 退款轉團款。
- 2026-05 核心基線仍為 `HKD 12,057,968`。
