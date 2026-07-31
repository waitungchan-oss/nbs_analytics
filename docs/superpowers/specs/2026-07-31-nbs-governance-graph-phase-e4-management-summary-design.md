# NBS Governance Graph Phase E-4 Management Summary Design

狀態：approved；spec review 補強完成
日期：2026-07-31  
風險：R1 standard engineering（read-only management summary projection；不改正式資料）

## 1. Goal

在既有 Agent Operations → Governance Graph section 內，建立一個 bounded、deterministic、read-only
的 Management Summary projection，讓管理層能快速回答：

- 目前是否有 protected、blocked 或 unknown governance signal？
- 哪些已驗證的 risk／impact／evidence coverage 需要下鑽？
- owner／dependency catalog 是否完整到足以支援治理觀察？
- 多個明確提供的 immutable summary snapshots 之間，風險與 coverage 是否有可驗證變化？

E-4 只整合既有 D-1 query、D-2 comparison、D-3 risk summary、D-4 change impact、E-1 evidence
lineage 與 E-3 owner/dependency read models。它是管理層可讀的 observation layer，不是新的 risk
engine、business decision engine、approval gate 或 workflow control input。

## 2. Product decision and scope

### 2.1 Confirmed product choice

- UI 位置：既有 Agent Operations → Governance Graph section 的 Management Summary 區塊。
- 核心服務：獨立 `GovernanceGraphManagementSummaryService`，只接受 caller 提供的已驗證 read models。
- UI boundary：optional、external、dependency-injected callback；`app_pages.py` 不建立 authority，
  不讀取 catalog／snapshot path，也不呼叫 writer、CLI、subprocess 或 control-plane service。
- 來源模型：每個 D-1～D-4、E-1、E-3 input 保留自己的 status、fingerprint、coverage 與 diagnostics；
  summary 只做 deterministic aggregation 與 exact projection。
- 缺少或不可信來源：fail closed 為 `unavailable`、`missing`、`unknown`、`blocked` 或 `invalid`；
  不輸出「零風險」、「無影響」或「owner 已完整」等未被 evidence 支持的正面結論。

### 2.2 In scope

- Management Summary public read model 與 canonical fingerprint contract。
- Protected／blocked／unknown attention counts 與 bounded attention items。
- Risk／impact／evidence／owner／dependency coverage 的明確聚合。
- 只基於明確提供的 summary snapshots 的 deterministic trend projection。
- 固定、無寫入的 management query presets 與 bounded drill-down references。
- `governance-graph-management-summary-export-v1` 的 in-memory／browser download payload contract。
- Agent Operations rendering、strict callback validation、selection lifecycle 與 no-write tests。
- schema、status、fingerprint、source provenance、raw/secret/path rejection 與 no-inference tests。

### 2.3 Explicit non-goals

- 不取代既有 P2-5 Management Decision Layer；不讀取或重算營收 target、attainment、forecast gap、
  KPI、WAPE 或 management target configuration。
- 不重新執行 D-1～D-4 rules，不重新讀取 Graph snapshot、canonical evidence、raw runtime、SQLite、
  Git、network、business rules 或任意 filesystem path。
- 不從 node 名稱、edge 順序、filename、timestamp、缺失資料、owner role、risk category、impact
  category 或文字內容推導 dependency、causal relation、business impact、responsible person 或
  remediation。
- 不建立 Graph snapshot、不建立 catalog、不回填 canonical artifacts、不修改 workflow status、
  approval、dispatch、rollback、baseline、revenue scope、business rules、cache 或既有 export schema；
  E-4 只新增本 spec 定義的 management-summary/export contracts。
- 不產生自動通知、recommendation action、approval decision、risk acceptance、blocking transition
  或 workflow state transition。
- 不保證 trend；只有在來源 snapshot 明確可比時才輸出 trend，否則輸出 `unknown`。
- 不以 UI table、display formatting 或 download payload 修正任何正式口徑。

## 3. Authority and provenance

### 3.1 Source-of-truth boundary

