# NBS Governance Graph Phase E-3 Owner／Dependency Catalog Design

狀態：approved for design implementation planning  
日期：2026-07-30  
風險：R1 standard engineering（只讀 catalog contract、validation 與 read-model projection；不改正式資料）

## 1. Goal

建立兩個彼此獨立、immutable、可驗證的 Governance Graph catalog：

- `governance-graph-owner-catalog-v1`：描述 Graph subject 所屬的治理角色／責任群組。
- `governance-graph-dependency-catalog-v1`：描述明確宣告的 workflow 或治理 dependency。

兩個 catalog 由共用的 read-only service 驗證並組合成 bounded read model，供 D-1 query、D-2
snapshot comparison、D-3 risk summary、D-4 change impact 與 E-2 Streamlit UI 在後續階段消費。

`owner` 只代表治理角色／責任群組，不代表個人、email、GitHub handle、approval authority 或
自動 dispatch 對象。`dependency` 只代表明確宣告且可追溯的關係，不由 Graph node 名稱、順序或
其他文字推測。

E-3 是 catalog contract、strict validation 與 read-model projection，不是新的 approval、
dispatch、runtime、SQLite、Git、Graph snapshot 或 canonical-artifact writer。

## 2. Product decision and scope

### 2.1 Confirmed product choice

- 採用兩個獨立 immutable catalog，加上一個共用 read-only service。
- Owner 與 dependency 使用不同 schema、不同 validation 入口與不同 failure diagnostics。
- 共用 service 只做 strict parsing、provenance binding、duplicate/conflict detection、
  deterministic ordering 與 bounded read-model 組合。
- Catalog 的建立、批准、持久化與生命周期由 E-3 以外的 approved producer／canonical workflow
  負責；E-3 不新增 writer 或 approval state machine。
- 若沒有 approved catalog input，consumer 必須顯示 `unavailable`、`missing` 或 `unknown`，
  不得自動建立空 catalog，也不得將缺失轉成「無 owner」或「無 dependency」。

### 2.2 In scope

- Owner catalog 與 dependency catalog 的 public schema、allowlist 與 fingerprint contract。
- 治理角色型 owner identity，例如 `spec_owner`、`plan_owner`、`implementation_owner`、
  `review_owner`、`verification_owner`、`hermes_owner`、`documentation_owner`。
- Dependency 的 `workflow_edge` 與 `declared_dependency` relation kind 分層。
- Graph snapshot／run identity binding、stale detection、cross-run mismatch detection。
- `available`、`missing`、`unknown`、`blocked`、`stale`、`invalid` 狀態與 precedence。
- Duplicate identical entry 的 deterministic handling 與 conflicting duplicate 的 fail-closed
  行為。
- 共用 bounded read model、deterministic fingerprint、CLI/service boundary 與測試契約。
- 後續 UI、query、comparison、risk、impact consumer 的 callback boundary。

### 2.3 Explicit non-goals

- 不保存個人姓名、email、GitHub handle、Git author、prompt 或聯絡資料。
- 不由 Graph node／edge、artifact filename、registry writer、timestamp、Git blame、文字內容、
  finding、risk category 或 impact category 推導 owner、dependency、downstream 或 causal relation。
- 不把既有 Graph edge 自動改名為 business dependency；既有 workflow relation 只能作為
  `workflow_edge` 的原樣投影。
- 不讀取 raw runtime、SQLite、Git、business rules、`target_governance`、network 或任意
  filesystem path。
- 不建立 Graph snapshot、不更新 canonical artifacts、不修改 workflow status、不寫 cache、
  Obsidian、SQLite、baseline、revenue scope、rollback 或 export schema。
- 不新增 approval、dispatch、repair、apply、prune、delete、runner 或 remediation path。
- 不執行自然語言 dependency discovery、graph traversal inference、business impact 計算或 risk score。
- 不在 E-3 直接實作 Streamlit UI；UI 只能在後續 approved callback 注入後消費 validated read model。

## 3. Authority and provenance

### 3.1 Source-of-truth boundary

Canonical artifacts 仍是治理事實的唯一真相來源；Governance Graph 是衍生、只讀 snapshot。
E-3 catalog 是一種明確宣告的治理 metadata artifact，但 catalog service 不負責建立或持久化它。
Service 只消費 caller 提供的 validated catalog envelope，並要求每個 catalog 與 selected Graph
snapshot 以 fingerprint 綁定。

既有來源的責任如下：

