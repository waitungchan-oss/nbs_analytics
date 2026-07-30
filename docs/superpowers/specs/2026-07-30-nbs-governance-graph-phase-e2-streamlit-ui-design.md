# NBS Governance Graph Phase E-2 Streamlit UI Design

狀態：approved for design implementation planning  
日期：2026-07-30  
風險：R1 standard engineering（只讀 presentation／callback wiring，不改正式資料）

## 1. Goal

在既有 Streamlit `Agent Operations` 的 Governance Graph section 擴充安全、可追溯的
read-only 操作體驗，讓工程使用者能查看：

- 已存在 Graph snapshot 的 bounded summary 與 D-1 exact-match query。
- 單一 canonical-evidence Graph node 的 E-1 evidence identity drill-down。
- D-2 snapshot comparison、D-3 risk summary、D-4 change impact 的資料狀態；若目前
  `agent-operations-snapshot-v1` 沒有對應 read model，畫面必須如實顯示 unavailable，
  不得由 UI 自行重跑、推導或修補。

E-2 是 presentation layer 與 callback wiring，不是新的 Graph、evidence、approval、
dispatch、runtime 或 database authority。

## 2. Product decision and scope

### 2.1 Confirmed product choice

- 保留既有 `Agent Operations` 入口與 selected-run workflow。
- 將 Graph UI 邏輯集中到獨立 read-only rendering helper，由既有 page 注入 callbacks。
- 不新增 top-level Streamlit tab、FastAPI endpoint、Vue page、background polling 或獨立 app。
- 使用既有 `AGENT_OPERATIONS_SNAPSHOT` session cache 與
  `AGENT_OPERATIONS_SELECTED_RUN_ID` selection；不得建立第二份 snapshot authority。

### 2.2 E-2 in scope

- Graph summary、freshness、lineage node table、blocker／diagnostic table。
- D-1 exact-match query filters 與 bounded query result。
- 從 canonical-evidence Graph node 選取 explicit `(runId, nodeId, path, sha256,
  snapshotFingerprint)`，由 app layer 呼叫 E-1 service 的 evidence lineage callback。
- Evidence lineage bounded drill-down：artifact basename、registry artifact kind、
  schema version、writer、status、reason、finalizedAt、fingerprint match 與 relation。
- 安全的 missing、unknown、blocked、stale、fingerprint mismatch、invalid、unavailable
  states 與單一 drill-down failure isolation。
- E-2 reserved panels／status slots：在明確 read model 尚未注入時，D-2、D-3、D-4
  顯示 unavailable；若未來由 approved callback 提供對應 validated result，UI 只渲染
  bounded result，不複製其語意。

### 2.3 Explicit non-goals

- 不從 UI 呼叫 `GovernanceGraphBuilder.build()`、`persist()`、CLI、subprocess、writer、
  `CanonicalEvidenceWriter` 或任何 approval／dispatch／repair／prune path。
- 不直接讀取 runtime JSON、canonical artifact、SQLite、Git、Obsidian、raw payload 或完整 log。
- 不由 node ID、finding ID、impact category、filename、順序或 timestamp 推導 evidence、
  dependency、risk 或 impact 關係。
- 不把 E-1 lineage result 寫入 `st.session_state` 作為新的 authority；最多保存 bounded
  selected-evidence key 供 rerun UX。
- 不在 E-2 實作 D-2 comparison、D-3 risk rules 或 D-4 impact rules；這些只透過已批准
  read-model callback 消費。
- 不修改 SQLite、baseline、revenue scope、business rules、rollback、export schema、
  workflow status、Graph snapshot 或 canonical artifacts。

## 3. Existing context and authority

```text
AgentOperationsService.build_snapshot()
        │
        ▼
agent-operations-snapshot-v1 + compact governanceGraph
        │                         │
        │                         └─ selected canonical evidence identity
        ▼                                      │
agent_operations_rendering.py ◄── callbacks ──┘
        │                                      │
        ▼                                      ▼
Streamlit Agent Operations             E-1 lineage service
```

