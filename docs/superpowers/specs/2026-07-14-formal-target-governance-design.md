# P2-6 Formal Target Configuration and Governance Design

## Goal

把 P2-5 的目標設定從「可選 JSON」提升為可驗證、可版本化、可追蹤的正式管理設定，並讓 Vue 管理層可以安全地建立 draft 或 approved 目標。

## Scope

- 新增 `GET /api/decisions/targets` 讀取目前目標設定與最近變更歷史。
- 新增 `PUT /api/decisions/targets`，以原子 replace 保存 `decision_targets.json`。
- 目標設定使用獨立檔案，不寫入 SQLite、`rules_config.json` 或正式交易資料。
- 保存每次變更的 revision、時間、修改人、原因、approval status 至 `.nbs_runtime/decision_targets_history.jsonl`。
- P2-6 支援 `combined` 月度總營收目標；分社、產品、銷售組目標保留後續擴展，不接受未支援維度。
- Vue Management Decisions 增加目標配置表單，可載入、增刪月份目標、保存 draft 或 approved。

## Configuration Contract

```json
{
  "version": "2026-07",
  "scope": "不含掛賬核銷與TT退款轉團款",
  "population": "全部正式分社＋正式四人專職銷售組",
  "approvalStatus": "draft",
  "updatedBy": "manager",
  "changeReason": "2026-07 月度目標設定",
  "approvedBy": null,
  "thresholds": {
    "forecastGapPct": 0.05,
    "qualityWarningScore": 75,
    "qualityCriticalScore": 60
  },
  "targets": [
    {
      "id": "2026-07-combined",
      "label": "2026-07 合計目標",
      "month": "2026-07",
      "scope": "combined",
      "targetRevenue": 10000000
    }
  ]
}
```

保存前必須驗證：正式 scope、正式 population、月份格式、目標金額大於 0、月份與 id 不重複、threshold 範圍、scope 只能是 `combined`；`approved` 必須有 `approvedBy`。

## Data Flow

`Vue target form` → `PUT /api/decisions/targets` → `validate_decision_targets` → atomic config replace + append history → reload decision overview。

P2-5 `GET /api/decisions/overview` 改為讀取同一份已驗證設定；若不存在，維持 `not_configured`。

## Guardrails

- 不修改 revenue scope、baseline、Facts、Forecast、WAPE、SQLite 或 upload。
- draft 目標可保存但不作為正式管理預警；只有 approved 設定才進入 P2-5 decision evaluation。
- API 不提供刪除歷史或覆寫 revision 的能力。
- 使用者輸入的 `updatedBy` 與 `changeReason` 必須保存，方便 Hermes/Obsidian 追蹤。

## Verification

- 驗證 service：合法配置、重複月份、錯誤 scope、負數目標、approved 缺 approvedBy、revision/history/atomic write。
- API：GET/PUT response、422 validation、OpenAPI contract。
- Vue：目標表單、保存成功後 decision refresh、錯誤提示與 static contract。
- 完整 pytest、Vue build、system acceptance、Hermes，並確認 2026-05 baseline unchanged。
