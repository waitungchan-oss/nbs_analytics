# NBS Governance Graph Phase C Task 3 Canonical Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 Task Gate、Terra diagnosis、protected incident 的 canonical evidence v1 writer／reader contract，讓 Agent Operations、Telemetry 與 Governance Graph 只消費已驗證 evidence，並在缺失或違規時 fail closed。

**Architecture:** 新增 code-owned canonical evidence models、registry、immutable final writer 與 safe reader；writer 只寫 run-contained allowlisted artifact，並以同 run `approval.json` 的 `contractFingerprint` 綁定 authority。`AgentOperationsService` 是唯一 runtime reader，將 bounded evidence compact result 傳給既有 Telemetry aggregation；Governance Graph builder 只把 validated evidence 投影為衍生 nodes，不能回寫 canonical truth。由於目前沒有既有 Task Gate／Terra／protected incident writer，本 Task 先建立明確 writer entrypoints 與 contract tests，未經另行批准的 runner 不會自動產生 evidence。

**Tech Stack:** Python 3、dataclasses、JSON canonical serialization、pathlib、既有 `WorkflowStore`、`WorkflowApproval`、`GovernanceGraphSnapshot`、pytest、Streamlit Agent Operations、Hermes。

## Global Constraints

- Canonical evidence 是唯一 truth；Graph、Telemetry、UI 與 analysis layer 永遠是 read-only derived consumers。
- 三類 artifact 固定檔名：`task-gate.json`、`terra-diagnosis.json`、`protected-incident.json`；同一 run/kind 只允許一份 immutable final artifact。
- `contractFingerprint` 必須等於同 run 已驗證 `approval.json` 的 contract fingerprint；artifact 自行宣告不能提升 authority。
- `evidenceFingerprint` 使用移除自身欄位後的 UTF-8 canonical JSON：`ensure_ascii=false`、`sort_keys=true`、`separators=(",", ":")`。
- read-state precedence 固定為：安全/schema/fingerprint violation=`invalid`；合法 terminal blocked=`blocked`；retained run 缺檔或未 finalization=`unknown`；完整 terminal artifact=`available`。
- retention denominator 只包含 retained 且通過 eligibility validation 的 runs；已移除的整個 run 不加入 v1 denominator。
- 不修改 SQLite、baseline、revenue scope、business rules、upload、rollback、export schema 或 Git；2026-05 baseline 固定 `HKD 12,057,968`。
- 不新增 approval、dispatch、runner、repair、retry routing、API、database、daemon、polling、background writer 或 UI action。
- 每個 Task 依序 TDD、focused tests、`git diff --check`、immutable strict Review；Review PASS 後才進下一 Task。

---

## File Structure

| Path | Responsibility |
|---|---|
| `backend/agents/canonical_evidence_models.py` | Envelope、kind-specific payload、lifecycle、read result 與 canonical fingerprint validation。 |
| `backend/agents/canonical_evidence_registry.py` | Code-owned writer／kind／filename／contract binding／status-reason／payload allowlists。 |
| `backend/agents/canonical_evidence_writer.py` | Run-contained immutable final write、approval binding、duplicate detection、atomic create-only primitive。 |
| `backend/agents/canonical_evidence_reader.py` | 唯一 safe reader；驗證 regular file、containment、approval binding、schema、fingerprint 與 bounded compact output。 |
| `backend/services/agent_operations_service.py` | 將每個 run 的 canonical evidence compact result 納入 Agent Operations snapshot；不自行掃描或寫入。 |
| `backend/services/governance_telemetry_aggregation.py` | 消費已驗證 compact evidence，填入 Task Gate／Terra／protected incident exact／unknown／invalid／blocked metrics。 |
| `backend/agents/governance_graph_service.py` | 在既有 projection builder 中投影 validated evidence nodes；只透過既有 projection writer 寫 derived snapshot。 |
| `tests/test_canonical_evidence_models.py` | Envelope、allowlist、lifecycle、canonical hash 與 enum/cap contract。 |
| `tests/test_canonical_evidence_writer.py` | Writer authority、approval binding、atomic create-only、duplicate、no-write boundary。 |
| `tests/test_canonical_evidence_reader.py` | Safe path、malformed／invalid／unknown／blocked isolation 與 compact redaction。 |
| `tests/test_agent_operations_service.py` | Agent Operations evidence integration、retention denominator、no-write regression。 |
| `tests/test_governance_telemetry_service.py` | 三類 evidence metric exact／unknown／invalid／blocked aggregation。 |
| `tests/test_governance_graph_service.py` | Evidence nodes、lineage、projection compatibility 與 projection no-inference。 |
| `tests/test_app_module_boundaries.py` | UI 不直接讀 runtime、不呼叫 writer、不新增 control-plane action。 |

