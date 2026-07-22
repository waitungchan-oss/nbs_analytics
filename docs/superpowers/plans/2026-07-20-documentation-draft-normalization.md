# Documentation Draft Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the read-only Documentation Agent return a small, strict draft that trusted Python code normalizes into the existing governed `documentation-proposal-v1`.

**Architecture:** The Codex runner accepts only `documentation-draft-v1` from stdout. `DocumentationAgentService` verifies the draft against the evidence and classifier, derives each allowed target identity and all hashes, then constructs the unchanged final proposal contract. Existing preview, explicit target approval, Controller atomic write, and Hermes acceptance remain the only routes to document writes.

**Tech Stack:** Python 3, dataclasses, JSON, pytest, existing `DocumentationProposalValidator`, `DocumentationWorkflow`, Codex CLI read-only sandbox.

## Global Constraints

- Keep the formal scope as `不含掛賬核銷與TT退款轉團款` and the 2026-05 baseline as `HKD 12,057,968`.
- Do not change SQLite, upload, rollback, revenue/business rules, export schema, Hermes runtime checks, Controller apply semantics, or the final `documentation-proposal-v1` schema.
- Documentation Agent remains stdin-only, `codex`-allowlisted, read-only, tool-free, 8,000 estimated input tokens, 1,500 estimated output tokens, 120-second timeout, and 64 KiB stdout cap.
- Raw LLM output must never contain or decide repo/vault absolute paths, operations, target identities, hashes, fingerprints, or final proposal evidence.
- A `ready` draft must contain exactly the classifier-required target kinds once each. Unknown, missing, extra, or duplicate targets fail closed.
- `brief_backfill` identity is selected from one safe evidence Markdown source and mapped to `docs/briefs/<basename>.md`; `system_map` is limited to the existing `## 2A. Agent Evidence Pipeline` section; ADR normalization remains explicitly blocked.
- Every implementation Task uses TDD, findings-first Review Agent review, targeted tests, and an independent commit. Implementation Agent must not write formal SQLite, baseline, runtime evidence, Git history, or documents outside its approved code/test/doc allowlist.

---

## File Structure

- Modify: `backend/agents/documentation_models.py`
  - Add strict in-memory `DocumentationDraft` parsing for the untrusted runner boundary; do not alter `DocumentationProposal`.
- Modify: `backend/agents/documentation_codex_runner.py`
  - Replace the final-proposal prompt/check with the bounded draft prompt/check.
- Modify: `backend/agents/documentation_agent_service.py`
  - Parse drafts, fail closed, derive target identities/hashes/content, and build the existing final proposal.
- Modify: `backend/agents/documentation_evidence.py`
  - Carry the approved manifest Brief source into bounded evidence without exposing absolute paths.
- Modify: `backend/agents/documentation_workflow.py`
  - No new write capability; ensure normalized proposal continues through unchanged preview/apply behavior if focused tests expose integration assumptions.
- Modify: `docs/agents/DOCUMENTATION_AGENT_CONTRACT.md`
  - Document the internal draft boundary and unchanged final Controller contract.
- Modify: `docs/agents/VERIFIED_DOCUMENTATION_BACKFILL.md`
  - Update the operator expectation from raw final JSON to normalized proposal/preview.
- Modify: `tests/test_documentation_models.py`
  - Test exact draft schema and invalid shapes.
- Modify: `tests/test_documentation_codex_runner.py`
  - Assert the runner accepts only a matching valid draft and still cannot leak runner arguments.
- Modify: `tests/test_documentation_agent_service.py`
  - Exercise normalization, target-set checks, content safety, and blocked ADR behavior.
- Modify: `tests/test_documentation_workflow.py`
  - Verify a normalized Brief + System Map proposal previews without writes and only applies with existing explicit approvals.
- Modify: `tests/test_documentation_evidence.py`
  - Verify the manifest Brief source is carried into bounded evidence.

## Task 1: Define the Strict Untrusted Draft Boundary

**Files:**
- Modify: `backend/agents/documentation_models.py`
- Modify: `tests/test_documentation_models.py`
- Modify: `backend/agents/documentation_codex_runner.py`
- Modify: `tests/test_documentation_codex_runner.py`

**Interfaces:**
- Consumes: `documentation-evidence-v1` with a lowercase 64-character `evidenceFingerprint`.
- Produces: `DocumentationDraft.from_dict(payload) -> DocumentationDraft` and `DOCUMENTATION_DRAFT_SCHEMA == "documentation-draft-v1"`.
- Invariant: final `DocumentationProposal.from_dict()` remains unchanged and does not accept draft payloads.

- [ ] **Step 1: Write failing draft-model tests**

