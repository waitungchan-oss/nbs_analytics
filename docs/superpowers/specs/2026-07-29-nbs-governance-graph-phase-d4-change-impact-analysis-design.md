# NBS Governance Graph Phase D-4 Change Impact Analysis Design

狀態：draft for review  
日期：2026-07-29  
風險：R1 standard engineering（本 spec 不改變正式業務風險裁決）

## 1. Goal

建立 deterministic、read-only 的 `GovernanceGraphImpactService`，把已驗證的 D-3
risk findings 投影成 bounded change-impact summary。D-4 v1 只描述 finding 已明確指出的
治理影響面，不新增第三套 comparison／risk engine，也不推測不存在的 dependency、owner、
causal relationship 或 business impact。

D-4 的輸出供後續 query、版本比較、風險摘要與只讀 UI 消費；它不是 approval、dispatch、
blocking、rollback、repair、baseline gate 或任何正式 workflow control input。

## 2. Scope and non-goals

### In scope

- 驗證並消費 D-3 `governance-graph-risk-summary-v1`，保留其
  `comparisonFingerprint` 與 `riskSummaryFingerprint`。
- 由 D-3 findings 的 `sourceChange`、`ruleId`、`level` 與 `evidenceIdentities` 建立
  deterministic impact records。
- 以 code-owned exact mapping 將 finding 投影為治理影響面，不重算 risk、不做 edge traversal。
- 產生 protected governance surface、verification assurance、implementation governance、
  documentation-only、workflow observability blocked、coverage unknown 等 bounded impact。
- 產生可重現的 `governance-graph-change-impact-v1` read model 與 stdin-only CLI。

### Explicit non-goals

- 不重新讀取 snapshot path、`governance-graph.json`、raw runtime artifacts、canonical
  evidence、SQLite、Git 或網路。
- 不自行重新比較 snapshot、讀取 D-2 comparison path、執行 D-2 reader，或重新執行 D-3 risk rules。
- 不由 nodeId、filename、時間順序、artifact path 或文字內容推導 dependency 或 downstream。
- 不計算營收、baseline、ROI、business impact、owner、approval authority 或 remediation。
- 不修改 Graph snapshot、canonical artifacts、workflow status、risk classification、SQLite、
  baseline、cache、runtime 或 Git。
- 不建立新的 approval／dispatch／runner／control-plane path；不自動產生 workflow transition。
- 不在 D-4 v1 建立 Streamlit write path；UI 只可另行消費此 read model。

## 3. Boundary and architecture

```text
immutable D-3 risk-summary-v1 ── GovernanceGraphImpactService
                                             │
                                             ├─ change-impact-v1
                                             ├─ stdin-only CLI
                                             └─ future read-only UI/query
```

D-4 唯一 input authority 是已驗證的 D-3 risk summary。D-3 finding 已包含 bounded source
identity、change type、risk level 與 evidence identity；D-4 只做 exact mapping，不回頭讀取
D-2 comparison 或 raw evidence。`comparisonFingerprint` 只作 provenance binding，不可用來
重新載入或推導差異。

服務是 pure read model：相同的 input envelope、schema version、impact policy version 與
input bytes 必須產生 byte-for-byte 相同的 output。服務不得建立 runtime directory、寫 sidecar、
啟動服務、讀寫 SQLite 或呼叫任何 writer。

## 4. Input contract

D-4 接受一個 bounded envelope：

```json
{
  "schemaVersion": "governance-graph-impact-input-v1",
  "riskSummary": { "schemaVersion": "governance-graph-risk-summary-v1", "...": "..." }
}
```

Rules：

- `riskSummary` 必須通過 D-3 strict public-envelope parser；parser 必須驗證 exact keys、
  `riskRuleRegistryVersion`、finding identities、coverage、diagnostics 與
  `riskSummaryFingerprint`。
- D-4 保留 `comparisonFingerprint` 與 `riskSummaryFingerprint` 作 provenance binding，
  但不得接受或解析另一份 comparison payload。
- schema、fingerprint、allowlist、duplicate identity、bounded metadata 任一失敗，結果為
  `invalid`，不消費另一側未驗證資料。
