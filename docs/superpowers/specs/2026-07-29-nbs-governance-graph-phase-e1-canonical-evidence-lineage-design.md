# NBS Governance Graph Phase E-1 Canonical Evidence Lineage Design

狀態：approved for spec review  
日期：2026-07-29  
風險：R1 standard engineering（只讀 evidence projection，不改正式資料）

## 1. Goal

建立 deterministic、read-only 的 `GovernanceGraphEvidenceLineageService`，將 Governance
Graph node、D-3 risk finding、D-4 impact observation 與其可驗證的 canonical evidence identity
串成 bounded lineage，支援 evidence identity deep drill-down。

E-1 的目的不是公開 evidence raw payload，而是回答：

```text
這個 node / finding / impact 的 evidence identity 是什麼？
它是否存在、是否 finalized、fingerprint 是否一致、由哪個 registry entry 定義？
```

## 2. Scope and non-goals

### In scope

- 驗證 explicit run reference、Graph node identity 與 evidence reference identity。
- 重用既有 `GovernanceGraphSnapshotReader`、`GovernanceEvidenceRef`、
  `GovernanceCanonicalEvidenceRef`、`CanonicalEvidenceReader` 與 `CanonicalEvidenceRegistry`。
- 產生 bounded evidence lineage read model：source identity、evidence identity、artifact kind、
  schema version、writer metadata、status、reason、finalizedAt 與 fingerprint match state。
- 支援 Graph node → evidence ref、risk/impact source finding → explicit evidence identity 的
  bounded links。
- 對 missing、unknown、invalid、blocked、stale、duplicate、fingerprint mismatch 做 fail-closed
  分類。
- 提供 stdin-only CLI 與後續 Streamlit drill-down 可共用的 service contract。

### Explicit non-goals

- 不回傳 canonical artifact raw JSON、prompt、command、stdout/stderr、secret、完整 log、SQLite
  rows、Excel 或 absolute filesystem path。
- 不由 nodeId、findingId、filename、generatedAt、snapshot 順序或 comparison fingerprint 猜測
  evidence 關係。
- 不從 D-3／D-4 空的 `evidenceIdentities` 反查 D-2 snapshot 或 raw artifact 補資料。
- 不建立、更新、修復、寫回或重建 Graph snapshot、canonical artifacts、runtime、SQLite、baseline、
  cache、Git 或 Obsidian。
- 不改變 D-3 risk rules、D-4 impact rules、Review、Hermes 或 Documentation authority。
- 不實作 Streamlit UI；E-2 才負責 presentation。

## 3. Design alternatives

### Option A：Snapshot pass-through only

只回傳 Graph 已存在的 `path`、`sha256`、status 與時間。改動最小，但無法確認 canonical
registry metadata、envelope finalized state 或 reference 是否仍與實際 artifact 一致。

### Option B：Explicit reference + bounded canonical envelope（推薦）

caller 必須提供明確 run／node／evidence identity；service 透過既有安全 reader 讀取並驗證
canonical envelope，只輸出 registry metadata 與 compact lineage。這能提供真正 drill-down，
同時維持 no-raw、read-only、fail-closed 邊界。

### Option C：完整 artifact introspection

回傳 canonical evidence payload 的選定欄位。可讀性最高，但容易擴張 schema、暴露敏感資料，
也會讓 E-1 變成第二個 artifact API。排除在 E-1 scope 外。

E-1 採 Option B。

## 4. Architecture and authority

```text
explicit run + source identity + evidence identity
                         │
                         ▼
GovernanceGraphEvidenceLineageService
        │                │                 │
        ▼                ▼                 ▼
 Graph snapshot     CanonicalEvidenceReader  Registry
        │                │                 │
        └──────── bounded lineage-v1 ───────┘
```

Canonical artifact 與 Graph snapshot 仍是真相來源；lineage result 是衍生 read model。Service
不得成為 approval、dispatch、runtime、SQLite、Git 或任何 writer 的入口。