```python
def test_documentation_draft_requires_exact_shape():
    draft = DocumentationDraft.from_dict({
        "schemaVersion": "documentation-draft-v1",
        "evidenceFingerprint": "a" * 64,
        "status": "ready",
        "proposals": [{"targetKind": "brief_backfill", "content": "Summary."}],
    })
    assert draft.proposals == ({"targetKind": "brief_backfill", "content": "Summary."},)

@pytest.mark.parametrize("payload", [
    {"schemaVersion": "documentation-draft-v1", "evidenceFingerprint": "a" * 64,
     "status": "ready", "proposals": [], "unexpected": True},
    {"schemaVersion": "documentation-draft-v1", "evidenceFingerprint": "A" * 64,
     "status": "ready", "proposals": []},
])
def test_documentation_draft_rejects_unknown_or_invalid_fields(payload):
    with pytest.raises(DocumentationSchemaError):
        DocumentationDraft.from_dict(payload)
```

- [ ] **Step 2: Run the focused test to verify failure**

Run: `.venv/bin/python -m pytest tests/test_documentation_models.py -q`

Expected: FAIL because `DocumentationDraft` and `DOCUMENTATION_DRAFT_SCHEMA` do not exist.

- [ ] **Step 3: Implement the minimal immutable draft model**

Add exact draft constants and a frozen dataclass. Its parser must accept only:

```python
{
    "schemaVersion": "documentation-draft-v1",
    "evidenceFingerprint": "<lowercase sha256>",
    "status": "ready|no_documentation_needed|blocked|context_overflow",
    "proposals": [{"targetKind": "brief_backfill|system_map|adr", "content": "..."}],
}
```

Reuse `_keys`, `_hash`, `_string`, and `TARGET_KINDS`. Keep duplicate/required-target policy out of the model; it belongs to the trusted service.

- [ ] **Step 4: Replace runner final-proposal validation with draft validation**

Set the fixed prompt to include the complete draft skeleton and constraints:

```text
Produce exactly one documentation-draft-v1 JSON object. A proposal item has only
targetKind and content. Do not emit target paths, operations, hashes, evidence,
proposal fingerprints, markdown fences, commentary, or any other keys.
```

Change `_valid_proposal` to `_valid_draft`; parse with `DocumentationDraft.from_dict()` and require the matching evidence fingerprint. Preserve the fixed `codex exec --sandbox read-only` argv and stdout cap.

- [ ] **Step 5: Run focused model and runner tests**

Run: `.venv/bin/python -m pytest tests/test_documentation_models.py tests/test_documentation_codex_runner.py -q`

Expected: PASS. Include assertions that old `documentation-proposal-v1`, unknown draft keys, non-JSON, and mismatched fingerprints return runner failure.

- [ ] **Step 6: Review and commit**

Run the local Review Agent with the Task 1 diff and focused-test evidence. Resolve all findings before committing:

```bash
.venv/bin/python scripts/review_agent.py --collect-only
git add backend/agents/documentation_models.py backend/agents/documentation_codex_runner.py \
  tests/test_documentation_models.py tests/test_documentation_codex_runner.py
git commit -m "feat: add strict documentation draft contract"
```

## Task 2: Normalize Drafts Into Governed Final Proposals

**Files:**
- Modify: `backend/agents/documentation_agent_service.py`
- Modify: `tests/test_documentation_agent_service.py`

**Interfaces:**
- Consumes: `DocumentationDraft`, collector `DocumentationEvidence`, and `required_targets: tuple[str, ...]`.
- Produces: `DocumentationProposal` created only through the existing `_proposal()` helper and `DocumentationProposal.from_dict()`.
- Invariant: no raw draft field is used as target identity, operation, content hash, evidence, or proposal fingerprint.

- [ ] **Step 1: Write failing normalization tests**

Replace the fake runner output with this draft fixture for code changes:

```python
{
    "schemaVersion": "documentation-draft-v1",
    "evidenceFingerprint": payload["evidenceFingerprint"],
    "status": "ready",
    "proposals": [
        {"targetKind": "brief_backfill", "content": "已驗證 runner 與 service 邊界。"},
        {"targetKind": "system_map", "content": "文件回填改採受控 draft normalization。"},
    ],
}
```

Assert the returned proposal is accepted by `DocumentationProposal.from_dict()`, has the Brief source path from evidence, and has a System Map identity with the current `2A. Agent Evidence Pipeline` section hash. Add negative cases for missing/extra/duplicate target kinds, `#`/`##` fragment headings, managed markers, and `adr` required target.

- [ ] **Step 2: Run the focused test to verify failure**

Run: `.venv/bin/python -m pytest tests/test_documentation_agent_service.py -q`

