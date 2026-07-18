# Documentation Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立一個由 Codex 自動或按需呼叫的獨立 Documentation Agent，僅根據已驗證 workflow evidence 產生 Obsidian Brief、system map 與 ADR proposal，再由受治理的本地 Controller 分級套用。

**Architecture:** 沿用現有 `.nbs_agent_runtime/runs/<run-id>/` 與 Evidence Bundle pipeline，先以 deterministic Collector / classifier 建立 `documentation-evidence-v1`，再把 compact evidence 交給受限 LLM runner 產生 `documentation-proposal-v1`。Agent 永遠不寫檔；validator 建立 preview，trusted Controller 只對 allowlisted Markdown target 做 hash-guarded、atomic、可備份套用。

**Tech Stack:** Python 3.10、標準函式庫 `dataclasses` / `json` / `hashlib` / `pathlib` / `subprocess` / `tempfile` / `os`、pytest、現有 Workflow Store / Agent runtime / Agent Operations / Hermes contracts、Markdown managed blocks。

## Global Constraints

- 正式口徑固定為「不含掛賬核銷與TT退款轉團款」。
- 2026-05 baseline 固定為 `HKD 12,057,968`。
- Documentation Agent runner 只可讀取 compact `documentation-evidence-v1`，不得讀寫 repo、Obsidian、SQLite、runtime、Git 或 network。
- Agent 只產生 proposal；只有 trusted Controller 可以寫入 allowlisted Markdown target。
- 未提供 approved Documentation runner 時必須 `blocked_missing_runner`，不得由主 Codex LLM 靜默代寫。
- Brief backfill 可由 Codex 在既有任務授權內套用；system map 與 ADR 必須另有明確 target approval。
- ADR 只可 create new，禁止覆寫、刪除、rename 既有 ADR。
- Obsidian vault 絕對路徑不得寫入 tracked source 或 runtime artifact。
- Streamlit Agent Operations 維持 read-only；Hermes 不執行 Documentation Agent 或 apply。
- 不修改正式 SQLite、upload、rollback、revenue、business rules、export schema、baseline 或產品計算。

---

### Task 1: Documentation Contracts, Models, and Policy

**Files:**
- Create: `docs/agents/DOCUMENTATION_AGENT_CONTRACT.md`
- Create: `backend/agents/documentation_models.py`
- Create: `agent_config/documentation_policies.json`
- Create: `tests/test_documentation_models.py`
- Modify: `backend/agents/__init__.py`

**Interfaces:**
- Consumes: existing `canonical_sha256()` semantics from `backend/agents/workflow_models.py`.
- Produces: `DocumentationEvidence.from_dict()`, `DocumentationProposal.from_dict()`, `DocumentationApplication.from_dict()`, `DocumentationTargetPolicy.from_dict()`, and constants for all three schema versions.

- [ ] **Step 1: Write RED schema tests**

Create strict tests proving valid round trips and rejection of unknown fields, bad hashes, unknown target kinds, invalid operations, duplicate target identities, and a proposal whose fingerprint differs from its evidence.

```python
from backend.agents.documentation_models import (
    DOCUMENTATION_EVIDENCE_SCHEMA,
    DocumentationEvidence,
    DocumentationProposal,
    DocumentationSchemaError,
)


def test_documentation_evidence_round_trip(valid_evidence_payload):
    model = DocumentationEvidence.from_dict(valid_evidence_payload)
    assert model.schema_version == DOCUMENTATION_EVIDENCE_SCHEMA
    assert model.to_dict() == valid_evidence_payload


def test_proposal_rejects_unknown_target_kind(valid_proposal_payload):
    valid_proposal_payload["proposals"][0]["targetKind"] = "sqlite"
    with pytest.raises(DocumentationSchemaError, match="targetKind"):
        DocumentationProposal.from_dict(valid_proposal_payload)
```

- [ ] **Step 2: Run Task 1 tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_documentation_models.py -q
```

Expected: collection fails because `backend.agents.documentation_models` does not exist.

- [ ] **Step 3: Implement strict immutable models**

Define these exact constants and allowed values:

```python
DOCUMENTATION_EVIDENCE_SCHEMA = "documentation-evidence-v1"
DOCUMENTATION_PROPOSAL_SCHEMA = "documentation-proposal-v1"
DOCUMENTATION_APPLICATION_SCHEMA = "documentation-application-v1"
DOCUMENTATION_POLICY_SCHEMA = "documentation-policy-v1"

