# Monthly Baseline Monitoring Governance Design

## Goal

為 `2026-01` 至 `2026-06` 建立正式月度營收基準治理。第一階段採監測模式：系統在正式 upload 成功後自動核對六個月份，但新增月份的 drift 只警告、不阻擋、不 rollback。完成一個穩定上傳週期後，由使用者在 Streamlit 人工確認，才將監測月份升級為 blocking baseline。

現有 `2026-05` frozen baseline 維持 blocking，不因本設計降級。

## Official Definition

- 正式口徑：`不含掛賬核銷與TT退款轉團款`
- 正式母體：全部正式分社加正式四人專職銷售組
- 專職銷售員：沿用 `SALES_REP_LIST`
- 日期欄位與月份歸屬：沿用正式 dashboard 計算鏈路的 `統一日期`
- 內部比較使用精確小數；UI 與報告使用顯示整數
- 金額容差沿用現有 gate：`abs(actual - expected) < HKD 1.00`

## Baseline Registry

| 月份 | 精確基準 | 顯示基準 | 初始模式 |
|---|---:|---:|---|
| `2026-01` | `10,711,053.50` | `10,711,054` | Monitoring |
| `2026-02` | `9,765,694.54` | `9,765,695` | Monitoring |
| `2026-03` | `14,628,841.00` | `14,628,841` | Monitoring |
| `2026-04` | `10,506,207.78` | `10,506,208` | Monitoring |
| `2026-05` | `12,057,967.92` | `12,057,968` | Blocking |
| `2026-06` | `9,083,241.29` | `9,083,241` | Monitoring |

六個月精確累計為 `HKD 66,753,006.03`。不可用逐月顯示整數相加的 `HKD 66,753,007` 取代精確累計，避免 aggregation rounding drift。

基準定義保存於獨立、可版本控制的 JSON 設定檔；runtime 穩定週期、promotion readiness 與操作紀錄保存於 SQLite governance history。基準設定與 runtime 歷史分離，避免服務重啟改寫正式定義。

## State Model

每個月份具備以下其中一種治理狀態：

- `monitoring`：自動核對；drift 只產生警告。
- `blocking`：drift 會令 upload stability gate 失敗並進入既有 rollback 流程。

整體治理狀態另包含：

- `monitoring`：尚未完成一個穩定 upload cycle。
- `promotion_ready`：最近一個符合資格的 upload cycle 六個月全部 matched。
- `blocking`：所有目標月份均已升級；2026-05 原有 blocking 身分保留。
- `drift`：至少一個監測月份不匹配，穩定週期歸零。

狀態轉換只能是：

`monitoring -> promotion_ready -> blocking`

若監測期間 drift：

`monitoring` 或 `promotion_ready -> drift -> monitoring`

系統不得自動執行 `promotion_ready -> blocking`。

## Stable Upload Cycle

一個合資格的穩定上傳週期必須同時符合：

1. 功能部署後完成一次正式 upload，而不是 preflight、頁面刷新或服務重啟。
2. Upload write path 成功，正式 SQLite integrity 為 `ok`。
3. 現有2026-05 blocking baseline matched。
4. 正式 revenue scope matched。
5. Upload stability gate accepted，沒有執行 rollback。
6. 2026年1月至6月六個月精確金額全部在容差內。
7. Stability history 已保存本次 upload Record ID 與六個月結果。

若任何條件失敗，穩定週期保持或重設為 `0 / 1`。只有正式 upload acceptance 能將週期更新為 `1 / 1`。

## Monitoring Service

新增純後端 monthly baseline service，負責：

- 從正式 dashboard summary 計算鏈路逐月取得分社、專職與 combined revenue。
- 對照 baseline registry，產出 expected、actual、delta、display values 與 status。
- 分開回傳 `monitoringChecks` 與 `blockingChecks`。
- 產出 `stableUploadCycles`、`requiredStableUploadCycles`、`promotionReady`。
- 不自行寫正式營收資料，不改 dashboard calculation，不修改收入排除口徑。

Upload stability gate 只使用 `blockingChecks` 決定 acceptance。`monitoringChecks` 會被保存及顯示，但在第一階段不得令 gate 失敗。

## User Interface

主要入口位於 Streamlit「業務規則配置」分頁，新增全寬 `Monthly Baseline Governance` 區域。Sidebar 與 dashboard KPI 篩選不控制本區域。

### Monitoring

顯示：