Canonical artifacts 是治理事實的唯一真相來源；Governance Graph 與 E-4 summary 都是衍生、只讀
projection。E-4 的 input authority 僅限 caller 提供、且已通過各自 public contract 的 read models：

| Input | E-4 可消費 | E-4 禁止推導 |
|---|---|---|
| D-1 query result | bounded exact-match records、status、query fingerprint | 新增 relationship、risk 或 owner |
| D-2 comparison result | explicit snapshot／node／edge／evidence changes、status、fingerprint | causal sequence、downstream traversal |
| D-3 risk summary | validated findings、risk level、coverage、fingerprint | 新 risk rule、risk score、owner |
| D-4 impact summary | exact impact categories、impact state、coverage、fingerprint | business impact、remediation、dependency |
| E-1 evidence lineage | bounded evidence identity、lineage status、provenance | missing evidence 的補值或 PASS |
| E-3 catalog read model | owner role、declared relation、coverage、fingerprint | 個人、authority、未宣告 dependency |

每個 source reference 必須保留：

```json
{
  "kind": "d3_risk_summary",
  "identity": "risk-summary",
  "fingerprint": "<lowercase-sha256>",
  "status": "available"
}
```

`sourceRef` 的 exact keys 為 `kind`、`identity`、`fingerprint`、`status`；`status` 只允許
`available`、`blocked`、`unknown`、`missing`、`unavailable`、`stale`、`invalid`。`fingerprint` 必須
是 64 字元 lowercase SHA-256；`identity` 必須符合 bounded safe identifier。任何額外 key 或未列入
allowlist 的 status 都是 `invalid`。

`kind`、`identity`、reason code、preset id 與 drill-down identity 都必須是 bounded safe identifier；
不得輸出 absolute path、URI、secret、prompt、command、stdout/stderr、raw payload 或完整 log。

### 3.3 Required source schemas and snapshot binding

E-4 只接受下列 exact public schemas；caller 必須先通過各 source 自己的 public parser，E-4 再做
schema、status、fingerprint 與 snapshot binding 的二次檢查：

| Input | Required `schemaVersion` | Required identity/binding |
|---|---|---|
| D-1 query | `governance-graph-query-v1` | `runId`、query fingerprint 或明確 query identity、selected snapshot fingerprint |
| D-2 comparison | `governance-graph-comparison-v1` | `leftReference`、`rightReference`、comparison fingerprint；兩側 references 必須是 caller 明確提供的 immutable identities |
| D-3 risk | `governance-graph-risk-summary-v1` | `comparisonFingerprint`、risk summary fingerprint、comparison status；透過 comparison 的 selected right reference 綁定 snapshot |
| D-4 impact | `governance-graph-change-impact-v1` | `comparisonFingerprint`、`riskSummaryFingerprint`、impact summary fingerprint；透過 D-3 → comparison 的 selected right reference 綁定 snapshot |
| E-1 lineage | `governance-graph-evidence-lineage-v1` | `runId`、`snapshotFingerprint`、lineage fingerprint |
| E-3 catalog | `governance-graph-owner-dependency-read-v1` | `snapshotFingerprint`、read-model fingerprint（available 時） |

Source reference `kind` 使用 closed allowlist：`d1_query`、`d2_comparison`、`d3_risk_summary`、
`d4_change_impact`、`e1_evidence_lineage`、`e3_owner_dependency_catalog`。不接受近似 kind、自由
文字或未列入本表的 schema。Compose 的 `snapshot_fingerprint` 明確代表 D-2 `rightReference.snapshotFingerprint`；
D-2 的 `leftReference` 仍保留為比較來源，但不會被當作 summary 的 selected snapshot。D-3 必須透過其
`comparisonFingerprint` 指向同一個 D-2 comparison，D-4 必須同時通過 D-3 → D-2 的傳遞鏈；E-1
與 E-3 的 `snapshotFingerprint` 必須直接等於 selected right snapshot。缺少必要 binding 為 `unknown`，
不同 binding 為 `stale`，schema／fingerprint／exact-key 失敗為
`invalid`。一側 invalid 不得污染另一側的 validated fields，但 overall status 仍依 §5.1 precedence
聚合。