TARGET_KINDS = frozenset({"brief_backfill", "system_map", "adr"})
OPERATIONS = frozenset({"update_managed_block", "replace_section", "create_file"})
PROPOSAL_STATUSES = frozenset({
    "ready", "no_documentation_needed", "blocked",
    "context_overflow", "invalid_agent_output",
})
APPLICATION_STATUSES = frozenset({
    "preview_ready", "awaiting_target_approval", "applied",
    "partially_applied", "blocked",
})
```

Use frozen dataclasses and exact-key validation. Normalize tuples, validate lower-case SHA-256, require timezone-aware ISO-8601 timestamps, and make `to_dict()` return canonical JSON-compatible objects. Reuse `canonical_sha256` rather than creating a second hash format.

- [ ] **Step 4: Add the tracked policy**

Create `agent_config/documentation_policies.json` with this exact governance surface:

```json
{
  "schemaVersion": "documentation-policy-v1",
  "tokenBudget": {"maxInputTokens": 8000, "maxOutputTokens": 1500},
  "targets": {
    "brief_backfill": {
      "riskTier": "low",
      "operations": ["update_managed_block"],
      "repoRoots": ["docs/briefs"],
      "obsidianSubdirectory": "70_Codex_Briefs",
      "requiresExplicitTargetApproval": false
    },
    "system_map": {
      "riskTier": "high",
      "operations": ["replace_section"],
      "repoPaths": ["NBS_ANALYTICS_SYSTEM_MAP.md"],
      "obsidianSubdirectory": "10_System",
      "requiresExplicitTargetApproval": true
    },
    "adr": {
      "riskTier": "high",
      "operations": ["create_file"],
      "repoRoots": ["Summay"],
      "obsidianSubdirectory": "20_Decisions",
      "requiresExplicitTargetApproval": true
    }
  },
  "protectedText": {
    "revenueScope": "不含掛賬核銷與TT退款轉團款",
    "mayBaseline": "HKD 12,057,968"
  }
}
```

- [ ] **Step 5: Write the formal Agent contract**

`DOCUMENTATION_AGENT_CONTRACT.md` must define required input/output JSON, the 8k/1.5k Token limits, no-tool runner boundary, allowed target kinds, blocked states, no-main-LLM-fallback rule, and the division between Agent proposal and Controller apply. It must link to the approved design spec.

- [ ] **Step 6: Run focused tests and review**

```bash
.venv/bin/python -m pytest tests/test_documentation_models.py tests/test_agent_dispatch_contract.py -q
.venv/bin/python -m py_compile backend/agents/documentation_models.py
git diff --check
```

Review findings-first for schema looseness, alternate baseline strings, unknown-field acceptance, and any write capability in the Agent contract.

- [ ] **Step 7: Commit Task 1**

```bash
git add docs/agents/DOCUMENTATION_AGENT_CONTRACT.md backend/agents/documentation_models.py backend/agents/__init__.py agent_config/documentation_policies.json tests/test_documentation_models.py
git commit -m "feat: define documentation agent contracts"
```

---

### Task 2: Verified Evidence Collector and Impact Classifier

**Files:**
- Create: `backend/agents/documentation_evidence.py`
- Create: `backend/agents/documentation_policy.py`
- Create: `tests/test_documentation_evidence.py`
- Create: `tests/test_documentation_policy.py`

**Interfaces:**
- Consumes: `WorkflowStore`, `WorkflowStatus`, Task 1 models/policy, completed run artifacts.
- Produces: `DocumentationEvidenceCollector.collect(run_id: str) -> DocumentationEvidence` and `DocumentationImpactClassifier.classify(changed_paths: tuple[str, ...], evidence: dict) -> dict`.

- [ ] **Step 1: Write RED gate and redaction tests**

Use temporary workflow fixtures for completed and incomplete runs. Assert that only a run with Review PASS, full verification PASS, Hermes PASS, and completed status can produce evidence.

```python
def test_collector_requires_all_verified_gates(completed_run_fixture):
    completed_run_fixture.write_artifact("hermes.json", {"overallStatus": "fail"})
    collector = DocumentationEvidenceCollector(completed_run_fixture.project_root)
    with pytest.raises(DocumentationEvidenceError, match="Hermes"):
        collector.collect(completed_run_fixture.run_id)


def test_collector_never_exposes_raw_outputs(completed_run_fixture):
    evidence = DocumentationEvidenceCollector(
        completed_run_fixture.project_root
    ).collect(completed_run_fixture.run_id).to_dict()
    encoded = json.dumps(evidence, ensure_ascii=False)
    assert "stdoutTail" not in encoded
    assert "runner command" not in encoded
    assert "transactionRows" not in encoded
```

- [ ] **Step 2: Write RED deterministic classification tests**

Cover these exact outcomes:

```python
@pytest.mark.parametrize(
    ("paths", "runner_required", "required_targets"),
    [
        (("tests/test_x.py",), False, ()),
        (("backend/routers/dashboard.py",), True, ("brief_backfill", "system_map")),
        (("backend/agents/workflow_models.py",), True, ("brief_backfill", "system_map")),
        (("database.py",), True, ("brief_backfill", "system_map", "adr")),
        (("docs/readme.md",), False, ()),
    ],
)
def test_classification(paths, runner_required, required_targets, classifier):
    result = classifier.classify(paths, evidence={"riskSurfaces": []})
    assert result["runnerRequired"] is runner_required
    assert tuple(result["requiredTargets"]) == required_targets
