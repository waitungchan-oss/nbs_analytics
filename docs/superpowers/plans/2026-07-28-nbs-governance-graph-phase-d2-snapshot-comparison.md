# NBS Governance Graph Phase D-2 Snapshot Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立兩個明確 immutable Governance Graph snapshot 之間的 deterministic、read-only comparison read model，供後續 Phase D-3 risk analysis 與 Phase D-4 change impact analysis 消費。

**Architecture:** 以 bounded immutable comparison models 表達兩側 snapshot identity、summary 與 sorted change records；以共用的 safe snapshot reader 讀取並驗證 run-contained `governance-graph.json`；CLI `compare` 只呼叫 comparison service，不建立 snapshot 或寫入任何 runtime state。

**Tech Stack:** Python 3.11、dataclasses、`GovernanceGraphSnapshot.from_dict()`、canonical SHA-256、argparse、pytest、既有 Hermes/system acceptance scripts。

## Global Constraints

- 只接受兩個明確 snapshot reference；不自動選擇 latest、current 或 fallback run。
- Graph 是 canonical artifacts 的 read-only derived projection；不得新增 approval、dispatch、runner、runtime control-plane 或 background writer。
- 不讀取 SQLite、Git、raw runtime artifact 或未驗證 canonical evidence。
- 不跨 run 推測 lineage、dependency、approval、causal relationship 或 impact。
- 不把 Graph schema 尚未提供的 edge 關係自行推導出來。
- 不修改 baseline、revenue scope、business rules、rollback 或 export schema。
- 正式口徑固定為「不含掛賬核銷與TT退款轉團款」；2026-05 baseline 固定為 `HKD 12,057,968`。
- 每個 Task 必須先寫 TDD、執行 focused tests、交 findings-first strict Review；Review PASS 後才可進入完整 pytest、system acceptance 與 Hermes。

---

## Inline Execution Protocol and Local Agent Boundaries

本 plan 預設採 **Inline Execution**：Codex 是 controller 與主要 implementation worker；本地 agent 只在下列明確邊界內協助，不會自動啟動另一套 Subagent-Driven worker。

### Per-Task sequence

1. Codex 先建立單一 approved Task contract 與 allowlisted files。
2. 執行 read-only Context collection，將 compact bundle 寫入 ignored `.nbs_agent_runtime/`；禁止把完整 repo 文件或 raw runtime rows 帶入主對話。
3. Codex 直接完成該 Task 的 TDD、最小實作與 focused verification。
4. Validation Runner 只執行 plan 列出的 `.venv/bin/python` compile／pytest 命令；不使用 LLM。
5. Review Agent 先以 `--collect-only` 收集 review evidence；只有在使用者明確批准 approved runner 時，才將 bounded review package 交給該 runner 產生 findings-first verdict。
6. Codex 處理 findings，重新執行 covering tests；Review PASS 後才進入下一個 Task。
7. Full pytest、`scripts/system_manager.py acceptance` 與 Hermes 集中在 Task 5，避免每個 Task 重複執行高成本驗證。

### Exact local commands

```bash
.venv/bin/python scripts/context_agent.py \
  --brief docs/superpowers/specs/2026-07-28-nbs-governance-graph-phase-d2-snapshot-comparison-design.md \
  --base main --collect-only --format json \
  --output .nbs_agent_runtime/reports/phase-d2-context.json

.venv/bin/python scripts/review_agent.py \
  --brief docs/superpowers/specs/2026-07-28-nbs-governance-graph-phase-d2-snapshot-comparison-design.md \
  --base <approved-base-sha> --head <approved-head-or-WORKTREE> \
  --context .nbs_agent_runtime/reports/phase-d2-context.json \
  --verification .nbs_agent_runtime/reports/phase-d2-verification.json \
  --strict --collect-only \
  --output .nbs_agent_runtime/reports/phase-d2-review-evidence.json
```

