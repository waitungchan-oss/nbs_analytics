# NBS Governance Graph Phase C Telemetry Design

狀態：approved for implementation planning
日期：2026-07-28
風險：R1 standard engineering
範圍：建立 Governance Graph 的跨 run、read-only telemetry read model，並在既有 Streamlit `Agent Operations` tab 顯示治理運作指標。

## 1. 目的

Phase B 已讓使用者查看單一 run 的 Governance Graph、lineage、freshness、blocker 與 evidence。Phase C 延伸為跨 run 的治理觀測，回答：

- 哪些 stage／gate 最常失敗或阻塞？
- 各 stage／gate 的 cycle time 是否可由 evidence 證實？
- Luna repair、stale evidence、protected incident 與 token usage 的實際頻率如何？
- 指標的 coverage 是否足以支持結論？

Telemetry 是衍生 read model，不是新的 workflow truth、approval、dispatch 或 routing input。

## 2. 已確認的產品選擇

- 使用現有 Streamlit `Agent Operations` tab，在 Governance Graph section 後增加 `Governance Telemetry` section。
- 新增獨立 `GovernanceTelemetryService`，只消費已驗證的 canonical workflow artifacts 與 `governance-graph.json` projection。
- 初版不新增 FastAPI endpoint、Vue page、獨立 application、database、daemon、polling 或 mutable metrics store。
- Telemetry snapshot 於 read time 計算，僅作 session-scoped display input；不得 persist、cache-as-authority 或反向寫入 runtime。
- 所有 aggregate 必須帶 coverage／unknown 資訊；evidence 不足時回報 `unknown`，不可補零、估算或把 retention 缺口當作沒有事件。

## 3. Scope 與 non-goals

### 3.1 In scope

- 版本化 `governance-telemetry-snapshot-v1` read model。
- 跨 run aggregate：cycle time、gate failures、agent activity、evidence health、protected incident coverage、token usage。
- 每個 run 的 bounded drill-down：run ID、brief filename、時間、safe status／reason code 與 bounded numeric values。
- `available`、`partial`、`unavailable`、`invalid`、`unknown`、`blocked` 的明確語意。
- 現有 `Agent Operations` 內的文字／表格展示與 coverage caveat。
- service、Agent Operations integration、renderer、module-boundary 與 no-write regression tests。

### 3.2 Non-goals

- approval、dispatch、retry、repair、prune、delete、runner 啟動或 Git 操作。
- 自動把 failure routing 至 Luna、Terra、使用者或其他 Agent。
- 修改 SQLite、baseline、revenue scope、business rules、upload、rollback、export schema 或 canonical artifacts。
- 新增 Graph truth store、mutable metrics database 或 background telemetry writer。
- 估算 Codex Plus／subscription quota、provider billing 或缺失 token usage。
- 由 UI 或 analysis layer 補建 Task Gate、Terra diagnosis、protected incident 或 lifecycle evidence。

## 4. Canonical evidence 與 authority boundary

Telemetry evidence 優先順序如下：

1. 同一 run 的 `manifest.json`、`status.json`、`events.jsonl`。
2. 已驗證的 stage artifacts：`risk-classification.json`、`design-spec-gate.json`、`plan-gate.json`、`implementation.json`、`targeted-verification.json`、`review.json`、`full-verification.json`、`hermes.json`、documentation／Git integration artifacts。
3. 通過 `GovernanceGraphSnapshot.from_dict()` validation 的 `governance-graph.json`。
4. `.nbs_agent_runtime/telemetry/*.jsonl` 只作 supplementary evidence；缺少 run ID、schema、fingerprint 或安全關聯時不得納入正式 aggregate。

Agent Operations service 是唯一 runtime artifact reader。Telemetry service 不得自行掃描任意檔名、呼叫 Graph builder、執行 Graph CLI 或建立 runtime directory。Telemetry 不改變 canonical artifact 的 authority，也不把 aggregate result 當作 workflow transition evidence。

建議 read model：

