# NBS Governance Graph Phase D-3 Risk Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax and stop after each approved Task.

**Goal:** 建立只消費 D-2 comparison result 的 deterministic、read-only Governance Graph Risk Summary read model，供 CLI、未來 UI 與 D-4 change impact analysis 使用。

**Architecture:** 先補 D-2 public envelope 的 reference compatibility bridge，再以 immutable risk models 驗證 bridge-complete comparison result。純函式 rule registry 產生 bounded findings 與 coverage；stdin-only CLI 只消費 comparison JSON，不讀 snapshot path、不寫 runtime。

**Tech Stack:** Python 3.10+、frozen dataclasses、既有 `GovernanceGraphComparisonResult`、`canonical_sha256`、argparse、pytest、Context Agent、approved local `codex` Review runner、Hermes read-only acceptance。

## Global Constraints

- D-3 只接受 `governance-graph-comparison-v1` bridge-complete result，不接受 run ID、snapshot path、raw artifact 或 SQLite。
- D-2 compatibility bridge 必須保留 existing fields，新增 bounded `leftReference`／`rightReference`；缺任一 reference 直接 `invalid`。
- Risk output schema 固定為 `governance-graph-risk-summary-v1`，registry 固定為 `d3-risk-rules-v1`。
- `D3-INVALID-COMPARISON` 與 `D3-UNAVAILABLE-COMPARISON` 只產生 diagnostics，不產生 risk finding 或 overall risk level。
- `D3_DOCUMENTATION_NODE_ALLOWLIST_V1` 只包含 explicit D-2 nodeId `documentation`；不得從 filename、文字內容、順序或 metadata 推導。
- D-3 只做 deterministic observation label；不得 approval、dispatch、blocking、rollback、repair、retry、prune、delete 或 business decision。
- 不讀寫 SQLite、baseline、revenue scope、business rules、rollback、export schema、cache、Git、canonical artifacts 或 Graph snapshot。
- 正式口徑固定為「不含掛賬核銷與TT退款轉團款」；2026-05 frozen baseline 固定為 `HKD 12,057,968`。
- 每個 Task 順序固定為：Context collect-only → TDD RED → 最小實作 → focused verification → strict Review → 停點。
- Context／Review／Hermes 永遠 read-only；Implementation Agent 若另行啟用，只能改 approved allowed paths，不得 commit、merge、push 或啟停服務。
- Review runner 優先使用已 approved 的本地 `codex`；若 runner 不可用，狀態保持 `unknown`／`blocked_missing_runner`，不得虛報 PASS。

---

## File Map

- Modify: `backend/agents/governance_graph_comparison_models.py` — D-2 result `to_dict()`／validation 的 reference bridge。
- Modify: `scripts/governance_graph.py` — D-2 compare envelope 輸出 references；Task 3 新增 stdin-only `risk-summary`。
- Modify: `tests/test_governance_graph_comparison_models.py` — bridge fields、fingerprint 與 backward-compatible model tests。
- Modify: `tests/test_governance_graph_cli.py` — compare bridge regression、risk-summary stdin／forbidden flags／exact envelope。
- Create: `backend/agents/governance_graph_risk_models.py` — immutable risk input、finding、coverage、diagnostic、summary models。
- Create: `backend/agents/governance_graph_risk_service.py` — pure D-2 validation、rule registry 與 risk evaluation。
- Create: `tests/test_governance_graph_risk_models.py` — schema、allowlist、fingerprint、bounded output tests。
- Create: `tests/test_governance_graph_risk_service.py` — rule semantics、status precedence、ordering、no-inference、no-write tests。
- Modify: `docs/superpowers/specs/2026-07-29-nbs-governance-graph-phase-d3-risk-summary-design.md` — only when an approved contract gap is found; update spec before code if semantics change.

## Local Agent Protocol

1. Codex 建立 one-Task contract、allowed paths 與 verification evidence。
2. Context Agent 僅執行：

```bash
.venv/bin/python scripts/context_agent.py \
  --brief docs/superpowers/specs/2026-07-29-nbs-governance-graph-phase-d3-risk-summary-design.md \
  --base main --collect-only --format json \
  --output .nbs_agent_runtime/reports/phase-d3-context-taskN.json
```

