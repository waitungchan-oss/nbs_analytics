# Phase 2 前置驗收包：穩定性與驗收基線

更新日期：2026-06-23  
專案路徑：`/Users/chanwaitung2025/Downloads/nbs_analytics`

## 1. 目的

本驗收包用來守住 Phase 2 Vue read-only cockpit 之前的數據口徑。後續無論調整 Streamlit UI、FastAPI response contract，或接入 Vue 前端，都必須先通過這裡列出的正式口徑 baseline。

Phase 2 前置驗收包只做 read-only 驗證：

- 不修改 SQLite。
- 不修改 `pipeline.py` 的清洗、匹配與彙總規則。
- 不修改正式 Excel export schema。
- 不接 upload / GMV / rules 寫入。

## 2. 正式收入口徑

正式口徑固定為：

```text
不含掛賬核銷與TT退款轉團款
```

排除規則：

- 排除 `收款類型 = 掛賬核銷` 的來源單據號。
- 排除 `收款方式 = TT 退款轉團款` 的來源單據號。
- 排除以來源單據號為整單口徑執行，避免同一單號在不同明細表中殘留。

## 3. Phase 2 核心數字錨點

第一個必守 baseline：

```text
月份：2026-05
範圍：2026-05-01 至 2026-05-31
視角：全部分社 + 全部專職銷售組
正式口徑：不含掛賬核銷與TT退款轉團款
分社營收：HKD 6,658,144
專職銷售組營收：HKD 5,399,824
分社 + 專職銷售組總營收：HKD 12,057,968
```

這個數字用於判斷 Phase 2 是否可以繼續。若 Streamlit、API、Vue 或正式 export 的同口徑計算結果不等於 `12,057,968`，不得宣稱 Phase 2 數據驗收通過。

### 3.1 Top 5 分社 baseline

`2026-05`、正式口徑、全部分社視角下，Top 5 分社必須為：

| 排名 | 分社 | 總營收 |
|---:|---|---:|
| 1 | 17荃灣綠楊坊分社 | HKD 1,705,339 |
| 2 | 36旺角銀行中心分社 | HKD 1,146,543 |
| 3 | 19沙田分社 | HKD 737,527 |
| 4 | 33銅鑼灣分社 | HKD 704,358 |
| 5 | 27屯門市廣場分社 | HKD 673,995 |

### 3.2 Top 專職銷售組 baseline

`2026-05`、正式口徑、全部專職銷售組視角下，Top 專職必須為：

| 排名 | 專職 | 總營收 |
|---:|---|---:|
| 1 | YTLAU 刘元太 | HKD 4,421,710 |
| 2 | SOGOR 苏清秩 | HKD 444,608 |
| 3 | ELSA 谢玲玲 | HKD 329,056 |
| 4 | JIA 江嘉韵 | HKD 204,450 |

### 3.3 2026-06 資料新鮮度 baseline

目前正式口徑資料新鮮度 baseline：

```text
最新收款日期：2026-06-22
最早收款日期：2025-01-01
正式口徑分析筆數：26,640
2026-06 分社 + 專職銷售組累計營收：HKD 6,889,645
```

## 4. API Contract

`/api/dashboard/summary` 必須回傳 `revenueTotals`、`dataFreshness`、`branchRanking`、`specialistRanking`：

```json
{
  "revenueTotals": {
    "branchRevenue": 6658144.0,
    "specialistRevenue": 5399823.92,
    "combinedRevenue": 12057967.92,
    "formattedCombinedRevenue": "HKD 12,057,968",
    "scope": "不含掛賬核銷與TT退款轉團款"
  },
  "dataFreshness": {
    "minDate": "2025-01-01",
    "maxDate": "2026-06-22",
    "rawRows": 27185,
    "analysisRows": 26640,
    "excludedRows": 545,
    "scope": "不含掛賬核銷與TT退款轉團款"
  }
}
```

Vue read-only cockpit 應直接使用 `revenueTotals.combinedRevenue` 作為「分社 + 專職銷售組」合計，不應在前端自行重算正式口徑。
Vue 的分社排行與專職排行應直接使用 `branchRanking` 與 `specialistRanking`，不應自行從明細或產品表重組排序。

## 5. 驗收測試

目前驗收測試：

```bash
.venv/bin/python -m pytest tests/test_phase2_precheck_acceptance.py -q
.venv/bin/python -m pytest tests/test_dashboard_service.py tests/test_dashboard_api.py -q
```

測試責任：

- `tests/test_phase2_precheck_acceptance.py`：讀取目前 SQLite 與現有 Python 計算鏈路，確認 2026-05 總額、ranking baseline 與 2026-06 data freshness。
- `tests/test_dashboard_service.py`：確認 dashboard summary 會回傳分社、專職、合計營收、filter-aware ranking 與 data freshness。
- `tests/test_dashboard_api.py`：確認 FastAPI response contract 保留 `revenueTotals`、`dataFreshness`、`branchRanking`、`specialistRanking` 欄位。

## 6. Phase 2 進場條件

開始 Vue read-only cockpit 前，至少確認：

- `2026-05` baseline 等於 `12,057,968`。
- `/api/dashboard/summary` 回傳 `revenueTotals`、`dataFreshness`、`branchRanking`、`specialistRanking`。
- `2026-05` Top 5 分社與 Top 專職 baseline 未漂移。
- `2026-06` 最新收款日期維持 `2026-06-22`，除非後續正式上傳新批次並更新本文件。
- Navigation hash change 不觸發 dashboard recompute。
- Apply 篩選才刷新 dashboard summary。
- Vue 不重算正式收入口徑、Forecast、WAPE、Export 或 GMV 結果。

## 7. 不在本階段處理

本驗收包不處理：

- 上傳新資料流程重構。
- Excel export prepare / download API。
- GMV 排除訂單 Vue view。
- 業務規則配置寫入。
- Forecast 模型調整。
- SQLite schema migration。
- AI cache 的手動「補算 AI」操作屬於營運流程，不在這份 baseline 驗收包的必做項目；它不應改變 2026-05 的正式口徑錨點。