```json
{
  "schemaVersion": "governance-telemetry-snapshot-v1",
  "generatedAt": "ISO-8601",
  "sourceGeneratedAt": {"earliest": "ISO-8601", "latest": "ISO-8601"},
  "coverage": {
    "eligibleRunCount": 0,
    "includedRunCount": 0,
    "unknownRunCount": 0,
    "diagnosticCount": 0
  },
  "cycleTimes": {},
  "gateFailures": {},
  "agentActivity": {},
  "evidenceHealth": {},
  "protectedIncidents": {},
  "tokenUsage": null,
  "runs": [],
  "diagnostics": []
}
```

每個 aggregate group 應包含 `observedCount`、`unknownCount`，必要時包含 `missingCount`；不得只輸出一個無 coverage 的平均值或百分比。

## 5. Metric contract

| Metric | 可接受 evidence | 初版語意 |
|---|---|---|
| Stage cycle time | 合法 stage `durationMs`；否則同一 stage 至少兩個合法 `events.jsonl` timestamps | 只有明確 start／end 才計入；可輸出 count、total、average、p50。不得由 artifact mtime 推算。 |
| Gate cycle time | 合法 gate lifecycle timestamps | 目前 gate artifacts 沒有一致的 duration contract，初版為 `unknown`，不得猜測。 |
| Spec Gate failures | `design-spec-gate.json` 或 validated Graph `spec_gate` failure／blocked evidence | 依 bounded reason code 分組為 `failed`／`blocked`。 |
| Plan Gate failures | `plan-gate.json` 或 validated Graph `plan_gate` evidence | 同上。 |
| Task Gate failures | 專用 canonical Task Gate artifact | 目前 repository 沒有此 artifact，固定為 `unknown`。 |
| Luna repair count | `implementation.json.repairLoopsUsed` 合法 non-negative integer | 彙總受限 implementation repair loop；不可描述為任意人工修復。 |
| Terra diagnosis count | 專用 Terra diagnosis canonical artifact 或安全事件種類 | 目前沒有 Terra report／writer，固定為 `unknown`。 |
| Stale evidence frequency | validated Graph freshness `stale` 或 bounded stale diagnostic／blocker | 只計有效 Graph projection；缺 Graph 不算 stale，應計入 unknown coverage。 |
| Protected incident count | validated canonical protected incident status／reason | 若 Graph／canonical mapping 未保留足夠 reason，固定為 `unknown`，不可由 `diagnosis_required` 推導。 |
| Token usage | supplied 且合法的 `inputTokens`／`outputTokens`／`totalTokens` | 彙總實際 supplied usage，附 `runsWithUsage`／`runsWithoutUsage`；缺失為 `null`。 |

所有 numeric values 必須驗證為 non-negative integer，拒絕 bool、負數、超出 hard cap 或不符合 schema 的值。

## 6. Read model states 與 freshness

- `available`：至少一個 metric 有 verified evidence。
- `partial`：部分 metrics 可用，另有缺失或不支援的 metrics。
- `unavailable`：沒有可安全讀取的 eligible run。
- `invalid`：telemetry input 違反 schema／path safety；提供 bounded diagnostic。
- `unknown`：特定 metric 沒有必要的 canonical evidence。
- `blocked`：相關 Graph／stage evidence 明確 blocked、stale 或 invalid；不可統計為成功。

Telemetry snapshot 不決定 Graph freshness，只報告 evidence availability。Snapshot 應帶 `sourceGeneratedAt` range 與 `latestRunUpdatedAt`；舊 Graph projection 不得被描述為 current workflow state。單一 malformed run 只影響該 run／metric coverage，不得使其他健康 run 消失。

## 7. Data flow 與 UI

```text
safe runtime run directories
  ├─ manifest/status/events + canonical stage artifacts
  ├─ validated governance-graph.json
  └─ bounded safe reader
          ↓
GovernanceTelemetryService (pure aggregation)
          ↓
governance-telemetry-snapshot-v1
          ↓
existing Agent Operations → Governance Telemetry section
```

UI 顯示順序建議：

1. coverage、eligible／included／unknown runs、stale evidence。
2. stage cycle time 與 Spec／Plan／Task gate failure 表格。
3. Luna repair、Terra diagnosis、protected incident coverage、token usage。
4. evidence health 與 bounded per-run drill-down。