所有 output 必須由 canonical JSON serialization 產生 deterministic fingerprint。相同 explicit
input、artifact bytes、registry version 與 policy version 必須 byte-for-byte 相同。

## 5. Input contract

E-1 接受 wrapper envelope `governance-graph-evidence-lineage-input-v1`：

```json
{
  "schemaVersion": "governance-graph-evidence-lineage-input-v1",
  "runId": "run-123",
  "snapshotFingerprint": "<optional sha256>",
  "source": {
    "kind": "node",
    "identity": "protected_incident"
  },
  "evidence": {
    "path": "protected-incident.json",
    "sha256": "<sha256>"
  }
}
```

Rules：

- `runId` 必須是 safe single path component；不得含 `/`、`\\`、`.`、`..`、glob 或 absolute path。
- `snapshotFingerprint` 若提供，必須與實際 immutable Graph snapshot `graphFingerprint` 完全一致。
- `source.kind` 只允許 `node`、`finding`、`impact`；`identity` 必須是 bounded safe identity。
- `evidence.path` 必須是 CanonicalEvidenceRegistry-owned、run-relative、regular file、registry allowlisted artifact；不得使用 D1 legacy filename alias，也不得是 symlink。一般 Graph artifact（例如 `hermes.json`）不是 E-1 canonical evidence，不能冒充 registry evidence。
- `evidence.sha256` 必須是 lowercase SHA-256；不得以 path、filename、node name 或 source identity
  代替 fingerprint。
- 不接受任意 path、raw payload、`--approve`、`--dispatch`、`--writer`、`--apply`、SQLite 或 Git
  flags。
- 缺少 `evidence` 時只可產生 `missing`／`unknown` result；不得自動從 source identity 推導 ref。

## 6. Lineage semantics

### 6.1 Explicit link types

E-1 v1 只接受以下可驗證 link：

- `node_evidence`：Graph canonical-evidence node 的 evidence ref 與 explicit evidence identity 一致。
- `finding_evidence`：D-3 finding 已明確提供的 evidence identity；空陣列保持 `missing`。
- `impact_evidence`：D-4 impact pass-through 的 evidence identity；不得由 impact category 猜測。

每條 link 必須保存 `source`、`evidenceIdentity`、`relation`、`status` 與 bounded evidence ref。
沒有 explicit link 時，輸出 `unknown`／`missing`，不輸出虛構 relation。

### 6.2 Evidence state precedence

固定 precedence：`invalid > fingerprint_mismatch > blocked > stale > unknown > missing > available`。

- `available`：registry、envelope、run binding、sha256、finalized state 全部一致。
- `missing`：明確指定的 registry artifact 不存在。
- `unknown`：上游只提供 source identity，沒有足夠 evidence identity 或 canonical state。
- `blocked`：evidence envelope 明確 blocked 或 upstream governance node blocked。
- `stale`：artifact fingerprint／manifest Git identity 與 Graph snapshot freshness 不一致。
- `fingerprint_mismatch`：explicit sha256 與實際 artifact bytes／envelope fingerprint 不一致。
- `invalid`：schema、path containment、symlink、duplicate key、registry 或 run binding 失敗。

E-1 不把 `unknown`、`missing` 或 `blocked` 轉成「無 evidence」以外的正面結論，也不自動修復。

## 7. Output contract

Schema 固定為 `governance-graph-evidence-lineage-v1`：

```json
{
  "schemaVersion": "governance-graph-evidence-lineage-v1",
  "status": "available",
  "lineagePolicyVersion": "e1-canonical-evidence-lineage-v1",
  "runId": "run-123",
  "snapshotFingerprint": "<sha256>",
  "source": {"kind": "node", "identity": "protected_incident"},
  "evidence": [{
    "path": "protected-incident.json",
    "sha256": "<sha256>",
    "artifactKind": "protected_incident",
    "schemaVersion": "governance-canonical-evidence-v1",
    "writer": "protected_incident_recorder",
    "status": "available",
    "reasonCode": null,
    "finalizedAt": "2026-07-29T08:00:00+00:00",
    "fingerprintMatched": true
  }],
  "links": [
    {"relation": "node_evidence", "sourceIdentity": "protected_incident", "evidencePath": "protected-incident.json", "evidenceSha256": "<sha256>"}
  ],
  "diagnostics": [],
  "lineageFingerprint": "<sha256>"
}
```

