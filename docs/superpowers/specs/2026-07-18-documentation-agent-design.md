# Documentation Agent Design

狀態：approved for implementation planning
日期：2026-07-18
範圍：已驗證 evidence 的文件分類、Documentation Agent 草稿、Obsidian 回填、system map 更新與 ADR 沉澱

## 1. 目的

建立一個受治理的 Documentation Agent，將 NBS Analytics 正式修改完成後的文件回填從主 Codex 對話中分離。Codex 在功能改動通過 Review、完整驗證與 Hermes 後，依 dispatch contract 呼叫獨立 Documentation Agent；Documentation Agent 只根據 compact、已驗證的 Evidence Bundle 產生結構化文件 proposal，不直接修改 repo、Obsidian、Git、SQLite、baseline 或 runtime。

本功能必須同時支援：

- 自動模式：每個已通過正式 gates、具有文件影響的功能修改完成後，由 Codex 自動呼叫 Documentation Agent。
- 按需模式：使用者指定 run ID 或要求「回填文件」時，由 Codex 呼叫同一套流程。
- 降低主對話 Token：文件語意整理交由獨立 runner；本地 Collector、分類、驗證、diff、套用與 telemetry 不使用 LLM。
- 防止文件漂移：沒有足夠 evidence、fingerprint 不一致或 runner 不可用時 fail closed，不得由主 Codex LLM 靜默代寫。

## 2. 已確認的產品選擇

- 採用「自動草稿＋分級寫入」。
- Documentation Agent 仍可使用 LLM，但只能消費受限 Evidence Bundle 並輸出固定 JSON；它不是零 LLM 工具。
- Codex 不在主對話臨時撰寫正式回填；Codex 負責 dispatch、檢查 proposal、授權本地 Controller 套用與 Git 整合。
- Obsidian Brief evidence 回填屬低風險目標；通過 deterministic validation 後，Codex 可在既有任務授權範圍內直接套用。
- `NBS_ANALYTICS_SYSTEM_MAP.md` 只有在模組、API、資料流、狀態機、權限或部署邊界改變時才更新。
- ADR 只有在持久架構、資料治理、正式口徑、權限、安全、保留策略或不可輕易逆轉的決策出現時才建立；既有 ADR 不由 Agent 覆寫。
- System map proposal 與新 ADR proposal 必須由 Codex 顯示影響並取得明確批准後才可套用。
- Documentation Agent 不取代 Review Agent 或 Hermes，也不重新執行它們的檢查。
- 第一階段不建立 Streamlit 寫入入口、daemon、排程器、向量資料庫或 Git Integration Agent。

## 3. 採用架構

```text
completed workflow run / explicit run ID
  -> Documentation Evidence Collector (local, read-only, 0 LLM token)
  -> Documentation Impact Classifier (local, deterministic)
  -> documentation-evidence-v1
  -> approved Documentation Agent runner (bounded LLM)
  -> documentation-proposal-v1
  -> Proposal Validator and Preview Builder (local, read-only)
  -> low risk: Codex-authorized apply
  -> high risk: explicit user approval, then Controller apply
  -> documentation-application-v1 + telemetry
```

Documentation Agent 是獨立 runner，不是主 Codex 的自由文字輸出。若 runner 未提供、超時、輸出 schema 錯誤或 evidence 不完整，結果是 `blocked`；Codex 可要求重新執行或讓使用者明確授權人工 fallback，但不得自動切回主 LLM 撰寫。

本設計沿用 `.nbs_agent_runtime/runs/<run-id>/`、Workflow Store 安全邊界、canonical SHA-256、既有 redaction、Token telemetry 與 retention policy，不建立第二套 workflow database。

## 4. 元件與責任

### 4.1 Documentation Evidence Collector

新增 `backend/agents/documentation_evidence.py`，只讀指定 completed run 的 allowlisted artifacts：

- `manifest.json`
- `status.json`
- `approval.json`
- `implementation.json`
- `targeted-verification.json`
- `review.json`
- `full-verification.json`
- `hermes.json`
- Brief、批准的 design / plan 路徑與受限 Git changed-file metadata

Collector 必須確認：

- workflow `status` 是 `completed`；
- Hermes `overallStatus` 是 `pass`；
- Review verdict 是 `pass`；
- full verification 成功；
- manifest Git identity、目前 target Git identity 與 proposal base 一致；
- dirty files 全部可歸屬，或明確排除 unrelated preserved changes；
- 不注入完整 patch、原始 SQLite rows、Excel、exports、logs、prompt、runner command 或 secrets。

