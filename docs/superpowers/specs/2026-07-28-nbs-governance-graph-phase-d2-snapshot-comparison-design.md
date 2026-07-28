# NBS Governance Graph Phase D-2 Snapshot Comparison Design

狀態：approved for spec drafting  
日期：2026-07-28  
風險：R1 standard engineering

## 1. Goal

建立兩個明確 immutable Governance Graph snapshot 之間的 deterministic、read-only comparison read model，供後續 Phase D-3 risk analysis 與 Phase D-4 change impact analysis 消費。

D-2 只比較已存在且通過 schema validation 的 snapshot，不建立、更新、修復或推測任何 snapshot，也不把 comparison result 寫回 runtime、SQLite、canonical artifacts 或 Agent Operations snapshot。

## 2. Non-goals

- 不自動選擇 latest snapshot、current snapshot 或任意 fallback run。
- 不跨 run 推測 lineage、dependency、approval、causal relationship 或 impact。
- 不實作 risk scoring、risk ranking、change impact analysis 或自然語言 diff。
- 不新增 approval、dispatch、runner、runtime control-plane 或 background writer。
- 不直接讀取 SQLite、Git、raw runtime artifact 或未驗證 canonical evidence。
- 不修改 baseline、revenue scope、business rules、rollback 或 export schema。
- 不把 Graph schema 尚未提供的 edge 關係自行推導出來。

## 3. Design principles

1. **Explicit inputs only**：comparison 必須收到 `left` 與 `right` 兩個明確 snapshot reference；每個 reference 至少包含 `runId`，可選 `snapshotFingerprint`。
2. **Immutable source**：只讀取 run-contained 的 `governance-graph.json`，並使用既有 `GovernanceGraphSnapshot.from_dict()` 做完整 schema validation。
3. **Single comparison semantics**：CLI、未來 Streamlit view 與 D-3/D-4 只能消費同一個 comparison service，不得各自實作 diff。
4. **No inference**：缺少 edge、evidence 或 lineage 資料時，輸出 empty／unknown／unavailable，不能由 node name、filename 或時間順序猜測關係。
5. **Bounded output**：只輸出節點、edge、evidence ref 的 bounded identity、status、fingerprint、reason code 與變更類型，不暴露 raw payload、prompt、command、stdout/stderr、absolute path 或 secret。

## 4. Architecture

```text
immutable Graph snapshot A ─┐
                            ├─ GovernanceGraphComparisonService
immutable Graph snapshot B ─┘              │
                                           ├─ comparison JSON / CLI
                                           └─ future risk / impact read models
```

`GovernanceGraphComparisonService` 是 D-2 唯一 comparison read model。它應重用 D-1 的安全 snapshot loading、path containment、duplicate-key rejection、fingerprint validation 與 bounded serialization conventions；若需要抽出共用 reader，抽出只能是 read-only infrastructure，不改變 D-1 的 output contract。

## 5. Snapshot reference contract

### 5.1 Input

```json
{
  "left": {
    "runId": "run-before",
    "snapshotFingerprint": "<optional sha256>"
  },
  "right": {
    "runId": "run-after",
    "snapshotFingerprint": "<optional sha256>"
  }
}
```

Rules:

- `left` 與 `right` 必須同時存在；缺少任一 side 為 `invalid`。
- `runId` 必須是 safe single path component；不得包含 `/`、`\\`、`.`、`..` 或 path traversal。
- `snapshotFingerprint` 若提供，必須是 lowercase SHA-256，且必須與實際 snapshot 的 `graphFingerprint` 完全相同。
- `left` 與 `right` 可以指向同一 immutable snapshot；此時 comparison 是 deterministic zero-diff result，不視為錯誤。
- 不接受 `latest`、空字串、glob、模糊 query、timestamp range 或自動 run selection。

### 5.2 Output envelope

Schema 固定為 `governance-graph-comparison-v1`：

```json
{
  "schemaVersion": "governance-graph-comparison-v1",
  "status": "available",
  "leftSnapshot": {
    "runId": "run-before",
    "graphFingerprint": "<sha256>",
    "generatedAt": "<utc-iso8601>",
    "freshness": "fresh"
  },
  "rightSnapshot": {
    "runId": "run-after",
    "graphFingerprint": "<sha256>",
    "generatedAt": "<utc-iso8601>",
    "freshness": "fresh"
  },
  "comparisonFingerprint": "<sha256>",
  "summary": {
    "addedNodes": 0,
    "removedNodes": 0,
    "changedNodes": 0,
    "unchangedNodes": 0,
    "addedEdges": 0,
    "removedEdges": 0,
    "changedEdges": 0,
    "addedEvidenceRefs": 0,
    "removedEvidenceRefs": 0,
    "changedEvidenceRefs": 0
  },
  "nodeChanges": [],
  "edgeChanges": [],
  "evidenceChanges": [],
  "diagnostics": []
}
```

`comparisonFingerprint` 使用固定 canonical JSON serialization，涵蓋 normalized input references、left/right snapshot identity、summary 與 sorted changes。相同兩個 snapshot（含左右順序）必須產生相同 fingerprint；交換 left/right 必須產生不同 fingerprint。

## 6. Diff semantics

### 6.1 Nodes

Node identity key 固定為 `nodeId`。比較結果只允許以下 change types：

- `added`：只存在於 right snapshot。
- `removed`：只存在於 left snapshot。
- `changed`：兩側皆存在，但 `nodeType`、`status`、`reasonCode`、`attempt`、`maxAttempts`、`fingerprint` 或 normalized `evidenceRefs` 有差異。
- `unchanged`：兩側 bounded node record 完全相同；預設只計入 summary，不輸出到 `nodeChanges`，除非未來另立 approved include-unchanged contract。

