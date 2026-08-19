# Refund Reconciliation Center Design

**日期：** 2026-08-19  
**狀態：** Draft for user review  
**範圍：** GMV 排除訂單看板的退款 Preflight、退款對帳引擎、退款對帳異常中心

## 1. 目標與決策

本功能把目前「上傳退款檔 → 直接計算 GMV → 下載報表」的流程，提升為可先檢查、可解釋、可追查的退款對帳流程。

使用者上傳 `退款明細數據.xlsx` 後，系統先完成退款檔案品質與匹配預覽，再產生總退款與已退款兩個 GMV 派生維度。對每一筆退款來源單據號，系統必須說明它是：

1. `正式口徑匹配`：存在於正式 Revenue Scope analysis frames，可影響正式口徑扣減結果。
2. `被收入規則排除`：存在於原始 SQLite，但只落在掛賬核銷或 TT 退款轉團款範圍，不影響正式口徑 GMV。
3. `SQLite 找不到`：原始正式 SQLite 找不到對應來源單據號，不能扣減 GMV。

本功能的主要決策價值，是讓營運人員知道「退款檔中的金額有多少真的被扣減、多少被收入規則排除、多少仍需追查」，而不是只看到一個未匹配數字。

## 2. 背景與現況

目前已存在以下能力：

- `app_workflows._parse_gmv_refund_data()` 能讀取退款來源單據號、退款原幣金額與退款狀態。
- `app_workflows._apply_gmv_refund_adjustments()` 能按來源單據號彙總退款，按原收款金額比例分配扣減，並以原收款金額為上限。
- GMV 看板已同時產生「總退款」與「已退款」兩個維度。
- 現有退款稽核 workbook 已有摘要、扣減明細與未匹配來源單據號。
- 正式 SQLite、正式 Revenue Scope、AI Forecast、WAPE 與正式 Export 有既有治理邊界。
- Upload preflight 已使用臨時 SQLite 與 `batchSummary`，但退款檔目前沒有相同層級的專用 preflight contract。

目前不改動正式資料的邊界必須保留：GMV 退款扣減是 session-only derived view，不回寫 SQLite，不污染正式 export cache，不覆蓋 AI Forecast 或 WAPE。

## 3. 範圍

### 3.1 In scope

- 退款檔案 schema、資料型別、空值、重複、狀態與金額 Preflight。
- 總退款、已退款兩個維度的匹配預覽。
- 來源單據號的三段式匹配狀態分類。
- 退款金額彙總、扣減上限、超額退款與可扣減金額的對帳。
- Streamlit GMV 看板中的只讀異常中心。
- 未匹配及異常清單下載。
- 保留現有完整報表及稽核報表輸出。

### 3.2 Out of scope

- 不新增 SQLite table、migration、正式退款資料存檔或退款歷史資料庫。
- 不改正式 upload transaction、upsert、rollback、baseline 或 Revenue Scope 規則。
- 不加入人工 mapping 寫入、規則批准或自動修正資料的按鈕；第一階段只提供診斷與下載。
- 不把退款結果接入正式 AI Forecast、Daily WAPE 或 Macro Backtest。
- 不實作退款發生日期與原訂單日期的期間口徑切換；該功能另立 spec。
- 不新增外部服務、背景 job、approval、dispatch 或 orchestration control。

## 4. 使用者流程

```text
上傳退款 Excel / CSV
        ↓
退款 Preflight
  ├─ schema / status / amount / duplicate checks
  ├─ 總退款匹配預覽
  └─ 已退款匹配預覽
        ↓
Preflight 狀態
  ├─ blocked：停止 GMV 計算，要求修正檔案
  ├─ warning：允許繼續，但明顯顯示風險
  └─ ready：進入退款扣減
        ↓
Reconciliation Engine
  ├─ 標準化來源單據號
  ├─ 判斷正式口徑 / 被收入規則排除 / SQLite 找不到
  ├─ 按來源單據號彙總
  ├─ 按原收款金額比例分配
  └─ 套用扣減上限
        ↓
Exception Center + GMV 派生視角 + 報表匯出
```

## 5. Preflight contract

### 5.1 輸入欄位

必要欄位：

- `來源單據號`
- `退款原幣金額`
- `退款状态` 或 `退款狀態`