`<approved-base-sha>` 與 `<approved-head-or-WORKTREE>` 只可由 Codex 依當前 Task contract 解析，不可由 agent 自行選擇。若沒有 approved Review runner，狀態必須保持 `unknown`／`blocked_missing_runner`，不得宣稱 Review PASS。

Implementation Agent 是 optional，不是 Inline Execution 預設 worker。若日後明確批准使用 `scripts/implementation_agent.py`，仍必須逐 Task 提供 contract、approved worktree、allowedWritePaths 與 approved offline runner；Implementation Agent 不得 commit、merge、push、啟停服務或執行 Hermes。

Context Agent、Review Agent、Validation Runner 與 Hermes 都不得修改 source、SQLite、baseline、runtime evidence、Git 或正式業務資料；`.nbs_agent_runtime/` 只保存 bounded ignored evidence，不是 canonical source of truth。

## File map

- Create: `backend/agents/governance_graph_comparison_models.py` — immutable input/reference、identity、summary、change records、result 與 deterministic fingerprint。
- Create: `backend/agents/governance_graph_snapshot_reader.py` — run-contained snapshot loading、symlink/path/size/duplicate-key/schema/fingerprint validation；只讀。
- Create: `backend/agents/governance_graph_comparison_service.py` — two-snapshot diff、status precedence、no-inference 與 deterministic sorting。
- Modify: `backend/agents/governance_graph_query_service.py` — 改用 shared snapshot reader，不改變既有 D-1 query output contract。
- Modify: `scripts/governance_graph.py` — 新增 `compare` parser 與 read-only CLI envelope。
- Create: `tests/test_governance_graph_comparison_models.py` — model contract 與 fingerprint tests。
- Create: `tests/test_governance_graph_snapshot_reader.py` — reader safety and regression tests。
- Create: `tests/test_governance_graph_comparison_service.py` — diff semantics、status、ordering、no-write tests。
- Modify: `tests/test_governance_graph_query_service.py` — 確認 D-1 query 在 reader extraction 後維持原有 semantics。
- Modify: `tests/test_governance_graph_cli.py` — parser、JSON envelope、invalid exit code、no-write boundary。

## Task 1: Comparison contract models and deterministic fingerprint

**Files:**
- Create: `backend/agents/governance_graph_comparison_models.py`
- Test: `tests/test_governance_graph_comparison_models.py`

**Interfaces:**
- Produces `COMPARISON_SCHEMA = "governance-graph-comparison-v1"`。
- Produces immutable `GovernanceGraphSnapshotReference(run_id, snapshot_fingerprint)`。
- Produces immutable `GovernanceGraphComparisonResult.from_parts(...)`、`to_dict()` 與 `comparison_fingerprint` property。
- Change records expose `changeType`, `nodeId`／edge identity／evidence identity、`before`、`after`；added／removed 的另一側固定為 `None`。

- [ ] **Step 1: Write failing model tests.**

```python
def test_reference_requires_safe_run_id_and_optional_sha256():
    reference = GovernanceGraphSnapshotReference.from_dict({"runId": "run-before"})
    assert reference.to_dict() == {"runId": "run-before", "snapshotFingerprint": None}
    with pytest.raises(GovernanceGraphComparisonSchemaError):
        GovernanceGraphSnapshotReference.from_dict({"runId": "../escape"})

def test_comparison_fingerprint_is_order_sensitive_and_reproducible():
    first = _result(left_run_id="before", right_run_id="after")
    second = _result(left_run_id="before", right_run_id="after")
    reversed_result = _result(left_run_id="after", right_run_id="before")
    assert first.comparison_fingerprint == second.comparison_fingerprint
    assert first.comparison_fingerprint != reversed_result.comparison_fingerprint

def test_result_rejects_raw_or_absolute_metadata():
    with pytest.raises(GovernanceGraphComparisonSchemaError):
        _result(diagnostics=({"code": "bad", "summary": "/private/raw.json"},))
```

