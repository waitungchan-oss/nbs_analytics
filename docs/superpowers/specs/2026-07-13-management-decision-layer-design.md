# P2-5 Management Decision Layer Design

## Goal

在 Facts、Forecast、Data Quality、System Health 與 Acceptance History 之上，建立唯讀的目標、預警與管理層決策 read model，讓管理層先看到需要處理的事項，再下鑽到原有分析畫面。

## Scope

- 新增 `GET /api/decisions/overview`。
- 以專案根目錄 `decision_targets.json` 作為可選的目標設定來源；檔案不存在時回傳 `not_configured`，不得把歷史實際值當成目標。
- MVP 目標支援月份總營收目標，保留 `scope` 欄位供後續分社、產品與銷售組擴展。
- 預警涵蓋：目標未設定、Actual vs Target、Month-End Forecast gap、Data Quality、System Health、baseline/rollback。
- Vue 新增 Management Decisions 區，直接展示 API 回傳的 summary、targets、alerts、decision cards 與 provenance。
- 不新增目標寫入 API、不自動通知、不自動執行業務動作。

## Data Flow

`decision router` → `Facts Service` + `forecast_read_service` + `data_quality_service` + `system_health_service` + target config → `decision_service` → typed API response → Vue decision panel。

Decision service 只讀取既有來源，所有金額與正式口徑由 Python service 產生；Vue 不重算達成率、差距、預警或排序。

## Response Contract

- `status`: `ready` 或 `degraded`。
- `targetConfig`: 設定版本、來源、狀態、thresholds。
- `targets`: actual、target、attainment、gap、forecasted value 與 status。
- `alerts`: severity、code、title、summary、recommendation、evidence。
- `decisions`: 管理層可採取的唯讀決策卡。
- `provenance`: Facts generation、scope、forecast cache、source statuses。

## Guardrails

- 不修改正式營收口徑、baseline、SQLite、Forecast 模型、WAPE 或報表。
- 目標設定檔不是交易規則，不會被 upload 或 pipeline 回寫。
- 預警是提示，不是自動阻擋；baseline blocking 仍由既有 acceptance gate 負責。
- 沒有目標設定時不可顯示假造達成率，只顯示 `not_configured`。

## Verification

- decision service unit tests 覆蓋 target matching、target missing、forecast gap、quality/health/baseline alerts。
- API schema/endpoint tests。
- Vue static contract、build、完整 pytest、system acceptance、Hermes。

## Alternatives Considered

1. **推薦：唯讀 decision overview API + Vue panel**，先固定契約，目標寫入與通知留後續。
2. 直接把預警寫進 SQLite，稽核性高但會擴大 schema、migration 與寫入風險。
3. 只在 Vue 端計算預警，改動快但會破壞 Python 單一正式口徑，不採用。