```

Add explicit risk-surface tests for `baseline`, `revenue_scope`, `permission`, `security`, `retention`, and `state_machine`.

- [ ] **Step 3: Run Task 2 tests and verify RED**

```bash
.venv/bin/python -m pytest tests/test_documentation_evidence.py tests/test_documentation_policy.py -q
```

Expected: both modules are missing.

- [ ] **Step 4: Implement bounded verified collection**

Use a fixed artifact allowlist and fixed summary keys:

```python
REQUIRED_ARTIFACTS = (
    "manifest.json",
    "status.json",
    "approval.json",
    "implementation.json",
    "targeted-verification.json",
    "review.json",
    "full-verification.json",
    "hermes.json",
)


class DocumentationEvidenceCollector:
    def __init__(self, project_root: Path, *, store: WorkflowStore | None = None): ...
    def collect(self, run_id: str) -> DocumentationEvidence: ...
```

Read artifacts through `WorkflowStore`, never arbitrary filenames. Keep only command names + exit codes, requirement coverage, bounded summaries, changed paths, hashes and gate results. Compute `documentationFingerprint` from canonical evidence excluding its own fingerprint field.

- [ ] **Step 5: Implement deterministic routing policy**

Map path groups and evidence risk surfaces without LLM. `tests/`, docs-only and format-only changes return `runnerRequired=false`; code/API/data-flow/agent-workflow changes require Brief and conditionally system map; protected governance surfaces require all three proposals. Unknown code paths default to Brief + conditional system map, never ADR by default.

- [ ] **Step 6: Run focused tests and review**

```bash
.venv/bin/python -m pytest tests/test_documentation_evidence.py tests/test_documentation_policy.py tests/test_workflow_store.py tests/test_workflow_models.py -q
.venv/bin/python -m py_compile backend/agents/documentation_evidence.py backend/agents/documentation_policy.py
git diff --check
```

Review for arbitrary artifact reads, raw command output, full patches, unbounded evidence, incorrect test-only skip, and any LLM-based permission decision.

- [ ] **Step 7: Commit Task 2**

```bash
git add backend/agents/documentation_evidence.py backend/agents/documentation_policy.py tests/test_documentation_evidence.py tests/test_documentation_policy.py
git commit -m "feat: collect verified documentation evidence"
```

---

### Task 3: Independent Documentation Agent Runner and Cache

**Files:**
- Create: `backend/agents/documentation_agent_service.py`
- Create: `tests/test_documentation_agent_service.py`
- Modify: `backend/agents/agent_runtime.py`
- Modify: `agent_config/token_budgets.json`

**Interfaces:**
- Consumes: Task 1/2 models and `DocumentationEvidence`.
- Produces: `DocumentationAgentService.draft(evidence, *, agent_command: str | None) -> DocumentationProposal` and bounded telemetry.

- [ ] **Step 1: Write RED runner boundary tests**

Test valid JSON, missing runner, non-zero exit, timeout, invalid schema, fingerprint mismatch, unapproved target, output over budget, and cache hit. Use a fake subprocess runner; do not call a network model.

```python
def test_missing_runner_is_blocked_without_main_llm_fallback(evidence, service):
    proposal = service.draft(evidence, agent_command=None)
    assert proposal.status == "blocked"
    assert proposal.warnings == ("blocked_missing_runner",)


def test_runner_receives_only_evidence_json(evidence, fake_runner, service):
    service.draft(evidence, agent_command="approved-doc-runner")
    payload = json.loads(fake_runner.stdin_text)
    assert payload["schemaVersion"] == "documentation-evidence-v1"
    assert "prompt" not in payload
    assert "absoluteVaultPath" not in payload
```

- [ ] **Step 2: Run Task 3 tests and verify RED**

```bash
.venv/bin/python -m pytest tests/test_documentation_agent_service.py -q
```

Expected: module does not exist.

- [ ] **Step 3: Implement the runner service**

Use this public surface:

```python
@dataclass(frozen=True)
class DocumentationRunnerResult:
    exit_code: int
    stdout: str
    stderr_tail: str
    duration_ms: int


class DocumentationAgentService:
    def __init__(self, project_root: Path, *, runner=None, runtime=None): ...
    def draft(
        self,
        evidence: DocumentationEvidence,
        *,
        agent_command: str | None,
    ) -> DocumentationProposal: ...