輸出 `documentation-evidence-v1`，包含 change summary、changed paths、批准 requirements、驗證摘要、Hermes 摘要、baseline guardrails、existing target fingerprints 與 allowed target identities。

### 4.2 Documentation Impact Classifier

新增 `backend/agents/documentation_policy.py`，以 deterministic rules 決定需要哪些文件目標，避免用 LLM 決定權限：

| Changed surface | Brief backfill | System map | ADR |
|---|---:|---:|---:|
| 功能、bugfix、agent workflow、API、資料流 | required | conditional | conditional |
| 純測試補強、格式、拼字、generated evidence | optional / skip | no | no |
| 模組責任、API contract、狀態機、部署或資料流 | required | required | conditional |
| baseline、正式口徑、權限、安全、retention、不可逆治理決策 | required | required | required proposal |

Classifier 只決定 `required`、`conditional`、`forbidden` 與風險層級，不生成文案。`conditional` 目標可以由 Agent 提議，但不能自行升級 apply 權限。

### 4.3 Documentation Agent Service

新增 `backend/agents/documentation_agent_service.py`，負責：

- 驗證 evidence schema 與 Token budget。
- 將 compact evidence 交給使用者或環境明確批准的 runner。
- 要求 runner 只輸出 `documentation-proposal-v1` JSON。
- 驗證 proposal 只能包含 classifier 允許的目標與操作。
- 按 documentation fingerprint 重用合法 cache。
- 保存 bounded telemetry，不保存完整 prompt 或 runner command。

Service 不得修改文件。它只返回 proposal 或 `blocked` report。

### 4.4 Proposal Validator and Preview Builder

新增 `backend/agents/documentation_validator.py`，將 Agent proposal 轉為可審查 preview，但仍不寫檔：

- 驗證 target path allowlist、target kind、operation、base SHA-256 與 expected section SHA-256。
- 驗證 protected baseline 與正式口徑文字沒有被改寫成其他值。
- 拒絕絕對敏感路徑、secret-like values、交易明細、客戶資料、runner command、完整 log 或原始 evidence。
- Brief 只允許更新 frontmatter 狀態與受管理的 `Implementation Evidence` 區塊。
- System map 只允許 replace 明確 heading 下的一個 section；不得整檔自由覆寫。
- ADR 只允許 create new；不得 replace、rename 或 delete 既有 ADR。
- 產生 unified diff、before/after hash 與 risk tier，供 Codex 或使用者審核。

### 4.5 Trusted Documentation Controller

新增 `backend/agents/documentation_controller.py`。Controller 是唯一寫入邊界，只有在 proposal 通過 validator 且取得相應授權後才能套用：

- `brief_backfill`：Codex 可在已批准任務中套用。
- `system_map`：需要明確 `--approve-target system_map`。
- `adr`：需要明確 `--approve-target adr`，且只建立新檔。
- 每次 apply 使用 same-directory temporary file + atomic replace。
- 覆寫既有 Brief / system map 前，在 `.nbs_agent_runtime/documentation-backups/<run-id>/` 保存 bounded backup。
- 寫入後重新計算 SHA-256，驗證與 proposal 一致。
- 任一 target 失敗時停止後續 target，保留已寫入 manifest 與 backup；不得自動猜測或部分重試。

Controller 不執行 Git stage、commit、merge 或 push。Codex 仍負責檢查 diff、測試、Review/Hermes 邊界與 Git 整合。

### 4.6 Obsidian Target Resolver

Obsidian vault root 不寫死在 tracked source。解析優先序：

1. CLI `--obsidian-vault`。
2. `NBS_OBSIDIAN_VAULT` 環境變數。
3. `.nbs_agent_runtime/documentation.local.json` 的本機設定。

若全部缺失，Obsidian target 回傳 `blocked_missing_vault`；repo proposal 仍可產生，但不得假裝已完成 Obsidian 回填。

允許的 Obsidian 子目錄只有：

- `70_Codex_Briefs/`
- `20_Decisions/`
- `10_System/`

Resolver 必須拒絕 symlink root、symlink target、path traversal、vault 外路徑與未知子目錄。第一階段現有 vault 是 `/Users/chanwaitung2025/Documents/Obsidian Vault/NBS_Analytics_Knowledge/`，但該絕對路徑只存在本機設定或 CLI，不進 tracked policy。

