# 個人成長與 NBS 系統優化路線

日期：2026-09-05；版本：1.0

對象：已有實際業務系統開發經驗、工作兩年的文科碩士背景專案主導者

性質：個人學習與專案優化建議；不是正式驗收紀錄、治理契約修訂或 implementation 授權。

## 1. 接下來最值得投入的方向

你已經建立並持續使用一套包含業務分析、Agent 分工與記憶元件的系統，也觀察到開發所需 Token 減少、結果品質良好。下一階段適合把這些經驗轉化為三種更穩定的能力：

1. **能解釋與診斷系統。** 知道結果如何產生，遇到問題能從證據找出原因。
2. **能量化並維持成效。** 看清楚哪些安排真的省成本、減少錯誤，並保留這些安排。
3. **能把方法交給別人。** 讓另一個人能理解、操作並驗證成果，也讓你的職業價值更容易被看見。

現在最快、最有用的下一步：挑一個最近完成、範圍清楚的 Task，用一頁紙記錄「問題、決策、涉及元件、驗證結果、成本、下一次會怎樣做」。這可以同時成為學習材料、改善基準和作品集素材。

## 2. 建議所依據的現況

### 2.1 已核對的檔案與程式行為

本次 source 基準為 `9ee29e0fce388552398c9daa89c90b7aae23f470`，工作位置是獨立 worktree。主部署只做指定 Memory Hub 目錄的唯讀檢查。

| 項目 | 本次核對結果 | 如何理解 |
|---|---|---|
| Memory Hub | 主部署 `catalog.json` 為 3,553 bytes，包含 3 筆 Context Agent 相關摘要；該目錄 `du -sk` 為 12 KiB | 已有落地保存的精簡知識；不是整個 Agent 系統或全部記憶的容量 |
| Memory Sidecar | 現階段 schema 要求 `writer_enabled=False`，provider 的寫入操作明確拒絕 | 關閉寫入是現有邊界，不應為了實驗自行開啟 |
| Short-term Offload | 預設 TTL 30 分鐘，上限 24 小時；每 run 最多 20 個 artifacts、200,000 bytes | 已有限額，但每 run 限額不等於跨所有 run 的總上限 |
| 過期檔案清理 | `cleanup_expired()` 已有刪除過期 JSON 的實作，service 提供 cleanup 入口 | 不缺清理函式；本次未核實正式部署何時呼叫、最近清掉多少 |
| Workflow retention | 設定為 90 天、保留最近 30 個 terminal runs；單 stage 5 MiB hard cap、單 run 25 MiB soft cap | 這是 workflow artifacts 的政策，不能套用成所有目錄的統一容量保證 |
| 用量觀測 | 已有 `scripts/codex_usage_report.py`，可整理輸入、cached input、輸出等資料 | 優先使用現有報表；本次未執行或驗證成本改善幅度 |
| 本地 Agent | 已有 CLI、runner、記憶 adapter 與驗證流程；`local_model` 的 schema 支援不證明模型已在本機推論 | 部署位置、接線與實際執行證據需要分開看 |

來源：[Memory Hub 部署設定](../agent_config/memory_hub_catalog_deployment.json)、[Sidecar 邊界](../backend/agents/memory_sidecar_provider_adapter.py)、[Offload policy](../backend/agents/short_term_offload_policy.py)、[Offload store](../backend/agents/short_term_offload_store.py)、[Workflow retention](../agent_config/workflow_retention.json)、[用量報表](../scripts/codex_usage_report.py)、[Runner topology](agents/RUNNER_TOPOLOGY_AND_TRANSPORT_MATRIX.md)。

### 2.2 你的使用觀察與尚未量測的部分

你回報：專案已能支撐實際工作，使用這套流程後，以更少 Token 得到良好實作結果。這是有價值的使用經驗，可作為持續優化的起點。

目前仍不宜寫成具體節省百分比，也不能把全部成效歸因於 Memory Hub。Context 裁剪、Task 切分、模型選擇、prompt caching、程式重用與驗證安排，都可能共同影響結果。

本文件未重新執行正式 SQLite、服務、完整 pytest 或 Hermes 驗收；也不沿用舊評估中的安全嚴重度作當前結論。