### Shared interfaces

Task 1 產生：

```python
class CanonicalEvidenceEnvelope:
    @classmethod
    def from_dict(cls, payload: dict[str, Any], *, expected_kind: str) -> "CanonicalEvidenceEnvelope": ...
    def to_dict(self) -> dict[str, Any]: ...
    def canonical_fingerprint(self) -> str: ...

class CanonicalEvidenceRegistry:
    @staticmethod
    def entry_for_kind(kind: str) -> dict[str, str]: ...
    @staticmethod
    def validate_writer_binding(envelope: CanonicalEvidenceEnvelope, approval: dict[str, Any]) -> None: ...
```

Task 2 產生：

```python
class CanonicalEvidenceWriter:
    def write_final(self, run_id: str, envelope: CanonicalEvidenceEnvelope) -> Path: ...

class CanonicalEvidenceReader:
    def read(self, run_id: str, kind: str) -> dict[str, Any]: ...
```

`read()` 只回傳 bounded compact result，狀態固定為 `available`、`unknown`、`invalid` 或 `blocked`，不得回傳 raw payload、absolute path、prompt、command、stdout/stderr 或 secret。

---

### Task 1: Canonical evidence models、registry 與 deterministic schema

**Files:**

- Create: `backend/agents/canonical_evidence_models.py`
- Create: `backend/agents/canonical_evidence_registry.py`
- Create: `tests/test_canonical_evidence_models.py`

**Consumes:** Approved spec §3–§5；既有 `WorkflowApproval.from_dict()`、`WorkflowStore` containment conventions 與 `canonical_sha256()`。

**Produces:** 可由 writer、reader、Graph 與 Telemetry 共用的 immutable envelope contract；此 Task 不寫 runtime。

- [ ] **Step 1: Write failing model and registry tests.**

  在 `tests/test_canonical_evidence_models.py` 建立 exact fixtures，覆蓋：

  ```python
  import hashlib, json

  def _sha256_without_evidence_fingerprint(payload):
      unsigned = dict(payload)
      unsigned.pop("evidenceFingerprint")
      return hashlib.sha256(json.dumps(
          unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
      ).encode("utf-8")).hexdigest()

  def _task_gate_payload(**overrides):
      payload = {
          "schemaVersion": "governance-canonical-evidence-v1",
          "artifactKind": "task_gate", "runId": "run-1",
          "writer": "task_gate_writer", "writerVersion": "1.0.0",
          "contractFingerprint": "a" * 64, "status": "passed", "reasonCode": None,
          "lifecycle": {"createdAt": "2026-07-28T00:00:00Z", "startedAt": "2026-07-28T00:00:01Z", "decidedAt": "2026-07-28T00:00:02Z", "finalizedAt": "2026-07-28T00:00:02Z"},
          "evidenceFingerprint": "0" * 64,
          "payload": {"taskId": "task-1", "decision": "passed", "requiredEvidenceKinds": ["implementation"], "missingEvidenceKinds": []},
      }
      payload.update(overrides)
      return payload

  def test_task_gate_envelope_accepts_approved_contract_and_canonicalizes():
      envelope = CanonicalEvidenceEnvelope.from_dict(_task_gate_payload(), expected_kind="task_gate")
      assert envelope.canonical_fingerprint() == _sha256_without_evidence_fingerprint(envelope.to_dict())

  def test_unknown_reason_status_or_payload_enum_is_invalid():
      with pytest.raises(CanonicalEvidenceSchemaError):
          CanonicalEvidenceEnvelope.from_dict(_task_gate_payload(reasonCode="made_up"), expected_kind="task_gate")

  def test_lifecycle_order_and_contract_fingerprint_are_strict():
      with pytest.raises(CanonicalEvidenceSchemaError):
          CanonicalEvidenceEnvelope.from_dict(_task_gate_payload(decidedAt="2026-07-28T00:00:00Z"), expected_kind="task_gate")
  ```

  Fixtures must include each exact status/reason mapping, all payload enum values, list／string caps, bool／negative／over-cap rejection, unknown top-level key rejection, and `contractFingerprint` format validation.