### 3.2 Snapshot binding

每個 summary input 必須帶有選定 Graph snapshot identity 或明確的 snapshot reference。若多個 input
存在不同 snapshot fingerprint、缺少必要 binding 或無法證明同一 observation boundary，E-4 status
必須是 `stale`、`unknown` 或 `invalid`，不可把不同 run／snapshot 混成單一摘要。

Trend input 必須是 caller 明確提供的 immutable summary snapshot envelopes；E-4 不從 filesystem、
runtime、Git history 或 database 查找前一個 snapshot。

## 4. Architecture and data flow

```text
validated D1 query ─┐
validated D2 compare ─┤
validated D3 risk ────┤
validated D4 impact ──┼─> GovernanceGraphManagementSummaryService
validated E1 lineage ┤                 │
validated E3 catalog ─┘                 ├─ management-summary-v1
                                       ├─ export-v1 (in-memory)
                                       └─ Agent Operations read-only section
```

Service contract：

```python
GovernanceGraphManagementSummaryService.compose(
    *,
    snapshot_fingerprint: str,
    query: Mapping[str, Any] | None,
    comparison: Mapping[str, Any] | None,
    risk: Mapping[str, Any] | None,
    impact: Mapping[str, Any] | None,
    lineage: Mapping[str, Any] | None,
    catalog: Mapping[str, Any] | None,
    trend_snapshots: Sequence[Mapping[str, Any]] = (),
) -> GovernanceGraphManagementSummary
```

服務必須 pure、deterministic、side-effect free：不讀檔、不寫檔、不啟動 subprocess、不連 network、
不讀 SQLite、不呼叫 D-1～D-4 writer、不建立 Graph snapshot，不保留 session authority。

### 4.1 Required inputs and partial isolation

The required set for an `available` summary is D-2 comparison、D-3 risk、D-4 impact、E-1 lineage 與
E-3 catalog；D-1 query is optional context and may be `unavailable` without invalidating an otherwise
complete risk/impact summary. A missing required source yields `missing` or `unavailable`; an invalid
required source yields `invalid`; a stale binding yields `stale`。No source is replaced by an empty
default.

Aggregation is field-preserving and fail-closed:

1. Parse and validate each source independently.
2. Retain only the valid source's bounded fields and source reference；discard invalid source payloads。
3. Compute overall status using the precedence in §5.1 across all required sources and any supplied
   optional source that is not `unavailable`; an omitted or `unavailable` optional D-1 query is excluded
   from overall status and only reported in query coverage。`invalid` and `stale` take precedence over
   lower-confidence states。
4. Build attention items only from validated source records; an invalid／unavailable source contributes
   diagnostics and coverage, never inferred findings。
5. `overallRiskLevel` is `unknown` whenever risk is missing／unknown／blocked without a safe level, or
   required coverage is incomplete；otherwise it is the deterministic maximum of validated D-3 levels。

Thus a valid D-3 finding remains visible when E-1 is missing, but the headline remains `unknown` and the
evidence coverage records the gap. A D-3 invalid payload never produces risk findings, even when D-2 is
available.

Coverage mapping is closed and source-driven. E-4 does not require a common `coverageStatus` key on the
source schemas; it derives one deterministic canonical state from each existing exact schema, and never
treats an empty record list as complete:

- D-2 `comparison`: `available` + valid left/right snapshot identities + both `freshness=fresh` + valid
  summary counts ⇒ `complete`; valid comparison with any non-fresh freshness ⇒ `partial`; explicit
  `blocked`/`unknown`/`stale`/`invalid` status maps directly.
- D-3 `risk`: `status=available` and `observedChanges == classifiedChanges` and all
  `unknownChanges/invalidChanges/blockedChanges == 0` ⇒ `complete`; status available with any positive
  unknown/invalid/blocked count or observed/classified mismatch ⇒ `partial`; other statuses map directly.
