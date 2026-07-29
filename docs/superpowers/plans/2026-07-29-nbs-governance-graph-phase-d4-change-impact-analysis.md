# NBS Governance Graph Phase D-4 Change Impact Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將已驗證的 D-3 `governance-graph-risk-summary-v1` 投影成 deterministic、read-only 的 `governance-graph-change-impact-v1`，供後續 query、版本比較與只讀 UI 消費。

**Architecture:** D-4 只接受完整 D-3 risk summary envelope，不回讀 D-2 snapshot、raw artifacts、canonical evidence、SQLite、Git 或 runtime。先補 D-3 strict parser／fingerprint bridge，再以 immutable impact models 與 versioned exact rule mapping 產生 bounded impact observations，最後暴露 stdin-only CLI。

**Tech Stack:** Python 3.10+、frozen dataclasses、`canonical_sha256`、argparse、pytest、既有 D-3 risk models、Context Agent、approved local Review runner、Hermes read-only acceptance。

**Execution status (2026-07-29):** Tasks 1–5 completed. Strict Review PASS (`cc2a058c76ffa11bae7987d9d55bc4c608a1b15e0e888a1a960d988ff4e0b4bc`), full pytest `1284 passed`, system acceptance PASS, Hermes PASS (`714 passed`). The isolated worktree uses an ignored SQLite test copy and ignored runtime cache directory; neither is Git-tracked or a write to the primary worktree database.

## Global Constraints

- D-4 唯一 input authority 是已驗證的 D-3 `governance-graph-risk-summary-v1`。
- D-4 不接受 run ID、snapshot path、raw artifact、D-2 comparison payload、SQLite、Git 或網路。
- D-3 `comparisonFingerprint` 只作 provenance binding；D-4 不重新執行 D-2 comparison 或 D-3 risk rules。
- D-3 strict parser 必須驗證 exact keys、`riskRuleRegistryVersion`、finding／coverage／diagnostic bounds 與 `riskSummaryFingerprint`。
- Impact policy version 固定為 `d4-impact-policy-v1`；未知 ruleId 或 registry version 必須 fail closed。
- Output schema 固定為 `governance-graph-change-impact-v1`；所有 identity、rationale、diagnostic 與 evidence ref 必須 bounded。
- `invalid`／`unavailable` 只輸出 diagnostics；`blocked` 保留已驗證 impacts；`unknown` 不得降級為 R0 或「無影響」。
- Invalid／unavailable 時 `comparisonFingerprint`、`riskSummaryFingerprint`、`impactSummaryFingerprint` 都是 `null`；valid／blocked／unknown 才可輸出已驗證 fingerprints。
- `coverageStatus` 必須明確為 `available`、`blocked` 或 `unknown`；unknown 且無 findings 不得被解讀為 zero impact。
- D-4 不新增 dependency traversal、owner inference、business impact、impact score、approval、dispatch、rollback、repair、writer 或 control-plane path。
- 正式口徑固定為「不含掛賬核銷與TT退款轉團款」；2026-05 frozen baseline 固定為 `HKD 12,057,968`。
- 每個 Task 順序固定為：Context collect-only → TDD RED → 最小實作 → focused verification → strict Review → 停點。
- Context／Review／Hermes 永遠 read-only；Implementation Agent 若啟用，只能修改該 Task allowlisted files，不得 commit、merge、push 或啟停服務。
- Review PASS 仍不等於完成；必須再通過 full pytest、system acceptance 與 Hermes。

---

## File Map

- Modify: `backend/agents/governance_graph_risk_models.py` — D-3 public envelope strict parser 與 fingerprint round-trip，不改變既有 output semantics。
- Create: `backend/agents/governance_graph_impact_models.py` — immutable input、impact、coverage、diagnostic、summary models 與 fingerprint。
- Create: `backend/agents/governance_graph_impact_service.py` — D-3 exact rule mapping 與 bounded read model。
- Modify: `scripts/governance_graph.py` — stdin-only `change-impact` subcommand。
- Create: `tests/test_governance_graph_impact_models.py` — model、allowlist、fingerprint、bounded output tests。
- Create: `tests/test_governance_graph_impact_service.py` — mapping、status、ordering、no-inference、no-write tests。
- Modify: `tests/test_governance_graph_cli.py` — CLI exact envelope、stdin handling、forbidden flags regression。
- Modify: `docs/superpowers/specs/2026-07-29-nbs-governance-graph-phase-d4-change-impact-analysis-design.md` — only if an approved contract gap is found; update spec before source semantics.

## Local Agent Protocol

1. Before each Task, run the approved Context Agent collection with the D-4 spec as brief:

```bash
.venv/bin/python scripts/context_agent.py \
  --brief docs/superpowers/specs/2026-07-29-nbs-governance-graph-phase-d4-change-impact-analysis-design.md \
  --base main --collect-only --format json
```