3. Codex 或明確批准的 Implementation Agent 只執行目前 Task；不可自行選擇下一 Task。
4. Review Agent 只讀 actual diff、spec、plan、context、verification；以 findings-first 回報。
5. Hermes 只在所有 code Task Review PASS、full verification PASS 後執行；Hermes PASS 不取代 Review PASS。
6. Documentation Agent 不在 D-3 implementation 中自動啟用；若文件 proposal 必要，必須在 final gates 後另立 approved dispatch。

## Task 0: D-2 Comparison Reference Compatibility Bridge

**Files:**
- Modify: `backend/agents/governance_graph_comparison_models.py`
- Modify: `scripts/governance_graph.py`
- Modify: `tests/test_governance_graph_comparison_models.py`
- Modify: `tests/test_governance_graph_cli.py`

**Interfaces:**
- `GovernanceGraphComparisonResult.to_dict()` 必須新增 bounded `leftReference` 與 `rightReference`，保留所有既有 v1 fields。
- `GovernanceGraphComparisonResult.from_dict(payload)` 必須驗證 exact public keys、reference allowlist、optional lowercase SHA-256、snapshot identity binding 與 comparison fingerprint。
- CLI `compare` 的 nested result 必須包含兩個 references；既有 query／build／validate／status envelope 不變。

- [ ] **Step 1: Write failing bridge tests.**

```python
def test_comparison_output_exposes_both_input_references():
    result = _comparison_result(left_fingerprint=None, right_fingerprint="a" * 64)
    payload = result.to_dict()
    assert payload["leftReference"] == {"runId": "run-left", "snapshotFingerprint": None}
    assert payload["rightReference"] == {"runId": "run-right", "snapshotFingerprint": "a" * 64}

def test_comparison_from_dict_rejects_missing_reference():
    payload = _comparison_result().to_dict()
    payload.pop("rightReference")
    with pytest.raises(GovernanceGraphComparisonSchemaError):
        GovernanceGraphComparisonResult.from_dict(payload)
```

- [ ] **Step 2: Run RED.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_comparison_models.py tests/test_governance_graph_cli.py -q`  
Expected: FAIL because D-2 output has no public references and no `from_dict` bridge.

- [ ] **Step 3: Implement the minimal bridge.**

Reuse the existing `GovernanceGraphSnapshotReference.from_dict()` and `comparison_fingerprint` canonical algorithm. Do not alter comparison semantics, change identity, status precedence, CLI command arguments, or snapshot reader behavior. Reject missing references, unsafe run IDs, invalid SHA-256 and fingerprint mismatch as `GovernanceGraphComparisonSchemaError`.

- [ ] **Step 4: Run bridge and D-2 regression tests.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_comparison_models.py tests/test_governance_graph_comparison_service.py tests/test_governance_graph_cli.py -q`  
Expected: PASS; existing D-2 output fields remain stable and compare remains read-only.

- [ ] **Step 5: Run strict Review and commit.**

```bash
git add backend/agents/governance_graph_comparison_models.py scripts/governance_graph.py tests/test_governance_graph_comparison_models.py tests/test_governance_graph_cli.py
git commit -m "feat: expose governance graph comparison references"
```

## Task 1: Immutable Risk Models and D-2 Input Validation

**Files:**
- Create: `backend/agents/governance_graph_risk_models.py`
- Test: `tests/test_governance_graph_risk_models.py`

**Interfaces:**
- `RISK_SUMMARY_SCHEMA = "governance-graph-risk-summary-v1"`
- `RISK_RULE_REGISTRY_VERSION = "d3-risk-rules-v1"`
- `GovernanceGraphRiskInput.from_dict(payload) -> GovernanceGraphRiskInput`
- `GovernanceGraphRiskFinding.from_dict(payload) -> GovernanceGraphRiskFinding`
- `GovernanceGraphRiskSummary.from_parts(...) -> GovernanceGraphRiskSummary`
- `GovernanceGraphRiskSummary.to_dict()` and `.risk_summary_fingerprint`

- [ ] **Step 1: Write failing model tests.**

