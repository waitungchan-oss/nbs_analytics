# Phase 2K-1 Vue 唯讀對齊驗收

## 範圍

本階段把下列穩定唯讀視圖接入 Vue：

- Annual Channel Summary
- Monthly Revenue Trend
- Full Branch Ranking
- Full Specialist Ranking
- Branch / Specialist Product Composition
- Reconciliation checks

Data Quality 與 Forecast 留待 Phase 2K-2。Upload、Export generation、GMV
與 Rules 寫入仍由 Streamlit 負責。

## API

```text
POST /api/dashboard/analytics
```

使用與 `/api/dashboard/summary` 相同的 filters。Vue 不從明細自行重算正式
營收，而是直接顯示 API 回傳的年度、月份、排行及產品數值。

## 2026-05 正式口徑驗收

篩選條件：

```json
{
  "years": [2026],
  "months": ["2026-05"],
  "dateRange": ["2026-05-01", "2026-05-31"],
  "branch": "全部分社",
  "salesGroup": "全部銷售組"
}
```

驗收結果：

- Summary combined revenue：`HKD 12,057,967.92`
- 顯示值：`HKD 12,057,968`
- Analytics combined revenue：`HKD 12,057,967.92`
- Annual total：`HKD 12,057,967.92`
- Monthly total：`HKD 12,057,967.92`
- Branch ranking / product total：`HKD 6,658,144`
- Specialist ranking / product total：`HKD 5,399,823.92`
- Reconciliation：`matched`
- 正式口徑：不含掛賬核銷與 TT 退款轉團款

所有 reconciliation delta 均為 `0.0`。

## 使用方式

開啟：

```text
http://127.0.0.1:5173/
```

使用同一個 Control Center 改變年份、月份、日期、分社與銷售組。按
`Show Full Ranking` 可由 Top 10 展開完整排行。

如果上游 Streamlit 在上傳後先把 AI cache 標成 deferred，Vue 仍維持
read-only，不負責觸發補算；`補算 AI` 仍是 Streamlit 端的顯式操作。
