# NBS Governance Graph Phase C Task 3 Canonical Evidence Design

狀態：approved for implementation planning
日期：2026-07-28
風險：R1 standard engineering
範圍：為 Task Gate、Terra diagnosis 與 protected incident 建立可驗證、run-scoped 的 canonical evidence contract，使後續 Telemetry 能如實計數並讓 Governance Graph 安全投影狀態。

## 1. 目的與決策

Phase C Task 1／2 已完成 read-only telemetry 與 UI；三個沒有 canonical evidence 的 metric 必須維持 `unknown`。本 Task 只定義它們未來所需的 artifact、唯一 writer、reason preservation、lifecycle timestamps、retention 與安全讀取契約。

此設計的核心決策如下：

- 每個 artifact 均為單一 run 內、版本化、immutable-after-finalization 的 canonical evidence；不得由 UI、Telemetry、Graph projection 或人工分析 layer 補建。
- Task Gate、Terra diagnosis、protected incident 是三種不同 authority 的 evidence，不互相推導。尤其 `diagnosis_required` 不等於 protected incident，Terra diagnosis 亦不等於 Task Gate failure。
- exact count 僅以完整 schema、合法 writer identity、合法 lifecycle 與安全關聯成功驗證的 artifact 計算；任何缺檔、重複、矛盾、過期或不安全輸入均為該 metric／run 的 `unknown` 或 `invalid`，絕不補零。
- `governance-graph.json` 是衍生 projection，不是 truth store；它可投影已驗證 evidence 的安全摘要，不能產生、修正或取代 canonical artifact。

## 2. Scope 與明確排除

In scope：run-scoped artifact schema、writer ownership、reason code、lifecycle timestamps、retention/coverage、safe reader、Graph projection、TDD 與 acceptance。

明確排除：Graph query、跨版本 comparison、dependency／impact analysis、risk summary、approval、dispatch、retry routing、runner 啟動、repair、prune、delete、Git 操作、API、database、daemon、polling、mutable metrics store，以及任何 SQLite、baseline、revenue scope、business rules、upload、rollback 或 export schema 的變更。

## 3. Authority 與 writer ownership

| Artifact | Filename | 唯一 canonical writer | 可寫入時點 | 禁止 writer |
|---|---|---|---|---|
| Task Gate evidence | `task-gate.json` | 已獲批准的 workflow/orchestration Task Gate writer | gate 作出 terminal decision 時 | UI、Telemetry、Graph builder、Review、Terra、手動 JSON 編輯 |
| Terra diagnosis | `terra-diagnosis.json` | 已獲批准的 Terra diagnosis runner | Terra 完成一次 bounded diagnosis 並輸出 terminal result 時 | workflow orchestrator、UI、Telemetry、Graph builder、手動 JSON 編輯 |
| Protected incident | `protected-incident.json` | 已獲批准的 protected-incident recorder | 受保護事件被 canonical detection/handling path 終結時 | UI、Telemetry、Graph builder、Terra（除非其同時為獲批准 recorder）、手動 JSON 編輯 |

writer 必須只寫入自己的 allowlisted filename，且同一 run／artifact kind 只允許一份 final artifact。writer 不得改寫 `manifest.json`、`status.json`、既有 stage artifacts、Graph projection、SQLite、baseline、runtime retention 或 Git。若設計上需要重試，必須由新的、另行批准的 lifecycle revision contract 處理；本 v1 不允許 overwrite 或由後寫者覆蓋先前 truth。

每份 artifact 的 `writer` 必須為 code-owned writer registry 中的 bounded identifier（例如
`task_gate_writer`、`terra_diagnosis_runner`、`protected_incident_recorder`），並與 filename
kind 的 allowlist 完全一致。reader 不接受 artifact 自行宣告而未在 registry 中的 identity。
registry 必須固定 `artifactKind`、filename、writer identifier、writer implementation
entrypoint、writer version range 與允許的 run contract fingerprint；implementation plan 必須
逐一列出三個 registry entry 的實際 module/call site、approved contract binding、final-write
primitive 與 duplicate detection test。未知 writer、writer/kind mismatch、contract binding
不匹配或同名多份 evidence 均 fail closed。

