# Verified Documentation Backfill Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create an auditable, commit-bound completed run that can safely drive the Documentation Agent for a real Obsidian Brief and System Map backfill.

**Architecture:** A deterministic verifier re-runs approved read-only gates against the current clean `main` commit, fingerprints bounded evidence, and creates normal workflow artifacts only after every gate passes. A constrained `codex` adapter then receives only documentation evidence and emits a strict proposal; the existing validator/controller retain preview and explicit target approval.

**Tech Stack:** Python 3.10, existing WorkflowStore/WorkflowModels, subprocess `codex`, pytest, SQLite read-only validation, existing Hermes scripts.

## Global Constraints

- Formal scope is `不含掛賬核銷與TT退款轉團款`; 2026-05 baseline is `HKD 12,057,968`.
- Backfill must reject dirty worktree, non-main branch, non-HEAD commit, failed/missing gate, or mismatched evidence hash.
- No SQLite writes, upload, rollback, baseline change, Hermes state change, Git mutation, or automatic System Map/ADR application.
- Obsidian root is local-only; serialized artifacts never contain an absolute vault path or runner command.
- Documentation runner input is capped at 8,000 estimated tokens; output at 1,500 tokens, 64 KiB, 120 seconds.
- System Map remains explicit `--approve-target system_map`; ADR remains proposal-only in this plan.

---

## File Structure

- `backend/agents/verified_backfill_models.py`: immutable manifest/evidence schema and canonical hash validation.
- `backend/agents/verified_backfill_service.py`: clean-main verification, bounded command execution, normal workflow artifact creation.
- `scripts/verified_documentation_backfill.py`: JSON-only local CLI to create a backfill run.
- `backend/agents/documentation_codex_runner.py`: fixed-instruction `codex` adapter that turns evidence stdin into proposal stdout.
- `backend/agents/documentation_agent_service.py`: delegate approved `codex` argv to the adapter without changing current allowlist/limits.
- `scripts/documentation_agent.py`: opt-in adapter selection, no persisted command.
- `tests/test_verified_backfill_models.py`, `tests/test_verified_backfill_service.py`, `tests/test_verified_documentation_backfill_cli.py`, `tests/test_documentation_codex_runner.py`: focused contract and security coverage.
- `docs/agents/VERIFIED_DOCUMENTATION_BACKFILL.md`: operator procedure and first-production checklist.

### Task 1: Verified Backfill Manifest and Store Boundary

**Files:**
- Create: `backend/agents/verified_backfill_models.py`
- Modify: `backend/agents/workflow_store.py`
- Create: `tests/test_verified_backfill_models.py`
- Modify: `tests/test_workflow_store.py`

**Interfaces:**
- Produces `VerifiedBackfillManifest.from_dict()` and `to_dict()`.
- Adds only `verified-backfill.json` to the WorkflowStore artifact allowlist.

- [ ] **Step 1: Write RED schema tests**

```python
def test_verified_backfill_manifest_rejects_non_main_or_dirty_state():
    with pytest.raises(ValueError, match="main"):
        VerifiedBackfillManifest.from_dict({**payload(), "sourceBranch": "codex/test"})
    with pytest.raises(ValueError, match="dirtyFiles"):
        VerifiedBackfillManifest.from_dict({**payload(), "dirtyFiles": [{"path": "x.py"}]})
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/test_verified_backfill_models.py -q`

Expected: import failure because the model does not exist.

- [ ] **Step 3: Implement immutable schema**

```python
@dataclass(frozen=True)
class VerifiedBackfillManifest:
    source_commit: str
    source_branch: str
    dirty_files: tuple[dict[str, str], ...]
    gate_hashes: dict[str, str]
    review_hash: str
```

Require 40-char lowercase commit SHA, `source_branch == "main"`, empty dirty files, and SHA-256 hashes for `pytest`, `systemAcceptance`, `hermes`, and `review`.

- [ ] **Step 4: Add Store allowlist test**

```python
store.write_artifact("run-backfill", "verified-backfill.json", manifest.to_dict())
assert store._read_json(store._run_file("run-backfill", "verified-backfill.json"))["sourceBranch"] == "main"
```

- [ ] **Step 5: Verify and commit**

Run: `.venv/bin/python -m pytest tests/test_verified_backfill_models.py tests/test_workflow_store.py -q`