- input 不接受 `runId`、snapshot path、任意 filesystem path、`--approve`、`--dispatch`、
  `--writer`、model command 或其他 control-plane 欄位。
- status precedence 沿用 D-2：`invalid > unavailable > blocked > unknown > available`。
  `invalid`／`unavailable` 只回傳 bounded diagnostics；`blocked` 保留已驗證 impact；`unknown`
  不得降級成「無影響」。

## 5. Impact semantics

### 5.1 Change seeds

每個 D-3 finding 都是 bounded impact seed。D-4 不增加或合併 D-2 change record。Seed identity
固定為 `sourceFindingId=findingId`；同一 finding 只保留一筆 impact record，排序固定，不依賴
輸入順序。

- 不使用 node／edge／evidence tuple 重新建立 dependency。

### 5.2 Exact impact mapping

mapping registry 固定為 `d4-impact-policy-v1`，只接受 exact `ruleId`：

| D-3 rule | impact category | impact state |
|---|---|---|
| `D3-PROTECTED-NODE`／`D3-PROTECTED-SURFACE` | `protected_governance_surface` | `observed` |
| `D3-VERIFICATION-REGRESSION` | `verification_assurance` | `observed` |
| `D3-BEHAVIORAL-CHANGE` | `implementation_governance` | `observed` |
| `D3-DOCUMENTATION-ONLY` | `documentation_only` | `observed` |
| `D3-BLOCKED-COMPARISON` | `workflow_observability_blocked` | `blocked` |
| `D3-UNKNOWN-COVERAGE` | `coverage_unknown` | `unknown` |

未知 `ruleId` 或不支援的 registry version 使 D-4 回傳 `invalid`／bounded diagnostic，不套用
近似 mapping。D-4 繼承 D-3 的 `R0`／`R1`／`R2`／`unknown` label，不新增數字型 impact score。

### 5.3 No-inference rules

- 不由 `nodeId` 名稱推導 dependency、owner、business impact 或 causal relationship。
- 不由 node order、generatedAt、filename、path 前綴或 UI state 推導 downstream。
- D-3 `overallRiskLevel=unknown`、comparison status `unknown` 或 evidence 缺失時，D-4 必須保留
  `unknown` coverage；不可輸出 R0 或「已完成影響分析」。

## 6. Output contract

Schema 固定為 `governance-graph-change-impact-v1`：

```json
{
  "schemaVersion": "governance-graph-change-impact-v1",
  "status": "available",
  "impactPolicyVersion": "d4-impact-policy-v1",
  "riskSummaryFingerprint": "<sha256>",
  "comparisonFingerprint": "<sha256>",
  "impactSummaryFingerprint": "<sha256>",
  "coverage": {
    "changedSeeds": 2,
    "mappedImpacts": 2,
    "protectedSignals": 0,
    "unknownImpacts": 0,
    "blockedImpacts": 0
  },
  "impacts": [
    {
      "impactId": "D3-VERIFICATION-REGRESSION:node:hermes:changed",
      "sourceFindingId": "D3-VERIFICATION-REGRESSION:node:hermes:changed",
      "riskLevel": "R1",
      "category": "verification_assurance",
      "impactState": "observed",
      "sourceChange": {"kind": "node", "identity": "hermes", "changeType": "changed"},
      "evidenceIdentities": [],
      "rationaleCode": "changed_seed"
    }
  ],
  "diagnostics": []
}
```

Output invariants：

- `impactId` 固定綁定 `sourceFindingId`，不使用 random UUID、mtime 或 iteration order。
- impacts 依 `(impactState priority, category, sourceFindingId)` 固定排序。
- `impactSummaryFingerprint` 覆蓋 schema、policy version、status、兩個 input fingerprints、
  coverage、sorted impacts 與 diagnostics；排除 fingerprint 欄位本身。
- 所有 identity、rationale、diagnostic 與 evidence ref 都必須 bounded；不得輸出 raw payload、
  absolute path、secret、prompt、command 或 stdout/stderr。
- `status` 與 `riskLevel` 分離；D-4 不得把 `blocked` 自動變成 approval denial。

## 7. Components and local agent boundaries

### 7.1 Production components