## 5. Target Contracts

### 5.1 Brief Backfill

Repo Brief 位於 `docs/briefs/`；Obsidian mirror 位於 `70_Codex_Briefs/`。回填內容使用受管理區塊：

```markdown
<!-- documentation-agent:implementation-evidence:start -->
## Implementation Evidence

- Run ID: `run-...`
- Git base/head: `...`
- Changed surfaces: ...
- Targeted verification: PASS
- Full verification: PASS
- Hermes: PASS
- Baseline: `HKD 12,057,968` matched
<!-- documentation-agent:implementation-evidence:end -->
```

同一 documentation fingerprint 重跑必須 idempotent：更新同一 managed block，不重複 append。若 Brief 沒有對應 Obsidian mirror，可建立 mirror；若存在不同內容且無可證明共同 base，必須 blocked，不可任意覆寫。

### 5.2 System Map Update

正式 repo target 是 `NBS_ANALYTICS_SYSTEM_MAP.md`。Obsidian 可選 mirror 是 `10_System/NBS Analytics System Map.md`。

Proposal 必須指定：

- `sectionHeading`
- `expectedSectionSha256`
- `replacementMarkdown`
- `evidenceRefs`
- `reason`

若 heading 不存在、重複、section hash 改變或 replacement 觸及另一 section，validator 必須 blocked。Agent 不得以整檔 replacement 規避 section guard。

### 5.3 ADR Creation

Repo ADR 位於 `Summay/ADR-<next>-<slug>.md`，Obsidian mirror 位於 `20_Decisions/ADR-<next> <title>.md`。ADR proposal 必須包含：

- Context
- Decision
- Alternatives considered
- Consequences
- Guardrails
- Verification evidence
- Related Brief、run ID、Git identity 與 Hermes result

ADR number 由本地 Controller 掃描 allowlisted repo ADR 後決定，Agent 只提供 slug/title，不可自行搶號。若同一 decision fingerprint 已存在，回傳 `duplicate_decision`，不得建立第二份 ADR。

## 6. Machine-Readable Contracts

### 6.1 Evidence

```json
{
  "schemaVersion": "documentation-evidence-v1",
  "runId": "run-id",
  "workflowFingerprint": "sha256",
  "git": {"base": "sha", "head": "sha", "changedPaths": []},
  "requirements": [],
  "verification": {},
  "hermes": {"overallStatus": "pass", "fingerprint": "sha256"},
  "guardrails": {
    "revenueScope": "不含掛賬核銷與TT退款轉團款",
    "mayBaseline": "HKD 12,057,968"
  },
  "classification": {},
  "targets": [],
  "documentationFingerprint": "sha256"
}
```

### 6.2 Proposal

```json
{
  "schemaVersion": "documentation-proposal-v1",
  "runId": "run-id",
  "documentationFingerprint": "sha256",
  "status": "ready",
  "summary": "bounded summary",
  "proposals": [
    {
      "targetKind": "brief_backfill",
      "operation": "update_managed_block",
      "targetIdentity": "repo-brief",
      "baseSha256": "sha256",
      "content": "markdown",
      "evidenceRefs": []
    }
  ],
  "skippedTargets": [],
  "warnings": []
}
```

`status` 只允許 `ready`、`no_documentation_needed`、`blocked`、`context_overflow`、`invalid_agent_output`。

### 6.3 Application Record

```json
{
  "schemaVersion": "documentation-application-v1",
  "runId": "run-id",
  "documentationFingerprint": "sha256",
  "status": "applied",
  "appliedAt": "ISO-8601",
  "targets": [
    {
      "targetKind": "brief_backfill",
      "pathIdentity": "repo-brief",
      "beforeSha256": "sha256",
      "afterSha256": "sha256",
      "authorization": "codex-low-risk"
    }
  ],
  "warnings": []
}
```

Application record 不保存外部 vault 絕對路徑，只保存 path identity 與 vault-relative path。

## 7. Dispatch 與使用方式

新增 `document` workflow command：

```bash
.venv/bin/python scripts/agent_workflow.py document \
  --run-id <run-id> \
  --documentation-agent-command '<approved documentation runner>' \
  --obsidian-vault '<vault-root>'
```

預設只產生 proposal 與 preview，不寫 canonical 文件。低風險 Brief 套用：