```

Parse command with the same approved-runner rules as Context/Review; pass evidence JSON only on stdin; enforce timeout and output byte cap; require exact fingerprint; validate target kinds against classifier output. Save proposal/cache under `.nbs_agent_runtime/documentation/` keyed by fingerprint. Do not persist command, full stderr or external path.

- [ ] **Step 4: Add Token budget and telemetry**

Add `documentationInput=8000` and `documentationOutput=1500` to the existing budget config without changing Context/Review/Implementation values. Telemetry must contain only:

```python
{
    "schemaVersion": "documentation-telemetry-v1",
    "runId": evidence.run_id,
    "documentationFingerprint": evidence.documentation_fingerprint,
    "inputCharacters": input_characters,
    "estimatedInputTokens": estimated_input_tokens,
    "outputTokens": supplied_output_tokens,
    "proposalCount": len(proposal.proposals),
    "cacheHit": cache_hit,
    "durationMs": duration_ms,
    "result": proposal.status,
}
```

- [ ] **Step 5: Run focused tests and review**

```bash
.venv/bin/python -m pytest tests/test_documentation_agent_service.py tests/test_agent_runtime.py tests/test_context_agent_service.py tests/test_review_agent_service.py -q
.venv/bin/python -m py_compile backend/agents/documentation_agent_service.py backend/agents/agent_runtime.py
git diff --check
```

Review runner argv handling, persistence, Token limits, cache invalidation, unauthorized target escalation and any implicit Codex fallback.

- [ ] **Step 6: Commit Task 3**

```bash
git add backend/agents/documentation_agent_service.py backend/agents/agent_runtime.py agent_config/token_budgets.json tests/test_documentation_agent_service.py
git commit -m "feat: draft governed documentation proposals"
```

---

### Task 4: Proposal Validation, Preview, and Obsidian Resolver

**Files:**
- Create: `backend/agents/documentation_validator.py`
- Create: `backend/agents/documentation_targets.py`
- Create: `tests/test_documentation_validator.py`
- Create: `tests/test_documentation_targets.py`

**Interfaces:**
- Consumes: validated Task 3 proposal, tracked policy, explicit/local Obsidian configuration.
- Produces: `DocumentationProposalValidator.build_preview(...) -> DocumentationPreview` and `ObsidianTargetResolver.resolve(...) -> ResolvedTarget` without writes.

- [ ] **Step 1: Write RED path and content safety tests**

Cover vault CLI/env/local-config priority, missing vault, symlink root/target, traversal, unknown subdirectory, stale base hash, duplicate system-map heading, multi-section replacement, baseline mutation, secret-like content, raw transaction rows and ADR overwrite.

```python
def test_system_map_requires_exact_section_hash(project_root, ready_proposal):
    ready_proposal = replace(ready_proposal, proposals=(system_map_proposal(base_sha="0" * 64),))
    with pytest.raises(DocumentationValidationError, match="stale_target"):
        DocumentationProposalValidator(project_root).build_preview(ready_proposal)


def test_vault_traversal_is_denied(tmp_path):
    resolver = ObsidianTargetResolver(tmp_path / "vault")
    with pytest.raises(PermissionError):
        resolver.resolve("brief_backfill", "../outside.md")
```

- [ ] **Step 2: Run Task 4 tests and verify RED**

```bash
.venv/bin/python -m pytest tests/test_documentation_validator.py tests/test_documentation_targets.py -q
```

Expected: modules do not exist.

- [ ] **Step 3: Implement target resolution**

Use this precedence and API:

```python
class ObsidianTargetResolver:
    @classmethod
    def from_sources(
        cls,
        project_root: Path,
        *,
        cli_root: Path | None,
        environ: Mapping[str, str],
    ) -> "ObsidianTargetResolver | None": ...

    def resolve(self, target_kind: str, relative_name: str) -> Path: ...
```

Read `.nbs_agent_runtime/documentation.local.json` only when CLI and `NBS_OBSIDIAN_VAULT` are absent. Resolve root and target, reject symlinks and escapes, and return no absolute path in serialized diagnostics.

- [ ] **Step 4: Implement pure preview building**

Define:

```python
@dataclass(frozen=True)
class DocumentationPreviewItem:
    target_kind: str
    path_identity: str
    vault_relative_path: str | None
    before_sha256: str | None
    after_sha256: str
    unified_diff: str
    risk_tier: str
    required_approval: str | None


class DocumentationProposalValidator:
    def build_preview(
        self,
        proposal: DocumentationProposal,
        *,
        obsidian: ObsidianTargetResolver | None = None,
    ) -> DocumentationPreview: ...