2. Implementation Agent (if dispatched) receives exactly one Task contract and only that Task's allowlisted files. It must return an implementation report and must not commit or select the next Task.
3. Review Agent receives the approved Task contract, compact context, actual diff and verification evidence. It reports findings-first and may not edit files.
4. After Review PASS, Codex runs full verification and Hermes. Hermes PASS does not replace semantic Review PASS.
5. Documentation Agent is not invoked for this spec-only plan until a later implementation run passes Review, full verification and Hermes and deterministic document-impact classification requests it.

---

### Task 1: D-3 Strict Risk Summary Compatibility Bridge

**Files:**
- Modify: `backend/agents/governance_graph_risk_models.py`
- Create: `tests/test_governance_graph_risk_models.py` (or extend the existing risk-model test file if it already exists)

**Interfaces:**
- Consumes: existing `GovernanceGraphRiskSummary`, `canonical_sha256`, `governance-graph-risk-summary-v1` output.
- Produces: `GovernanceGraphRiskSummary.from_dict(payload: Mapping[str, Any]) -> GovernanceGraphRiskSummary` with exact-key, registry-version, bounded-field, and fingerprint validation.

- [ ] **Step 1: Write failing parser tests.**

Cover: valid `to_dict()` round-trip; extra top-level key rejection; missing `riskRuleRegistryVersion`; tampered `comparisonFingerprint`; tampered `riskSummaryFingerprint`; invalid finding source identity; duplicate `findingId`; and invalid/unavailable summaries containing findings.

- [ ] **Step 2: Run the RED tests.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_risk_models.py -q`  
Expected: FAIL because strict public-envelope parsing is absent or incomplete.

- [ ] **Step 3: Implement the minimal strict parser.**

Reuse existing frozen model constructors and `canonical_sha256`; enforce exact public keys, `RISK_SUMMARY_SCHEMA`, `RISK_RULE_REGISTRY_VERSION`, existing bounded validators, deterministic finding order, coverage keys, diagnostics, and both fingerprints. Do not add any filesystem or writer dependency.

- [ ] **Step 4: Run focused bridge tests.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_risk_models.py tests/test_governance_graph_risk_service.py -q`  
Expected: PASS with existing D-3 behavior unchanged.

- [ ] **Step 5: Strict Review and Codex integration commit.**

Review only `backend/agents/governance_graph_risk_models.py` and its focused tests. After Review PASS, Codex stages only the allowlisted files and commits: `git commit -m "feat: add strict governance graph risk summary parser"`.

### Task 2: Immutable Change Impact Models

**Files:**
- Create: `backend/agents/governance_graph_impact_models.py`
- Create: `tests/test_governance_graph_impact_models.py`

**Interfaces:**
- Consumes: parsed D-3 risk summary from Task 1.
- Produces: immutable `GovernanceGraphImpactInput`, `GovernanceGraphImpactRecord`, `GovernanceGraphImpactCoverage`, `GovernanceGraphImpactDiagnostic`, and `GovernanceGraphImpactSummary`; `to_dict()` must emit `governance-graph-change-impact-v1`, with nullable provenance fingerprints for invalid/unavailable results.

- [ ] **Step 1: Write failing model tests.**

Cover exact input envelope (`schemaVersion`, `riskSummary` only); bounded identity and summary validation; exact impact fields; fixed coverage keys including `coverageStatus`; status enum; fingerprint reproducibility; deterministic ordering; nullable invalid/unavailable provenance; and rejection of absolute paths, control text, raw payload fields, duplicate sourceFindingIds, and random IDs.

- [ ] **Step 2: Run RED.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_impact_models.py -q`  
Expected: FAIL because the D-4 models do not yet exist.

- [ ] **Step 3: Implement frozen models and canonical fingerprints.**

Use `dataclass(frozen=True)`, `MappingProxyType`, existing safe-text conventions, `canonical_sha256`, and fixed `D4_IMPACT_POLICY_VERSION = "d4-impact-policy-v1"`. `impactSummaryFingerprint` covers schema, policy, status, both source fingerprints, coverage, sorted impacts, and diagnostics, excluding itself.

- [ ] **Step 4: Run focused model tests.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_impact_models.py -q`  
Expected: PASS.

- [ ] **Step 5: Strict Review and Codex integration commit.**

Review only the new model and tests. After Review PASS, Codex stages only the allowlisted files and commits: `git commit -m "feat: add governance graph change impact models"`.

### Task 3: Exact D-3 Rule Mapping Service

**Files:**
- Create: `backend/agents/governance_graph_impact_service.py`
- Create: `tests/test_governance_graph_impact_service.py`

**Interfaces:**
- Consumes: `GovernanceGraphImpactInput` and validated D-3 summary from Tasks 1–2.
- Produces: `GovernanceGraphImpactService.evaluate(payload: Mapping[str, Any] | GovernanceGraphImpactInput) -> GovernanceGraphImpactSummary`.

- [ ] **Step 1: Write failing service tests.**

Cover one-to-one exact mappings for `D3-PROTECTED-NODE`, `D3-PROTECTED-SURFACE`, `D3-VERIFICATION-REGRESSION`, `D3-BEHAVIORAL-CHANGE`, `D3-DOCUMENTATION-ONLY`, `D3-BLOCKED-COMPARISON`, and `D3-UNKNOWN-COVERAGE`; inherited R0/R1/R2/unknown labels; invalid/unavailable diagnostics-only; blocked preservation; unknown coverage; deterministic ordering; repeated byte-identical output; and no-write tree equality.

