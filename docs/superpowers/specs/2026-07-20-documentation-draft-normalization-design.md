# Documentation Draft Normalization Design

日期：2026-07-20
狀態：approved for implementation planning

## 1. 目的

修復 Documentation Agent 在真實 production backfill 中已產生有用內容、卻因為
無法精確輸出 `documentation-proposal-v1` 的完整欄位、hash 與 target identity 而被
拒絕的問題。

本設計將 LLM 的責任縮小為產生受限的 documentation draft；可信任的 Python service
負責將 draft 正規化為既有嚴格的 `documentation-proposal-v1`。最終 proposal 仍必須
通過既有 model、validator、preview、explicit target approval 與 Controller apply。

正式口徑固定為 `不含掛賬核銷與TT退款轉團款`；2026-05 baseline 固定為
`HKD 12,057,968`。

## 2. 範圍與非目標

本輪只調整 Documentation Agent runner 與 service 之間的輸出契約、trusted
normalization、相關測試與治理文件。

本輪不調整：

- `documentation-proposal-v1`、preview、Controller apply 或 target approval 的既有
  強制規則。
- 正式 SQLite、upload、rollback、baseline、revenue/business rules、export schema 或
  Hermes runtime acceptance。
- Documentation Agent 的 read-only 邊界、Codex CLI allowlist、Token budget、timeout 或
  stdout cap。
- 任意檔案路徑、任意 System Map section、ADR 自動建立或任何自動套用。

## 3. 問題根因

現行 runner 要求 LLM 直接輸出最終 `documentation-proposal-v1`。這使 LLM 必須同時：

1. 選擇合法 target identity。
2. 產生符合 operation 的完整內容。
3. 計算每個內容 SHA-256。
4. 建立完整 evidence payload。
5. 計算 canonical proposal fingerprint。

這些是 deterministic system work，不是語意撰寫工作。真實 Codex runner 已回傳有用的
文件意圖，但因 status、欄位與 proposal item shape 不符而成為
`invalid_agent_output`。

## 4. 選定架構

採用「受限 draft -> trusted normalization -> 現有嚴格 proposal」三層契約。

```text
documentation-evidence-v1
  -> read-only Codex runner
  -> documentation-draft-v1
  -> trusted DocumentationAgentService normalization
  -> documentation-proposal-v1
  -> existing validator preview
  -> explicit apply / target approval
  -> trusted Controller
```

### 4.1 LLM Draft Contract

runner 只接受單一 JSON object `documentation-draft-v1`。draft 必須有：

- `schemaVersion`：固定 `documentation-draft-v1`。
- `evidenceFingerprint`：必須與 stdin evidence 相同。
- `status`：只可為 `ready`、`no_documentation_needed`、`blocked` 或
  `context_overflow`。
- `proposals`：每筆只包含 `targetKind` 與 `content`。

`targetKind` 只可為 classifier 已要求的種類；draft 不得包含 target path、operation、
hash、proposal fingerprint、vault path、absolute path 或額外欄位。`content` 是普通
Markdown fragment，不得自行帶入一級或二級 heading、managed marker、secret 或原始交易
資料欄位。

當 classifier 要求文件回填時，`ready` draft 的 target kinds 必須與 required targets
完全相同且不重複。這讓 LLM 不能省略 System Map、偷偷加入 ADR，或選擇未授權目標。

### 4.2 Trusted Normalizer

`DocumentationAgentService` 在接受 draft 後，以現有 evidence 與本地受控內容建立最終
proposal：

- `brief_backfill`：從 evidence 中已核准的 manifest Brief source 取得唯一 Markdown
  basename，並映射到 validator 允許的固定 `docs/briefs/<basename>.md` repo-relative
  identity；operation 固定為 `update_managed_block`。即使來源原本位於 local runtime，
  runtime path 也不會直接進入 final target identity。內容由 trusted wrapper 加上
  task/run identity，再置入 draft fragment。
- `system_map`：只允許 `NBS_ANALYTICS_SYSTEM_MAP.md` 的既有
  `## 2A. Agent Evidence Pipeline` section。normalizer 先讀取該完整 section、以目前
  section SHA-256 建立 identity，保留原文字，並在末端追加一個 task/run-specific 的三級
  小節及 draft fragment；operation 固定為 `replace_section`。identity 使用既有 validator
  可解析的完整 `## 2A. Agent Evidence Pipeline` heading 與目前 section hash。