```

Implement exact managed-block replacement for Brief, one-heading section replacement for system map, and create-only ADR preview. Reject changes to protected text and redact unsafe evidence before diff generation. Preview is read-only and contains no writable `Path` objects in its serialized form.

- [ ] **Step 5: Run focused tests and review**

```bash
.venv/bin/python -m pytest tests/test_documentation_validator.py tests/test_documentation_targets.py tests/test_documentation_models.py -q
.venv/bin/python -m py_compile backend/agents/documentation_validator.py backend/agents/documentation_targets.py
git diff --check
```

Review path normalization, Unicode filenames, duplicate headings, managed-block idempotency, protected strings, secret redaction and accidental writes during preview.

- [ ] **Step 6: Commit Task 4**

```bash
git add backend/agents/documentation_validator.py backend/agents/documentation_targets.py tests/test_documentation_validator.py tests/test_documentation_targets.py
git commit -m "feat: validate documentation previews"
```

---

### Task 5: Trusted Atomic Apply Controller

**Files:**
- Create: `backend/agents/documentation_controller.py`
- Create: `tests/test_documentation_controller.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: Task 4 `DocumentationPreview`, explicit apply flags and target approvals.
- Produces: `DocumentationController.apply(...) -> DocumentationApplication`, bounded backups and before/after hashes.

- [ ] **Step 1: Write RED authorization and atomicity tests**

Test low-risk Brief apply, unapproved system map/ADR, ADR create-only, exact-hash stale detection, idempotent reapply, atomic replace, backup creation, simulated second-target failure and vault-relative application records.

```python
def test_high_risk_target_requires_explicit_approval(
    controller, system_map_preview, system_map_path
):
    before = system_map_path.read_bytes()
    result = controller.apply(system_map_preview, apply_brief=True, approved_targets=frozenset())
    assert result.status == "awaiting_target_approval"
    assert system_map_path.read_bytes() == before


def test_application_record_never_contains_absolute_vault_path(
    controller, brief_preview, vault_root
):
    result = controller.apply(brief_preview, apply_brief=True, approved_targets=frozenset())
    encoded = json.dumps(result.to_dict(), ensure_ascii=False)
    assert str(vault_root) not in encoded
```

- [ ] **Step 2: Run Task 5 tests and verify RED**

```bash
.venv/bin/python -m pytest tests/test_documentation_controller.py -q
```

Expected: controller module does not exist.

- [ ] **Step 3: Implement trusted apply boundary**

Use this API:

```python
class DocumentationController:
    def __init__(self, project_root: Path, *, runtime_root: Path | None = None): ...

    def apply(
        self,
        preview: DocumentationPreview,
        *,
        apply_brief: bool,
        approved_targets: frozenset[str],
    ) -> DocumentationApplication: ...
```

Before every write, re-read and re-hash the target. For existing files, copy bytes to `.nbs_agent_runtime/documentation-backups/<run-id>/<path-identity>.md`; then write UTF-8 bytes to a same-directory temporary file, `flush`, `fsync`, and `os.replace`. After replace, verify expected after hash. ADR uses exclusive create and Controller-assigned next ADR number. Never expose a `force` option.

- [ ] **Step 4: Add runtime ignore rule and application manifest**

Ensure `.nbs_agent_runtime/` remains ignored; do not add a second tracked backup directory. Save application JSON under the run using an allowlisted `documentation-application.json` artifact, with vault-relative identities only.

- [ ] **Step 5: Run focused tests and review**

```bash
.venv/bin/python -m pytest tests/test_documentation_controller.py tests/test_documentation_validator.py tests/test_documentation_targets.py -q
.venv/bin/python -m py_compile backend/agents/documentation_controller.py
git diff --check
```

Review atomicity, partial failure evidence, backup permissions, external vault escape, Windows/macOS path behavior, repeat apply, ADR numbering and lack of Git operations.

- [ ] **Step 6: Commit Task 5**

```bash
git add backend/agents/documentation_controller.py tests/test_documentation_controller.py .gitignore
git commit -m "feat: apply approved documentation updates"
```

---

### Task 6: Documentation Workflow and CLI Dispatch

**Files:**
- Create: `backend/agents/documentation_workflow.py`
- Create: `scripts/documentation_agent.py`
- Create: `tests/test_documentation_workflow.py`
- Create: `tests/test_documentation_agent_cli.py`
- Modify: `scripts/agent_workflow.py`
- Modify: `backend/agents/workflow_store.py`
- Modify: `tests/test_agent_workflow_cli.py`
- Modify: `tests/test_workflow_store.py`

**Interfaces:**
- Consumes: Tasks 1–5 and a completed run ID.
- Produces: `DocumentationWorkflow.run(...) -> DocumentationApplication | DocumentationProposal`, standalone CLI, and `agent_workflow.py document` command.

- [ ] **Step 1: Write RED end-to-end workflow tests**

Cover deterministic no-doc skip, missing runner, preview-only, low-risk Brief apply, high-risk approval, same-fingerprint cache hit, on-demand completed run, incomplete run, and command non-persistence.