## 4. 共用 canonical envelope v1

三類 artifact 都必須為 UTF-8 JSON object，符合下列 envelope；時間採 UTC ISO-8601（含 `Z`），禁止以檔案 mtime 推算。

```json
{
  "schemaVersion": "governance-canonical-evidence-v1",
  "artifactKind": "task_gate",
  "runId": "bounded-run-id",
  "writer": "task_gate_writer",
  "writerVersion": "bounded-version",
  "contractFingerprint": "sha256-hex",
  "status": "passed",
  "reasonCode": null,
  "lifecycle": {
    "createdAt": "2026-07-28T00:00:00Z",
    "startedAt": "2026-07-28T00:00:01Z",
    "decidedAt": "2026-07-28T00:00:02Z",
    "finalizedAt": "2026-07-28T00:00:02Z"
  },
  "evidenceFingerprint": "sha256-hex",
  "payload": {}
}
```

必填欄位不得有未知 top-level key；字串、list、reason code、diagnostic 與數值都採既有 bounded/hard-cap 原則。`runId` 必須與 containing run directory、`manifest.json` 完全一致。`contractFingerprint` 必須等於同一 run 已驗證 `approval.json` 的 `contractFingerprint`；approval 缺失、mismatch 或非合法 SHA-256 都是 `invalid`，不得只相信 artifact 自行宣告。`evidenceFingerprint` 是 writer 對 stable canonical content 的 SHA-256；canonical content 定義為移除 top-level `evidenceFingerprint` 後，以 UTF-8、`ensure_ascii=false`、`sort_keys=true`、`separators=(",", ":")` 的 JSON object serialization。reader 必須以完全相同規則重新計算並比較。fingerprint mismatch、bool masquerading as integer、負值、超 cap、invalid timestamp、非 regular file、symlink、traversal、非 object JSON 或 schema mismatch 都使該 artifact `invalid`，不可降格為無事件。

`status`、`reasonCode` 與 lifecycle 的共通規則：

- `status` 僅可使用該 artifact kind allowlist；`reasonCode` 必須為該 kind/status allowlist 的 bounded code，成功狀態必為 `null`。
- `createdAt <= startedAt <= decidedAt <= finalizedAt`。terminal artifact 必有 `decidedAt` 與 `finalizedAt`；不完整 lifecycle 是 `unknown`，順序矛盾是 `invalid`。
- `finalizedAt` 是唯一可供 retention ordering 與 Graph/Telemetry observation 使用的 terminal timestamp；cycle duration 僅在 `startedAt`、`decidedAt` 均合法時，按其差值計算。
- artifact 不承載 prompt、runner command、stdout/stderr、raw data、patch、secret、absolute path 或內部推理。

v1 的 bounded allowlist 固定如下；implementation plan 不得自行擴張而不更新 spec：

| Kind | Status | Allowed reason codes | Payload caps |
|---|---|---|---|
| `task_gate` | `passed`／`failed`／`blocked` | `passed→null`；`failed→gate_failed`／`missing_evidence`／`schema_violation`；`blocked→blocked_dependency`／`missing_evidence` | `requiredEvidenceKinds`／`missingEvidenceKinds` ∈ `{risk,spec_gate,plan_gate,implementation,targeted_verification,review,full_verification,hermes,documentation,git_integration}`；list ≤16；每項 ≤64；taskId ≤128 |
| `terra_diagnosis` | `completed`／`blocked`／`not_required` | `completed→protected_incident`／`diagnosis_failed`；`blocked→blocked_missing_evidence`／`runner_error`；`not_required→null` | `diagnosisKind` ∈ `{protected_incident,task_gate,workflow_failure,data_integrity}`；`outcome` ∈ `{diagnosed,not_reproducible,blocked,no_action}`；`findingCode` ∈ `{malformed_artifact,stale_artifact,gate_failed,protected_incident,dependency_blocked,unknown}`；各字串 ≤128；ref basename ≤128 |
| `protected_incident` | `detected`／`contained`／`closed`／`blocked` | `detected/contained/closed→policy_violation`／`data_integrity`／`security_boundary`／`protected_incident`；`blocked→blocked_missing_evidence`／`security_boundary` | `incidentCode` ∈ `{policy_violation,data_integrity,security_boundary,protected_incident,stale_artifact}`；`severity` ∈ `{low,medium,high,critical}`；`affectedScope` ∈ `{workflow_artifact,canonical_evidence,runtime,security_boundary,data_integrity}`；各字串 ≤128；布林 scalar only |