- [ ] **Step 2: Run model tests to verify RED.**

  Run:

  ```bash
  .venv/bin/python -m pytest tests/test_canonical_evidence_models.py -q
  ```

  Expected: FAIL because the model and registry modules do not yet exist.

- [ ] **Step 3: Implement envelope, registry and canonical serialization.**

  Implement the exact schema constants from the approved spec. `CanonicalEvidenceEnvelope.from_dict()` must reject unknown keys, enforce UTC ISO-8601 lifecycle order, validate kind/status/reason/payload allowlists, validate `runId` and lowercase SHA-256 values, and expose `canonical_fingerprint()` using the fixed serialization rule. `CanonicalEvidenceRegistry` must expose exactly three entries with filename, writer identifier, writer entrypoint, status/reason mapping, payload caps and schema version; no caller may register a new writer at runtime.

- [ ] **Step 4: Run model GREEN verification.**

  ```bash
  .venv/bin/python -m pytest tests/test_canonical_evidence_models.py -q
  .venv/bin/python -m py_compile backend/agents/canonical_evidence_models.py backend/agents/canonical_evidence_registry.py
  git diff --check
  ```

  Expected: all model tests pass; no runtime or existing artifact changes.

- [ ] **Step 5: Commit and strict-review Task 1.**

  ```bash
  git add backend/agents/canonical_evidence_models.py backend/agents/canonical_evidence_registry.py tests/test_canonical_evidence_models.py
  git commit -m "feat: define canonical evidence schema and registry"
  ```

  Submit only the immutable Task 1 commit and verification evidence to findings-first strict Review. Do not start Task 2 unless verdict is `pass`.

---

### Task 2: Immutable canonical writer and approved contract binding

**Files:**

- Create: `backend/agents/canonical_evidence_writer.py`
- Modify: `backend/agents/workflow_store.py` only to expose an allowlisted read／write primitive if the existing private primitive cannot satisfy create-only semantics
- Create: `tests/test_canonical_evidence_writer.py`

**Consumes:** Task 1 `CanonicalEvidenceEnvelope` and `CanonicalEvidenceRegistry`; existing `WorkflowStore._run_file()`, `_atomic_json()` and `WorkflowApproval` validation.

**Produces:** `CanonicalEvidenceWriter.write_final(run_id, envelope) -> Path` for three approved writer entrypoints; no existing orchestrator automatically invokes it because no Task Gate／Terra／protected incident decision path currently exists.

- [ ] **Step 1: Write failing writer tests.**

  Cover valid writes for all three kinds, rejection when `approval.json` is absent／not approved／contract fingerprint mismatches, writer-kind mismatch, runId mismatch, symlink／traversal／non-regular target, existing artifact duplicate, and bytes unchanged after a rejected second write:

  ```python
  def test_writer_creates_one_final_artifact_and_rejects_duplicate(tmp_path):
      _write_approved_run(tmp_path, "run-1")
      writer = CanonicalEvidenceWriter(tmp_path)
      first = writer.write_final("run-1", _task_gate_envelope())
      before = first.read_bytes()
      with pytest.raises(CanonicalEvidenceWriteError):
          writer.write_final("run-1", _task_gate_envelope())
      assert first.read_bytes() == before
  ```

- [ ] **Step 2: Run writer tests to verify RED.**

  ```bash
  .venv/bin/python -m pytest tests/test_canonical_evidence_writer.py -q
  ```

  Expected: FAIL because the writer module does not yet exist.

- [ ] **Step 3: Implement create-only, contained final write.**

  Resolve the run directory through `WorkflowStore` containment, validate the run manifest and approved `approval.json`, compare envelope `contractFingerprint`, enforce registry writer entrypoint／kind binding, reject symlink and non-regular paths, and use an exclusive create／same-directory atomic primitive that never overwrites an existing canonical artifact. The writer may write only its registry filename; it must not modify manifest/status, Graph projection, SQLite, baseline, runtime retention or Git.

- [ ] **Step 4: Run writer GREEN and no-write verification.**

  ```bash
  .venv/bin/python -m pytest tests/test_canonical_evidence_writer.py -q
  .venv/bin/python -m py_compile backend/agents/canonical_evidence_writer.py
  git diff --check
  ```

  Expected: valid writer tests pass; rejected writes leave all existing bytes unchanged.