```python
def test_document_workflow_preview_does_not_write(workflow, completed_run, fake_runner):
    before = completed_run.brief_path.read_bytes()
    result = workflow.run(
        completed_run.run_id,
        agent_command=fake_runner.command,
        obsidian_vault=completed_run.vault_root,
        apply_brief=False,
        approved_targets=frozenset(),
    )
    assert result["status"] == "preview_ready"
    assert completed_run.brief_path.read_bytes() == before


def test_document_workflow_never_persists_runner_command(workflow, completed_run, fake_runner):
    workflow.run(completed_run.run_id, agent_command=fake_runner.command)
    artifacts = completed_run.read_all_artifact_text()
    assert fake_runner.command not in artifacts
```

- [ ] **Step 2: Write RED CLI contract tests**

Assert single JSON stdout, redacted stderr, `--run-id`, required runner when classifier needs LLM, optional `--obsidian-vault`, `--apply-brief`, repeatable `--approve-target {system_map,adr}`, and these exit codes:

```python
DOCUMENTATION_EXIT_CODES = {
    "applied": 0,
    "preview_ready": 0,
    "no_documentation_needed": 0,
    "awaiting_target_approval": 1,
    "blocked": 2,
    "context_overflow": 4,
    "invalid_agent_output": 5,
}
```

- [ ] **Step 3: Run Task 6 tests and verify RED**

```bash
.venv/bin/python -m pytest tests/test_documentation_workflow.py tests/test_documentation_agent_cli.py tests/test_agent_workflow_cli.py -q
```

Expected: workflow/CLI modules and `document` subcommand are missing.

- [ ] **Step 4: Implement the coordinator**

```python
class DocumentationWorkflow:
    def run(
        self,
        run_id: str,
        *,
        agent_command: str | None,
        obsidian_vault: Path | None = None,
        apply_brief: bool = False,
        approved_targets: frozenset[str] = frozenset(),
    ) -> dict: ...
```

Order is fixed: collect -> classify -> deterministic skip or Agent draft -> validate -> preview artifact -> optional apply -> application artifact -> telemetry. Do not change the core workflow terminal status or Hermes result; documentation is a post-acceptance sidecar.

- [ ] **Step 5: Add safe Store artifacts**

Extend the fixed artifact allowlist with:

```python
"documentation-evidence.json",
"documentation-proposal.json",
"documentation-preview.json",
"documentation-application.json",
"documentation-telemetry.json",
```

Keep existing 5 MiB per-artifact and 25 MiB per-run caps. Retention must compact these as ordinary stage artifacts and preserve application summary in archive evidence.

- [ ] **Step 6: Implement both CLI entry points**

`scripts/documentation_agent.py` owns the focused parser and calls `DocumentationWorkflow`. `scripts/agent_workflow.py document` exposes the same options and delegates to the same backend workflow, not a subprocess or duplicate implementation. Both must redact external absolute paths and never persist runner command.

- [ ] **Step 7: Run focused tests and review**

```bash
.venv/bin/python -m pytest tests/test_documentation_workflow.py tests/test_documentation_agent_cli.py tests/test_agent_workflow_cli.py tests/test_workflow_store.py tests/test_workflow_retention.py -q
.venv/bin/python -m py_compile scripts/documentation_agent.py scripts/agent_workflow.py backend/agents/documentation_workflow.py backend/agents/workflow_store.py
git diff --check
```

Review duplicate CLI logic, unsafe argv logging, accidental state transition changes, sidecar retention, missing no-doc fast path and implicit apply.

- [ ] **Step 8: Commit Task 6**

```bash
git add backend/agents/documentation_workflow.py backend/agents/workflow_store.py scripts/documentation_agent.py scripts/agent_workflow.py tests/test_documentation_workflow.py tests/test_documentation_agent_cli.py tests/test_agent_workflow_cli.py tests/test_workflow_store.py
git commit -m "feat: dispatch documentation workflow"
```

---

### Task 7: Codex Policy, Agent Operations, and Hermes Visibility

**Files:**
- Modify: `AGENTS.md`
- Modify: `docs/agents/NBS_AGENT_ARCHITECTURE.md`
- Modify: `docs/agents/CODEX_AGENT_DISPATCH.md`
- Modify: `NBS_HERMES_MONITORING.md`
- Modify: `NBS_ANALYTICS_SYSTEM_MAP.md`
- Modify: `backend/services/agent_operations_service.py`
- Modify: `agent_operations_rendering.py`
- Create: `tests/test_documentation_agent_docs.py`
- Modify: `tests/test_agent_operations_service.py`
- Modify: `tests/test_agent_operations_rendering.py`
- Modify: `tests/test_hermes_post_change_check.py`

**Interfaces:**
- Consumes: Task 6 documentation sidecar artifacts.
- Produces: mandatory Codex dispatch rule, read-only Operations summary and Hermes inspection boundary.