- `AgentOperationsService` 是 UI 用的 runtime-artifact reader；renderer 不讀檔。
- E-2 允許對既有 compact `governanceGraph` read model 做一個 allowlisted additive extension：
  `snapshotFingerprint` 必須由已驗證 `GovernanceGraphSnapshot.graph_fingerprint` 原樣帶出；
  renderer 不計算、猜測或從檔案讀取 fingerprint。
- `GovernanceGraphQueryService` 是 D-1 query authority；renderer 不另寫 filters。
- `GovernanceGraphEvidenceLineageService` 是 E-1 evidence lineage authority；renderer 不
  讀 canonical raw artifact 或自行驗證 SHA。
- Canonical artifacts 是真相來源；Graph snapshot 是衍生、只讀 snapshot；UI 是第三層
  presentation projection。
- Review Agent、Hermes、Documentation Agent 的權責不因 E-2 改變。

## 4. Architecture

### 4.1 Components

| Component | E-2 responsibility | Forbidden responsibility |
|---|---|---|
| `backend/services/agent_operations_service.py` | 維持既有 compact Graph snapshot read model | 不提供 raw artifact 或 writer callback |
| `agent_operations_rendering.py` | Render Graph panels、filters、bounded lineage result；建立 explicit request data | 不讀檔、不呼叫 CLI、不實作 E-1/D-2/D-3/D-4 semantics |
| `app_pages.py` | 將 query／lineage 與 future validated read-model callbacks 注入 renderer | 不把 callback 變成 approval／dispatch／write path |
| `GovernanceGraphQueryService` | D-1 exact query | 不由 UI 改寫 query rules |
| `GovernanceGraphEvidenceLineageService` | E-1 node-to-canonical-evidence lineage | 不由 UI傳入 arbitrary path 或 raw payload |
| future comparison/risk/impact adapters | 只提供已驗證 D-2/D-3/D-4 bounded result | E-2 不反查或推導缺失資料 |

### 4.2 Rendering boundary

建議新增一個 focused helper，例如 `governance_graph_rendering.py`，負責：

- `render_governance_graph_workspace(...)`
- `render_graph_query_panel(...)`
- `render_evidence_lineage_panel(...)`
- `render_derived_analysis_status(...)`

既有 `render_agent_operations(...)` 只負責維持頁面 lifecycle、selected run 與 callback
注入。若實作期間現有 helper 已足夠小，則可保留檔案並只抽出必要函式；不得為了 UI
美化進行無關重構。

## 5. Data contracts and callback interfaces

### 5.1 Query callback

沿用既有 callback 形狀：

```python
query_graph(run_id: str, filters: dict[str, str | None]) -> dict[str, Any]
```

Callback 必須回傳 `governance-graph-query-v1` bounded result；renderer 只顯示
`status`、snapshot identity、matched counts、bounded nodes／evidence refs 與 diagnostics。
Malformed result 顯示 `invalid`，不得把 exception、raw response 或 absolute path 寫到頁面。

### 5.2 E-1 lineage callback

E-2 只從已呈現的 canonical-evidence Graph node row 建立 explicit request：

```python
lineage_lookup(request: dict[str, Any]) -> dict[str, Any]
```

Request 必須完全符合：

```json
{
  "schemaVersion": "governance-graph-evidence-lineage-input-v1",
  "runId": "run-123",
  "snapshotFingerprint": "<graphFingerprint>",
  "source": {"kind": "node", "identity": "protected_incident"},
  "evidence": {
    "path": "protected-incident.json",
    "sha256": "<sha256>"
  }
}
```

Rules：

- 只允許 `CanonicalEvidenceRegistry` owned canonical filenames：`task-gate.json`、
  `terra-diagnosis.json`、`protected-incident.json`。
- 一般 Graph artifacts，例如 `hermes.json`、`review.json`，不可冒充 E-1 canonical evidence；
  UI 顯示 disabled／informational state，不送出 lookup。
- `runId`、snapshot fingerprint、path、SHA 必須來自同一 selected run 的 validated
  compact Graph result；renderer 不重新讀檔或計算 fingerprint。