```python
def test_risk_input_requires_bridge_complete_comparison():
    payload = _comparison_payload()
    payload.pop("leftReference")
    with pytest.raises(GovernanceGraphRiskSchemaError):
        GovernanceGraphRiskInput.from_dict(payload)

def test_risk_summary_fingerprint_is_reproducible_and_bounded():
    first = _summary(findings=(_finding("D3-VERIFICATION-REGRESSION"),))
    second = _summary(findings=(_finding("D3-VERIFICATION-REGRESSION"),))
    assert first.risk_summary_fingerprint == second.risk_summary_fingerprint
    assert "/private/raw" not in json.dumps(first.to_dict())
```

- [ ] **Step 2: Run RED.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_risk_models.py -q`  
Expected: FAIL because risk models do not exist.

- [ ] **Step 3: Implement immutable bounded models.**

Use frozen dataclasses, exact key allowlists, safe public text, lowercase SHA-256, level/status/coverage allowlists, bounded finding identities, deterministic sorting and canonical SHA-256. `invalid`／`unavailable` inputs may carry diagnostics but `findings` must be empty. `from_dict` must delegate D-2 bridge validation instead of duplicating snapshot or raw artifact parsing.

- [ ] **Step 4: Run model tests.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_risk_models.py -q`  
Expected: PASS with deterministic fingerprints and rejection of raw/absolute/secret fields.

- [ ] **Step 5: Strict Review and commit.**

```bash
git add backend/agents/governance_graph_risk_models.py tests/test_governance_graph_risk_models.py
git commit -m "feat: define governance graph risk summary contract"
```

## Task 2: Deterministic Risk Rule Registry and Pure Service

**Files:**
- Create: `backend/agents/governance_graph_risk_service.py`
- Test: `tests/test_governance_graph_risk_service.py`

**Interfaces:**
- `D3_DOCUMENTATION_NODE_ALLOWLIST_V1 = frozenset({"documentation"})`
- `RISK_RULES_V1` is immutable and versioned `d3-risk-rules-v1`.
- `GovernanceGraphRiskService.evaluate(comparison: Mapping[str, Any] | GovernanceGraphComparisonResult) -> GovernanceGraphRiskSummary`
- No method accepts run IDs, paths, snapshot readers, SQLite handles or writers.

- [ ] **Step 1: Write failing service tests.**

```python
def test_invalid_and_unavailable_are_diagnostics_only():
    assert service.evaluate(_comparison(status="invalid")).to_dict()["findings"] == []
    assert service.evaluate(_comparison(status="unavailable")).to_dict()["findings"] == []

def test_protected_signal_is_r2_and_verification_change_is_r1():
    summary = service.evaluate(_comparison(changes=[_node("protected_incident", "changed"), _node("hermes", "changed")]))
    assert summary.overall_risk_level == "R2"
    assert {item["ruleId"] for item in summary.to_dict()["findings"]} == {"D3-PROTECTED-NODE", "D3-VERIFICATION-REGRESSION"}

def test_documentation_allowlist_is_exact_and_no_edge_inference():
    summary = service.evaluate(_comparison(changes=[_node("documentation", "changed")], edges=[]))
    assert summary.overall_risk_level == "R0"
    assert summary.to_dict()["findings"][0]["ruleId"] == "D3-DOCUMENTATION-ONLY"
```

- [ ] **Step 2: Run RED.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_risk_service.py -q`  
Expected: FAIL because the service and registry do not exist.

- [ ] **Step 3: Implement pure rule evaluation.**

Validate/reconstruct the bridge-complete D-2 result first. Apply diagnostics-only handling, then R2 protected rules, R1 blocked/verification/behavior rules, R0 exact documentation allowlist, and unknown coverage. Use explicit source identities from D-2 records; never inspect raw paths or infer edges. Deduplicate by `(ruleId, source kind, identity, changeType)`, sort by level priority/rule priority/findingId, and calculate coverage and risk fingerprint.

- [ ] **Step 4: Run service tests and no-write regression.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_risk_models.py tests/test_governance_graph_risk_service.py -q`  
Expected: PASS; repeated evaluation is byte-identical and filesystem tree bytes remain unchanged.

- [ ] **Step 5: Strict Review and commit.**