- D-4 `impact`: `coverage.coverageStatus=available` ⇒ `complete`, `blocked` ⇒ `blocked`, `unknown` ⇒
  `unknown`; any other status or invalid coverage is `invalid`.
- E-1 `lineage`: `status=available` with valid `snapshotFingerprint`, lineage fingerprint and validated
  evidence list ⇒ `complete`; valid `blocked`/`stale`/`unknown` status maps directly; missing evidence
  entries yield `partial` only when the envelope explicitly remains available.
- E-3 `catalog`: owner and dependency statuses both `available` with valid read-model fingerprint ⇒
  `complete`; one available and one missing/unknown/stale/blocked ⇒ `partial`; overall invalid/stale/
  blocked/unknown maps directly.

The canonical summary states are `available`, `partial`, `unknown`, `missing`, `unavailable`, `stale`,
`blocked`, and `invalid`. `comparison`, `risk`, `impact`, `lineage`, and `catalog` are sufficient only
when all five map to `available`/`complete`; optional D-1 `query` may be `unavailable` without lowering
required-source status.

## 5. Public output contract

Schema：`governance-graph-management-summary-v1`

```json
{
  "schemaVersion": "governance-graph-management-summary-v1",
  "managementPolicyVersion": "e4-management-summary-v1",
  "status": "available",
  "snapshotFingerprint": "<sha256>",
  "summaryFingerprint": "<sha256>",
  "overallRiskLevel": "R1",
  "headline": {
    "attentionStatus": "attention",
    "protectedCount": 1,
    "blockedCount": 0,
    "unknownCount": 1,
    "evidenceCoverage": "partial",
    "ownerCoverage": "available",
    "dependencyCoverage": "unknown"
  },
  "risk": {
    "status": "available",
    "overallRiskLevel": "R1",
    "findingCount": 2,
    "levels": {"R2": 1, "R1": 1, "R0": 0, "unknown": 0},
    "sourceRef": {"kind": "d3_risk_summary", "identity": "risk-summary", "fingerprint": "<sha256>", "status": "available"}
  },
  "impact": {
    "status": "available",
    "observedCount": 2,
    "blockedCount": 0,
    "unknownCount": 1,
    "categories": ["protected_governance_surface", "verification_assurance"],
    "sourceRef": {"kind": "d4_impact_summary", "identity": "change-impact", "fingerprint": "<sha256>", "status": "available"}
  },
  "coverage": {
    "query": "available",
    "comparison": "available",
    "risk": "available",
    "impact": "available",
    "lineage": "partial",
    "catalog": "unknown"
  },
  "attentionItems": [
    {
      "attentionId": "protected_governance_surface:node:protected_incident:observed:D3-PROTECTED-SURFACE",
      "severity": "R2",
      "category": "protected_governance_surface",
      "state": "observed",
      "summaryCode": "protected_signal_requires_governance_review",
      "sourceRefs": [],
      "drillDown": {"kind": "node", "identity": "protected_incident"}
    }
  ],
  "trend": {
    "status": "unknown",
    "basis": "insufficient_comparable_snapshots",
    "observations": [],
    "changedDimensions": []
  },
  "presets": [
    {"presetId": "protected_surfaces", "labelCode": "protected_surfaces", "available": true},
    {"presetId": "blocked_verification", "labelCode": "blocked_verification", "available": false},
    {"presetId": "unknown_coverage", "labelCode": "unknown_coverage", "available": true},
    {"presetId": "owner_dependency_gaps", "labelCode": "owner_dependency_gaps", "available": true},
    {"presetId": "recent_changes", "labelCode": "recent_changes", "available": true}
  ],
  "diagnostics": [],
  "sourceRefs": []
}
```

### 5.0 Canonical serialization and fingerprint

All E-4 fingerprints use the existing `canonical_sha256` algorithm: UTF-8 JSON, `ensure_ascii=false`,
`sort_keys=true`, `separators=(",", ":")`, SHA-256 lowercase hex. Before hashing:

- object keys are the exact public keys defined by this spec；unknown keys are invalid;
- `attentionItems`、`sourceRefs`、`categories`、`observations` and `presets` are normalized by their
  canonical identity and sorted; exact duplicates are deduplicated, conflicting identities are invalid;
- `diagnostics` are retained, bounded, deduplicated by `(code, summary)` and sorted;
- `summaryFingerprint` covers `schemaVersion`、`status`、`snapshotFingerprint`、`overallRiskLevel`、
  `managementPolicyVersion`、
  `headline`、`risk`、`impact`、`coverage`、`attentionItems`、`trend`、`presets`、`diagnostics` and
  `sourceRefs`, while excluding `summaryFingerprint` itself;
- `exportFingerprint` covers `schemaVersion`、`summarySchemaVersion`、`snapshotFingerprint`,
  `summaryFingerprint`、`managementPolicyVersion`、`selectedPresetId` and the normalized `summary`, while excluding
  `exportFingerprint` itself.

The summary fingerprint is computed before export filtering. A preset export retains the original
`summaryFingerprint` as provenance and computes a new `exportFingerprint`; it must not rewrite the
summary's meaning or silently remove diagnostics. Same normalized input must produce byte-for-byte
identical output regardless of mapping insertion order.

### 5.1 Status and risk semantics

Status precedence：`invalid > stale > blocked > unknown > missing > unavailable > available`。

- `available`：required inputs valid、same snapshot、coverage sufficient。
- `blocked`：source is validated but explicitly blocked; bounded attention may remain visible。
- `unknown`：coverage or comparability insufficient；不得降級為 R0 或 no attention。
- `missing`：required source envelope explicitly absent from the caller。
- `unavailable`：callback／read model 尚未提供；不自行建立 empty model。
- `stale`：snapshot／fingerprint mismatch。
- `invalid`：schema、allowlist、fingerprint、boundedness 或 exact-key contract 失敗。

`overallRiskLevel` precedence：`R2 > R1 > R0`；若 coverage 不足、source status 非 available 或
無法安全分類，必須是 `unknown`。E-4 不計算 numeric risk score。

### 5.2 Headline semantics

`headline` 只能是 read-model aggregation：

- `protectedCount`：只計入 D-3／D-4 已明確標示 protected 的 bounded items。
- `blockedCount`：只計入 source 或 impact 已明確為 blocked 的 items。
- `unknownCount`：只計入 source、coverage 或 trend 已明確為 unknown 的 items。
- `evidenceCoverage`、`ownerCoverage`、`dependencyCoverage` 保留 `available`、`partial`、`unknown`、
  `missing`，不得將空列表解讀為 complete。
- `attentionStatus` 只允許 `clear`、`attention`、`unknown`；`clear` 只可在 required coverage
  complete 且沒有 protected／blocked／unknown signal 時出現。

## 6. Attention items and presets

### 6.1 Attention item mapping

E-4 只投影既有 D-3／D-4 exact identity，不新增 rule：

| Source signal | Category | Severity | State |
|---|---|---|---|
| D-3 protected finding / D-4 protected impact | `protected_governance_surface` | `R2` | `observed` |
| D-3 verification regression / D-4 verification assurance | `verification_assurance` | `R1` | `observed` |
| D-3 behavioral change / D-4 implementation governance | `implementation_governance` | `R1` | `observed` |
| D-4 blocked impact | `workflow_observability_blocked` | `unknown` | `blocked` |
| D-3/D-4 unknown coverage | `coverage_gap` | `unknown` | `unknown` |
| E-1 missing/stale lineage | `evidence_coverage_gap` | `unknown` | `unknown` |
| E-3 missing/unknown owner or dependency coverage | `catalog_coverage_gap` | `unknown` | `unknown` |