- `backend/agents/governance_graph_risk_models.py`：先補 D-3 public envelope 的 strict `from_dict()`、exact-key、registry-version 與 fingerprint round-trip parser；不得改變既有 D-3 output semantics。
- `backend/agents/governance_graph_impact_models.py`：immutable input、impact、coverage、diagnostic、summary models 與 fingerprints。
- `backend/agents/governance_graph_impact_service.py`：strict D-3 parser、exact rule mapping、bounded output；不得讀 raw paths。
- `scripts/governance_graph.py change-impact`：stdin-only risk-summary JSON input，呼叫同一 service；禁止 run-id/path/writer/control flags。
- `tests/test_governance_graph_impact_models.py`、`tests/test_governance_graph_impact_service.py`、`tests/test_governance_graph_cli.py`：contract、semantic、CLI、no-write tests。

### 7.2 Agent boundaries

- Context Agent：`.venv/bin/python scripts/context_agent.py --collect-only`，只輸出 compact evidence bundle。
- Review Agent：只讀 D-4 diff、approved spec/plan 與 verification evidence，findings-first；不得修改檔案或代替 Hermes。
- Hermes：只做 runtime、SQLite、baseline、service、Git 與 artifact read-only acceptance；Hermes PASS 不等於 D-4 semantic PASS。
- Implementation Agent（另行批准時）：一次一個 Task、只修改 allowlisted files，不得 commit、merge、push 或啟停服務。
- Codex：整合 findings、執行 full verification、Hermes、final acceptance 與 Git integration。

本 spec 不新增 risk writer、approval runner、Agent Operations write path、SQLite writer 或 Graph snapshot builder。

## 8. Error handling and fail-closed behavior

- malformed envelope、schema mismatch、fingerprint mismatch、duplicate identity、unsafe identity 或 unsupported rule registry → `invalid`。
- risk summary `unavailable` → `unavailable`，不得 fallback 到 latest snapshot。
- risk summary `blocked` → 保留已驗證 impact，並在 coverage／diagnostics 標示 blocked。
- risk summary `unknown` 或 evidence coverage 不足 → 保留 bounded impacts，並明確輸出 `unknown` coverage 與 diagnostic。
- 任何錯誤都不得寫入 runtime、canonical artifacts、Graph snapshot、SQLite、baseline 或 Git。

## 9. Testing and acceptance

Required tests：

- input envelope exact keys、D-3 fingerprint binding、raw/absolute path rejection、duplicate identity rejection。
- complete risk summary with no findings → deterministic empty impacts；不得宣稱 business-level no impact。
- one-to-one exact mapping for every D-3 rule, including protected／blocked／unknown semantics。
- D-3 parser tamper／extra-key／fingerprint rejection and deterministic ordering.
- R2/protected D-3 findings propagate as protected signals only。
- invalid／unavailable／blocked／unknown status precedence and no-fallback behavior。
- repeated same input byte-for-byte output；service／CLI 前後 no-write tree equality。
- CLI stdin-only exact envelope and rejection of `--run-id`, `--path`, `--approve`, `--dispatch`, `--writer` flags。

Acceptance sequence：

```bash
.venv/bin/python -m py_compile \
  backend/agents/governance_graph_risk_models.py \
  backend/agents/governance_graph_impact_models.py \
  backend/agents/governance_graph_impact_service.py \
  scripts/governance_graph.py
.venv/bin/python -m pytest \
  tests/test_governance_graph_impact_models.py \
  tests/test_governance_graph_impact_service.py \
  tests/test_governance_graph_cli.py -q
.venv/bin/python -m pytest -q
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/hermes_post_change_check.py
```

Completion requires Task-level TDD, strict Review PASS, full pytest PASS, system acceptance PASS,
Hermes PASS, clean worktree, and proof that SQLite SHA-256, frozen baseline `HKD 12,057,968` and
formal scope 「不含掛賬核銷與TT退款轉團款」 are unchanged. D-4 must not auto-repair degraded runtime
state during acceptance.

## 10. Future compatibility

Future versions may add explicit owner/dependency catalogs, impact confidence, version comparison UI,
risk trend or human-readable explanation only through a new approved contract. D-4 v1 must remain a
bounded observation layer and cannot become an approval or business decision engine.