- `Monitoring` 狀態 badge
- 正式口徑與正式母體
- 穩定上傳週期 `0 / 1`
- 六個月表格：月份、顯示基準、目前金額、精確差額、狀態、治理模式
- 最近一次合資格 upload Record ID 與驗收時間
- 未 ready 時 disabled 的「升級為阻擋式基準」按鈕

### Promotion Ready

六個月 matched 且完成一個週期後：

- 顯示 `Ready` 狀態 badge
- 顯示「6 / 6 月份匹配，已完成 1 個穩定上傳週期」
- 啟用「升級為阻擋式基準」按鈕
- 明示2026-05已是 blocking，本次實際新增2026-01、02、03、04、06

### Confirmation

按下升級按鈕後開啟二次確認區域或 dialog，必須顯示：

- 升級月份及六個月精確／顯示基準
- 正式口徑與正式母體
- 依據的 upload Record ID、驗收時間與最近可用 Hermes 結果；尚無 Hermes 結果時顯示 `N/A`
- 生效後 drift 會拒絕 upload 並觸發 rollback
- Checkbox：「我理解升級後的上傳阻擋與 rollback 影響」
- `取消`與`確認升級`按鈕

確認按鈕只有在 checkbox 勾選、promotion readiness 仍有效、最新月度結果仍 matched 時可用。提交前必須重新計算，不可只信任頁面舊 state。

Hermes 結果屬獨立稽核證據，不作 promotion readiness 的必要條件，避免 Hermes 排程或服務狀態阻塞本地治理操作。

### Blocking

升級後顯示：

- `Blocking` 狀態 badge
- 生效月份 `2026-01` 至 `2026-06`
- 啟用時間、baseline version、promotion record ID
- 最近驗收狀態 `6 / 6 Matched`

### Monitoring Drift

若 monitoring month drift：

- 黃色警告顯示月份、expected、actual 與 delta
- 穩定週期重設為 `0 / 1`
- promotion button disabled
- 明示「目前僅警告，不阻擋 upload、不執行 rollback」

## Promotion Transaction

人工升級是一個原子治理操作：

1. 重新計算六個月並確認全部 matched。
2. 確認依據的 upload acceptance record 仍是最新合資格記錄。
3. 建立 promotion 前設定備份。
4. 將2026-01、02、03、04、06由 `monitoring` 更新為 `blocking`；2026-05保持不變。
5. 寫入 governance history：時間、舊模式、新模式、baseline version、upload Record ID、六個月快照與操作者來源。
6. 重新讀取設定並驗證 blocking months 完整。

任一步驟失敗時不得保留部分月份升級。失敗只回報治理操作錯誤，不得修改正式營收 SQLite rows。

## Hermes And Audit

Hermes read-only check 新增月度治理輸出：

- Registry version 與 scope
- 各月 mode、expected、actual、delta、status
- Stable upload cycle
- Promotion readiness
- 最近 promotion event

Hermes 在 `promotion_ready` 時回報 informational ready signal；在 monitoring drift 時回報 warning；在 blocking drift 時沿用現有 critical baseline drift 等級。

## Compatibility

- 現有2026-05 `PHASE2B` API contract 與 blocking 行為保持相容。
- 第一階段新增 multi-month monitoring payload，不移除現有 `baselineMonth`、`formattedExpectedTotal` 等欄位。
- Vue 維持 read-only，不自行重算或執行 promotion。
- Streamlit 只發出治理命令並展示後端結果，不在 rendering layer 計算正式營收。

## Verification

- Registry schema、精確值、顯示值與 scope unit tests。
- 六個月全部 matched、單月 monitoring drift、2026-05 blocking drift tests。
- Stable upload cycle 只由 accepted upload 推進的 tests。
- Promotion readiness、stale record rejection、unchecked confirmation rejection tests。
- Promotion atomicity、history persistence 與 config backup tests。
- Upload rollback tests：monitoring drift 不 rollback；blocking drift 會 rollback。
- Dashboard/API backward compatibility tests。
- 正式 SQLite read-only spot check：六個月數字與 registry matched。
- `scripts/system_manager.py acceptance` 與 Hermes post-change check。

## Non-Goals

- 不自動升級 monitoring month 為 blocking。
- 不修改歷史交易金額、分社歸屬或銷售員歸屬。
- 不以 UI rounding、Excel 顯示或 dashboard formatting 修正 drift。
- 不把頁面刷新、服務重啟或 preflight 當作穩定 upload cycle。
- 不在本階段取消2026-05既有 frozen baseline contract。