解析後統一使用 `退款狀態`。`總退款` 維度包含所有非空退款狀態；`已退款` 維度只精確匹配 `已退款`。

若檔案包含以下欄位，Preflight 可讀取並保留於異常明細，但第一階段不改變扣減主鍵：

- `退款單號`
- `退款單狀態`
- `原幣幣種`
- `退款日期` 或同義日期欄位

### 5.2 Preflight 結果

新增或整理成一個純函式 read model，建議介面如下：

```python
build_gmv_refund_preflight(
    raw_tour: pd.DataFrame,
    raw_others: pd.DataFrame,
    refund_rows: pd.DataFrame,
    formal_tour: pd.DataFrame | None = None,
    formal_others: pd.DataFrame | None = None,
) -> dict
```

回傳結構固定包含：

```python
{
    "status": "ready" | "warning" | "blocked",
    "schema": {
        "required": [...],
        "missing": [...],
        "status_column": "退款狀態",
    },
    "fileMetrics": {
        "rows": int,
        "sourceOrders": int,
        "duplicateRows": int,
        "emptySourceRows": int,
        "invalidAmountRows": int,
        "negativeAmountRows": int,
        "statusCounts": dict[str, int],
        "refundTotal": float,
    },
    "dimensions": {
        "總退款": { ...matching metrics... },
        "已退款": { ...matching metrics... },
    },
    "issues": [
        {
            "code": str,
            "severity": "blocking" | "warning" | "info",
            "count": int,
            "amount": float,
            "examples": list[str],
            "message": str,
        },
    ],
    "exceptionRows": pd.DataFrame,
}
```

每個退款維度的 matching metrics 至少包含：

- `sourceOrders`
- `matchedFormalOrders`
- `matchedExcludedOrders`
- `unmatchedOrders`
- `formalMatchRate`
- `unmatchedAmount`
- `refundTotal`
- `appliedRefundTotal`
- `overRefundTotal`

### 5.3 Blocking 與 Warning 規則

Blocking：

- 缺少必要欄位。
- 清洗後沒有任何有效來源單據號與退款金額。
- `退款原幣金額` 無法解析且沒有任何可用金額。

Warning：

- 存在未知或空白退款狀態；總退款仍可計算，但已退款只會取精確 `已退款`。
- 存在重複退款列或重複退款單號。
- 存在負值、零值或非數字退款金額。
- 正式口徑匹配率低、SQLite 找不到來源單據號、被收入規則排除或超額退款。

第一階段不以固定匹配率門檻阻塞計算；系統必須顯示風險，是否繼續由使用者決定。這避免把跨期間退款檔誤判成上傳失敗。

## 6. Reconciliation Engine contract

現有 `_apply_gmv_refund_adjustments()` 保持向後相容，擴充其結果或由新的 preflight helper 共用純函式，不改變既有報表計算結果。

對帳引擎必須遵守：

1. 以標準化 `來源單據號` 作為匹配鍵。
2. 同一來源單據號的退款原幣金額先彙總。
3. 同一來源單據號有多筆收款列時，按各列原收款金額比例分配退款扣減。
4. 實際扣減不可超過原收款原幣金額；超出部分列為 `超額退款`，不產生負 GMV。
5. 不刪除原始收款列，只將扣減後金額用於 session-only 派生 frame。
6. `總退款` 使用全部有效退款列；`已退款` 只使用 `退款狀態 == 已退款`。
7. 正式口徑匹配狀態必須以既有 `_build_revenue_scope_frames()` 的結果判斷。
8. 不把 `被收入規則排除` 或 `SQLite 找不到` 的退款金額誤報為正式口徑實際扣減。

Exception row 至少包含以下欄位：

```text
退款維度
來源單據號
退款狀態
退款明細金額
匹配狀態
原因代碼
是否可扣減
原收款金額
實際扣減金額
超額退款金額
資料表
分社
銷售代表
```

建議原因代碼：

- `FORMAL_MATCHED`
- `REVENUE_SCOPE_EXCLUDED`
- `SQLITE_SOURCE_NOT_FOUND`
- `OVER_REFUND`
- `DUPLICATE_REFUND_ROW`
- `INVALID_REFUND_AMOUNT`
- `EMPTY_SOURCE_ID`
- `UNKNOWN_REFUND_STATUS`

## 7. Exception Center UI