- [ ] **Step 2: Run model tests and verify RED.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_comparison_models.py -q`  
Expected: FAIL because the comparison models do not exist yet.

- [ ] **Step 3: Implement bounded immutable models.**

Implement exact allowlists for statuses (`available`, `unavailable`, `unknown`, `invalid`, `blocked`), change types (`added`, `removed`, `changed`), safe identifiers, lowercase SHA-256 and public metadata keys. Use the existing `canonical_sha256` helper and canonical sorted structures for `comparisonFingerprint`.

- [ ] **Step 4: Run focused model tests.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_comparison_models.py -q`  
Expected: PASS with deterministic fingerprints and no raw metadata leakage.

- [ ] **Step 5: Commit Task 1.**

```bash
git add backend/agents/governance_graph_comparison_models.py tests/test_governance_graph_comparison_models.py
git commit -m "feat: define governance graph comparison contract"
```

## Task 2: Shared safe snapshot reader and D-1 regression boundary

**Files:**
- Create: `backend/agents/governance_graph_snapshot_reader.py`
- Modify: `backend/agents/governance_graph_query_service.py`
- Create: `tests/test_governance_graph_snapshot_reader.py`
- Modify: `tests/test_governance_graph_query_service.py`

**Interfaces:**
- Produces `GovernanceGraphSnapshotReader(project_root, runtime_root=None)`。
- Produces `read(run_id, expected_fingerprint=None) -> SnapshotReadResult`，其中 result 明確區分 `available`、`unavailable`、`invalid`，並只暴露 bounded identity／diagnostic 與已驗證 `GovernanceGraphSnapshot`。
- Query service 改由 reader 取得 snapshot；既有 `GovernanceGraphQueryService.query(...)` signature 與 D-1 output 維持不變。

- [ ] **Step 1: Add reader safety tests before extraction.**

```python
def test_reader_missing_snapshot_is_unavailable_without_write(tmp_path):
    _write_run(tmp_path, "run-1")
    before = _tree_bytes(tmp_path)
    result = GovernanceGraphSnapshotReader(tmp_path).read("run-1")
    assert result.status == "unavailable"
    assert _tree_bytes(tmp_path) == before

@pytest.mark.parametrize("mutator", [_make_symlink, _make_duplicate_json, _make_bad_fingerprint])
def test_reader_rejects_unsafe_or_invalid_snapshot(tmp_path, mutator):
    _write_valid_snapshot(tmp_path)
    mutator(tmp_path / ".nbs_agent_runtime" / "runs" / "run-1" / "governance-graph.json")
    assert GovernanceGraphSnapshotReader(tmp_path).read("run-1").status == "invalid"
```

- [ ] **Step 2: Run reader tests and verify RED.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_snapshot_reader.py -q`  
Expected: FAIL because the shared reader does not exist yet.

- [ ] **Step 3: Extract D-1 read-only loading logic.**

Move the established regular-directory/file checks, run containment, 5 MiB snapshot cap, duplicate JSON key rejection, `GovernanceGraphSnapshot.from_dict()` validation and optional fingerprint match into the reader. Preserve dangling symlink rejection and never create missing directories or snapshots.

- [ ] **Step 4: Adapt Query Service without changing its contract.**

Replace only its private loading path with `GovernanceGraphSnapshotReader`; keep exact filters, canonical artifact mapping, deterministic ordering, status precedence and `governance-graph-query-v1` serialization unchanged.

- [ ] **Step 5: Run reader and D-1 regression tests.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_snapshot_reader.py tests/test_governance_graph_query_service.py -q`  
Expected: PASS; tree bytes remain identical for missing/invalid/read-only paths.

- [ ] **Step 6: Commit Task 2.**

```bash
git add backend/agents/governance_graph_snapshot_reader.py backend/agents/governance_graph_query_service.py tests/test_governance_graph_snapshot_reader.py tests/test_governance_graph_query_service.py
git commit -m "refactor: share safe governance graph snapshot reader"
```