D-3 mapping is closed by the existing exact `ruleId` registry, not free-text matching:
`D3-PROTECTED-NODE` and `D3-PROTECTED-SURFACE` map to protected;
`D3-VERIFICATION-REGRESSION` maps to verification; `D3-BEHAVIORAL-CHANGE` maps to implementation;
`D3-BLOCKED-COMPARISON` maps to blocked; `D3-UNKNOWN-COVERAGE` maps to coverage gap; and
`D3-DOCUMENTATION-ONLY` produces no management attention item. Any other D-3 `ruleId` is `invalid` for
E-4 attention projection. D-4 mapping uses only its exact `category` and `impactState` allowlists from
`governance-graph-change-impact-v1`: `protected_governance_surface`、`verification_assurance`、
`implementation_governance`、`workflow_observability_blocked` map to the same-named E-4 categories;
`coverage_unknown` maps to `coverage_gap`; `documentation_only` produces no management attention item.
Unknown category/state is `invalid`, never approximated.

The allowed attention categories are exactly `protected_governance_surface`、`verification_assurance`、
`implementation_governance`、`workflow_observability_blocked`、`coverage_gap`、
`evidence_coverage_gap`、`catalog_coverage_gap`。Severity is exactly `R2`、`R1`、`R0` or `unknown`;
state is exactly `observed`、`blocked` or `unknown`。Drill-down `kind` is closed to `node`、`edge`、
`evidence`、`finding`、`impact`、`owner`、`dependency`；identity must be a bounded safe identifier.
Unknown source rule／category／identity is `invalid`; approximate matching is forbidden. `attentionId`
is always the canonical tuple `category:kind:identity:state:sourceIdentity`; when the source does not
expose a finding／impact identity, `sourceIdentity` is the literal `none`. Two records with the same tuple
are exact duplicates and are deduplicated; the same tuple with different payload is `invalid`. `sourceRefs`
are deduplicated by `(kind, identity, fingerprint)` and sorted by `(kind, identity, fingerprint)`. Attention
items are sorted by `(severity priority, state priority, attentionId)`.

### 6.2 Preset semantics

Preset IDs are immutable code-owned identifiers. Presets only filter the already composed summary; they
do not run a new query, mutate state, or infer missing relations:

- `protected_surfaces`：explicit protected signals only。
- `blocked_verification`：explicit blocked／verification signals only。
- `unknown_coverage`：unknown／missing／stale coverage only。
- `owner_dependency_gaps`：E-3 owner/dependency coverage gaps only。
- `recent_changes`：only caller-supplied bounded D-2 changes within the selected summary boundary；
  no timestamp-based discovery。

Preset `available=true` iff at least one validated item matches the exact preset predicate; an empty
match is `available=false` and does not mean zero risk. The canonical UI selection state is either
`null` or the exact bounded object `{presetId, snapshotFingerprint}`. The selection is session-scoped,
must match the currently selected snapshot, and is cleared on run／snapshot mismatch or invalid preset.
The export maps this state to `selectedPresetId` (a string or `null`) and preserves the original
`summaryFingerprint`; it does not serialize the session selection object. A preset export contains the
filtered view plus original summary provenance, and computes a new `exportFingerprint`. No preset is an
approval, dispatch, export-write, repair or writer action.

## 7. Trend contract

Trend is optional and input-driven. Caller must provide at least two valid, same-schema summary snapshots
with comparable snapshot fingerprints and explicit observation order. E-4 must not query history itself.

Each trend envelope must include these exact keys and no others:

```json
{
  "schemaVersion": "governance-graph-management-summary-v1",
  "managementPolicyVersion": "e4-management-summary-v1",
  "snapshotFamily": "family-identifier",
  "snapshotFingerprint": "<sha256>",
  "summaryFingerprint": "<sha256>",
  "summary": {},
  "overallRiskLevel": "R1",
  "attentionCount": 2,
  "unknownCount": 0,
  "headline": {
    "attentionStatus": "attention",
    "ownerCoverage": "available",
    "dependencyCoverage": "available",
    "evidenceCoverage": "available"
  }
}
```