使用現有 manual `Refresh`；不新增 action button、runner control、approval 或 workflow mutation。資料不足時顯示 coverage caveat，不顯示誤導性的空白 KPI 卡。

## 8. Privacy、security 與 no-write invariants

- 沿用 project-root containment、symlink／traversal、regular-file、hard-cap、JSON object、schema validation 與 absolute-path redaction。
- 不呈現 runner command、完整 prompt、內部推理、secret、原始資料列、完整 log、完整 patch 或 Git remote credential。
- drill-down 只保留安全 `runId`、brief filename、時間、bounded status／reason code 與 aggregate numeric values。
- 不掃描 Git、SQLite、網路或外部 provider；Telemetry 不成為 billing audit。
- 不呼叫 `build()`／`persist()`、不寫 `.nbs_agent_runtime`、不修改 workflow status，不清除 dashboard／AI／upload caches。
- 正式口徑「不含掛賬核銷與TT退款轉團款」及 2026-05 baseline `HKD 12,057,968` 不由 Telemetry 重算或修改。

## 9. Testing 與 acceptance

最少測試範圍：

1. `GovernanceTelemetryService`：無 runtime、缺 Graph、invalid／symlink／oversize、path leakage、valid duration 優先、events fallback、missing timestamps、gate failure、repair loops、supplied／missing token usage、stale count、unknown metrics、多 run isolation。
2. `AgentOperationsService` integration：read-only、無 rebuild／persist／workflow mutation、snapshot 不洩漏敏感值或 absolute path。
3. Renderer：`available`、`partial`、`unavailable`、`invalid`、metric-level `unknown`、token 缺失顯示「未提供」、無 write／runner／workflow controls。
4. Module boundaries：Telemetry 不得直接讀取 UI 外部 path，不得新增 API 或 database writer。

正式 acceptance 順序：affected `py_compile` → focused pytest → full `pytest -q` → `scripts/system_manager.py acceptance` → `scripts/hermes_post_change_check.py`；並確認 SQLite SHA-256 byte-identical、baseline matched、Git/worktree evidence 未被 Telemetry 寫入。

## 10. Suggested implementation decomposition

### Task 1 — Telemetry read-model contract 與 safe aggregation

新增 schema／service 與 TDD；只計算目前有 canonical evidence 的 stage cycle time、Spec／Plan Gate、Luna repair、stale evidence、supplied token usage。Task Gate、Terra diagnosis、protected incident 明確輸出 unknown。

### Task 2 — Agent Operations rendering

接入既有 read model，增加 aggregate、coverage、unknown caveat 與 bounded drill-down；不改 Graph builder、workflow orchestrator 或 persistence。

### Task 3 — Canonical evidence gaps（另立 spec／plan）

若未來需要 Task Gate、Terra diagnosis、protected incident 的 exact count，另行設計並批准 canonical artifact、writer、reason preservation 與 lifecycle timestamp contract。不得由 Phase C UI 或 aggregate layer 偷補。

## 11. Deferred decisions

- 是否需要跨 session 的歷史 retention；目前只支援現有 retained runs，coverage 必須如實呈現。
- 是否需要獨立 API／export；目前不在 Phase C scope。
- p50／median 的最低樣本量與 outlier policy；需在 implementation plan 前固定。
- telemetry read model 是否完全由 `AgentOperationsService` 組合，或抽出共用 safe reader；需以最小 diff 決定，不得重複 artifact parser。

## 12. Spec self-review

- Placeholder scan：未發現 placeholder marker 或未定義的 required implementation marker。
- Scope check：Phase C 僅包含 read-only aggregation 與既有 Agent Operations rendering；canonical evidence schema 擴展被拆至另案。
- Authority check：所有 metrics 均要求可驗證 evidence；缺資料明確為 `unknown`，不補零、不推測。
- Boundary check：沒有 UI action、approval、dispatch、runtime／SQLite／Git write path，也沒有 baseline／revenue rule 變更。
- Consistency check：`GovernanceTelemetryService`、`governance-telemetry-snapshot-v1`、coverage states、metric definitions 與 suggested Tasks 互相一致。