| Source | E-3 可消費內容 | E-3 禁止推導 |
|---|---|---|
| Validated Graph snapshot | node／edge identity、既有 workflow relation、snapshot fingerprint | owner、business dependency、causal relation |
| Canonical evidence registry／reader | explicit artifact identity、schema、writer provenance、SHA | writer 名稱即 owner、artifact name 即 dependency |
| D-1 query | bounded exact-match result | 新增 catalog relation 或補 missing owner |
| D-2 comparison | explicit changed identity、comparison provenance | dependency traversal 或 owner inference |
| D-3 risk summary | bounded finding、risk level、evidence identity | responsible person、downstream owner |
| D-4 impact summary | bounded impact category、source finding | causal dependency、business impact |
| `target_governance`／business rules | 不可作為 E-3 authority | 任何 owner／dependency metadata |

### 3.2 Shared provenance contract

每個 owner／dependency catalog envelope 與每筆 entry 必須包含：

```json
{
  "source": {
    "kind": "approved_catalog",
    "identity": "owner-catalog-v1",
    "fingerprint": "<lowercase-sha256>"
  },
  "snapshotFingerprint": "<validated-graph-sha256>",
  "status": "available"
}
```

規則：

- `source.kind` 使用 closed allowlist；v1 至少允許 `approved_catalog` 與既有
  `graph_contract`／`canonical_evidence` provenance，不接受任意文字。
- `source.identity`、subject identity、owner identity、relation 與 reason code 都必須是 bounded
  safe identifier。
- `source.fingerprint` 與 `snapshotFingerprint` 都必須是 lowercase SHA-256。
- `snapshotFingerprint` 必須與 selected Graph snapshot 完全一致；不一致即 `stale`。
- 不接受 absolute path、path traversal、URI、secret、raw JSON、prompt、command 或完整 log。
- Service 不會根據 input path 自行載入另一個 catalog 或 snapshot。

## 4. Owner catalog contract

### 4.1 Schema

Schema version：`governance-graph-owner-catalog-v1`

```json
{
  "schemaVersion": "governance-graph-owner-catalog-v1",
  "catalogPolicyVersion": "e3-owner-policy-v1",
  "catalogFingerprint": "<sha256-over-canonical-envelope>",
  "snapshotFingerprint": "<validated-graph-sha256>",
  "entries": [
    {
      "subject": {"kind": "node", "id": "review"},
      "owner": {"kind": "governance_role", "id": "review_owner"},
      "source": {
        "kind": "approved_catalog",
        "identity": "owner-catalog-v1",
        "fingerprint": "<sha256>"
      },
      "status": "available"
    }
  ],
  "diagnostics": []
}
```

### 4.2 Owner allowlist

v1 owner kind 固定為 `governance_role`。owner id 只允許：

```text
spec_owner
plan_owner
implementation_owner
review_owner
verification_owner
hermes_owner
documentation_owner
```

若未來增加 role，必須先更新 spec／policy version 與 tests；不接受近似名稱、自由文字或
個人 identity。

### 4.3 Owner identity semantics

- Subject identity 必須對應 validated Graph node 或明確 catalog subject kind。
- 同一 `(subject.kind, subject.id)` 只可有一個有效 owner。
- 完全相同的 duplicate entry 一律依 canonical identity deterministic dedupe；不得依輸入
  順序、mtime 或最後一筆資料決定結果。
- 同一 subject 對應不同 owner、不同 source 或不同 snapshot binding 是 conflict，整個 owner
  catalog 結果為 `invalid`；不可 last-write-wins。
- 沒有 entry 時是 `missing`；subject identity 不足時是 `unknown`；不得輸出「no owner」作為
  正面事實。

## 5. Dependency catalog contract

### 5.1 Schema

Schema version：`governance-graph-dependency-catalog-v1`

```json
{
  "schemaVersion": "governance-graph-dependency-catalog-v1",
  "catalogPolicyVersion": "e3-dependency-policy-v1",
  "catalogFingerprint": "<sha256-over-canonical-envelope>",
  "snapshotFingerprint": "<validated-graph-sha256>",
  "entries": [
    {
      "from": {"kind": "node", "id": "implementation"},
      "to": {"kind": "node", "id": "verification"},
      "relation": "requires",
      "relationKind": "workflow_edge",
      "source": {
        "kind": "approved_catalog",
        "identity": "dependency-catalog-v1",
        "fingerprint": "<sha256>"
      },
      "status": "available"
    }
  ],
  "diagnostics": []
}
```

### 5.2 Relation semantics

`relation` 只允許既有 Governance Graph contract 的 bounded vocabulary：

```text
requires
produces
implements
reviews
verifies
blocks
derived_from
committed_as
documented_by
```

`relationKind` 只允許：

- `workflow_edge`：Graph 已有 workflow relation 的原樣投影。
- `declared_dependency`：未來經批准且明確登錄的治理 dependency。