Unknown reason code、超 cap 或 kind/status 不匹配一律 `invalid`；不得轉為 `unknown` 或 `blocked`。

## 5. Kind-specific payload contract

### 5.1 Task Gate

`artifactKind` 固定為 `task_gate`；`status` 僅可為 `passed`、`failed`、`blocked`。`failed`／`blocked` 必須有 `reasonCode`；`passed` 必為 `null`。`payload` 僅可包含：

```json
{
  "taskId": "bounded-task-id",
  "decision": "passed",
  "requiredEvidenceKinds": ["implementation", "targeted_verification"],
  "missingEvidenceKinds": []
}
```

`decision` 必須等於 top-level `status`。`requiredEvidenceKinds` 與 `missingEvidenceKinds` 只允許 bounded allowlist values；後者必為前者子集。Task Gate 的 Telemetry aggregate 依 `failed`／`blocked` 分組，只有完整 terminal artifact 才納入；不存在 artifact、非 terminal lifecycle 或未支援 decision 都是 `unknown`。

### 5.2 Terra diagnosis

`artifactKind` 固定為 `terra_diagnosis`；`status` 僅可為 `completed`、`blocked`、`not_required`。`completed` 與 `blocked` 必有 `reasonCode`，`not_required` 必為 `null`。`payload` 僅可包含：

```json
{
  "diagnosisKind": "protected_incident" ,
  "outcome": "diagnosed",
  "incidentRef": "protected-incident.json",
  "findingCode": "bounded-finding-code"
}
```

`diagnosisKind`、`outcome` 與 `findingCode` 均須 allowlist validation；`incidentRef` 僅可為同 run 的 basename，不可為 path。`completed` 表示一份 Terra diagnosis 已由其 writer terminally produced，不表示修復、approval、Task Gate success 或 incident 已關閉。Terra diagnosis count 只計 `completed` 的 verified artifact；`blocked` 與缺少 canonical evidence 分別保留為 blocked／unknown coverage。

### 5.3 Protected incident

`artifactKind` 固定為 `protected_incident`；v1 是「一份 terminal observation」，不是可覆寫的 incident state machine。`status` 僅可為 `detected`、`contained`、`closed`、`blocked`，表示該份 observation 在 finalization 時的狀態；`reasonCode` 對所有 status 必填，且必須來自 protected incident allowlist。不得以後寫入覆蓋 `detected` 變成 `contained` 或 `closed`。若未來需要狀態轉換，必須另立 immutable revision／event-chain contract，不得在 v1 使用 overwrite。`payload` 僅可包含：

```json
{
  "incidentCode": "bounded-incident-code",
  "severity": "low",
  "affectedScope": "workflow_artifact",
  "terraDiagnosisRequired": true,
  "terraDiagnosisRef": "terra-diagnosis.json"
}
```

`severity`、`affectedScope` 與 `incidentCode` 均採 bounded allowlist。`terraDiagnosisRequired` 只表達這份 incident 的 writer 已記錄診斷需求；它不構成 Terra artifact 存在、完成、成功或任何 aggregate count 的證據。若 `terraDiagnosisRef` 存在，reader 僅驗證其 basename、同 run reference 與 kind；不得由 reference 自動改寫任一 artifact status。Telemetry 的 protected incident count 只計 verified `detected`、`contained`、`closed` 或 `blocked` evidence，並按 terminal status 分組；未保留足夠 status/reason 的資料仍為 `unknown`。