- [ ] **Step 5: Commit and strict-review Task 2.**

  ```bash
  git add backend/agents/canonical_evidence_writer.py backend/agents/workflow_store.py tests/test_canonical_evidence_writer.py
  git commit -m "feat: add immutable canonical evidence writer"
  ```

  Strict Review must confirm writer authority and no overwrite before Task 3.

---

### Task 3: Safe canonical evidence reader and Agent Operations／Telemetry integration

**Files:**

- Create: `backend/agents/canonical_evidence_reader.py`
- Modify: `backend/services/agent_operations_service.py`
- Modify: `backend/services/governance_telemetry_aggregation.py`
- Modify: `backend/services/governance_telemetry_service.py` only if the public snapshot pass-through needs an explicit evidence field
- Modify: `tests/test_canonical_evidence_reader.py`
- Modify: `tests/test_agent_operations_service.py`
- Modify: `tests/test_governance_telemetry_service.py`

**Consumes:** Task 1 models/registry, Task 2 writer output, existing `AgentOperationsService` safe reader boundary and retained-run policy.

**Produces:** Per-run compact `canonicalEvidence` payload and exact／unknown／invalid／blocked telemetry metrics.

- [ ] **Step 1: Write failing reader and integration tests.**

  Use `CanonicalEvidenceWriter` to create valid fixtures. Assert: valid evidence is compacted; missing retained artifact is `unknown`; blocked terminal artifact is `blocked`; contract／evidence fingerprint mismatch, malformed JSON, unknown key, symlink, traversal, duplicate and over-cap payload are `invalid`; invalid evidence does not increment success or blocked counts; deleted runs are excluded from denominator; no raw payload or absolute path leaks.

  ```python
  def test_agent_operations_exposes_canonical_evidence_without_scanning_or_writing(tmp_path):
      _write_approved_run_with_task_gate(tmp_path)
      before = _runtime_bytes(tmp_path)
      snapshot = AgentOperationsService(tmp_path).build_snapshot()
      evidence = snapshot["runs"][0]["canonicalEvidence"]["task_gate"]
      assert evidence["status"] == "available"
      assert _runtime_bytes(tmp_path) == before
      assert str(tmp_path) not in json.dumps(snapshot)
  ```

- [ ] **Step 2: Run reader/integration tests to verify RED.**

  ```bash
  .venv/bin/python -m pytest tests/test_canonical_evidence_reader.py tests/test_agent_operations_service.py tests/test_governance_telemetry_service.py -q
  ```

  Expected: new canonical evidence assertions fail because no reader integration or exact metrics exist.

- [ ] **Step 3: Implement the safe reader and pass compact evidence only.**

  `CanonicalEvidenceReader.read()` must use only the three registry filenames, validate regular-file and project/run containment, load and validate `approval.json`, recompute both fingerprints, redact all unsafe details, and return bounded state／status／reason／finalizedAt／artifact basename／SHA-256. `AgentOperationsService` must call the reader within existing run containment and include a compact `canonicalEvidence` mapping without changing existing run item fields or cache semantics.

- [ ] **Step 4: Implement exact telemetry aggregation from compact evidence.**

  Extend `governance_telemetry_aggregation.py` so Task Gate failed／blocked counts, Terra completed／blocked counts and protected incident terminal observation counts come only from `available`／`blocked` compact evidence. Maintain `unknownCount`, `invalidCount`, `blockedCount`, `observedCount`, retained denominator and snapshot `partial` semantics exactly as the spec; do not read raw files from the aggregation layer and do not infer one evidence kind from another.

- [ ] **Step 5: Run Task 3 GREEN verification.**

  ```bash
  .venv/bin/python -m pytest tests/test_canonical_evidence_reader.py tests/test_agent_operations_service.py tests/test_governance_telemetry_service.py -q
  .venv/bin/python -m py_compile backend/agents/canonical_evidence_reader.py backend/services/agent_operations_service.py backend/services/governance_telemetry_aggregation.py
  git diff --check
  ```

  Expected: focused tests pass; no raw evidence, absolute path or writer command appears in compact snapshots.

- [ ] **Step 6: Commit and strict-review Task 3.**

  ```bash
  git add backend/agents/canonical_evidence_reader.py backend/services/agent_operations_service.py backend/services/governance_telemetry_aggregation.py backend/services/governance_telemetry_service.py tests/test_canonical_evidence_reader.py tests/test_agent_operations_service.py tests/test_governance_telemetry_service.py
  git commit -m "feat: consume canonical evidence in telemetry"
  ```

  Review scope must include only the reader and compact aggregation integration; writer files remain immutable from Task 2.