- `lineage_lookup` 的 result 必須是 `governance-graph-evidence-lineage-v1`；結果不寫入
  session state。只保存下方定義的 bounded selection mapping，不保存 lineage result。

### 5.2.1 Compact snapshot extension

`AgentOperationsService._compact_governance_graph()` 的 E-2 additive output 固定增加：

```json
{
  "status": "available",
  "snapshotFingerprint": "<validated graphFingerprint>",
  "overallStatus": "awaiting_authorization",
  "freshness": "fresh",
  "nodeStatuses": [],
  "blockers": [],
  "diagnostics": [],
  "evidence": []
}
```

`snapshotFingerprint` 只在 source snapshot 已通過 `GovernanceGraphSnapshot.from_dict()`
時出現；`unavailable`、`invalid`、`blocked` result 不得填入猜測 fingerprint。既有
`agent-operations-snapshot-v1` consumers 必須接受此 additive field，其他 compact semantics
保持不變。

### 5.3 Derived analysis callbacks

E-2 可預留 optional callbacks，但不得在沒有資料時自行呼叫 D-2/D-3/D-4 service：

```python
comparison_lookup(...) -> dict[str, Any] | None
risk_summary_lookup(...) -> dict[str, Any] | None
impact_lookup(...) -> dict[str, Any] | None
```

若 callback 未提供或 Agent Operations snapshot 沒有 validated result，畫面顯示：

```text
狀態：unavailable；目前沒有可供 UI 消費的 validated read model。
此頁不會自行建立 snapshot、重跑分析或推導關係。
```

不能把缺少 D-2/D-3/D-4 result 顯示為 zero changes、low risk、no impact 或 PASS。

## 6. UI information architecture

Selected run 的 Governance Graph workspace 依固定順序顯示：

1. **Graph summary**：overall status、freshness、snapshot fingerprint short form。
2. **Lineage**：node ID、node type、status、reason code；只顯示 Graph snapshot 已有節點。
3. **Query**：D-1 exact-match filters，結果顯示 matched nodes／evidence refs／unknown、
   invalid、blocked counts。
4. **Canonical evidence lineage**：先選 canonical-evidence node，再顯示 explicit identity
   與 E-1 bounded drill-down；不可下載 raw artifact。
5. **Comparison / risk / impact status**：若有 validated callback result，顯示其 bounded
   summary；否則顯示 unavailable，不顯示推導結論。
6. **Blockers and diagnostics**：只顯示 safe code／bounded summary，不顯示 exception payload。

UI control semantics：

- `Refresh` 只使用既有 `on_refresh`，重建 Agent Operations compact snapshot；不建立 Graph snapshot。
- Query filter change 只觸發 Streamlit rerun；不清除 dashboard、upload、AI、export caches。
- Selected evidence 只保留 bounded identity key；selected run 改變時清除不相容 identity。
- 不提供 Build、Persist、Approve、Dispatch、Repair、Apply、Delete、Prune、Download Raw 或 Git buttons。

## 7. State and error semantics

| Condition | UI state | Rendering rule |
|---|---|---|
| Valid Graph snapshot | `available` | Render summary and lineage |
| No Graph snapshot | `unavailable` | Explain missing snapshot; no build action |
| E-1 evidence unavailable | `missing`／`unknown` | Show bounded reason; no inferred relation |
| Evidence blocked/stale | `blocked`／`stale` | Warning; never render as PASS |
| SHA/snapshot mismatch | `fingerprint_mismatch` | Warning; clear incompatible selection |
| Invalid schema/path/symlink/reader result | `invalid` | Isolate drill-down; keep other panels usable |
| D-2/D-3/D-4 result absent | `unavailable` | Explicitly state no validated read model |
| Malformed callback payload | `invalid` | Safe message; no raw exception or payload |

單一壞 run、壞 query、壞 lineage result 或壞 future adapter 不得 white-screen 其他
`Agent Operations` panels 或其他 runs。

## 8. Security and governance constraints

- 所有 displayed text 必須 bounded、safe；禁止 absolute filesystem path、raw JSON、prompt、
  command、stdout/stderr、secret、完整 log、SQLite row、Excel row。
