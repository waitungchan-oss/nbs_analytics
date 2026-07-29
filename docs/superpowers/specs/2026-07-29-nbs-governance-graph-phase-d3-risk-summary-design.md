# NBS Governance Graph Phase D-3 Risk Analysis / Risk Summary Design

狀態：draft for review  
日期：2026-07-29  
風險：R1 standard engineering（本 spec 不改變正式業務風險裁決）

## 1. Goal

建立一個 deterministic、read-only 的 `GovernanceGraphRiskService`，只消費已驗證的 D-2
`governance-graph-comparison-v1` result，產生 bounded 的 risk summary，供後續只讀 UI、查詢與
變更影響分析消費。

D-3 的輸出是觀測與解釋層，不是 approval、dispatch、blocking、rollback、repair 或任何正式
風險裁決入口。它只能描述「比較結果顯示了哪些可追溯風險訊號」，不能宣稱不存在資料的風險為零。

## 2. Scope and non-goals

### In scope

- 解析 D-2 comparison result 的 schema、fingerprint、status、summary 與 bounded change records。
- 以固定、版本化、可測試的 rule registry 產生 risk findings、risk level、unknown／blocked coverage。
- 保留每個 finding 的 source change identity、evidence reference identity 與 deterministic rationale。
- 產生可重現的 `governance-graph-risk-summary-v1` read model 與 comparison fingerprint binding。
- 讓 CLI、未來 Streamlit view 與 D-4 只消費同一個 risk service output。

### Explicit non-goals

- 不重新讀取 `governance-graph.json`、raw runtime artifacts、canonical evidence、SQLite、Git 或網路。
- 不自行比較 snapshot；D-2 comparison result 是唯一 input authority。
- 不推測不存在的 edge、lineage、dependency、approval、causal relationship、owner 或 business impact。
- 不修改 `risk-classification.json`、workflow `status.json`、Graph snapshot、SQLite、baseline、cache 或 Git。
- 不執行 approval、dispatch、runner、repair、retry、prune、delete、rollback 或 policy transition。
- 不計算營收、重算 2026-05 baseline，亦不改變正式收入口徑。
- 不產生自然語言建議、管理層決策或自動風險接受／拒絕結果。

## 3. Boundary and architecture

```text
governance-graph-comparison-v1
              │
              ▼
GovernanceGraphRiskService (pure read model)
              │
              ├── governance-graph-risk-summary-v1
              ├── read-only CLI / future Streamlit tab
              └── D-4 change-impact consumer
```

D-3 只能接受一個完整的 D-2 comparison result object。服務不接受 `left_run_id`、`right_run_id`、
任意 path、snapshot directory 或 raw artifact payload；這避免 risk layer 形成第二個 snapshot
reader 或繞過 D-2 的 validation boundary。

所有 rule evaluation 必須是 pure function：相同 `comparisonFingerprint`、schema version、rule
registry version 與 input bytes 必須產生相同 output bytes。服務不得建立 runtime directory，亦不得
寫入任何 sidecar、cache、SQLite、Graph projection 或 canonical artifact。

## 4. Input contract

### 4.1 Required input

輸入必須符合 D-2 `governance-graph-comparison-v1`，且只允許公開欄位：

- `schemaVersion`
- `status`
- `leftSnapshot`
- `rightSnapshot`
- `comparisonFingerprint`
- `summary`
- `nodeChanges`
- `edgeChanges`
- `evidenceChanges`
- `diagnostics`

Risk service 必須重新驗證 D-2 result 的 schema、allowlist、SHA-256、bounded metadata、change
identity、summary consistency 與 fingerprint；不可只相信 caller 已驗證。Fingerprint validation
必須 normative reuse D-2 `GovernanceGraphComparisonResult.comparison_fingerprint` 的
`canonical_sha256` algorithm：UTF-8、`ensure_ascii=false`、`sort_keys=true`、
`separators=(",", ":")`，涵蓋 D-2 normalized references、兩側 snapshot identity、status、summary、
sorted changes 與 diagnostics，且排除任何既有 fingerprint 欄位。若無法套用同一 D-2 schema／algorithm
version，D-3 必須回傳 `invalid`，不得自行 canonicalize。

### 4.2 Input status handling