## 6. Read model 與 unknown/fail-closed contract

唯一 runtime reader 繼續是 `AgentOperationsService` 的既有安全邊界；後續 `GovernanceTelemetryService` 只消費其已驗證、bounded compact evidence，不能自行掃描任意檔案、建立 runtime directory、呼叫 writer 或讀取外部路徑。

每個 evidence kind 的 read result 必有 `status`：

| Read state | 意義與 Telemetry 行為 |
|---|---|
| `available` | 完整、fingerprint 驗證、terminal lifecycle 與 kind-specific schema 合法；可納入對應 metric。 |
| `unknown` | retained run 內 artifact 缺失、尚未 finalization 或合法 evidence 不足；增加 `unknownCount`，不得記為零。已被 retention 移除的整個 run 不在 v1 denominator。 |
| `invalid` | 安全、schema、fingerprint 或 timestamp violation；隔離該 run/kind、輸出 bounded diagnostic，不得納入成功或 failure count。 |
| `blocked` | canonical terminal status 明確 blocked；保留 status/reason 並視 metric 合約納入 blocked count，不得當作 success。 |

aggregate 必須同時輸出 `observedCount`、`unknownCount`，必要時 `invalidCount`、`blockedCount`、`missingCount`，並把 retained eligible run 數與 included run 數分開。單一 malformed run 不得隱藏健康 run；all-unknown input 則 metric 保持 `unknown`。不得由 Graph node、`diagnosis_required`、UI state、events、mtime 或其他 artifact inference 推導這三種 canonical evidence。

Read-state precedence 固定為：安全／schema／fingerprint violation → `invalid`；合法 terminal blocked artifact → `blocked`；缺檔、未 finalization 或 retained evidence gap → `unknown`；完整 terminal artifact → `available`。snapshot-level `partial` 可以彙總 unknown／invalid coverage，但不得把 invalid 當作 blocked，也不得把任何非 available state 當作 success。

## 7. Governance Graph projection

在三類 canonical artifact 均通過 validation 後，Graph builder 可建立只讀 projection node：`task_gate`、`terra_diagnosis`、`protected_incident`。每個 node 僅包含 `nodeId`、safe `status`、`reasonCode`、`finalizedAt`、artifact basename、SHA-256 與 bounded evidence status。

Graph builder 不得：建立缺少的 artifact、把 unknown 轉為 passed、由 Terra reference 推導 incident、由 incident 需求推導 diagnosis、改寫 lifecycle，或為了 projection freshness 寫回 canonical evidence。未驗證 artifact 必投影為 `unknown` 或 `invalid`（依 read result），不可作為 downstream allow/deny transition truth。Graph 和 Telemetry 都不得把 projection 回灌 canonical writer。

## 8. Retention 與可觀測性

artifact 與其 run 共同受既有 workflow retention policy 管理；Telemetry 只報告目前 retained、可安全讀取的 run。retention 不得逐檔刪除這三類 evidence，也不得由 Telemetry/Hermes/UI 執行 prune。

Telemetry v1 的 denominator 僅包含目前 retained 且通過 run eligibility validation 的 runs。若 retained run 缺少某份 artifact，才增加該 kind 的 `unknownCount`／`missingCount`；若整個 run 已被 retention 移除，v1 不把它放入分母，也不聲稱該 run 沒有 failure、diagnosis 或 incident。不得在本 Task 偷增 retention coverage ledger；若未來需要跨 retention 的歷史完整性，必須另立 immutable ledger spec。任何 retention policy 變更都必須保持 run-level atomicity，保留 manifest/status 與 canonical evidence 一致性，並另經批准。

## 9. Security 與 no-write invariants