Expected: FAIL because current service expects `documentation-proposal-v1` and does not normalize draft items.

- [ ] **Step 3: Implement fail-closed normalizer helpers**

Implement private helpers in `DocumentationAgentService` with these responsibilities:

```python
def _parse_result(self, evidence, stdout, required_targets, output_limit) -> DocumentationProposal: ...
def _normalize_draft(self, evidence, draft, required_targets) -> DocumentationProposal: ...
def _brief_identity(self, evidence) -> str: ...
def _system_map_proposal(self, evidence, fragment) -> dict[str, str]: ...
def _validate_draft_fragment(self, content: str) -> str: ...
```

Rules to implement exactly:

- Parse `DocumentationDraft`; any parser/fingerprint/target-set/fragment failure returns `_proposal(..., "invalid_agent_output", ...)`.
- `ready` requires `set(targetKinds) == set(required_targets)` and equal list lengths. `no_documentation_needed` is invalid when `required_targets` is non-empty.
- If `adr` is required, return `_proposal(..., "blocked", ("adr_draft_normalization_not_implemented",))`; do not derive an ADR path.
- `_brief_identity()` selects one safe evidence Markdown source; if it is a local runtime Brief, map only its basename to `docs/briefs/<basename>.md`. Build `update_managed_block` content with a trusted task heading plus the safe fragment.
- `DocumentationEvidenceCollector` adds the validated relative `manifest.briefPath` and `briefSha256` to bounded evidence sources; it never adds an absolute or traversal path.
- `_system_map_proposal()` reads only `NBS_ANALYTICS_SYSTEM_MAP.md`, finds exactly `## 2A. Agent Evidence Pipeline`, hashes the full existing section, and creates the validator-compatible identity containing the full heading and `|sha256=<hash>`. Its replacement content preserves the original section unchanged then appends exactly one trusted `### Documentation Backfill: <safe-task-id>` subsection.
- `_validate_draft_fragment()` rejects blank content, managed markers, `^#{1,2}\\s`, absolute-path patterns, and anything rejected by `DocumentationProposalValidator._check_content`.
- Construct each final item with fixed operation and `sha256(content.encode("utf-8")).hexdigest()`. Finish by serializing through `_proposal()` and `DocumentationProposal.from_dict()`.

- [ ] **Step 4: Run focused service tests**

Run: `.venv/bin/python -m pytest tests/test_documentation_agent_service.py -q`

Expected: PASS. Confirm cache contains only a final `documentation-proposal-v1`, never the raw draft.

- [ ] **Step 5: Review and commit**

Run findings-first Review Agent review against the Task 2 diff, then:

```bash
git add backend/agents/documentation_agent_service.py tests/test_documentation_agent_service.py
git commit -m "feat: normalize documentation drafts safely"
```

## Task 3: Preserve the Existing Workflow and Apply Boundaries

**Files:**
- Modify: `tests/test_documentation_workflow.py`
- Modify only if test-driven need is proven: `backend/agents/documentation_workflow.py`
- Modify: `docs/agents/DOCUMENTATION_AGENT_CONTRACT.md`
- Modify: `docs/agents/VERIFIED_DOCUMENTATION_BACKFILL.md`

**Interfaces:**
- Consumes: normalized final proposal from `DocumentationAgentService.draft()`.
- Produces: the same `documentation-preview.json` and optional `documentation-application.json` schema already consumed by Hermes and Agent Operations.
- Invariant: preview has no document write; Brief requires `--apply-brief`; System Map requires `--approve-target system_map`; no runner command or vault absolute path is persisted.

- [ ] **Step 1: Write failing workflow integration tests**

Extend the temporary project fixture with a minimal protected System Map containing the exact `## 2A. Agent Evidence Pipeline` section. Make its fake runner return the Task 2 draft fixture. Add assertions:

```python
result = workflow.run(run_id, agent_command=FakeRunner.command)
assert result["status"] == "preview_ready"
assert {item["targetKind"] for item in result["items"]} == {"brief_backfill", "system_map"}
assert before_brief == brief.read_bytes()
assert before_map == system_map.read_bytes()

applied = workflow.run(
    run_id, agent_command=FakeRunner.command,
    apply_brief=True, approved_targets=frozenset({"system_map"}),
)
assert applied["status"] == "applied"
```

Also verify omitting `system_map` approval returns `awaiting_target_approval` and leaves the System Map byte-identical.

- [ ] **Step 2: Run workflow tests to verify failure**

Run: `.venv/bin/python -m pytest tests/test_documentation_workflow.py -q`

Expected: FAIL until the fake runner and fixture use the new draft/normalization path.

- [ ] **Step 3: Make only the minimum workflow adjustment required by tests**