`workflow_edge` 不得在 service 內被重新命名為 `business_dependency`、`causal_dependency`、
`downstream` 或 `impact_link`。v1 不允許 self-loop；from/to identity、relation、relationKind
的組合必須唯一。

### 5.3 Duplicate and conflict semantics

- 完全相同 identity 的 duplicate entry 一律 deterministic dedupe；不得依輸入順序決定結果。
- 同一 identity 但 source、snapshot、status 或 relation metadata 不同時為 conflict，結果為
  `invalid`。
- 不允許依輸入順序、mtime、generatedAt 或最後一筆資料覆蓋前一筆。
- 不存在 entry 是 `missing`；缺少 from/to identity 或上游 read model 不足是 `unknown`。

## 6. Shared read-only service and output model

### 6.1 Service boundary

建議介面：

```python
OwnerDependencyReadService.resolve(
    *,
    snapshot_fingerprint: str,
    owner_catalog: Mapping[str, Any] | None,
    dependency_catalog: Mapping[str, Any] | None,
) -> OwnerDependencyReadModel
```

Service 必須是 deterministic、read-only、side-effect free：

- 不讀檔、不啟動 subprocess、不連接 network、不讀 SQLite。
- 不寫入 runtime、Graph snapshot、canonical artifacts、cache、Git 或正式業務資料。
- 不建立空 catalog、不補 default owner、不猜測 dependency。
- Owner 與 dependency 各自驗證；一側 invalid 不得讓另一側產生推測性資料。
- 只輸出 bounded public model，錯誤不得包含 exception payload、raw response 或絕對路徑。

### 6.2 Output schema

Schema version：`governance-graph-owner-dependency-read-v1`

```json
{
  "schemaVersion": "governance-graph-owner-dependency-read-v1",
  "status": "available",
  "snapshotFingerprint": "<sha256>",
  "ownerCatalogFingerprint": "<sha256-or-null>",
  "dependencyCatalogFingerprint": "<sha256-or-null>",
  "readModelFingerprint": "<sha256-or-null>",
  "owners": [],
  "dependencies": [],
  "coverage": {
    "ownerStatus": "available",
    "dependencyStatus": "available",
    "ownerEntries": 1,
    "dependencyEntries": 1,
    "unknownCount": 0,
    "missingCount": 0,
    "staleCount": 0,
    "blockedCount": 0
  },
  "diagnostics": []
}
```

`readModelFingerprint` 覆蓋 schema version、policy versions、status、source fingerprints、
coverage、canonical-sorted owners／dependencies 與 diagnostics；排除 fingerprint 欄位本身。
Invalid／stale／unavailable result 不得計算或填入未驗證的 fingerprint。

### 6.3 Status precedence

跨 catalog 的 overall status 固定 precedence：

```text
invalid > stale > blocked > unknown > missing > unavailable > available
```

定義：

- `available`：schema、policy、source、snapshot binding、entries 全部一致。
- `unavailable`：caller 沒有提供任何 validated catalog input。
- `missing`：catalog 存在但沒有對應 subject／dependency entry。
- `unknown`：identity 或上游 evidence 不足，無法判定。
- `blocked`：明確的 protected governance 或上游 gate 限制阻擋讀取。
- `stale`：catalog／source／snapshot fingerprint 不一致。
- `invalid`：schema、allowlist、safe metadata、duplicate conflict 或 provenance 驗證失敗。

若 owner catalog 為 `available`、dependency catalog 為 `invalid`，overall 仍為 `invalid`；
owners 只能保留已驗證結果，不能產生任何 dependency fallback。若 catalog 缺失，UI 與
downstream service 必須保留 `missing`／`unknown`，不可轉成 zero owner 或 zero dependency。

## 7. Consumer integration boundary

### 7.1 D-1～D-4

- D-1 query 只可查詢已驗證 catalog read model；不得在 query service 內新增推導規則。
- D-2 comparison 可比較 owner／dependency catalog fingerprints 與 explicit entry changes，
  但不跨 run 推測 owner 或 dependency。
- D-3 risk 只能引用 catalog 明示的 status、owner role 或 dependency relation；不能把缺失
  owner 當成 R0，也不能自動產生 risk score。
- D-4 impact 只能把 explicit dependency change 投影成 bounded impact seed；不得做 traversal、
  causal inference 或 business impact 計算。

### 7.2 E-2 Streamlit

E-2 只接受未來 approved callback：

```python
catalog_lookup(run_id: str, snapshot_fingerprint: str) -> dict[str, Any]
```

UI 只渲染 bounded owner role、dependency relation、status、source identity short form 與
coverage／diagnostic code。若 callback 未提供或 result 未通過 public parser，顯示：