Invalid／unavailable／unknown results 不得產生假 fingerprint：若 run 或 evidence identity
無法信任，`snapshotFingerprint` 與 `lineageFingerprint` 必須為 `null`；已驗證的 `available`、
`blocked`、`stale`、`fingerprint_mismatch` result 才可保留 source fingerprint 並計算 lineage
fingerprint。

Output invariants：

- 所有 fields bounded；不輸出 raw envelope、absolute path、secret、runner command 或 full log。
- links 依 `(relation, sourceIdentity, evidencePath, evidenceSha256)` 固定排序。
- `evidence` 最多 12 筆；`lineageFingerprint` 覆蓋 schema、policy、status、run／snapshot identity、source、evidence、
  sorted links 與 diagnostics，排除 fingerprint 欄位本身。
- 一份 result 只描述一個 explicit source 與最多 12 筆其 evidence refs；batch query 另立 contract。

## 8. Components and agent boundaries

- `backend/agents/governance_graph_evidence_lineage_models.py`：immutable input、evidence detail、link、diagnostic、result models 與 fingerprint。
- `backend/agents/governance_graph_evidence_lineage_service.py`：只讀 snapshot／canonical reader adapter、registry validation、state precedence、bounded projection。
- `scripts/governance_graph.py evidence-lineage`：stdin-only wrapper JSON；不得接受 path／writer／control flags。
- Future E-2 Streamlit：只呼叫此 service；不自行讀 raw evidence 或套用 state rules。

Agent boundaries：Context／Review／Hermes 保持 read-only；Implementation Agent 只執行一個
approved Task；Documentation Agent 不消費或回填 lineage，除非另有 approved documentation contract。

## 9. Testing and acceptance

必須測試：

- exact input／output keys、schema、bounded identity、registry allowlist、path traversal、symlink、duplicate JSON key rejection。
- available node_evidence：Graph ref、registry metadata、envelope fingerprint 與 finalized state 一致。
- finding／impact 空 evidence identity → `missing`／`unknown`，不得回讀或猜測。
- missing、unknown、blocked、stale、fingerprint mismatch、invalid precedence matrix。
- explicit sha256 mismatch、run binding mismatch、snapshot fingerprint mismatch。
- raw payload／absolute path／secret／runner command 不會出現在 output。
- repeated input byte-for-byte fingerprint；links deterministic ordering；service／CLI 前後 no-write tree equality。
- stdin-only CLI success、malformed／empty input exit code `2`，並拒絕 `--run-id`、`--path`、`--approve`、`--dispatch`、`--writer`。

Acceptance：

```bash
.venv/bin/python -m py_compile \
  backend/agents/governance_graph_evidence_lineage_models.py \
  backend/agents/governance_graph_evidence_lineage_service.py \
  scripts/governance_graph.py
.venv/bin/python -m pytest \
  tests/test_governance_graph_evidence_lineage_models.py \
  tests/test_governance_graph_evidence_lineage_service.py \
  tests/test_governance_graph_cli.py -q
.venv/bin/python -m pytest -q
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/hermes_post_change_check.py
```

Completion requires strict Review PASS、full pytest PASS、system acceptance PASS、Hermes PASS、
clean worktree，以及確認 SQLite、baseline `HKD 12,057,968`、正式口徑「不含掛賬核銷與TT退款轉團款」
未改變。E-1 不自動建立 snapshot，也不在 acceptance 中修復 runtime state。

## 10. Future compatibility

E-1 只建立 evidence lineage authority。E-2 UI、E-3 owner／dependency catalog、E-4 management
summary 必須消費此 read model，不得重新讀 raw artifacts 或複製 state semantics。若要回傳
canonical envelope 選定 payload 欄位、批次 lineage、owner 或自然語言解釋，必須另立 approved contract。