```bash
git add backend/agents/governance_graph_risk_service.py tests/test_governance_graph_risk_service.py
git commit -m "feat: add deterministic governance graph risk service"
```

## Task 3: Stdin-only Risk Summary CLI

**Files:**
- Modify: `scripts/governance_graph.py`
- Modify: `tests/test_governance_graph_cli.py`

**Interfaces:**
- Command: `cat comparison.json | .venv/bin/python scripts/governance_graph.py risk-summary`
- Output: outer `nbs-governance-graph-cli-v1` envelope with nested `governance-graph-risk-summary-v1` result.
- Parser accepts no `--run-id`, `--path`, `--file`, `--approve`, `--dispatch`, `--writer` or model flags.

- [ ] **Step 1: Write failing CLI tests.**

```python
def test_risk_summary_reads_bridge_result_from_stdin(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_comparison_payload())))
    assert cli.main(["risk-summary"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["result"]["schemaVersion"] == "governance-graph-risk-summary-v1"

def test_risk_summary_rejects_control_plane_flags():
    with pytest.raises(SystemExit):
        cli._parser().parse_args(["risk-summary", "--run-id", "run-1"])
```

- [ ] **Step 2: Run RED.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_cli.py -q`  
Expected: FAIL because `risk-summary` is not registered.

- [ ] **Step 3: Implement stdin-only branch.**

Read one UTF-8 JSON object from stdin, reject empty/multiple/invalid JSON with an invalid CLI envelope and exit code `2`, call only `GovernanceGraphRiskService.evaluate()`, render bounded JSON through existing `_render`, and never call `GovernanceGraphBuilder`, `GovernanceGraphSnapshotReader`, `WorkflowStore`, SQLite or Git.

- [ ] **Step 4: Run CLI and D-2 regression tests.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_cli.py tests/test_governance_graph_comparison_service.py tests/test_governance_graph_query_service.py -q`  
Expected: PASS; risk-summary is read-only and all prior CLI commands remain unchanged.

- [ ] **Step 5: Strict Review and commit.**

```bash
git add scripts/governance_graph.py tests/test_governance_graph_cli.py
git commit -m "feat: expose governance graph risk summary cli"
```

## Task 4: Full Verification, Hermes and Final Acceptance

**Files:**
- Read-only review of all D-3 diff, spec and this plan.
- No additional source files unless a strict Review finding identifies a concrete contract gap.

- [ ] **Step 1: Run affected compile and tests.**

```bash
.venv/bin/python -m py_compile \
  backend/agents/governance_graph_comparison_models.py \
  backend/agents/governance_graph_comparison_service.py \
  backend/agents/governance_graph_risk_models.py \
  backend/agents/governance_graph_risk_service.py \
  scripts/governance_graph.py
.venv/bin/python -m pytest \
  tests/test_governance_graph_comparison_models.py \
  tests/test_governance_graph_comparison_service.py \
  tests/test_governance_graph_risk_models.py \
  tests/test_governance_graph_risk_service.py \
  tests/test_governance_graph_cli.py -q
git diff --check
```

- [ ] **Step 2: Run final strict Review.**

Review must verify D-2 bridge compatibility, exact rule registry, diagnostics-only invalid/unavailable behavior, protected R2 observation, documentation allowlist, deterministic fingerprints/order, stdin-only boundary, no raw artifact access and no control-plane writes.

- [ ] **Step 3: Run full acceptance.**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/hermes_post_change_check.py
```

Expected: full pytest PASS, system acceptance PASS, Hermes `Overall status: PASS`. Existing degraded monitor findings must be reported separately, never repaired by D-3.

- [ ] **Step 4: Verify immutable boundaries.**

Record `git status --short`, `nbs_marketing_data.db` SHA-256 before/after, Graph/runtime/canonical artifact tree bytes, baseline `HKD 12,057,968` and revenue scope. Confirm stdin risk-summary creates no snapshot and does not alter any file.

- [ ] **Step 5: Reconcile plan and stop before integration.**

Mark Tasks 0–4 complete only when their Review/full gates are satisfied. Do not push, merge, delete branch or invoke Documentation Agent without explicit user authorization.