```text
狀態：unavailable；目前沒有可供 UI 消費的 validated Owner／Dependency read model。
此頁不會自行建立 catalog、Graph snapshot 或推導關係。
```

UI 不保存 raw catalog、lineage result、path、secret、prompt、command 或完整 logs；最多保存
bounded selected subject／relation identity，並在 run、fingerprint、node 或 gating 不一致時
清除 selection。

## 8. Local agent and governance boundaries

### Context Agent

- 只收集 E-3 brief、既有 specs、contract、symbols、tests 的 compact context。
- 不產生 catalog、不修改 code、SQLite、runtime、baseline 或 Git。

### Implementation Agent

- 只執行已批准的一個 E-3 implementation Task。
- 不建立 owner／dependency authority，不自行選擇 catalog source，不 commit、merge、push。

### Review Agent

- 只 review immutable diff、指定 tests、spec／plan contract。
- 必須檢查 no-inference、source binding、duplicate conflict、bounded output 與 no-write。
- Review PASS 不等於 Hermes PASS。

### Hermes

- 只做 read-only runtime、artifact、Graph、SQLite、baseline、system acceptance 驗收。
- 必須確認 catalog validation 沒有 writer、invocation、runtime 或正式資料寫入。

### Documentation Agent

- 只在 Review PASS、full verification PASS、Hermes PASS 後消費 documentation evidence。
- 沒有 approved runner 時回報 `blocked_missing_runner`，不得自動回填 owner／dependency catalog。

## 9. Testing and acceptance contract

Implementation plan 必須採 TDD RED→GREEN，並拆成可獨立 review 的 tasks。最低測試範圍：

### Models

- exact keys、schema version、policy version、fingerprint round-trip。
- role／relation／relationKind allowlist。
- absolute path、path traversal、secret、raw payload、unsafe identity rejection。
- missing、unknown、stale、blocked、invalid 與 precedence。

### Service

- valid owner／dependency projection。
- same subject multiple owner conflict。
- identical duplicate deterministic behavior。
- conflicting duplicate invalid。
- cross-run／snapshot fingerprint mismatch。
- one invalid catalog does not create fallback data in the other catalog。
- deterministic ordering、canonical fingerprint 與 repeated-run equality。
- Graph workflow edge relation preserved as `workflow_edge`。

### Consumers／CLI／UI boundary

- stdin／callback-only read path。
- no CLI、subprocess、writer、persist、approval、dispatch、repair、prune、download raw。
- missing callback／malformed result renders `unavailable`。
- selected run／fingerprint mismatch clears bounded selection only。
- D-1～D-4 output schemas unchanged except explicitly approved additive catalog fields。

### Final acceptance

```bash
.venv/bin/python -m py_compile <affected Python files>
.venv/bin/python -m pytest <affected tests> -q
.venv/bin/python -m pytest -q
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/hermes_post_change_check.py
```

Review Agent 與 Hermes 都必須 explicit PASS；任何 timeout、degraded、unknown runner、stale
catalog、baseline mismatch 或 formal scope mismatch 都不得宣稱完成。

必須確認下列 invariant 未改變：

- baseline：`HKD 12,057,968`
- 正式口徑：「不含掛賬核銷與TT退款轉團款」
- SQLite integrity、Graph snapshot、canonical artifacts、runtime、workflow status、Git state
- no write、no invocation、no approval／dispatch、no catalog auto-generation

## 10. Proposed implementation decomposition

Phase E-3 implementation plan 應至少拆成：

1. **Catalog models and strict public envelopes**：owner／dependency models、allowlists、fingerprints、
   duplicate semantics。
2. **Read-only catalog service**：independent owner/dependency validation、status precedence、
   deterministic combined read model。
3. **D-1～D-4 read-model adapters**：只消費 validated catalog result，不改寫既有 semantics。
4. **E-2 bounded UI callback**：只讀顯示 owner／dependency，缺資料明確 unavailable。
5. **Full verification and final acceptance**：strict Review、system acceptance、Hermes、plan
   reconciliation。

每個 Task 都必須有 allowlisted files、TDD、focused tests、strict Review checkpoint，且完成後
停止等待下一個 Task authorization；E-3 不包含 push、PR、merge 或 branch deletion。

## 11. Success criteria

E-3 only succeeds when：

1. Owner 只以治理角色／責任群組表示，沒有個人 identity。
2. Dependency 只來自 explicit catalog 或原樣投影的 `workflow_edge`。
3. Catalog、snapshot、source fingerprint binding 可驗證且 deterministic。
4. 缺失、未知、過期、阻擋、無效與衝突資料 fail closed。
5. D-1～D-4 與 E-2 只能消費 validated read model，不新增推導或 writer。
6. Review、full verification、system acceptance、Hermes 與 invariants 全部 PASS。