## 3. 個人能力：以現有專案作為學習材料

文科訓練帶來的閱讀、論證、概念拆解與表達能力，可以直接用於需求釐清與系統設計。接下來補強能讓你獨立判斷技術結果的部分。

| 能力 | 建議練習 | 學會的判準 |
|---|---|---|
| Python 閱讀 | 每週選一個既有小函式，先自己寫出輸入、輸出、副作用與錯誤情況，再請 AI 核對 | 能解釋每個重要分支，並指出一個容易出錯的輸入 |
| 資料與 SQL | 在臨時 fixture 練習 `JOIN`、`GROUP BY`、NULL、重複列與 transaction | 能解釋為何 join 後金額可能膨脹，以及怎樣驗證守恆 |
| Git 與版本 | 在練習 repo 解讀 diff、parent commit、分支與 revert | 能指出一次修改包含什麼、如何回退，以及回退可能影響什麼 |
| 除錯 | 對已解決問題重建最小案例；先寫假設，再用證據排除 | 能分清資料、程式、環境與服務問題，不靠連續猜測改動 |
| 測試設計 | 為一個真實業務規則設計正常、邊界、錯誤三種案例 | 改錯規則時測試會失敗，而不是只檢查函式有回傳值 |
| Agent 原理 | 追蹤一次「輸入 → 記憶提示 → 模型 → 工具 → 結果」 | 能指出哪步需要 LLM、哪步只是普通程式，以及資訊可能在哪裡遺失 |

每週選一個你真正碰到的問題即可。AI 可以協助解說與校對，但先寫下自己的判斷，會更容易發現理解缺口。

小型 SQL 練習例子：收入表某訂單只有一列，退款表同訂單有兩列。直接 join 後加總收入會怎樣？先手算，再用臨時資料驗證。這類練習與你現有業務直接相關。