沿用 D-2 precedence：`invalid > unavailable > blocked > unknown > available`。

- `invalid`／`unavailable`：輸出相同 status、零 risk findings、只保留 bounded diagnostics；不得從另一側猜測風險。這兩種狀態的 registry entries 是 diagnostics-only，不是 findings。
- `blocked`：可以輸出由已驗證 bounded changes 產生的 findings，但 summary status 保持 `blocked`。
- `unknown`：可以輸出明確標示 `unknown` 的 coverage finding；不可轉為 R0 或「無風險」。
- `available`：執行完整 rule registry。

### 4.3 D-2 compatibility bridge

D-2 v1 的 public envelope 必須在 D-3 implementation 前補上兩個 bounded、backward-compatible
欄位：`leftReference` 與 `rightReference`，內容固定為 D-2 input reference（`runId` 與 optional
`snapshotFingerprint`）。D-3 不得從 `leftSnapshot.graphFingerprint` 反推 optional input fingerprint，
因為「caller 未提供 fingerprint」與「caller 明確提供 fingerprint」必須保持可區分。

這個 bridge 不改變 D-2 comparison semantics、status、summary 或 existing fields；D-2 schema
version 仍維持 `governance-graph-comparison-v1`，但 implementation plan 必須先補上 model／CLI
serialization、exact-envelope tests 與 D-2 regression evidence。D-3 Risk Service 只接受包含兩個
references 的 bridge-complete result；缺少任一 reference 為 `invalid`，不得執行 rule evaluation。

## 5. Risk vocabulary and rule registry

### 5.1 Risk levels

D-3 使用既有 Governance Graph vocabulary，但只作 observation label：

- `R0`：comparison 可驗證且只包含 documentation-only／non-behavioral bounded changes，沒有
  blocked、invalid、protected 或 verification integrity signal。
- `R1`：一般 code、agent contract、Graph projection、Review、Verification 或 Hermes 變更，或
  存在 blocked／stale／missing verification signal，但未觸及 protected surface。
- `R2`：已驗證的 protected signal，例如 `protected_incident`、`protected_incident` diagnostic、
  或 D-2 bounded node／evidence 明確標示 `baseline`、`sqlite`、`revenue`、`rollback`、
  `business_rules`、`export_schema` surface。D-3 只能報告 R2，不能批准或處理它。
- `unknown`：D-2 status、evidence coverage 或 change identity 不足以安全分類。

未知 surface、未知 reason code、缺少 required bounded identity 或 schema 不一致，一律 `unknown`
或 `invalid`，不得猜成 R0／R1。

### 5.2 Deterministic rule registry v1

Rule registry 必須是 code-owned、immutable、versioned，固定 `riskRuleRegistryVersion`，每條規則
具有 `ruleId`、`priority`、`level`、`category` 與 bounded description。v1 最小規則如下：

| Rule ID | Trigger（只看 D-2 bounded fields） | Level | Finding category |
|---|---|---|---|
| `D3-INVALID-COMPARISON` | comparison status=`invalid` | diagnostics-only（不產生 finding） | input_integrity |
| `D3-UNAVAILABLE-COMPARISON` | comparison status=`unavailable` | diagnostics-only（不產生 finding） | evidence_availability |
| `D3-BLOCKED-COMPARISON` | comparison status=`blocked` | R1 | workflow_blocked |
| `D3-PROTECTED-NODE` | node change identity `protected_incident` 或 bounded diagnostic code=`protected_incident` | R2 | protected_surface |
| `D3-PROTECTED-SURFACE` | bounded reason／surface exact-match `baseline`, `sqlite`, `revenue`, `rollback`, `business_rules`, `export_schema` | R2 | protected_surface |
| `D3-VERIFICATION-REGRESSION` | `review`, `targeted_verification`, `full_verification` 或 `hermes` node/evidence 出現 removed、changed、blocked 或 invalid signal | R1 | verification_integrity |
| `D3-BEHAVIORAL-CHANGE` | `implementation`、API/Graph code node 的 added／removed／changed signal | R1 | behavioral_change |
| `D3-DOCUMENTATION-ONLY` | 所有 changes 僅限 `D3_DOCUMENTATION_NODE_ALLOWLIST_V1` 的 explicit D-2 nodeId，且無其他較高優先 rule | R0 | documentation_only |
| `D3-UNKNOWN-COVERAGE` | unknown evidence、缺少可判定 change identity 或 comparison metadata 不足 | unknown | coverage_gap |