Run: `.venv/bin/python -m py_compile backend/agents/verified_backfill_models.py backend/agents/workflow_store.py`

Commit: `feat: define verified documentation backfill manifest`

### Task 2: Deterministic Backfill Verifier and JSON CLI

**Files:**
- Create: `backend/agents/verified_backfill_service.py`
- Create: `scripts/verified_documentation_backfill.py`
- Create: `tests/test_verified_backfill_service.py`
- Create: `tests/test_verified_documentation_backfill_cli.py`

**Interfaces:**
- Produces `VerifiedBackfillService.create(source_commit: str, reason: str) -> dict`.
- Runs only fixed argv lists for `git`, pytest, system acceptance, Hermes, and phase2 baseline checks.

- [ ] **Step 1: Write RED gate tests**

```python
def test_create_blocks_dirty_main_before_writing_run(service):
    result = service.create(source_commit="a" * 40, reason="documentation backfill")
    assert result["status"] == "blocked"
    assert result["reason"] == "dirty_worktree"
    assert list(service.store.runs_root.iterdir()) == []
```

Add fixtures for non-main, stale commit, failed pytest, failed Hermes, failed baseline, and review verdict not PASS. Assert no run directory for every blocked outcome.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/test_verified_backfill_service.py tests/test_verified_documentation_backfill_cli.py -q`

Expected: import failure because the service/CLI do not exist.

- [ ] **Step 3: Implement verifier**

```python
def create(self, *, source_commit: str, reason: str) -> dict[str, object]:
    identity = self._clean_main_identity(source_commit)
    gates = self._run_fixed_gates()
    review = self._collect_review(identity, gates)
    if not self._all_pass(gates, review):
        return {"status": "blocked", "reason": self._first_failure(gates, review)}
    return self._create_completed_run(identity, gates, review, reason)
```

The completed run must contain standard `approval.json`, `implementation.json`, `targeted-verification.json`, `review.json`, `full-verification.json`, `hermes.json`, plus `verified-backfill.json`; all values are bounded summaries and canonical hashes. The CLI accepts `--source-commit`, `--reason`, `--no-notify`, emits one redacted JSON document, and never accepts arbitrary command strings.

- [ ] **Step 4: Verify and commit**

Run: `.venv/bin/python -m pytest tests/test_verified_backfill_service.py tests/test_verified_documentation_backfill_cli.py tests/test_documentation_evidence.py -q`

Run: `.venv/bin/python -m py_compile backend/agents/verified_backfill_service.py scripts/verified_documentation_backfill.py`

Commit: `feat: create verified documentation backfill runs`

### Task 3: Constrained Codex Documentation Runner

**Files:**
- Create: `backend/agents/documentation_codex_runner.py`
- Modify: `backend/agents/documentation_agent_service.py`
- Modify: `scripts/documentation_agent.py`
- Create: `tests/test_documentation_codex_runner.py`
- Modify: `tests/test_documentation_agent_cli.py`

**Interfaces:**
- `CodexDocumentationRunner.run(argv, *, input_text, timeout_seconds, max_output_bytes) -> DocumentationRunnerResult`.
- Only activates for an explicitly supplied `codex` command.

- [ ] **Step 1: Write RED adapter tests**

```python
def test_runner_passes_evidence_only_and_rejects_non_json(fake_subprocess):
    result = CodexDocumentationRunner(fake_subprocess).run(("codex",), input_text='{"schemaVersion":"documentation-evidence-v1"}', timeout_seconds=120, max_output_bytes=65536)
    assert fake_subprocess.stdin_payload == '{"schemaVersion":"documentation-evidence-v1"}'
    assert result.exit_code != 0
```

Add tests for exact schema/fingerprint, output cap, timeout, command redaction, and no vault path in argv/persisted telemetry.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/test_documentation_codex_runner.py tests/test_documentation_agent_cli.py -q`

Expected: import failure because the adapter does not exist.

- [ ] **Step 3: Implement fixed instruction adapter**

The adapter invokes `codex` with a fixed local instruction that demands only `documentation-proposal-v1`; evidence remains stdin. It must not grant tools or pass local paths beyond the project cwd already enforced by the existing subprocess boundary. Existing `DocumentationAgentService` remains the sole validator and cache writer.

- [ ] **Step 4: Verify and commit**