- [ ] **Step 1: Write RED governance tests**

Assert the docs contain all of these exact concepts:

```python
def test_governance_requires_independent_documentation_agent():
    combined = read_governance_docs()
    assert "documentation-evidence-v1" in combined
    assert "documentation-proposal-v1" in combined
    assert "不得由主 Codex LLM 靜默代寫" in combined
    assert "system map 與 ADR" in combined
    assert "明確" in combined and "approval" in combined
    assert "Hermes" in combined and "read-only" in combined
```

- [ ] **Step 2: Write RED Agent Operations/Hermes tests**

Create completed-run fixtures containing `documentation-application.json` and assert snapshot fields:

```python
assert run["documentation"] == {
    "status": "applied",
    "proposalCount": 2,
    "appliedTargetCount": 1,
    "pendingApprovalCount": 1,
    "updatedAt": "2026-07-18T12:00:00+08:00",
}
```

When sidecar is absent, status is `not_requested`; when invalid, bounded diagnostic is returned. Hermes may report status/cap/read-only policy but tests must prove it never invokes `document`, applies files or writes sidecar artifacts.

- [ ] **Step 3: Run Task 7 tests and verify RED**

```bash
.venv/bin/python -m pytest tests/test_documentation_agent_docs.py tests/test_agent_operations_service.py tests/test_agent_operations_rendering.py tests/test_hermes_post_change_check.py -q
```

Expected: documentation fields and governance text are missing.

- [ ] **Step 4: Update dispatch and architecture truth sources**

Document this exact operational rule: after verified functional change + Hermes PASS, Codex calls `agent_workflow.py document`; deterministic no-doc changes skip without LLM; user-requested backfill uses a completed run ID; missing runner blocks instead of main-LLM fallback. Move Documentation Agent from future candidate to active pipeline while preserving Context/Implementation/Review/Hermes boundaries.

- [ ] **Step 5: Add read-only visibility**

`AgentOperationsService` may read only the five documentation sidecar artifacts from the Store allowlist. It emits compact counts/status, never markdown proposal text, absolute vault paths, diffs or backups. Rendering adds one compact Documentation status block to selected run details; no buttons or writes.

- [ ] **Step 6: Extend Hermes contract and checks**

Hermes checks only artifact schema presence, status consistency, cap warnings, and read-only permissions. It must not invoke runner, preview, apply, backup, Git or Obsidian writes. Add targeted tests to `scripts/hermes_post_change_check.py` coverage without turning Documentation PASS into a replacement for runtime acceptance.

- [ ] **Step 7: Run focused governance tests and review**

```bash
.venv/bin/python -m pytest tests/test_documentation_agent_docs.py tests/test_agent_operations_service.py tests/test_agent_operations_rendering.py tests/test_agent_dispatch_contract.py tests/test_agent_read_only_contract.py tests/test_hermes_post_change_check.py -q
.venv/bin/python -m py_compile backend/services/agent_operations_service.py agent_operations_rendering.py scripts/hermes_post_change_check.py
git diff --check
```

Review for stale “Future Documentation Agent” text, UI write controls, Hermes overlap, proposal content leaks, and any change to baseline/SQLite/business rules.

- [ ] **Step 8: Commit Task 7**

```bash
git add AGENTS.md docs/agents/NBS_AGENT_ARCHITECTURE.md docs/agents/CODEX_AGENT_DISPATCH.md NBS_HERMES_MONITORING.md NBS_ANALYTICS_SYSTEM_MAP.md backend/services/agent_operations_service.py agent_operations_rendering.py tests/test_documentation_agent_docs.py tests/test_agent_operations_service.py tests/test_agent_operations_rendering.py tests/test_hermes_post_change_check.py
git commit -m "docs: activate documentation agent governance"
```

---

### Task 8: End-to-End Acceptance and Formal Evidence

**Files:**
- Create: `docs/agents/DOCUMENTATION_AGENT_ACCEPTANCE.md`
- Modify only if a verified defect is found: files owned by Tasks 1–7 and their tests.

**Interfaces:**
- Consumes: all completed Tasks 1–7.
- Produces: formal acceptance evidence, temporary-vault end-to-end proof and a clean reviewable branch; no merge.

- [ ] **Step 1: Record pre-verification identities**

```bash
git status --short --branch
git rev-parse HEAD
shasum -a 256 nbs_marketing_data.db
```

Save HEAD, branch, dirty-file ownership and formal DB SHA-256 in the acceptance document draft.

- [ ] **Step 2: Run Documentation focused pack**

```bash
.venv/bin/python -m pytest \
  tests/test_documentation_models.py \
  tests/test_documentation_evidence.py \
  tests/test_documentation_policy.py \
  tests/test_documentation_agent_service.py \
  tests/test_documentation_validator.py \
  tests/test_documentation_targets.py \
  tests/test_documentation_controller.py \
  tests/test_documentation_workflow.py \
  tests/test_documentation_agent_cli.py \
  tests/test_documentation_agent_docs.py -q
```