Rules 以 `priority` 由高至低 deterministic 排序；同一 rule、同一 source identity 只產生一筆
finding。`D3-INVALID-COMPARISON` 與 `D3-UNAVAILABLE-COMPARISON` 只產生 diagnostics，不得進入
`findings`、`classifiedChanges` 或 `overallRiskLevel`。Rule registry 不得讀取檔名以外的 raw content，
也不得從 node 順序或時間先後推導因果。

`D3_DOCUMENTATION_NODE_ALLOWLIST_V1` 是 code-owned、immutable、versioned 的 exact identity set，
v1 僅包含 D-2 schema 已明確提供的 nodeId `documentation`。只有 node change 的 `nodeId` exact-match
此 set，且所有 non-node changes 為空、所有 evidence status 為 available，才可觸發
`D3-DOCUMENTATION-ONLY`；不得以 filename、文字內容、node order 或未驗證 metadata 擴張 allowlist。

## 6. Output contract

Schema 固定為 `governance-graph-risk-summary-v1`：

```json
{
  "schemaVersion": "governance-graph-risk-summary-v1",
  "status": "available",
  "riskRuleRegistryVersion": "d3-risk-rules-v1",
  "comparisonFingerprint": "<sha256>",
  "riskSummaryFingerprint": "<sha256>",
  "overallRiskLevel": "R1",
  "findings": [
    {
      "findingId": "D3-VERIFICATION-REGRESSION:hermes:changed",
      "ruleId": "D3-VERIFICATION-REGRESSION",
      "level": "R1",
      "category": "verification_integrity",
      "confidence": "high",
      "sourceChange": {
        "kind": "node",
        "identity": "hermes",
        "changeType": "changed"
      },
      "evidenceIdentities": [],
      "rationaleCode": "verification_node_changed",
      "summary": "Hermes bounded status or fingerprint changed between snapshots."
    }
  ],
  "coverage": {
    "observedChanges": 1,
    "classifiedChanges": 1,
    "unknownChanges": 0,
    "invalidChanges": 0,
    "blockedChanges": 0
  },
  "diagnostics": []
}
```

### 6.1 Output invariants

- 所有 findings、source identities、rationale codes 與 summaries bounded；不得輸出 raw payload、
  prompt、command、stdout/stderr、absolute path、secret 或未驗證 metadata。
- `findingId` 是由 `ruleId`、source kind、identity、changeType 的 canonical tuple 產生；不可使用
  random UUID、mtime 或 iteration order。
- `riskSummaryFingerprint` 覆蓋 normalized input `comparisonFingerprint`、rule registry version、
  status、overall level、sorted findings、coverage 與 diagnostics。
- `overallRiskLevel` 使用 precedence `R2 > R1 > R0`；若沒有可安全分類的 finding 則為 `unknown`，
  不得默認為 R0。
- findings 固定依 `(level priority, rule priority, findingId)` 排序；同 fingerprint result 必須
  byte-for-byte reproducible。
- status 與 risk level 分離：`status=blocked` 可以有 `overallRiskLevel=R1/R2`；
  `status=unknown` 不得宣稱低風險。

## 7. No-inference and fail-closed semantics

- D-3 不由 `nodeId` 名稱猜測 owner、dependency、business impact 或 approval authority。
- D-3 不由 `generatedAt`、snapshot 順序、filename、缺檔或 UI state 推導事件因果。
- 沒有 edge records 時，risk summary 不新增 edge-related finding；只能反映 D-2 已輸出的 node／evidence
  signals。
- missing／unknown evidence 保留 `unknownChanges`；不改寫成成功、零風險或已修復。
- invalid comparison 只輸出 bounded input-integrity diagnostic；不得消費另一側 diff。
- 任何 risk summary 不能成為 workflow transition、approval、dispatch、rollback 或 baseline gate 的
  input。

## 8. Components and local agent boundaries

### 8.1 Production components