The nested `summary` must pass the complete `governance-graph-management-summary-v1` validator,
must contain the same `snapshotFingerprint` and `summaryFingerprint` as the envelope, and its
`headline` counts must exactly equal `attentionCount` and `unknownCount`. E-4 recomputes the canonical
summary fingerprint and rejects a mismatch; `summaryFingerprint` is never treated as opaque caller
evidence. Snapshots are comparable only when schema version, management policy version, and caller-
supplied `snapshotFamily` are identical, each fingerprint is valid and unique, and the envelope passes
these exact binding rules. The caller-provided sequence is authoritative; E-4 preserves that order and
compares the first and last observations without sorting or reversing them.

Output:

```json
{
  "status": "available",
  "basis": "explicit_summary_snapshots",
  "observations": [
    {"snapshotFingerprint": "<sha256>", "overallRiskLevel": "R1", "attentionCount": 2, "unknownCount": 0}
  ],
  "changedDimensions": ["attentionCount", "ownerCoverage"]
}
```

Rules:

- fewer than two comparable snapshots → `unknown` / `insufficient_comparable_snapshots`。
- different schema、policy、snapshot family、duplicate fingerprints 或 invalid input → `invalid` /
  bounded diagnostic；missing/unknown coverage remains `unknown`, not a zero value。
- `changedDimensions` only lists exact field changes; no causal explanation, forecast or direction claim。
- trend must not be used to declare improvement, deterioration, target attainment or risk acceptance。

## 8. Read-only export contract

Schema：`governance-graph-management-summary-export-v1`。

Export is a bounded serialization of the validated summary plus selected preset identity. It may be
returned in-memory or offered through Streamlit browser download, but it must not write a canonical
artifact, runtime file, SQLite row, Git object or approval record.

Required envelope:

```json
{
  "schemaVersion": "governance-graph-management-summary-export-v1",
  "summarySchemaVersion": "governance-graph-management-summary-v1",
  "managementPolicyVersion": "e4-management-summary-v1",
  "snapshotFingerprint": "<sha256>",
  "summaryFingerprint": "<sha256>",
  "selectedPresetId": "protected_surfaces",
  "summary": {},
  "exportFingerprint": "<sha256>"
}
```

Export must preserve source statuses and diagnostics. It must never omit `unknown`, `missing`, `stale` or
`blocked` labels merely to make the management output look complete.

### 8.1 Diagnostic contract

Invalid or rejected source inputs map only to the following closed diagnostic codes:
`source_schema_invalid`, `source_fingerprint_invalid`, `source_snapshot_missing`,
`source_snapshot_mismatch`, `source_status_invalid`, `source_binding_invalid`,
`source_payload_forbidden`, `trend_envelope_invalid`, `trend_fingerprint_mismatch`,
`preset_selection_invalid`, and `preset_snapshot_mismatch`. Each diagnostic contains only the exact
keys `{code, summary, sourceKind}`; `sourceKind` is either one of the six closed source kinds or `null`.
`summary` is a bounded label code, not raw exception text. Diagnostics are deduplicated by
`(code, sourceKind, summary)` and sorted by `(code, sourceKind or "", summary)`. No other diagnostic
code, free-form message, path, secret, command or raw payload is permitted.

## 9. Streamlit integration boundary

The existing Agent Operations rendering receives an optional callback:

```python
management_summary_lookup(
    run_id: str,
    snapshot_fingerprint: str,
    preset_id: str | None = None,
) -> Mapping[str, Any]
```

Renderer requirements:

- callback `None` → explicit `unavailable` message。
- callback exceptions、schema mismatch、wrong snapshot、raw／secret／path fields → bounded `invalid` or
  `unavailable` display without leaking payload。
- UI accepts only exact public schema and canonical allowlists；it does not re-run aggregation。
- selected preset／attention identity is bounded and cleared on run／snapshot mismatch。
- UI does not create snapshot、catalog、trend source、export file path or management decision。
- existing D-1～D-4 panels remain independently readable; E-4 failure must not turn them into PASS or
  hide their diagnostics。
- Within the existing Governance Graph section, render deterministically in this order: graph metadata and
  canonical lineage, E-4 Management Summary, E-3 owner/dependency catalog, D-2 comparison, D-3 risk, then
  D-4 impact. A malformed E-4 callback only replaces its own bounded panel message; it does not reorder,
  suppress, or alter the other panels.