位置：既有 Streamlit「GMV 排除訂單看板」內，位於退款 Preflight 之後、完整報表下載之前。

介面分成四個區域：

### 7.1 Preflight summary

顯示上傳檔案行數、來源單據數、狀態分布、退款總額、重複列、金額異常及 Preflight 狀態。

### 7.2 Dimension comparison

用表格並列 `總退款` 與 `已退款`：來源訂單數、正式口徑匹配數、被收入規則排除數、SQLite 找不到數、正式匹配率、實際扣減金額、超額退款金額。

### 7.3 Exception detail

以唯讀 dataframe 展示異常列，至少提供匹配狀態與原因代碼。使用者可以依退款維度與匹配狀態篩選，並查看來源單據號、退款金額與可扣減金額。

### 7.4 Download

提供異常明細 CSV 下載；既有總退款／已退款完整報表與稽核 workbook 下載保持不變。下載內容必須帶有退款維度與匹配狀態，避免使用者拿到無法解讀的匿名清單。

## 8. 錯誤處理與安全邊界

- 退款檔案解析錯誤時，顯示可操作的缺欄位或格式訊息，不顯示完整 traceback 給一般使用者。
- Preflight blocked 時不執行 GMV 扣減，也不生成報表。
- Warning 時允許繼續，但在摘要與報表下載區保留 Warning 狀態。
- 清空、新上傳或檔案 signature 改變時，清除舊的 preflight、exception rows 與 workbook session state，避免 stale state。
- 不建立或修改 SQLite、baseline、rollback、rules config、AI cache 或正式 export cache。
- 不接受 Excel 內的公式結果作為業務規則；只讀取解析後的欄位值。

## 9. 測試與驗收

### 9.1 Unit tests

- 缺少必要欄位會回傳 `blocked`，並列出缺少欄位。
- `退款状态` 與 `退款狀態` 都能標準化為 `退款狀態`。
- 總退款包含多種狀態，已退款只包含 `已退款`。
- 同一來源單據號多筆退款會正確彙總。
- 多筆收款列會按原收款金額比例扣減且不刪列。
- 超額退款會被 cap，並正確產生 `超額退款`。
- 正式口徑、被收入規則排除、SQLite 找不到三種匹配狀態可穩定分類。
- 重複、空值、負值與未知狀態會產生正確 issue code。

### 9.2 UI / contract tests

- GMV 頁面先顯示 Preflight，再顯示扣減結果。
- blocked 狀態不會顯示或生成完整報表按鈕結果。
- exception dataframe 包含必要欄位，CSV 下載包含退款維度與匹配狀態。
- 新上傳檔案會清除舊的 preflight、exception 與 workbook state。
- 正式 SQLite 檔案 signature、generation 與 row counts 在流程前後不變。

### 9.3 Acceptance evidence

- Targeted refund/preflight tests pass。
- Full pytest pass。
- `py_compile` 與 `git diff --check` pass。
- `scripts/hermes_post_change_check.py --json` 回傳 `overallStatus: pass`。
- 手動驗收同一退款檔可看到總退款與已退款並列結果，且可從異常中心追到未匹配來源單據號。

## 10. 實作檔案邊界

預計只修改既有業務功能路徑：

- `app_workflows.py`：退款 Preflight read model、匹配分類與 exception row 組裝。
- `app_pages.py`：GMV 看板 Preflight 與 Exception Center UI、CSV download、stale state 清理。
- `tests/test_gmv_refund_adjustment.py`：退款引擎與維度測試。
- `tests/test_gmv_refund_preflight.py`：新增 Preflight 與匹配分類測試。
- `tests/test_streamlit_gmv_refund_contract.py`：新增頁面 contract 測試。

第一階段不新增資料庫、API router、migration 或外部服務。

## 11. Definition of Done

此 spec 的 implementation 完成條件：

1. 使用者上傳退款檔後，必須先看到可讀的 Preflight 結果。
2. 使用者能分別看到總退款與已退款的匹配、扣減及異常數字。
3. 每個非正式扣減來源單據號都有可下載、可追查的原因分類。
4. 退款扣減仍按既有原幣、比例分配與 cap 規則執行。
5. 正式 SQLite、AI Forecast、WAPE、baseline 及正式 export 未被修改。
6. 完整測試與 Hermes 驗收通過。