- `backend/agents/governance_graph_risk_models.py`：immutable input/output models、schema、fingerprint。
- `backend/agents/governance_graph_risk_service.py`：pure rule evaluation；只接受 D-2 result。
- `scripts/governance_graph.py risk-summary`：只讀 CLI，從 stdin 接收一個 bridge-complete D-2 JSON
  object 並輸出 risk summary；不能接受 run IDs、path、file path 或 writer flags。呼叫方式固定為：
  `cat comparison.json | .venv/bin/python scripts/governance_graph.py risk-summary`。
- 未來 Streamlit Risk Summary tab：只呼叫同一 risk service；不得自行套規則。

### 8.2 Local agent boundaries

- Context Agent：只執行 `scripts/context_agent.py --collect-only`，只產生 compact context；不得修改
  spec、source、runtime、SQLite、baseline 或 Git。
- Review Agent：只讀取 D-3 diff、approved spec/plan、verification evidence；findings-first；不得
  修改檔案、代替 Hermes 或作正式 risk decision。
- Hermes：只做 read-only artifact、runtime、baseline、SQLite integrity 與 acceptance；Hermes PASS
  不等於 Risk Summary correctness PASS。
- Implementation Agent（若另行批准）：一次只做一個 Task、只寫 allowed files、不得 commit／merge／
  push／啟停服務；不得新增 risk writer 或 control-plane path。
- Codex：負責整合 approved findings、final verification 與停點；不得把 Risk Summary 回寫 canonical
  artifact。

## 9. Testing and acceptance

### 9.1 Required tests

- D-2 input schema、fingerprint、allowlist、duplicate source identity、raw/absolute path rejection。
- zero-diff comparison → deterministic no-finding／R0 only when evidence is complete。
- blocked／unknown／invalid／unavailable status precedence 與 no-fallback behavior。
- protected incident／protected surface → R2 finding；不得呼叫 writer 或 transition。
- verification node changes → R1 finding；documentation-only bounded changes → R0。
- same input repeated runs、reversed left/right fingerprints、finding ordering、coverage counts。
- no-inference：無 edges／無 evidence 不產生猜測 findings。
- no-write tree equality：runtime、Graph snapshot、canonical artifacts、SQLite、baseline、Git worktree
  在 service／CLI 執行前後保持不變。
- CLI exact envelope、invalid exit code、禁止 `--run-id`／`--path`／`--approve`／`--dispatch` 等
  control flags。

### 9.2 Acceptance sequence

```bash
.venv/bin/python -m py_compile \
  backend/agents/governance_graph_risk_models.py \
  backend/agents/governance_graph_risk_service.py \
  scripts/governance_graph.py
.venv/bin/python -m pytest \
  tests/test_governance_graph_risk_models.py \
  tests/test_governance_graph_risk_service.py \
  tests/test_governance_graph_cli.py -q
.venv/bin/python -m pytest -q
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/hermes_post_change_check.py
```

Completion requires: approved D-3 Design Spec and Implementation Plan, per-Task TDD, strict Review
PASS, full pytest PASS, system acceptance PASS, Hermes PASS, clean worktree, and proof that
`nbs_marketing_data.db` SHA-256、baseline `HKD 12,057,968` 與正式口徑「不含掛賬核銷與TT退款轉團款」
未改變。D-3 不得在 acceptance 中自動修正 degraded monitor 或任何正式 data state。

## 10. Future compatibility

D-4 change impact analysis 只能消費 `governance-graph-risk-summary-v1` 與其來源
`comparisonFingerprint`；不得重新讀 raw artifacts 或重做 risk rules。若要加入 owner、dependency、
business impact、risk trend、natural-language explanation 或 remediation suggestion，必須另立
approved contract，不得在 D-3 v1 隱式擴張。

## 11. Self-review checklist

- D-3 只消費 D-2 comparison result，沒有第二個 snapshot reader 或 raw artifact path。
- Risk level 是 deterministic observation label，不是 approval 或 automatic gate。
- `R2` 只由明確 protected bounded signal 觸發；unknown 不會被壓成 R0。
- 所有 output bounded、fingerprinted、可重現，並保留 source change identity。
- Context／Review／Hermes／Implementation Agent 邊界與 D-2 一致。
- no-write、SQLite、baseline、revenue scope、business rules、rollback、export schema 與 Git
  boundaries 均明確保留。