- E-4 must remain independent of the P2-5 Management Decision Layer: tests must prove there is no import or
  call to decision-layer services, target/forecast/attainment APIs, or revenue decision data, and no
  `attainment`, `target`, `forecast gap`, or equivalent decision fields in the E-4 public schemas.

No new FastAPI endpoint is required in E-4 v1. A future API may consume the same service contract only
through a separately approved design.

## 10. Local agent boundaries

- Context Agent：只使用 `scripts/context_agent.py --collect-only`；輸出 compact bundle；不修改 spec、
  source、runtime、SQLite、baseline 或 Git。
- Review Agent：只讀 approved E-4 spec／plan、實際 diff 與 evidence；findings-first；不得修改檔案、
  代替 Hermes 或作管理決策。
- Hermes：只讀驗收 runtime、SQLite integrity、baseline、services、Git 與 E-4 artifacts；不得建立
  summary、寫 export、修復 coverage 或呼叫 writer。
- Implementation Agent（若另行批准）：一次只執行一個 allowlisted Task；不得 commit、merge、push、
  啟停服務或修改正式資料。
- Codex：負責 spec／plan、findings 修正、完整驗證、Hermes、final acceptance 與 integration。

## 11. Testing and acceptance

### 11.1 Required tests

- Exact public keys、schema、status precedence、lowercase SHA-256 與 deterministic fingerprint。
- Complete available inputs、single-side missing、unknown／blocked／stale／invalid isolation。
- D-1～D-4、E-1、E-3 source fingerprint binding 與 wrong-snapshot rejection。
- Protected／blocked／unknown attention mapping、dedupe、ordering 與 no-inference。
- Coverage semantics：empty／missing 不得變成 complete 或 zero-risk。
- Trend：zero／one／two comparable snapshots、reversed input order（輸出 observation 順序與
  first/last `changedDimensions` 必須按 caller 順序相應改變，不得被 service 自動排序）、schema
  mismatch、unknown coverage。
- Trend：same-family/policy comparability、duplicate fingerprint rejection、first/last comparison and
  explicit preservation of caller order。
- Preset exact filtering、bounded selection cleanup、refresh preservation 與 no write。
- Export exact envelope、fingerprint、secret/path/raw payload rejection、browser download only。
- Streamlit callback malformed／exception isolation；不得呼叫 CLI、subprocess、network、SQLite、
  snapshot builder 或 writer。
- Deterministic panel ordering and an executable static boundary test proving P2-5 decision-layer
  separation (no decision service import/call or business-target fields).
- Service、renderer、export serializer 前後 runtime／SQLite／Git／canonical artifact tree equality。

### 11.2 Acceptance sequence

```bash
.venv/bin/python -m py_compile \
  backend/agents/governance_graph_management_summary_models.py \
  backend/agents/governance_graph_management_summary_service.py \
  governance_graph_rendering.py \
  agent_operations_rendering.py \
  app_pages.py
.venv/bin/python -m pytest \
  tests/test_governance_graph_management_summary_models.py \
  tests/test_governance_graph_management_summary_service.py \
  tests/test_governance_graph_rendering.py \
  tests/test_agent_operations_rendering.py -q
.venv/bin/python -m pytest -q
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/hermes_post_change_check.py
```

Completion requires Task-level TDD、strict Review PASS、full pytest PASS、system acceptance PASS、Hermes
PASS、clean worktree，以及 proof that SQLite integrity、baseline `HKD 12,057,968`、正式口徑
「不含掛賬核銷與TT退款轉團款」與 Graph snapshot writer boundary unchanged。

## 12. Future compatibility

Future versions may add explicitly approved management metrics、version comparison UI、risk trend
visualization、owner/dependency drill-down or a FastAPI read endpoint only through a new schema/policy
version. E-4 v1 remains a bounded observation and export layer; it cannot become an approval、dispatch、
business target、risk acceptance or remediation engine.