---

### Task 4: Governance Graph evidence projection and boundary regression

**Files:**

- Modify: `backend/agents/governance_graph_service.py`
- Modify: `backend/agents/governance_graph_models.py` only if validated node type/field compatibility requires an additive schema change
- Modify: `tests/test_governance_graph_service.py`
- Modify: `tests/test_governance_graph_models.py` only for additive node contract coverage
- Modify: `tests/test_app_module_boundaries.py`

**Consumes:** Task 3 compact evidence reader result and existing `GovernanceGraphBuilder.persist()` derived projection writer.

**Produces:** `task_gate`、`terra_diagnosis`、`protected_incident` Graph nodes with safe evidence references; missing／invalid evidence remains `unknown`／`invalid` and never changes canonical artifacts.

- [ ] **Step 1: Write failing projection tests.**

  Build a run through approved writer fixtures, call the existing Graph builder in test setup, and assert valid evidence nodes contain only node ID, safe status, reason, finalized timestamp, basename and SHA-256. Add tests for missing, invalid, blocked, cross-kind reference and no-inference behavior; assert canonical artifact bytes are unchanged by projection.

- [ ] **Step 2: Run projection tests to verify RED.**

  ```bash
  .venv/bin/python -m pytest tests/test_governance_graph_service.py tests/test_governance_graph_models.py -q
  ```

  Expected: new evidence-node assertions fail because Graph builder currently knows only the original canonical node set.

- [ ] **Step 3: Add additive Graph projection support.**

  Extend the existing builder node allowlist and compact model only for the three validated evidence kinds. Reuse existing artifact SHA and projection writer; never import or call canonical writer from Graph builder. Keep `governance-graph-v1` compatibility for existing nodes and reject unknown projection fields.

- [ ] **Step 4: Run projection and boundary GREEN verification.**

  ```bash
  .venv/bin/python -m pytest tests/test_governance_graph_service.py tests/test_governance_graph_models.py tests/test_app_module_boundaries.py -q
  .venv/bin/python -m py_compile backend/agents/governance_graph_service.py backend/agents/governance_graph_models.py
  git diff --check
  ```

- [ ] **Step 5: Commit and strict-review Task 4.**

  ```bash
  git add backend/agents/governance_graph_service.py backend/agents/governance_graph_models.py tests/test_governance_graph_service.py tests/test_governance_graph_models.py tests/test_app_module_boundaries.py
  git commit -m "feat: project canonical evidence in governance graph"
  ```

---

## Final Integration and Acceptance

After Tasks 1–4 each receive strict Review `pass`:

1. Run affected compile and focused suites for all canonical evidence, Agent Operations, Telemetry and Graph modules.
2. Run full `.venv/bin/python -m pytest -q`.
3. Run `.venv/bin/python scripts/system_manager.py acceptance`.
4. Run `.venv/bin/python scripts/hermes_post_change_check.py --skip-monitor --json`.
5. Record before／after SHA-256 for `nbs_marketing_data.db`, confirm baseline `HKD 12,057,968`, confirm no SQLite／baseline／Git mutation, and list only expected canonical evidence／Graph projection writes from explicit writer／builder calls.
6. Run `git diff --check`, inspect `git status --short`, and confirm UI still consumes only compact snapshot fields with no writer or control-plane action.
7. Documentation dispatch is skipped unless the deterministic classifier identifies a real documentation target; no main-Codex fallback is allowed.
8. Stop after final acceptance and report task commits, Review verdicts, Hermes result, baseline／SQLite evidence, and any remaining unknown coverage. Do not push or merge without explicit authorization.

## Plan Self-Review

- Spec §3–§4: Tasks 1–2 cover registry, approval contract binding, canonical serialization and immutable writer.
- Spec §5–§6: Tasks 1 and 3 cover all kind/status/reason/payload caps and read-state precedence.
- Spec §7: Task 4 covers Graph evidence nodes and derived projection-only writes.
- Spec §8–§9: Tasks 2–4 cover retained denominator, path safety, redaction and no-write boundaries.
- Spec §10: Every Task has RED, GREEN, focused verification, strict Review and a stop point.
- Explicit exclusion: query, version comparison, dependency／impact analysis and risk summary are not in this plan.
- Placeholder scan: no `TODO`, `TBD`, or unspecified implementation step remains.