## Task 3: Two-snapshot comparison service and diff semantics

**Files:**
- Create: `backend/agents/governance_graph_comparison_service.py`
- Test: `tests/test_governance_graph_comparison_service.py`

**Interfaces:**
- Consumes `GovernanceGraphSnapshotReader` and Task 1 models.
- Produces `GovernanceGraphComparisonService.compare(left_run_id, right_run_id, left_snapshot_fingerprint=None, right_snapshot_fingerprint=None) -> GovernanceGraphComparisonResult`。

- [ ] **Step 1: Write failing diff tests.**

```python
def test_same_snapshot_is_zero_diff_and_deterministic(tmp_path):
    _write_valid_snapshot(tmp_path, "run-1")
    result = GovernanceGraphComparisonService(tmp_path).compare(
        left_run_id="run-1", right_run_id="run-1"
    )
    assert result.status == "available"
    assert result.summary.unchanged_nodes >= 1
    assert result.node_changes == ()

def test_missing_side_is_unavailable_without_fallback(tmp_path):
    _write_valid_snapshot(tmp_path, "run-left")
    result = GovernanceGraphComparisonService(tmp_path).compare(
        left_run_id="run-left", right_run_id="run-missing"
    )
    assert result.status == "unavailable"
    assert result.node_changes == ()

def test_edges_are_not_inferred_when_snapshot_has_no_edges(tmp_path):
    _write_valid_snapshot(tmp_path, "run-left")
    _write_valid_snapshot(tmp_path, "run-right")
    result = GovernanceGraphComparisonService(tmp_path).compare(
        left_run_id="run-left", right_run_id="run-right"
    )
    assert result.edge_changes == ()
    assert result.summary.added_edges == result.summary.removed_edges == 0
```

- [ ] **Step 2: Run diff tests and verify RED.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_comparison_service.py -q`  
Expected: FAIL because the comparison service does not exist yet.

- [ ] **Step 3: Implement normalized record extraction.**

Convert validated nodes and evidence refs to bounded dictionaries. Key nodes by `nodeId`; key edges by `(source, target, type)` only if the validated snapshot exposes canonical edge records; key evidence by `(path, sha256)`. Do not derive any edge or artifact relationship from names or ordering.

- [ ] **Step 4: Implement added/removed/changed comparison.**

Compare normalized records with sorted keys. Emit only added, removed and changed records; count unchanged records without emitting them. For evidence identity matches, compare status and lifecycle timestamps. Keep before/after records bounded and use `None` for the absent side.

- [ ] **Step 5: Implement status precedence and fingerprints.**

Return `invalid > unavailable > blocked > unknown > available`; invalid/unavailable results contain no guessed diff. Compute result fingerprint from normalized references, both identities, summary and sorted changes. Preserve blocked/unknown statuses from validated node/evidence records.

- [ ] **Step 6: Run service tests and verify deterministic output.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_comparison_service.py tests/test_governance_graph_comparison_models.py -q`  
Expected: PASS, including reversed left/right fingerprint difference and no-write tree equality.

- [ ] **Step 7: Commit Task 3.**

```bash
git add backend/agents/governance_graph_comparison_service.py tests/test_governance_graph_comparison_service.py
git commit -m "feat: compare immutable governance graph snapshots"
```

## Task 4: Read-only CLI compare command

**Files:**
- Modify: `scripts/governance_graph.py`
- Modify: `tests/test_governance_graph_cli.py`

**Interfaces:**
- CLI command: `compare --left-run-id <id> --right-run-id <id> [--left-snapshot-fingerprint <sha256>] [--right-snapshot-fingerprint <sha256>]`。
- Produces outer `nbs-governance-graph-cli-v1` envelope with nested `governance-graph-comparison-v1` result。

- [ ] **Step 1: Add parser and no-write tests.**