每筆 node change 必須包含 `nodeId`、`changeType`，以及 bounded `before`／`after` record；removed 的 `after`、added 的 `before` 必須為 `null`。

### 6.2 Edges

若 canonical Graph snapshot schema 提供 edge records，edge identity key 固定為 `(source, target, type)`，並比較 `status` 與 `reasonCode`。若目前 schema 沒有 edge records，`edgeChanges` 必須保持空集合與零計數；不得由 node ordering、nodeId 命名或 artifact filename 推導 edge。

### 6.3 Evidence references

Evidence identity key 固定為 `(path, sha256)`；只接受 run-relative allowlisted evidence refs。比較 `status`、`generatedAt`／`finalizedAt` 與 bounded metadata：

- `added`／`removed` 表示 ref identity 只出現在一側。
- `changed` 表示 identity 相同但 status 或 lifecycle timestamp 改變。
- evidence 缺失不轉成 available，也不由 node status 虛構 evidence ref。

Evidence changes 必須按照 `path`、`sha256` 固定排序；不可輸出 absolute path 或 raw artifact content。

## 7. Status and error semantics

Comparison status precedence 固定為：

`invalid > unavailable > blocked > unknown > available`

- `available`：兩個 snapshot 都存在、schema valid、fingerprint consistent，comparison 可安全呈現。
- `unavailable`：任一明確 side 沒有已建 snapshot；不得 fallback 到其他 run。
- `invalid`：input contract、path containment、symlink、duplicate key、snapshot schema 或 fingerprint validation 失敗。
- `blocked`：兩個合法 snapshot 中存在明確 blocked node／edge／evidence，comparison 仍保留 bounded diff。
- `unknown`：snapshot 合法，但必要的 evidence／edge metadata 不足以判定某些變化。

Invalid 或 unavailable 時，不輸出另一側猜測的 diff；只保留合法可用的 snapshot identity（若已安全讀取）與 bounded diagnostics。任何 error result 都不得寫入 runtime 或補建 snapshot。

## 8. Components and boundaries

### 8.1 Comparison service

新增 `backend/agents/governance_graph_comparison_service.py`，提供明確的兩側輸入與 immutable result，例如：

```python
class GovernanceGraphComparisonService:
    def compare(
        self,
        *,
        left_run_id: str,
        right_run_id: str,
        left_snapshot_fingerprint: str | None = None,
        right_snapshot_fingerprint: str | None = None,
    ) -> GovernanceGraphComparisonResult: ...
```

新增 `backend/agents/governance_graph_comparison_models.py`，定義 input reference、snapshot identity、summary、change records、diagnostics 與 immutable result dataclasses。模型必須執行 schema allowlist、bounded metadata、safe strings 與 deterministic fingerprint validation。

### 8.2 CLI

擴充 `scripts/governance_graph.py compare`：

```bash
.venv/bin/python scripts/governance_graph.py compare \
  --left-run-id run-before \
  --right-run-id run-after \
  [--left-snapshot-fingerprint <sha256>] \
  [--right-snapshot-fingerprint <sha256>]
```

CLI 只呼叫 Comparison Service，輸出 `governance-graph-comparison-v1` JSON，不建立 snapshot、不修改 runtime、不讀取 SQLite 或 Git。

### 8.3 Future consumers

D-3 risk analysis 與 D-4 change impact analysis 只能消費 D-2 comparison result；它們不得重新讀取 raw artifacts 或自行比較 snapshot。D-2 不新增 Streamlit UI；UI 需求另立 scope，避免把 comparison semantics 與 presentation coupling。

## 9. Testing and acceptance

### 9.1 Tests

- 新增 `tests/test_governance_graph_comparison_models.py`：input contract、allowlist、bounded records、fingerprint、left/right order sensitivity。
- 新增 `tests/test_governance_graph_comparison_service.py`：same snapshot zero diff、added/removed/changed nodes、edge absence no-inference、evidence lifecycle changes、deterministic ordering、missing／invalid／blocked／unknown semantics、symlink／path traversal／duplicate-key rejection、no-write tree equality。
- 擴充 `tests/test_governance_graph_cli.py`：`compare` parser、exact JSON envelope、invalid input exit code、no snapshot creation。

### 9.2 Required verification

```bash
.venv/bin/python -m py_compile \
  backend/agents/governance_graph_comparison_models.py \
  backend/agents/governance_graph_comparison_service.py
.venv/bin/python -m pytest \
  tests/test_governance_graph_comparison_models.py \
  tests/test_governance_graph_comparison_service.py \
  tests/test_governance_graph_cli.py -q
.venv/bin/python -m pytest -q
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/hermes_post_change_check.py
```

Acceptance 必須另外確認：

- 同一 pair 與同一左右順序的 `comparisonFingerprint` 可重現；交換左右 side 會改變 fingerprint。
- missing／invalid snapshot 不會 fallback 或建立新 snapshot。
- comparison output 不含 raw payload、absolute path、secret 或未驗證 evidence。
- SQLite SHA、baseline `HKD 12,057,968` 與正式口徑「不含掛賬核銷與TT退款轉團款」不變。
- Review PASS、full verification PASS、Hermes PASS 後，才可進入 Phase D-3。

## 10. Future compatibility

D-2 result 必須保留兩側 immutable snapshot identity、comparison fingerprint、bounded node／edge／evidence changes 與 status counts，讓 D-3 可以針對已驗證的差異做 risk summary，讓 D-4 可以在另立 contract 後消費同一批差異；任何 dependency、risk 或 impact 語意都不在本 spec 內自行增加。