- [ ] **Step 2: Run RED.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_impact_service.py -q`  
Expected: FAIL because the service does not yet exist.

- [ ] **Step 3: Implement the pure mapping service.**

Define an immutable versioned registry. Map only exact known rule IDs; preserve `sourceFindingId`, `sourceChange`, `evidenceIdentities`, inherited risk level, rationale code and bounded summaries. Reject unknown registry versions/rules fail-closed. Do not read paths, edges, snapshots, SQLite, runtime or Git.

- [ ] **Step 4: Run focused service and regression tests.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_risk_models.py tests/test_governance_graph_impact_models.py tests/test_governance_graph_impact_service.py tests/test_governance_graph_risk_service.py -q`  
Expected: PASS.

- [ ] **Step 5: Strict Review and Codex integration commit.**

Review only the service and its tests. After Review PASS, Codex stages only the allowlisted files and commits: `git commit -m "feat: add governance graph change impact service"`.

### Task 4: Stdin-only Change Impact CLI

**Files:**
- Modify: `scripts/governance_graph.py`
- Modify: `tests/test_governance_graph_cli.py`

**Interfaces:**
- Consumes: one UTF-8 `governance-graph-impact-input-v1` wrapper JSON object from stdin; the wrapper contains exactly one `riskSummary` object.
- Produces: bounded `governance-graph-change-impact-v1` envelope and exit code `0` for valid evaluated input, `2` for malformed/invalid input.

- [ ] **Step 1: Write failing CLI tests.**

Cover `change-impact` stdin success, exact output envelope, invalid JSON/empty stdin exit code `2`, strict parser failure envelope, repeated deterministic output, and parser rejection of `--run-id`, `--path`, `--file`, `--approve`, `--dispatch`, `--writer`, and model flags. Assert the CLI does not instantiate snapshot readers, workflow stores, SQLite or Git writers.

- [ ] **Step 2: Run RED.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_cli.py -q`  
Expected: FAIL because `change-impact` is not registered.

- [ ] **Step 3: Implement the stdin-only branch.**

Read exactly one UTF-8 wrapper JSON object from stdin, call only `GovernanceGraphImpactService.evaluate()`, render bounded JSON using the existing CLI envelope convention, and return `2` on parse/schema failure. Do not accept paths or control-plane flags.

- [ ] **Step 4: Run CLI and D-3/D-2 regressions.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_cli.py tests/test_governance_graph_risk_models.py tests/test_governance_graph_risk_service.py tests/test_governance_graph_comparison_models.py tests/test_governance_graph_comparison_service.py -q`  
Expected: PASS; existing `build`, `validate`, `status`, `compare`, and `risk-summary` commands remain unchanged.

- [ ] **Step 5: Strict Review and Codex integration commit.**

Review only the CLI and tests. After Review PASS, Codex stages only the allowlisted files and commits: `git commit -m "feat: expose governance graph change impact cli"`.

### Task 5: Full Verification, Hermes and Final Acceptance

**Files:**
- Read-only review of all D-4 diff, spec, plan, task reports and verification evidence.
- No source modifications unless a concrete strict Review finding identifies a contract violation; any such fix must be isolated and re-reviewed.

- [ ] **Step 1: Compile affected Python files.**

```bash
.venv/bin/python -m py_compile \
  backend/agents/governance_graph_risk_models.py \
  backend/agents/governance_graph_impact_models.py \
  backend/agents/governance_graph_impact_service.py \
  scripts/governance_graph.py
```

- [ ] **Step 2: Run focused D-4 and regression tests.**

```bash
.venv/bin/python -m pytest \
  tests/test_governance_graph_risk_models.py \
  tests/test_governance_graph_impact_models.py \
  tests/test_governance_graph_impact_service.py \
  tests/test_governance_graph_cli.py \
  tests/test_governance_graph_comparison_models.py \
  tests/test_governance_graph_comparison_service.py -q
```

- [ ] **Step 3: Run full pytest and system acceptance.**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/system_manager.py acceptance
```

- [ ] **Step 4: Run strict final Review and Hermes.**

Review must be findings-first with an approved runner and immutable base/head evidence. Then run:

```bash
.venv/bin/python scripts/hermes_post_change_check.py
```

Expected: Review PASS, full pytest PASS, system acceptance PASS, Hermes `Overall status: PASS`, and no new Graph/runtime writes.

- [ ] **Step 5: Verify protected invariants and reconcile the plan.**

Confirm clean worktree; unchanged SQLite SHA-256; unchanged frozen baseline `HKD 12,057,968`; unchanged formal scope 「不含掛賬核銷與TT退款轉團款」; and no new approval／dispatch／writer path. Update this plan’s task completion markers only after all evidence is present.

- [ ] **Step 6: Commit final documentation only if required.**

If verification produces a concrete documentation contract change, update the spec/plan in a separate documentation-only commit and re-run strict Review. Do not silently modify canonical artifacts or formal system documentation.