- reader 沿用 project-root/run containment、regular-file、symlink/traversal rejection、hard cap、UTF-8 JSON object、schema validation、fingerprint comparison 與 absolute-path redaction。
- compact read model、Graph projection 和 UI 只可顯示 safe run ID、artifact basename、bounded status/reason/finding/incident code、lifecycle timestamps 與 bounded numeric coverage；不可顯示敏感 payload 或 raw artifact。
- 不掃描 Git、SQLite、網路、provider、billing、prompt 或 log；不新增 database/API/daemon/background writer。
- 除三個獲批准 canonical writers 的單檔 final write 外，所有 consumer 均 read-only；不得寫 `.nbs_agent_runtime`、workflow status、Graph projection、baseline、SQLite、cache 或 Git。
- 正式口徑「不含掛賬核銷與TT退款轉團款」與 2026-05 baseline `HKD 12,057,968` 不在本 Task 計算、讀取或修改範圍。

## 10. Implementation planning、TDD 與 acceptance

### 10.1 Implementation planning gate

本 spec 不直接批准 code changes；下一步必須另立 Task 3 Implementation Plan，並在 plan
中逐 Task 固定：三個 writer registry entry 與實際 module/call site、approved contract
binding、immutable final-write primitive、duplicate detection、既有
`AgentOperationsService` safe reader extension、`GovernanceTelemetryService` compact
consumer、Graph projection compatibility、allowed files、forbidden files、TDD red/green
commands、strict Review checkpoint 與每個 Task 的停點。writer 的 terminal decision 必須以
各自 envelope 的 lifecycle 為 truth，不得藉由改寫 `manifest.json` 或 `status.json` 對接。
Graph projection 的 derived write 只能由既有 approved Graph builder／WorkflowStore projection
writer 執行；canonical evidence writer 不得寫 Graph projection，consumer 也不得寫任何
artifact。

實作前先以既有 approved workflow fixture/writer 建立 RED tests；不得手工製造未驗證 canonical truth。最低測試需覆蓋：

1. 三個 artifact 的 valid schema、writer registry、approval `contractFingerprint` binding、run binding、fingerprint、immutable finalization、status/reason allowlist 與 lifecycle order。
2. 缺檔、retained-run evidence gap、duplicate、symlink、traversal、oversize、malformed JSON、unknown key、timestamp mismatch、contract fingerprint mismatch、evidence fingerprint mismatch、bool/negative/over-cap inputs 的 fail-closed isolation。
3. Task Gate `passed`／`failed`／`blocked`、Terra `completed`／`blocked`／`not_required`、protected incident terminal observations，且禁止三者間 inference。
4. Graph projection 僅投影 validated compact evidence；Telemetry 不自行讀檔或寫入，並如實輸出 observed/unknown/invalid/blocked coverage。
5. no-write regression：artifact bytes、Graph projection bytes、runtime directory contents、SQLite SHA-256、baseline、workflow status、Git/worktree 均在 consumers 執行前後不變；不得出現 absolute path、prompt、command、secret 或 raw payload。

正式 acceptance 順序：affected `py_compile` → focused pytest → findings-first strict Review → full `pytest -q` → `scripts/system_manager.py acceptance` → `scripts/hermes_post_change_check.py --skip-monitor --json`。完成前須證明 SQLite byte-identical、baseline matched、Graph/runtime/canonical artifacts 僅有已授權 writer 產生的 expected diff，且沒有 Git commit/merge；implementation 仍須在每個 approved Task 後停下等待下一個明確授權。

## 11. Spec self-review

- 本 spec 將 exact count 所需的 authority、artifact、reason、lifecycle 與 retention contract 明確化；缺證據仍固定為 `unknown`。
- Task Gate、Terra diagnosis、protected incident 各自有單一 writer 與不可互推的 schema，沒有 UI/Telemetry/Graph 偷補 path。
- 所有 consumer 維持 read-only；沒有 approval、dispatch、repair、routing、runtime、SQLite、baseline 或 Git write path。
- Graph query、version comparison、dependency／impact analysis 與 risk summary 已明確排除，需另立經批准的 spec／plan。