- E-1 lineage result 最多顯示其 contract 允許的 12 筆 evidence refs；UI 不擴大 cap。
- UI 不把 Graph status 轉換成 Review PASS、Full Verification PASS 或 Hermes PASS。
- UI 不改變正式口徑「不含掛賬核銷與TT退款轉團款」與 2026-05 baseline `HKD 12,057,968`。
- 所有 callbacks 必須 read-only；禁止 service management、subprocess、Git、SQLite、runtime
  或 canonical writer。
- Streamlit session state 只保存 UX selection，不保存新的 canonical／approval／analysis authority。
- selected evidence 使用固定 key `AGENT_OPERATIONS_SELECTED_EVIDENCE`，value 只能是 bounded
  mapping `{runId, nodeId, path, sha256, snapshotFingerprint}`；不可保存 lineage result、raw
  payload 或 diagnostics。selected run 改變、snapshot fingerprint 改變、node 不再存在或
  canonical artifact gating 失敗時，renderer 必須 `pop` 此 key。

## 9. Test strategy

### 9.1 Rendering tests

新增或拆分 focused tests 覆蓋：

- valid Graph summary、lineage table、D-1 query panel。
- canonical node selection 產生 exact E-1 request；一般 Graph artifact disabled。
- E-1 `available`、`missing`、`unknown`、`blocked`、`stale`、`fingerprint_mismatch`、
  `invalid` rendering。
- evidence cap、bounded metadata、no raw／absolute path／secret leak。
- malformed compact Graph、query、lineage、future comparison/risk/impact result 不 white-screen。

### 9.2 Page/callback tests

- `app_pages.py` 注入 query／lineage callbacks，不直接讀檔或呼叫 CLI／subprocess／builder。
- selected run、selected evidence key、Refresh 與 existing session cache lifecycle 保持一致。
- missing snapshot 不會建立 runtime directory 或 Graph projection。
- future D-2/D-3/D-4 callbacks 未提供時顯示 unavailable，不推導 zero／low risk／no impact。
- 一個 broken run 不會隱藏其他 valid runs；D-1 query output contract 不變。

### 9.3 No-write and integration tests

測試前後比較 tracked worktree、runtime、Graph snapshot、canonical artifacts、SQLite 與
Git state；確認 E-2 renderer／callbacks 沒有 writer side effect。既有 Agent Operations、
Governance Graph、query、comparison、risk、impact 與 Hermes tests 必須保持通過。

## 10. Acceptance gates

最少執行：

```bash
.venv/bin/python -m py_compile agent_operations_rendering.py app_pages.py
.venv/bin/python -m pytest \
  tests/test_agent_operations_rendering.py \
  tests/test_governance_graph_cli.py \
  tests/test_governance_graph_evidence_lineage_models.py \
  tests/test_governance_graph_evidence_lineage_service.py \
  tests/test_app_pages_governance_graph.py -q
.venv/bin/python -m pytest -q
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/hermes_post_change_check.py
```

完成條件：

- strict Review Agent PASS，且 immutable diff／allowlist 可追溯。
- full pytest、system acceptance、Hermes PASS；timeout、degraded、unknown 一律阻擋完成宣稱。
- Git tracked worktree clean；SQLite integrity、baseline、正式口徑、runtime writer boundary 未改變。
- D-1 query、E-1 lineage output schema 未被 UI 重新定義或複製。
- E-1 models/service focused tests、`app_pages.py` callback-boundary tests 與 explicit
  renderer no-write integration tests 均通過。
- E-2 不自動建立 Graph snapshot；沒有 snapshot 時仍顯示 unavailable。

## 11. Future compatibility

E-2 的 rendering helper 與 callbacks 為後續 E-3 owner／dependency catalog、E-4 management
summary 的 presentation foundation。未來任何 D-2/D-3/D-4 panel 必須先提供 approved,
validated, bounded read model；UI 不可反查 raw artifacts、重新計算 risk 或自行建立 impact
relationship。若要加入 batch lineage、自然語言查詢、raw artifact download、owner/dependency
inference 或 export，必須另立 contract 與 approval gate。