```python
def test_parser_exposes_compare_with_explicit_sides():
    args = cli._parser().parse_args([
        "compare", "--left-run-id", "run-before", "--right-run-id", "run-after",
    ])
    assert (args.command, args.left_run_id, args.right_run_id) == (
        "compare", "run-before", "run-after"
    )

def test_compare_emits_exact_read_only_envelope(tmp_path, monkeypatch, capsys):
    _write_valid_snapshot(tmp_path, "run-before")
    _write_valid_snapshot(tmp_path, "run-after")
    before = _tree_bytes(tmp_path)
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    assert cli.main(["compare", "--left-run-id", "run-before", "--right-run-id", "run-after"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["result"]["schemaVersion"] == "governance-graph-comparison-v1"
    assert _tree_bytes(tmp_path) == before
```

- [ ] **Step 2: Run CLI tests and verify RED.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_cli.py -q`  
Expected: FAIL because `compare` is not registered.

- [ ] **Step 3: Implement CLI wiring.**

Register only the four explicit compare arguments, instantiate `GovernanceGraphComparisonService` in the compare branch, return the result envelope, and map result statuses through the existing read-only exit-code policy. Do not instantiate `GovernanceGraphBuilder` for compare.

- [ ] **Step 4: Run CLI and boundary tests.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_cli.py tests/test_governance_graph_comparison_service.py -q`  
Expected: PASS; invalid input returns a comparison `status=invalid` result and no snapshot is created.

- [ ] **Step 5: Commit Task 4.**

```bash
git add scripts/governance_graph.py tests/test_governance_graph_cli.py
git commit -m "feat: expose governance graph snapshot comparison cli"
```

## Task 5: Final verification, strict review and acceptance

**Files:**
- Read-only review of all D-2 diff and approved spec/plan.
- No additional source files are authorized unless a review finding identifies a concrete contract gap.

- [ ] **Step 1: Run affected compile and focused tests.**

```bash
.venv/bin/python -m py_compile \
  backend/agents/governance_graph_comparison_models.py \
  backend/agents/governance_graph_snapshot_reader.py \
  backend/agents/governance_graph_comparison_service.py \
  backend/agents/governance_graph_query_service.py \
  scripts/governance_graph.py
.venv/bin/python -m pytest \
  tests/test_governance_graph_comparison_models.py \
  tests/test_governance_graph_snapshot_reader.py \
  tests/test_governance_graph_comparison_service.py \
  tests/test_governance_graph_query_service.py \
  tests/test_governance_graph_cli.py -q
git diff --check
```

- [ ] **Step 2: Submit actual diff and evidence to Review Agent.**

Review must use findings-first format and verify: explicit two-side inputs, no fallback/latest selection, canonical exact keys, no edge inference, bounded output, deterministic fingerprint, status precedence, D-1 regression, and no writes to SQLite/baseline/runtime/Git control-plane.

- [ ] **Step 3: Resolve findings and rerun affected tests.**

Any Critical or Important finding blocks full verification. Re-run the focused command from Step 1 after each correction and stop if the approved contract would need to change.

- [ ] **Step 4: Run full verification.**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/hermes_post_change_check.py
```

Expected: full pytest PASS, system acceptance PASS, Hermes `Overall status: PASS`; any existing degraded monitor issue must be reported separately and not hidden.

- [ ] **Step 5: Confirm immutable data boundaries.**

Record `git status --short`, SQLite SHA before/after, baseline result (`HKD 12,057,968`) and formal revenue scope. Confirm comparison requests do not create `governance-graph.json` when either side is missing and do not alter canonical artifacts.

- [ ] **Step 6: Mark plan reconciliation and stop before integration.**

After Review/full verification/Hermes PASS, mark Tasks 1–5 completed in the plan and report the branch, commits, tests and acceptance evidence. Do not push, merge or delete the feature branch without explicit user authorization.
