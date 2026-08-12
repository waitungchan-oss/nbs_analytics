# MacroLens US Skill Design

狀態：draft for user review  
版本：v1 product and Skill design  
日期：2026-08-11

## 1. 產品目的

MacroLens US 是一個面向美國上市股票與 ETF 的中英雙語市場研究與投資決策支援 Agent。它把宏觀經濟資料、公司基本面、產業脈絡、估值與事件資訊整理成可追溯的研究報告，協助使用者理解市場與決定下一步研究方向。

MacroLens US 不執行交易、不連接券商帳戶、不管理資金、不保證投資回報，也不把一般市場研究包裝成未經授權的個人化投資建議。

## 2. v1 範圍

### 支援範圍

- 美國上市股票與 ETF。
- 美國宏觀資料：CPI、Core CPI、Nonfarm Payrolls、失業率、GDP、聯邦基金利率與 Treasury yields。
- 科技股與半導體股基本面、產業與供應鏈研究。
- 長期投資研究。
- 事件驅動研究，包括財報、FOMC、經濟數據、公司公告、併購與監管事件。
- 中文、英文或中英雙語報告。
- 使用者主動查詢，以及每日／每週報告工作流；若 Capafy runtime 不提供可用排程或通知能力，保留相同流程的手動觸發備援。
- 第一版以 DeepSeek API 加 Web Search 取得資料，不要求獨立市場資料 API 作為必要依賴。

### 明確非目標

- 自動下單、調整持倉或操作券商帳戶。
- 個人化資產配置、保證收益或勝率。
- 高頻交易、即時交易訊號或延遲敏感的 execution system。
- 非美國市場的完整覆蓋。
- 取代持牌投資顧問、研究主管、法遵或稅務專業人員。
- 使用沒有商業使用權的資料、未驗證的網頁摘錄或秘密資料。

## 3. 目標使用者與主要任務

### 目標使用者

- 一般美股投資者。
- 長期投資者。
- 科技股與半導體研究者。
- 需要快速理解市場資料的管理者或商業使用者。

### 主要任務

使用者可提出一個股票、ETF、產業、宏觀主題或事件，以及希望回答的問題。Agent 先確認研究範圍與資料新鮮度，再輸出一份有來源和限制聲明的研究報告。

例子：

- 分析最新 CPI 與非農對美國股票市場可能造成的影響。
- 比較兩至四間科技或半導體公司的基本面與估值。
- 針對一間公司製作長期投資研究報告。
- 分析財報、FOMC 或重大公告後的可能影響、風險與後續觀察指標。
- 為一個美股或 ETF watchlist 產生每日／每週研究摘要。

## 4. 使用者輸入契約

### 必填輸入

- 研究主題：股票代號、ETF、產業、宏觀指標或事件。
- 研究問題或想作出的判斷。
- 語言偏好：中文、英文或雙語。

### 可選輸入

- watchlist。
- 投資研究時間框架：短期事件、六至十二個月或長期。
- 指定比較公司、ETF 或指標。
- 使用者提供的公開資料或報告。
- 每日／每週報告頻率與時區。

### 輸入驗證

- 股票代號、ETF 名稱、公司名稱與資料來源必須能互相對應。
- 缺少研究問題、時間框架或關鍵資料時，Agent 應先列出缺口並要求補充。
- 不接受 API keys、券商登入、銀行資料、私人身份資料或未經授權的內部資料。
- 不得把使用者提供的推測直接當成已驗證事實。

## 5. 研究工作流

```text
Scope
  -> Data retrieval
  -> Freshness and source validation
  -> Fundamental / macro analysis
  -> Scenario construction
  -> Risk and uncertainty review
  -> Bilingual report packaging
```

### Scope

確認研究問題、標的、時間範圍、比較對象、語言與輸出類型。若問題同時包含多個不可比較的任務，先拆分並說明範圍。

### Data retrieval

依研究問題使用 Web Search 找尋宏觀數據、公司申報／財報、公司公告、價格、估值與產業資料。搜尋時優先限定官方或第一手來源，例如 BLS、FRED、BEA、Federal Reserve、SEC filings 與公司 Investor Relations。第一版不把獨立市場資料 API 作為必要依賴，但可在後續版本增加具商業授權的結構化資料 provider。

### Validation

每項重要數據必須保留資料日期、發布日期、原始來源 URL、單位、期間與是否可能修訂。搜尋結果摘要只能作為發現線索，不能單獨作為重要數字的證據；Agent 必須打開並核對原始頁面或原始文件。遇到資料過期、缺失、互相矛盾、搜尋失敗或官方來源不可讀時，報告必須明確標示，不得靜默補值或猜測。

### Analysis

將觀察到的資料與推論分開，至少處理：宏觀背景、基本面、估值、產業驅動因素、催化劑、風險與可能的反例。

### Scenarios

以 Bull、Base、Bear 三種情境表達不確定性。情境必須列出觸發條件、支持證據、主要風險與需要追蹤的指標，不得輸出看似精確但沒有依據的報酬保證。

### Review before delivery

交付前檢查：來源是否存在、日期是否清楚、數字是否一致、事實與推論是否分開、雙語內容是否語意一致、免責聲明是否存在。

## 6. 報告輸出契約

每份報告應包含：