- `adr`：本次不為一般 documentation code change 自動產生。若 classifier 將來要求 ADR，
  draft 仍只能表達內容；normalizer 必須走明確的 create-only identity policy，未實作該
  policy 前回傳 `blocked`，不能猜測路徑。

normalizer 自行提供 contract evidence、`contentSha256`、canonical proposal fingerprint
及 `generatedAt`，然後呼叫 `DocumentationProposal.from_dict()`。任何 draft schema、
fingerprint、status、target set、content 或 current System Map section 不符時，必須回傳
`invalid_agent_output` 或 `blocked`，不能降級成寬鬆 proposal。

### 4.3 完整治理鏈不變

正規化後的 proposal 不等於已套用。既有治理保持不變：

1. validator 重算 preview、檢查內容、protected governance text 與 stale section hash。
2. Brief 仍需要 `--apply-brief`。
3. System Map 仍需要明確 `--approve-target system_map`。
4. Controller 才能以 backup、atomic replace 和 after hash 進行寫入。
5. Hermes 仍為最後 read-only acceptance，不呼叫 runner、preview 或 apply。

## 5. Runner Prompt 與限制

`CodexDocumentationRunner` 的固定 prompt 必須包含 draft schema 的精確 JSON skeleton、
allowed status、required target set 的遵守要求，以及「只輸出一個 JSON object」規則。
它維持：

- `codex exec --sandbox read-only`。
- stdin-only evidence input。
- 無 tools、filesystem、network、Git、SQLite 或 vault access。
- approved executable 仍只有 `codex`。
- input 8,000 estimated tokens、output 1,500 estimated tokens、120 秒 timeout、64 KiB
  stdout cap。

runner 僅做 draft 的低成本結構檢查與 evidence fingerprint 檢查；最終 proposal 的完整
安全驗證仍由 trusted service 與既有 model/validator 完成。

## 6. 錯誤處理與可觀測性

- runner timeout 或 nonzero exit：保持既有 `blocked` 結果。
- non-JSON、未知 draft key、schema mismatch、fingerprint mismatch、target mismatch、
  重複 target 或不安全 fragment：`invalid_agent_output`。
- current System Map 缺少受控 section 或 section 無法安全正規化：`blocked`，不產生
  partially trusted proposal。
- cache 只保存已正規化且嚴格驗證通過的最終 proposal；不保存 raw LLM output。
- telemetry 維持 bounded aggregate 值，不記錄 prompt、raw response、absolute vault path
  或 runner command。

## 7. 測試與驗收

至少新增或更新以下測試：

1. runner 只接受 matching fingerprint 的 `documentation-draft-v1`，拒絕舊 final proposal
   shape、未知 key 與 non-JSON。
2. service 將合法 draft 轉成嚴格可解析的 final proposal，並由
   `DocumentationProposal.from_dict()` 驗證。
3. normalizer 對 Brief 產生固定 repo-relative identity、managed block operation 和正確
   content hash。
4. normalizer 對 System Map 保留原 section、建立 expected section hash，並只追加三級
   task/run-specific 小節。
5. fingerprint mismatch、缺漏/額外/重複 target、不安全 heading 或 marker、未實作 ADR
   policy 都不得進入 preview。
6. 現有 validator/controller tests 必須持續通過，確認 protected revenue scope 與 baseline
   不可被移除或改寫。
7. 先跑 documentation focused pytest；完成後跑完整 pytest、
   `scripts/system_manager.py acceptance` 與
   `scripts/hermes_post_change_check.py --skip-monitor --json`。
8. 使用現有 verified backfill run 再執行一次 Documentation Agent，預期結果為 strict
   `ready` proposal 和可驗證 preview；實際 Brief/System Map apply 仍使用明確授權。

## 8. 風險控制

- 不把 raw LLM JSON 直接交給 Controller。
- 不讓 LLM 計算安全關鍵 hash、決定檔案路徑或修改 System Map 的任意 section。
- 不以放寬 parser 換取成功率。
- 當 content 或 target 不安全時失敗封閉；保留現有 proposal/apply artifacts 作稽核證據。
- 本次修復只消除 runtime-only artifact patch 的需求，不回寫或改寫任何舊 run。