```bash
.venv/bin/python scripts/agent_workflow.py document \
  --run-id <run-id> \
  --documentation-agent-command '<approved documentation runner>' \
  --obsidian-vault '<vault-root>' \
  --apply-brief
```

System map 或 ADR 必須另帶：

```bash
--approve-target system_map
--approve-target adr
```

Codex dispatch contract 必須規定：

- 完成每個 verified functional change 後執行 `document`。
- 純 typo、format-only、generated evidence 或沒有文件影響的測試補強可由 deterministic classifier 回傳 `no_documentation_needed`，不調用 LLM。
- 使用者說「回填文件」時，對指定或最近 completed run 執行同一命令。
- 未提供 approved runner 時停止為 `blocked_missing_runner`；不得由主 Codex LLM 靜默代寫。
- runner command 不保存到 artifact。
- Documentation 失敗不推翻已通過的產品 runtime/Hermes acceptance，但 future Git Integration 必須要求 documentation `applied` 或人工批准 `skipped`。

## 8. Fingerprint、Cache 與 Idempotency

Documentation fingerprint 必須涵蓋：

- run ID、workflow manifest fingerprint 與 approved contract identity；
- Git base/head、changed-path identity 與 diff fingerprint；
- Review、full verification 與 Hermes fingerprints；
- classifier policy version、Documentation Agent contract version；
- target base hashes與既有 managed-block hashes。

只有 fingerprint 完全相同時可以重用 proposal。任何 target 在 proposal 後改變，都必須使 apply 失敗並重新 collect；不得三方模糊 merge。

重跑同一 fingerprint：

- 不重複建立 ADR；
- 不重複 append Brief evidence；
- 不重複寫 application record；
- 可以回傳既有 proposal/application identity 與 `cacheHit=true`。

## 9. 權限與安全邊界

### Documentation Agent runner 可以

- 讀取單一 compact `documentation-evidence-v1`。
- 產生固定 schema proposal。
- 對 evidence 做有限語意摘要與文件草稿。

### Documentation Agent runner 不可以

- 讀取 repo、Obsidian vault、SQLite、Excel、exports、runtime logs 或 secrets。
- 執行 shell、network discovery、Git、測試、Hermes 或任意工具。
- 修改檔案、選擇絕對路徑、stage、commit、merge 或 push。
- 更改 baseline、正式口徑、business rules、驗證結果或 evidence。

### Trusted Controller 可以

- 讀寫明確 allowlisted Markdown target。
- 建立 bounded backup、atomic replace 與 application artifact。
- 依明確 authorization 套用低風險或高風險 proposal。

### Trusted Controller 不可以

- 修改程式碼、config、SQLite、exports、runtime evidence 或 Git。
- 建立 classifier 未允許的 target。
- 以 `--force` 忽略 base hash、symlink、path traversal 或 approval gate。

## 10. Token Budget 與 Telemetry

| 階段 | 上限 | 說明 |
|---|---:|---|
| Collector / classifier / validator / controller | 0 LLM token | 純本地 Python |
| Documentation Agent input | 8k estimated tokens | compact verified evidence only |
| Documentation Agent output | 1.5k tokens | 固定 JSON、最多三個 target proposal |

超出上限回傳 `context_overflow`，由 Collector縮小 evidence；Agent 不可自行探索。

Telemetry 保存：run ID、documentation fingerprint、input characters、estimated input tokens、output tokens（runner supplied 才保存）、target counts、cache hit、duration、result 與 error code。不得保存完整 prompt、proposal markdown、絕對 vault path、runner command、原始資料或 secrets。

## 11. Error Handling

| 狀況 | 結果 |
|---|---|
| workflow 未 completed | `blocked_workflow_incomplete` |
| Review/full verification/Hermes 缺失或未 PASS | `blocked_unverified_evidence` |
| runner 未提供 | `blocked_missing_runner` |
| Obsidian root 未配置 | repo proposal 可預覽；Obsidian apply 為 `blocked_missing_vault` |
| Agent JSON/schema 錯誤 | `invalid_agent_output`，保存 bounded diagnostic |
| evidence 超預算 | `context_overflow` |
| target base hash 已變 | `stale_target`，重新 collect |
| symlink/traversal/未知 target | `permission_denied` |
| system map 未批准 | 保留 proposal，application 顯示 `awaiting_target_approval` |
| ADR decision 重複 | `duplicate_decision`，不建立新檔 |
| 部分 apply 失敗 | 停止後續 target，保存 application manifest 與 backups |

