# NBS Governance Graph Phase D-1 Query Read Model Design

狀態：approved for spec drafting  
日期：2026-07-28  
風險：R1 standard engineering

## 1. Goal

建立 Governance Graph 的 deterministic、read-only query read model，供 Streamlit Graph tab、CLI JSON 與後續 Phase D 的 snapshot version comparison、risk analysis、change impact analysis 共用。

本 Phase 只查詢已存在且通過 schema validation 的 Graph snapshot，不建立、更新、修復或推測 snapshot。

## 2. Non-goals

- 不新增 approval、dispatch、runner、runtime control-plane 或 background writer。
- 不直接讀取 SQLite、Git、raw runtime artifact 或未驗證 canonical evidence。
- 不新增 Graph snapshot builder 或 snapshot version writer。
- 不實作自然語言查詢、模糊搜尋、跨 run 自動推測、dependency inference、risk scoring 或 change impact analysis。
- 不修改 baseline、revenue scope、business rules、rollback 或 export schema。

## 3. Architecture

```text
canonical artifacts
        ↓
validated Graph snapshot
        ↓
GovernanceGraphQueryService
        ├── Streamlit Graph filters
        └── governance_graph.py query JSON
```

`GovernanceGraphQueryService` 是唯一 query read model；UI 與 CLI 不得自行解析 runtime artifact 或實作另一套 Graph filter semantics。Query service 只消費既有 `GovernanceGraphSnapshot` validation 與既有 Graph projection schema。

## 4. Query contract

### 4.1 Input filters

初版只支援 deterministic exact-match filters：

- `runId`
- `nodeType`
- `nodeStatus`
- `nodeId`
- `edgeType`
- `artifactKind`
- `evidenceStatus`
- `snapshotFingerprint`

所有 filter 都是 optional；未提供 filter 代表該維度不篩選。未知 filter key、錯誤型別、不安全 path component、超長字串或非法 enum 必須 fail closed。

### 4.2 Output

```json
{
  "schemaVersion": "governance-graph-query-v1",
  "status": "available",
  "snapshotIdentity": {
    "runId": "run-1",
    "graphFingerprint": "<sha256>",
    "generatedAt": "<utc-iso8601>",
    "freshness": "fresh"
  },
  "queryFingerprint": "<sha256>",
  "matchedNodes": [],
  "matchedEdges": [],
  "evidenceRefs": [],
  "unknownCount": 0,
  "invalidCount": 0,
  "blockedCount": 0,
  "diagnostics": []
}
```

`queryFingerprint` 使用固定 canonical JSON serialization，涵蓋 normalized filters 與 snapshot identity；相同 snapshot、相同 filters 必須產生相同 fingerprint，不同 filters 或 snapshot 必須產生不同 fingerprint。

### 4.3 Status semantics

- `available`：snapshot 存在、schema valid，query 結果可安全呈現。
- `unavailable`：指定或目前 run 沒有已建 snapshot。
- `invalid`：snapshot schema、fingerprint、path、filter 或 evidence reference 違反 contract。
- `unknown`：snapshot 合法，但必要欄位或 evidence 狀態不足以判定某些 query metadata。
- `blocked`：節點或 evidence 明確為 blocked；保留計數與節點狀態，不降級成 available。

Status precedence 固定為：`invalid > blocked > unknown > available`。Query 不得把 `unknown`、`invalid` 或 `blocked` 修正為正常值。

## 5. Components and boundaries

### 5.1 Query service

新增 `backend/agents/governance_graph_query_service.py`，提供：

```python
class GovernanceGraphQueryService:
    def query(
        self,
        *,
        run_id: str | None = None,
        node_type: str | None = None,
        node_status: str | None = None,
        node_id: str | None = None,
        edge_type: str | None = None,
        artifact_kind: str | None = None,
        evidence_status: str | None = None,
        snapshot_fingerprint: str | None = None,
    ) -> GovernanceGraphQueryResult: ...
```

Service 必須：

- 透過既有 Graph snapshot reader／schema model 讀取資料。
- 僅使用 run-contained、regular-file、safe path validation。
- 只回傳 bounded nodes、edges、evidence refs 與 diagnostics。
- 不暴露 prompt、command、stdout/stderr、absolute path、secret 或 raw payload。
- 保持 deterministic ordering：nodeId、edge source/target/type、evidence path 皆使用固定排序。

### 5.2 CLI

擴充 `scripts/governance_graph.py query`，接受相同 exact-match filters，輸出 `governance-graph-query-v1` JSON。CLI 只呼叫 Query Service，不得自行建立 snapshot 或寫入 runtime。

### 5.3 Streamlit

在現有 Graph tab 加入 read-only filters，透過 Agent Operations／Query Service 的 compact result 呈現。UI 不得直接讀 raw artifact，也不得在 filter、refresh 或 page load 時建立 snapshot。

## 6. Error handling and no-inference rules

- Snapshot missing：回傳 `unavailable`，不 fallback 到另一個 run。
- Snapshot malformed、duplicate key、symlink、path traversal、fingerprint mismatch：回傳 `invalid`。
- Evidence missing：保留 `unknown`，不推測 node 或 edge 關係。
- Evidence blocked：保留 `blocked`，不轉成 failed 或 available。
- Filter 不匹配：回傳 available 加空結果；不把空結果解讀成資料不存在或風險為零。
- Query service 不得建立 snapshot、修改 canonical artifacts、修改 SQLite、baseline、Git 或 runtime status。

## 7. Testing and acceptance

### 7.1 Tests

- 新增 `tests/test_governance_graph_query_service.py`：contract、filter、ordering、fingerprint、status precedence、missing/invalid/blocked/unknown。
- 擴充 `tests/test_governance_graph_cli.py`：query command、exact JSON、invalid args、no-write boundary。
- 擴充 `tests/test_agent_operations_rendering.py` 或 boundary tests：UI filters 只消費 compact query result，不呼叫 writer。

### 7.2 Required verification

```bash
.venv/bin/python -m py_compile backend/agents/governance_graph_query_service.py
.venv/bin/python -m pytest tests/test_governance_graph_query_service.py tests/test_governance_graph_cli.py -q
.venv/bin/python -m pytest -q
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/hermes_post_change_check.py
```

Acceptance 必須確認：

- query contract deterministic、bounded、read-only。
- 同一 snapshot／filters 的 `queryFingerprint` 可重現。
- snapshot missing 不會由 UI／CLI 自動建立。
- SQLite SHA、baseline `HKD 12,057,968` 與正式口徑「不含掛賬核銷與TT退款轉團款」不變。
- Review PASS、full verification PASS、Hermes PASS 後才可進入下一個 Phase D sub-project。

## 8. Future compatibility

Query result 必須保留 `snapshotIdentity` 與 `queryFingerprint`，讓後續 version comparison 能比較兩個 immutable snapshot；保留 status counts 與 evidence refs，讓後續 risk／impact analysis 消費已驗證結果，而不需要重新讀取 raw artifacts。