Run: `.venv/bin/python -m pytest tests/test_documentation_codex_runner.py tests/test_documentation_agent_service.py tests/test_documentation_agent_cli.py -q`

Run: `.venv/bin/python -m py_compile backend/agents/documentation_codex_runner.py backend/agents/documentation_agent_service.py scripts/documentation_agent.py`

Commit: `feat: add constrained codex documentation runner`

### Task 4: Real Backfill Procedure and Controlled Apply

**Files:**
- Create: `docs/agents/VERIFIED_DOCUMENTATION_BACKFILL.md`
- Modify: `docs/agents/CODEX_AGENT_DISPATCH.md`
- Modify: `tests/test_documentation_agent_docs.py`
- Create: `tests/test_verified_backfill_integration.py`

**Interfaces:**
- Documents one exact operator sequence: backfill create, proposal, preview, `--apply-brief`, `--approve-target system_map`, Review, Hermes.

- [ ] **Step 1: Write RED integration test**

```python
def test_verified_backfill_can_preview_then_apply_only_approved_targets(tmp_path):
    run = create_verified_run(tmp_path)
    preview = documentation_workflow.run(run, agent_command="codex")
    assert preview["status"] == "preview_ready"
    assert apply_with_system_map_approval(run)["status"] == "applied"
```

Assert an apply without `system_map` approval leaves that file byte-identical; assert serialized application records omit the temporary vault's absolute path.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/test_verified_backfill_integration.py -q`

Expected: integration fixture/helper is missing.

- [ ] **Step 3: Add operator procedure and integration fixture**

Document local-only vault configuration, exact CLI sequence, blocked outcomes, Review/Hermes role split, and cleanup policy. The integration test uses a temporary vault only and never writes the real vault.

- [ ] **Step 4: Verify and commit**

Run: `.venv/bin/python -m pytest tests/test_verified_backfill_integration.py tests/test_documentation_agent_docs.py tests/test_documentation_workflow.py -q`

Run: `.venv/bin/python scripts/hermes_post_change_check.py`

Commit: `docs: add verified documentation backfill procedure`

### Task 5: Production Backfill Evidence and First Proposal

**Files:**
- Runtime only: `.nbs_agent_runtime/runs/<run-id>/...`
- Local only: `.nbs_agent_runtime/documentation.local.json`
- Obsidian only: approved Brief target under the configured vault
- Modify only after preview: `NBS_ANALYTICS_SYSTEM_MAP.md`

**Interfaces:**
- Uses Task 2 CLI and Task 3 runner; no new production code.

- [ ] **Step 1: Create a verified run on clean main**

Run: `.venv/bin/python scripts/verified_documentation_backfill.py --source-commit HEAD --reason "Documentation Agent production backfill" --no-notify`

Expected: JSON `status=completed` with a run ID and no absolute vault path.

- [ ] **Step 2: Configure local vault and generate preview**

Run: `.venv/bin/python scripts/documentation_agent.py --run-id <run-id> --agent-command "codex" --obsidian-vault "/Users/chanwaitung2025/Documents/Obsidian Vault"`

Expected: `preview_ready`; do not write targets yet.

- [ ] **Step 3: Review proposal and apply authorized targets**

Run Review Agent on the backfill evidence/proposal/preview. After a PASS, run:

```bash
.venv/bin/python scripts/documentation_agent.py \
  --run-id <run-id> --agent-command "codex" \
  --obsidian-vault "/Users/chanwaitung2025/Documents/Obsidian Vault" \
  --apply-brief --approve-target system_map
```

Expected: Brief and System Map application hashes match preview; no ADR write.

- [ ] **Step 4: Final acceptance and commit only tracked System Map change**

Run: `.venv/bin/python -m pytest -q`

Run: `.venv/bin/python scripts/hermes_post_change_check.py`

Run: `.venv/bin/python scripts/system_manager.py acceptance`

Commit any approved tracked `NBS_ANALYTICS_SYSTEM_MAP.md` update separately; never commit local vault or runtime artifacts.

## Plan Self-Review

- Every spec boundary maps to one of Tasks 1–5.
- No task permits manual status mutation, arbitrary runner command, DB/baseline write, ADR auto-apply, or absolute vault persistence.
- Task 5 is the only real-vault action and requires prior preview and explicit System Map approval.