Expected: all pass.

- [ ] **Step 3: Run Agent workflow regression pack**

```bash
.venv/bin/python -m pytest \
  tests/test_agent_cli.py \
  tests/test_agent_runtime.py \
  tests/test_context_agent_service.py \
  tests/test_review_agent_service.py \
  tests/test_implementation_agent_service.py \
  tests/test_agent_workflow_cli.py \
  tests/test_agent_workflow_integration.py \
  tests/test_workflow_store.py \
  tests/test_workflow_retention.py \
  tests/test_agent_operations_service.py \
  tests/test_agent_operations_rendering.py \
  tests/test_hermes_post_change_check.py -q
```

Expected: all pass with no change to existing runner or workflow status contracts.

- [ ] **Step 4: Run compile and full pytest**

```bash
.venv/bin/python -m py_compile \
  backend/agents/documentation_models.py \
  backend/agents/documentation_evidence.py \
  backend/agents/documentation_policy.py \
  backend/agents/documentation_agent_service.py \
  backend/agents/documentation_validator.py \
  backend/agents/documentation_targets.py \
  backend/agents/documentation_controller.py \
  backend/agents/documentation_workflow.py \
  scripts/documentation_agent.py \
  scripts/agent_workflow.py
.venv/bin/python -m pytest -q
```

Expected: compile and full suite pass.

- [ ] **Step 5: Run isolated end-to-end preview/apply test**

Use pytest temporary project/vault fixtures and a deterministic fake Documentation runner. The test must prove: completed run -> evidence -> proposal -> preview -> Brief apply -> system map pending approval -> repeat run cache/idempotency. It must not use the real Obsidian vault or formal SQLite.

```bash
.venv/bin/python -m pytest tests/test_documentation_workflow.py::test_completed_run_end_to_end_preview_and_apply -q
```

Expected: pass; temporary Brief contains one managed evidence block, system map unchanged without approval, and no absolute vault path appears in artifacts.

- [ ] **Step 6: Run formal system acceptance and Hermes**

```bash
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/hermes_post_change_check.py --skip-monitor --json
```

Expected: all services ready, `overallStatus=pass`, revenue scope matched and May baseline matched.

- [ ] **Step 7: Verify DB and documentation invariants**

```bash
shasum -a 256 nbs_marketing_data.db
git diff --check
git status --short --branch
rg -n "HKD 12,057,968|不含掛賬核銷與TT退款轉團款" \
  docs/agents/DOCUMENTATION_AGENT_CONTRACT.md \
  docs/agents/NBS_AGENT_ARCHITECTURE.md \
  NBS_HERMES_MONITORING.md
```

Expected: DB SHA-256 equals Step 1, protected strings exist, and all dirty files belong to acceptance evidence only.

- [ ] **Step 8: Write formal acceptance evidence**

`DOCUMENTATION_AGENT_ACCEPTANCE.md` must record exact commit IDs, test counts, command exit results, temporary-vault scenario, DB before/after SHA-256, baseline/scope evidence, residual risks, and confirmation that the real Obsidian vault was not mutated by tests.

- [ ] **Step 9: Final findings-first review**

Review the complete base-to-head diff against the design spec. Findings must lead; zero findings must explicitly state remaining risks: external runner availability, local vault configuration and first real production backfill observation.

- [ ] **Step 10: Commit Task 8**

```bash
git add docs/agents/DOCUMENTATION_AGENT_ACCEPTANCE.md
git commit -m "test: verify documentation agent workflow"
git status --short --branch
```

Expected: clean worktree after commit. Do not merge until the user authorizes branch integration.

---

## Execution Order and Gates

1. Execute exactly one Task at a time in an isolated `codex/` worktree.
2. For each Task: RED test -> minimal GREEN implementation -> focused tests -> findings-first Review Agent -> commit.
3. Do not start the next Task when Review has findings or the focused tests fail.
4. Task 5 is the first Task allowed to write Markdown targets, and only inside temporary test fixtures during implementation.
5. Task 6 introduces callable workflow behavior; it must not change existing `run` / `approve` semantics.
6. Task 7 activates automatic Codex dispatch policy only after Tasks 1–6 are verified.
7. Task 8 is the only full-system acceptance Task and does not authorize merge.

## Expected Outcome

After Task 8, Codex can call an independent Documentation Agent after verified functional changes or on user request. Deterministic local code decides whether documentation is needed, validates every proposal, applies only authorized targets, preserves recovery evidence and reports status to Agent Operations/Hermes. The main Codex LLM is not used as a silent fallback, and NBS formal DB, baseline, revenue scope and product calculations remain unchanged.