閱讀入口：[Python 例外與 traceback](https://docs.python.org/3/tutorial/errors.html)；[SQLite transaction](https://www.sqlite.org/lang_transaction.html) 與 [isolation](https://www.sqlite.org/isolation.html)。每次只讀能解釋當前問題的段落，再回到自己的案例。

## 4. 成效衡量：留下小而有用的紀錄

先整理 5–10 個範圍相近的已完成 Task。樣本只用來找到趨勢，不應用小樣本宣稱普遍優勢。

### 4.1 每個 Task 的最小紀錄

| 欄位 | 記錄方式 |
|---|---|
| 任務與範圍 | 需求、完成條件、涉及模組、風險等級 |
| 版本與執行條件 | commit／source fingerprint、模型、reasoning profile、是否使用 memory、cache 情況 |
| 用量 | uncached input、cached input、output；保留來源欄位，避免重複加總 reasoning 或 cumulative events |
| 時間 | 人工投入與工具／模型等待分開記錄 |
| 品質 | 第一次 Review 結果、返工次數、各驗證 gate 結果 |
| 後續結果 | 發現回歸的觀察期間、是否出現同類錯誤；尚未觀察就填「未觀察」 |
| 證據位置 | 指向原報表或 artifact，不再複製完整 log |

總 Token 少不一定代表費用同比例下降：模型不同、cached input 與其他 token 的計價可能不同。需要計算金額時再核對當時價格或帳單；資料不足就只報用量。

### 4.2 想知道記憶貢獻多少時

先利用既有 A/B 工具和已批准 runner，以同一 immutable workload、同一模型/profile、相同初始 source 與工具權限比較 `recall_off`／`recall_on`。每次在獨立執行環境進行，避免前一輪結果流入下一輪；記錄 cache 差異與執行順序。

目前治理對相應 Agent 的 default-on 要求三組獨立 off/on evidence，需依既有 contract 執行。三組是治理所需的最小證據，不是統計上已證明普遍有效。缺 runner 或 evidence 就記錄未驗證。

個人學習時，也可以先人工比较「現有知識摘要是否有幫助」，但不要把這種觀察寫成正式 A/B PASS。

保留記憶的理由可以是：品質不下降而成本減少；或成本稍增，但明顯避免一個高代價錯誤。記憶對不同 Task 的效益可以不同。

## 5. 記憶優化：固定容量內，提高留下資訊的價值

### 5.1 先查磁碟實際花在哪裡

優先做指定目錄的唯讀容量盤點，列出現有 bytes、檔案數、有效／過期數、增長速度、清理入口與最近執行證據。

| 類別 | 盤點重點 |
|---|---|
| Memory Hub | catalog、來源文件、可用筆數、失效筆數；不要把「存在」直接當作「可以被 Agent 使用」 |
| Short-term Offload | 跨所有 runs 的總量、過期檔案、cleanup 是否實際被呼叫 |
| Workflow evidence／telemetry | retention 適用條件、受保護 runs、是否只是 soft-cap warning |
| 模型與報表 cache | cold/warm 成本、重建需要多久；它們和語意記憶是不同資料 |
| SQLite 備份、quarantine、worktrees | 單獨盤點，依各自政策處理，不能借用 memory TTL 清理 |

Memory Hub 本次只佔約 12 KiB。這個數字說明應先找出真正的大宗佔用，不能據此推斷整個專案磁碟用量很小。

### 5.2 驗證限制是否有效

已有 cleanup 函式，因此下一個問題是「部署有沒有執行它」，以及「執行後空間是否下降」。

在 temporary fixture 驗證以下情境，再決定是否有需要修改實作：

- 過期資料被移除，未過期資料仍能讀取。
- 達到容量上限時，回傳可理解的結果。
- 中斷或寫入失敗不損壞原有有效資料。
- 清理只作用於授權目錄；遇到不安全路徑時停止。
- 跨 run 累積是否另有總上限；單 run 限制不能替代這項檢查。

如需調整容量，先用實測決定預算。候選策略是軟門檻提示整理，硬門檻停止新寫入並保留既有可用資料。這是待評估方案，本文件沒有設定任何新門檻或啟用刪除。

### 5.3 讓小型知識庫更有用

值得保存的內容通常是：反覆出現的業務限制、難以重建的決策原因、已驗證的失敗教訓，以及下一次可以直接使用的檢查方法。

完整對話、大段 log、重複程式碼和沒有證據的猜測，通常不適合直接塞進長期記憶。

人工整理一張「經驗卡」可先練習以下欄位：

> **情境**：什麼條件下會用到？
>
> **教訓**：下次應注意什麼？
>
> **來源**：哪個 commit、測試或事件支持？
>
> **適用範圍**：哪些版本／模組適用？
>
> **重新檢查條件**：什麼變化會讓它失效？

經驗卡只是內容草稿，不會自動成為 Memory Hub 的正式 record，也不繞過既有 catalog/schema、權限與新鮮度規則。Sidecar writer 維持目前設定；若日後確有更新需求，另立 Task 設計批准、去重與版本取代流程。

跨任務記憶與單次工作狀態的概念，可參考 [LangChain Memory overview](https://docs.langchain.com/oss/python/concepts/memory)。閱讀目的是釐清原理，不表示需要遷移框架。

## 6. Agent 與業務系統：按實際瓶頸選一個優化

| 如果觀察到 | 優先檢查 | 小步改進的預期結果 |
|---|---|---|
| Agent 常重讀相同內容 | Context 是否太長、證據引用是否精準、是否已有效重用 | 減少無關閱讀，仍能定位必需證據 |
| Review 常退回同類問題 | Task 是否模糊、完成條件是否缺漏、檢查是否可提前完成 | 減少可避免的返工 |
| Token 少了但等待仍長 | 真正時間花在模型、工具、測試、cache 或 serialization 哪一段 | 優化耗時最大的已確認階段 |
| 功能一改便牽動很多模組 | 依賴與資料契約是否不清楚 | 先改善一個邊界，縮小影響面 |
| UI 或資料結果難以解釋 | source、generation、cache freshness 是否能追溯 | 使用者知道結果來自哪個版本 |

現有 Agent 的多寡、MCP 接口數量、本地模型規模，都不適合作為成長指標。有明確跨工具接入需求時再考慮 MCP；有隱私、離線或可核算的成本需求時，再評估本地推論的品質與維護代價。

如果將系統提供給更多使用者，先核實實際監聽範圍、寫入 API 權限、敏感資料流向與操作責任。嚴重度以當時可達路徑和影響評估，不直接沿用舊報告標籤。

任何後續正式改動仍按 [AGENTS.md](../AGENTS.md)、[Dispatch contract](agents/CODEX_AGENT_DISPATCH.md) 與 [Hermes contract](../NBS_HERMES_MONITORING.md) 執行。正式口徑固定為「不含掛賬核銷與TT退款轉團款」，2026-05 frozen baseline 固定為 `HKD 12,057,968`。記憶與 Graph 維持 read-only／non-authoritative；本文件沒有授權改動業務資料或治理狀態。

## 7. 90 天行動安排

以下以開始執行當天計算，不是自動排程。建議每週先預留約 3–4 小時，依工作量調整；學習可持續，implementation 每次只批准一個 Task。

| 時段 | 主題 | 具體交付物 | 完成判準 |
|---|---|---|---|
| 第 1–14 天 | 建立個人基準 | 一頁端到端資料流、3 個已完成 Task 的復盤、指定目錄容量清單 | 能自己解釋流程；知道成本與容量資料在哪裡 |
| 第 15–30 天 | 練習判斷與整理證據 | 一個最小除錯案例、5–10 個可比較 Task 的紀錄、cleanup 接線盤點 | 區分 code／runtime／evidence；找出一個有證據的瓶頸 |
| 第 31–60 天 | 改善一個已確認問題 | 一個 approved Task，附 baseline、最小改動、rollback 與所需驗證 | 既有 gates 通過；能說明改善結果與代價；若無問題則保留現況並記錄理由 |
| 第 61–90 天 | 驗證可維護性與表達成果 | 經脫敏的案例說明、短示範、一份操作說明 | 另一人依說明能理解或在 fixture 操作；可用數據說明價值與限制 |

每週可採以下節奏：讀懂一小段程式 45 分鐘、做一個隔離練習 60 分鐘、整理任務／用量／容量紀錄 30 分鐘、完成一頁復盤 30 分鐘。其餘時間處理當週最值得投入的問題。

如果某週沒有新瓶頸，不必為完成計畫而新增元件。可以用同樣時間改善理解、操作說明或案例表達。

## 8. 把成果轉化成職業價值

適合強化的定位是「理解業務、能主導 AI 協作與系統交付的人」。職稱可以隨公司而變，應以能交付的能力作主軸。

挑三個案例：一個資料正確性問題、一個效率改善、一個失敗後修正的案例。每個案例用相同結構說明：

1. 原本誰遇到什麼問題，代價是什麼。
2. 你負責哪些需求、規則、設計與取捨；AI／其他人負責哪些工作。
3. 為什麼採用這個方案，以及放棄了哪些可行替代方案。
4. 如何測試、如何判定完成、有哪些使用證據。
5. 最後改善了什麼，仍有哪些限制。

不必把「專案巨大」作為核心賣點。能把複雜需求做成可維護、可理解的系統，更容易讓別人判斷你的能力。公開前確認資料與程式的分享權限，示範使用合成資料；沒有量測的百分比就不要填入履歷。

## 9. 第一週可直接使用的清單

- [ ] 選定一個最近完成的 Task，保存原始證據引用。
- [ ] 自己畫出該 Task 的資料流與 Agent 呼叫關係，再請 AI 核對。
- [ ] 讀懂其中一個關鍵函式，列出輸入、輸出、副作用與錯誤條件。
- [ ] 用現有用量紀錄填一筆 Task 表；缺失欄位填「未提供」。
- [ ] 只讀盤點指定資料目錄，分開記憶、workflow evidence、cache、備份與 worktrees。
- [ ] 選出一個最值得調查的問題；先記錄預期收益與如何驗證，再決定是否需要 implementation。

## 10. 文件交付範圍與證據限制

本次只新增本 Markdown 建議文件。沒有修改既有 system map、handoff、ADR、正式規則、資料庫、記憶 catalog 或 runtime；沒有建立排程、啟用 writer 或執行清理。

本文件可以作個人學習與討論的起點；後續若用它提出正式 Task，仍需以當時 source、runtime 與批准範圍形成具體 spec。它不代表 Strict Review、full pytest、Hermes、UI acceptance 或 release gate PASS。