Documentation failure 不得改寫 Hermes pass，也不得顯示成產品 baseline drift；Agent Operations 應把它呈現為 post-acceptance documentation attention。

## 12. 與既有 Agent 的邊界

| Agent | 責任 | 與 Documentation Agent 的交界 |
|---|---|---|
| Context Agent | 規劃前最小上下文 | 不負責改後文件；Documentation 不重掃規劃 context |
| Implementation Agent | 單一批准 Task 實作 | 只提供 final report/diff evidence，不寫正式文件 |
| Review Agent | requirement/diff findings | Documentation 只消費 PASS report，不重做 code review |
| Hermes | runtime、SQLite、baseline、服務與正式 acceptance | Documentation 只消費 PASS evidence，不重跑或修改 Hermes |
| Codex | dispatch、授權、驗證 proposal、Git 整合 | 不用主 LLM 靜默代寫 Agent 被要求產生的正式回填 |

## 13. 測試與驗收

### Contract 與 Collector

- schema 嚴格欄位、未知欄位拒絕、canonical fingerprint。
- completed/PASS gates、缺 artifact、壞 JSON、oversize artifact。
- evidence redaction、Token budget、changed-path classification。
- 純 docs/test-only change 可 deterministic skip，不調用 runner。

### Agent Service

- valid runner proposal、invalid JSON、unknown target、timeout、non-zero exit。
- runner command 不落 artifact；相同 fingerprint cache hit。
- Agent 無 repo/vault/filesystem/network 工具能力。

### Validator 與 Controller

- managed Brief block idempotency。
- system map section hash、單 section boundary、stale target。
- ADR create-only、number allocation、duplicate decision。
- vault root/symlink/traversal/unknown directory 防護。
- atomic writes、backup、partial failure manifest、before/after hash。
- baseline `HKD 12,057,968` 與正式口徑文字保護。

### CLI 與整合

- `document` preview、`--apply-brief`、`--approve-target`、on-demand run。
- single JSON stdout、redacted stderr、統一 exit code。
- Agent Operations 可讀 documentation sidecar，但保持 read-only。
- Hermes 只驗證 documentation artifacts/permissions，不執行 Agent 或 apply。
- existing Context/Implementation/Review/workflow tests 不回歸。

### 正式驗收

1. Documentation Agent focused tests。
2. Agent workflow、Agent Operations、dispatch 與 Hermes regression tests。
3. Python compile。
4. Full pytest。
5. `scripts/system_manager.py acceptance`。
6. `scripts/hermes_post_change_check.py --skip-monitor --json`。
7. 正式 SQLite SHA-256 前後一致。
8. 2026-05 baseline 保持 `HKD 12,057,968`，正式口徑保持「不含掛賬核銷與TT退款轉團款」。
9. 在 temporary repo/vault fixture 完成一次 end-to-end preview + apply；不得用正式 SQLite 或未批准文件做破壞性測試。

## 14. 明確非目標

- 讓 Documentation Agent 自行瀏覽 repo 或 Obsidian。
- 讓 Agent 直接寫檔、Git stage/commit/merge/push。
- 自動覆寫既有 ADR、Incident 或 baseline 文件。
- 每個 typo、format-only 或測試補強都消耗 LLM Token。
- Streamlit 裡批准或套用文件。
- 新 FastAPI/Vue endpoint、queue、daemon、scheduler、vector database 或 long-term semantic memory。
- 修改正式 SQLite、baseline、正式口徑、營收規則、報表計算或 upload path。
- 取代 Review Agent、Hermes 或 Git Integration Agent。

## 15. 完成定義

- Codex 能在 verified functional change 後自動呼叫獨立 Documentation Agent，也能按 run ID 補跑。
- Agent 只消費 compact verified evidence，固定 JSON 輸出，runner 不可讀寫 repo/vault。
- 主 Codex 不在 Agent 缺失或失敗時靜默代寫正式回填。
- Brief、system map 與 ADR 有 deterministic target policy、preview、approval、fingerprint 與 idempotent apply。
- Obsidian path 不硬編入 tracked source，vault 外寫入與 symlink/path traversal 被拒絕。
- Brief 低風險回填可由 Codex 套用；system map 與 ADR 必須明確批准。
- Documentation artifacts 能被 Agent Operations 與 Hermes read-only 檢查，但不改變它們的責任。
- 全部正式驗收通過，Git diff 可追溯，正式 DB、baseline、正式口徑與產品行為不變。