1. Executive summary／執行摘要。
2. Research question and scope／研究問題與範圍。
3. Data timestamp and sources／資料時間與來源。
4. Macro context／宏觀背景。
5. Company or ETF fundamentals／公司或 ETF 基本面。
6. Valuation and peer comparison／估值與同業比較（適用時）。
7. Catalysts／主要催化劑。
8. Risks and counter-evidence／主要風險與反方證據。
9. Bull, Base, Bear scenarios／三種情境。
10. Watchlist indicators／後續觀察指標。
11. Limitations／資料限制與不確定性。
12. Research conclusion／研究結論與下一步研究建議。
13. General research disclaimer／一般市場研究免責聲明。

報告可以提供研究候選、比較與條件式觀察，但不得使用「保證」、「必然上漲」、「一定下跌」等表述，也不得在沒有使用者授權和完整資料的情況下給出個人化買賣指令。

## 7. Daily / weekly report design

使用者可配置：

- watchlist。
- daily 或 weekly cadence。
- 使用者時區。
- 中文、英文或雙語。
- 宏觀、基本面、估值、事件或綜合模式。

每日／每週報告只報告自上一個週期以來的新資訊、重要變化與需要重新檢查的風險，避免每次重複產生完整長報告。若沒有足夠的新資料，應回報「沒有足夠新證據」而不是製造變化。

排程執行需要 Capafy runtime 的 scheduler、通知或等效能力。產品必須先以 publisher runtime 的實際驗證結果決定自動發送方式；若無法由 Capafy 可靠觸發，v1 只能提供使用者主動觸發的 daily／weekly report command，不得宣稱已支援背景自動發送。

## 8. 技術與資料依賴

- LLM：DeepSeek API，使用其當時有效且支援的 model identifier。
- Web Search：用於搜尋最新新聞、宏觀數據、公司申報／財報、公司公告、價格、估值與產業資料；搜尋工具必須能回傳可開啟、可引用的來源 identity。
- 官方來源：優先使用 BLS、FRED、BEA、Federal Reserve、SEC filings 與公司 Investor Relations 頁面。
- 結構化市場資料 API：v1 非必要依賴；只有在 Web Search 無法可靠取得價格或估值、且 provider 具備商業授權時，才進入後續版本。
- Secret storage：API credential 只能透過 Capafy credential mechanism 或等效 secret store 注入，不能硬編碼在 Skill、報告、log 或範例中。

DeepSeek 負責語言理解、工具呼叫決策與分析；Web Search 負責發現及取得外部資料。DeepSeek API 的 Tool Calls 需要由執行環境提供實際搜尋工具，模型本身不會自動執行任意函式。搜尋摘要不得取代官方原始頁面或原始文件的驗證。第一版不需要額外市場資料 API key，但必須確認 Capafy publisher runtime 的 Web Search、來源擷取、引用與排程能力。

## 9. Capafy packaging and monetization

Skill package 的最小內容：

```text
macrolens-us/
├── SKILL.md
├── references/
│   ├── report-schema.md
│   ├── source-and-freshness-policy.md
│   ├── bilingual-style-guide.md
│   └── risk-disclaimer.md
├── scripts/
│   ├── validate_input.py
│   ├── validate_market_data.py
│   └── render_report.py
└── examples/
    ├── macro-report-example.md
    ├── equity-report-example.md
    └── watchlist-report-example.md
```

Capafy Agent Card 暫定文案：

> MacroLens US — Bilingual US Market Research
>
> Research US stocks and ETFs with macro context, company fundamentals, valuation, catalysts, risks, and Bull/Base/Bear scenarios. Track CPI, payrolls, rates, earnings, and major events with dated sources. General market research and decision support only — no trading, no guaranteed returns, and no personalized financial advice.

初始售賣方式：Run on Capafy，先以 Free Trial 或低價訂閱驗證使用需求；每日／每週自動報告能力通過 runtime acceptance 後，再以訂閱作為主要模式。

## 10. Safety, privacy and governance boundary

- 不收集券商帳戶、交易權限、私鑰或支付資料。
- 不執行交易、投資組合變更或對外金融承諾。
- 使用者輸入與外部資料的處理方式須在 Agent Card 和 privacy declaration 中說明。
- 報告保留來源、日期與限制；不可刪除關鍵反方證據來強化結論。
- 任何 API credential 不得進入 Skill package、Git、範例或輸出。
- 若資料來源不可用、過期或未授權，Agent 必須降級為 blocked／insufficient evidence，而不是改用未驗證來源。

## 11. Open decisions before implementation

以下事項不是產品方向問題，但必須在進入實作前明確化：

1. Capafy publisher runtime 是否能執行可靠的 Web Search、開啟原始來源、保存來源 identity 與產生引用。
2. Capafy publisher runtime 是否能執行可靠的 daily／weekly scheduler、通知與必要的外部工具 calls。
3. 第一版是否輸出 HTML／PDF，或只輸出 Markdown。
4. 每日／每週報告的預設時區、交付時間與 watchlist 儲存方式。
5. DeepSeek 的預設 model、token budget、fallback 與成本上限。
6. 針對不同司法管轄區是否需要額外的投資研究聲明或限制。

這些決定未完成前，不能宣稱 MacroLens US 已經具備無人值守、即時或合規的投資研究服務能力。