Keep `DocumentationWorkflow.run()` orchestration unchanged unless the test reveals an integration mismatch. Do not add new CLI flags, approval shortcuts, vault writes, or runtime status mutations. Update the two operator documents to say:

```text
The runner returns documentation-draft-v1; trusted service normalizes it into
documentation-proposal-v1 before validator preview. The operator still inspects
the final proposal and preview, then explicitly approves Brief/System Map apply.
```

- [ ] **Step 4: Run focused integration and contract tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_documentation_agent_cli.py \
  tests/test_documentation_workflow.py \
  tests/test_documentation_validator.py \
  tests/test_documentation_controller.py -q
```

Expected: PASS with preview-only and explicit-approval behavior unchanged.

- [ ] **Step 5: Review and commit**

Use Review Agent findings-first review, then:

```bash
git add backend/agents/documentation_workflow.py tests/test_documentation_workflow.py \
  docs/agents/DOCUMENTATION_AGENT_CONTRACT.md docs/agents/VERIFIED_DOCUMENTATION_BACKFILL.md
git commit -m "docs: govern normalized documentation drafts"
```

If `documentation_workflow.py` has no necessary code diff, omit it from `git add` and state that the existing orchestration already preserved the boundary.

## Task 4: Full Regression and Verified Production Proposal

**Files:**
- Modify only when acceptance exposes a tested defect: files from Tasks 1-3.
- Runtime output only: `.nbs_agent_runtime/runs/<run-id>/documentation-*.json` and bounded reports.

**Interfaces:**
- Consumes: merged implementation on clean `main`, a verified completed backfill run, and explicit `codex` runner.
- Produces: a strict final proposal and `preview_ready` result; application is separate and remains explicitly approved.
- Invariant: no runtime artifact is manually patched to repair runner output or gate schema.

- [ ] **Step 1: Run the complete documentation regression pack**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_documentation_models.py \
  tests/test_documentation_codex_runner.py \
  tests/test_documentation_agent_service.py \
  tests/test_documentation_agent_cli.py \
  tests/test_documentation_workflow.py \
  tests/test_documentation_validator.py \
  tests/test_documentation_controller.py \
  tests/test_verified_backfill_models.py \
  tests/test_verified_backfill_service.py \
  tests/test_verified_backfill_integration.py \
  tests/test_verified_documentation_backfill_cli.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full project gates from clean main**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/hermes_post_change_check.py --skip-monitor --json
```

Expected: all code tests pass; service acceptance passes; Hermes returns `overallStatus=pass`. If unrelated runtime data failure exists, record it as a blocker and do not claim production proposal success.

- [ ] **Step 3: Create a fresh verified backfill run and request a proposal**

Only after all gates pass, run:

```bash
.venv/bin/python scripts/verified_documentation_backfill.py \
  --source-commit HEAD \
  --reason "Documentation draft normalization production verification" \
  --no-notify

.venv/bin/python scripts/documentation_agent.py \
  --run-id <new-run-id> \
  --agent-command "codex" \
  --obsidian-vault "<vault-root>"
```

Expected: `preview_ready`, a `documentation-proposal-v1` that passes strict parsing, and preview items limited to Brief and System Map. Do not apply in this Task.

- [ ] **Step 4: Review the production proposal and preview**

Run Context/Review collectors in read-only mode against the new run artifacts. Verify: target set is exact, no absolute vault path, protected governance text is retained, no ADR target exists, and no runtime artifact was edited by hand.

- [ ] **Step 5: Final verification, commit, and handoff**

Run `git diff --check`, repeat focused tests if a defect was fixed, then commit only tracked source/test/document changes:

```bash
git add backend/agents scripts tests docs/agents
git commit -m "test: verify documentation draft production flow"
```

Only create this commit when Task 4 changed tracked files. Otherwise leave Task 4 as an evidence-only acceptance step and report the existing Task 1-3 commits plus the new verified run ID. The separate controlled apply may proceed only after the user confirms the preview and supplies the already-required Brief/System Map approvals.

## Plan Self-Review

- Spec coverage: Tasks 1-2 cover draft parsing, prompt, normalization, deterministic target identities, fixed operations/hashes, and fail-closed errors. Task 3 covers unchanged preview/apply governance and documentation. Task 4 covers regression, real runner, and no-manual-patch production evidence.
- Placeholder scan: no unfinished markers, deferred implementation, or undefined file paths remain.
- Type consistency: `DocumentationDraft` is introduced before the runner and service consume it; `DocumentationProposal` remains the output consumed by workflow, validator, Controller, and Hermes.
- Scope check: ADR normalization is deliberately blocked, avoiding a separate high-risk design in this plan.
