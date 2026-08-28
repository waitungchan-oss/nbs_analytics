# Strict Review Verification Chain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立以 immutable Verification Session 為核心的兩階段驗收鏈路，讓 Strict Review、full pytest、Hermes 與 fresh evidence 對同一份 source seal 工作，並可在 runner 或單一 batch 失敗後安全續跑。

**Architecture:** 先建立 source seal 與 session manifest，再執行 pre-review targeted verification 與 Strict Review；Strict Review PASS 後才執行 full pytest 和 Hermes，最後由 deterministic Completion Attestation 裁決整輪是否完成。既有 `review-report-v1` 與 `verification-v1` schema 保持不變，新的 session、gate、runner capability 與 command metadata 寫入 `.nbs_agent_runtime/verification_sessions/<sessionId>/`。

**Tech Stack:** Python 3、dataclasses、canonical JSON/SHA-256、pytest、現有 `EvidenceCollector`、`AgentRuntime`、`review_agent.py`、`hermes_post_change_check.py`、本地 Codex CLI。

**Spec:** `docs/superpowers/specs/2026-08-28-strict-review-verification-chain-design.md`

## Global Constraints

- 正式收入口徑固定為 `不含掛賬核銷與TT退款轉團款`。
- 2026-05 frozen baseline 固定為 `HKD 12,057,968`。
- 不修改 SQLite、baseline、revenue scope、business rules、export schema、正式業務資料、active export 或 trusted reference pointer。
- Context Agent、Review Agent、Hermes 與 Memory Hub 維持 read-only；Memory Hub 只提供 non-authoritative bounded hints。
- `verification-v1` top-level 只能包含 `commands`；不得加入 session metadata。
- Review `pass` 仍不等於整輪完成；只有 `completion-attestation-v1` 的 `complete` 才能宣稱完成。
- Implementation 每個 Task 都要先寫 failing test，再寫最小實作，再跑 targeted test、findings-first Review、full pytest 與 Hermes。
- 不使用 synthetic agent response；runner capability failure 必須 fail closed。
- 不執行 commit、push、merge 或正式服務重啟；Git 整合另由使用者明確授權。

---

## File Structure

- Create: `backend/agents/verification_session.py` — immutable source seal、session manifest、gate identity 與 strict transition。
- Create: `backend/agents/verification_chain.py` — deterministic gate orchestration、resume、final attestation 與 terminal state。
- Modify: `backend/agents/verification_evidence_writer.py` —保留 `verification-v1`，增加外層 command metadata 與 source-bound evidence writer。
- Modify: `backend/agents/review_runner_profile.py` —加入 static/live capability receipt 與 bounded probe contract。
- Modify: `backend/agents/review_agent_service.py` —讓 batch identity、batch resume 與 aggregation 使用 session fingerprint。
- Modify: `scripts/review_agent.py` —支援 session source seal、capability receipt 與 batch resume，不改既有非-session CLI compatibility。
- Create: `scripts/verification_chain.py` —提供 `seal`、`run-review`、`run-full`、`run-hermes`、`attest`、`status` CLI。
- Modify: `scripts/hermes_post_change_check.py` —只增加 session evidence input/output binding，不改 Hermes read-only checks。
- Create: `tests/test_verification_session.py` —session schema、fingerprint、state transition。
- Create: `tests/test_verification_chain.py` —gate ordering、resume、atomic terminal state、stale source。
- Modify: `tests/test_verification_evidence_writer.py` —外層 metadata、source binding、existing schema compatibility。
- Modify: `tests/test_review_runner_profile.py` —live probe、receipt reuse、transport failure。
- Modify: `tests/test_review_agent_service.py` —batch identity、resume 與 deterministic aggregation。
- Modify: `tests/test_strict_review_runner_acceptance.py` —端到端 session acceptance 與舊報告隔離。
- Modify: `docs/agents/REVIEW_AGENT_CONTRACT.md` —記錄 session-aware Review input boundary。
- Modify: `NBS_HERMES_MONITORING.md` —記錄 session gate 與 primary/isolated profile 明確分流。

## Task 1: 建立 immutable Verification Session contract

**Files:**
- Create: `backend/agents/verification_session.py`
- Test: `tests/test_verification_session.py`

**Interfaces:**
- Consumes: project root、approved brief path、base SHA、current HEAD、filtered worktree/diff fingerprints、contract/policy versions。
- Produces: `VerificationSession.create(...) -> VerificationSession`、`VerificationSession.to_dict() -> dict`、`VerificationSession.from_dict(payload) -> VerificationSession`、`VerificationSession.assert_fresh(...) -> None`。
- Invariant: canonical session fingerprint 只由 source seal 與 policy identity 產生；session object 不包含 SQLite rows、Excel、完整 logs 或 secrets。

- [ ] **Step 1: Write failing schema and fingerprint tests**

```python
def test_session_round_trip_has_exact_schema(tmp_path):
    session = VerificationSession.create(
        project_id="nbs_analytics", base_sha="a" * 40, head_sha="b" * 40,
        brief_path="docs/briefs/task.md", brief_fingerprint="c" * 64,
        worktree_fingerprint="d" * 64, diff_fingerprint="e" * 64,
        contract_fingerprint="f" * 64, policy_fingerprint="0" * 64,
    )
    restored = VerificationSession.from_dict(session.to_dict())
    assert restored.session_id == session.session_id
    assert restored.source_fingerprint == session.source_fingerprint
    assert set(session.to_dict()) == {
        "schemaVersion", "sessionId", "status", "projectId", "baseSha", "headSha",
        "briefPath", "briefFingerprint", "worktreeFingerprint", "diffFingerprint",
        "contractFingerprint", "policyFingerprint", "createdAt", "gates",
    }

def test_session_rejects_changed_worktree():
    session = _session()
    with pytest.raises(StaleVerificationSession):
        session.assert_fresh(head_sha=session.head_sha, brief_fingerprint=session.brief_fingerprint,
                             worktree_fingerprint="f" * 64, diff_fingerprint=session.diff_fingerprint)
```

- [ ] **Step 2: Run focused tests to verify failure**

Run: `PYTHONPATH=. ./.venv/bin/pytest tests/test_verification_session.py -q`

Expected: FAIL because `VerificationSession` and `StaleVerificationSession` do not exist.

- [ ] **Step 3: Implement exact immutable model**

Implement frozen dataclass fields, exact schema validation, lowercase SHA-256 validation, RFC3339 `createdAt`, `source_fingerprint` property and `assert_fresh`. Allowed status values are `created`, `sealed`, `review_running`, `review_passed`, `full_verification_passed`, `hermes_passed`, `complete`, `blocked_runner_capability`, `blocked_runner_transport`, `review_changes_required`, `context_overflow`, `verification_failed`, `hermes_failed`, `stale_source`, `invalid_evidence`.

- [ ] **Step 4: Add safe atomic session persistence**

Add `write_session(path, session)` and `read_session(path)`. The writer must use a temporary file in the same directory, flush/fsync, then `os.replace`; it may only write below `.nbs_agent_runtime/verification_sessions/`.

- [ ] **Step 5: Run focused tests**

Run: `PYTHONPATH=. ./.venv/bin/pytest tests/test_verification_session.py -q`

Expected: all session schema, fingerprint and freshness tests pass.

- [ ] **Step 6: Commit checkpoint**

```bash
git add backend/agents/verification_session.py tests/test_verification_session.py
git commit -m "feat: add immutable verification session contract"
```

## Task 2: Extend evidence writer without breaking verification-v1

**Files:**
- Modify: `backend/agents/verification_evidence_writer.py`
- Test: `tests/test_verification_evidence_writer.py`

**Interfaces:**
- Consumes: `VerificationSession`、real command result、bounded stdout/stderr。
- Produces: existing `write_verification_v1(commands, output)` plus `write_gate_evidence(session, gate, commands, output_dir) -> GateEvidence`。
- Invariant: `verification-v1` remains exactly `{"commands": [...]}`; gate metadata is stored in `gate.json` beside it。

- [ ] **Step 1: Write compatibility and binding tests**

```python
def test_gate_evidence_keeps_verification_v1_shape(tmp_path):
    result = write_gate_evidence(_session(), "full_pytest", [_command()], tmp_path)
    assert json.loads(result.verification_path.read_text()) == {"commands": [_command()]}
    metadata = json.loads(result.metadata_path.read_text())
    assert metadata["sessionId"] == result.session_id
    assert metadata["sourceFingerprint"] == result.source_fingerprint

def test_gate_evidence_records_nonzero_command(tmp_path):
    result = write_gate_evidence(
        _session(), "full_pytest", [{**_command(), "exitCode": 1}], tmp_path
    )
    metadata = json.loads(result.metadata_path.read_text())
    assert metadata["status"] == "failed"
```

- [ ] **Step 2: Run focused tests to verify failure**

Run: `PYTHONPATH=. ./.venv/bin/pytest tests/test_verification_evidence_writer.py -q`

Expected: FAIL because `write_gate_evidence` does not exist.

- [ ] **Step 3: Implement bounded gate evidence**

Add exact gate allowlist (`pre_review`, `strict_review`, `full_pytest`, `hermes`, `completion`), command fingerprint, evidence fingerprint, start/finish timestamps, producer version, stdout/stderr digest and reuse reason. A nonzero command is recorded as `failed` evidence rather than rejected, so failure evidence remains auditable. Keep command tails capped at 4,000 characters.

- [ ] **Step 4: Add stale and path-safety tests**

Verify that a metadata source fingerprint differing from the session is rejected, output outside the session directory is rejected, and existing `validate_verification_v1()` accepts the generated verification file unchanged.

- [ ] **Step 5: Run focused tests**

Run: `PYTHONPATH=. ./.venv/bin/pytest tests/test_verification_evidence_writer.py -q`

Expected: all compatibility, bounds, digest and path-safety tests pass.

- [ ] **Step 6: Commit checkpoint**

```bash
git add backend/agents/verification_evidence_writer.py tests/test_verification_evidence_writer.py
git commit -m "feat: bind verification evidence to session gates"
```

## Task 3: Add runner static/live capability contract

**Files:**
- Modify: `backend/agents/review_runner_profile.py`
- Modify: `scripts/codex_runner_preflight.py`
- Test: `tests/test_review_runner_profile.py`

**Interfaces:**
- Consumes: existing `RunnerProfile`、CLI version/cache identity、allowlisted executable。
- Produces: `preflight_runner(profile) -> RunnerPreflightResult` with existing CLI-compatible `ready`/`blocked_runtime`; `probe_runner(profile) -> RunnerCapabilityReceipt` with `static_ready`/`turn_ready`/`blocked_runner_capability`/`blocked_runner_transport`。
- Invariant: live probe is short, read-only, fixed-prompt, no business data, no source/runtime writes and no unbounded retry.

- [ ] **Step 1: Write failing live capability tests**

```python
def test_static_ready_requires_live_probe_before_review(tmp_path, fake_codex):
    profile = _profile(fake_codex, tmp_path)
    static = preflight_runner(profile)
    assert static.status == "ready"
    receipt = probe_runner(profile)
    assert receipt.status == "turn_ready"
    assert receipt.cache_fingerprint

def test_live_nonzero_exit_is_transport_blocked(profile, monkeypatch):
    monkeypatch.setattr(subprocess, "run", _failed_probe)
    assert probe_runner(profile).status == "blocked_runner_transport"
```

- [ ] **Step 2: Run focused tests to verify failure**

Run: `PYTHONPATH=. ./.venv/bin/pytest tests/test_review_runner_profile.py -q`

Expected: FAIL because `probe_runner` and `RunnerCapabilityReceipt` do not exist.

- [ ] **Step 3: Implement bounded live probe**

Use a fixed command shape with `--ephemeral --json`, a 15-second timeout, output cap 8 KiB and a minimal JSON response contract. Normalize nonzero exit, timeout, invalid JSON and model mismatch to `blocked_runner_transport`; preserve only bounded diagnostics. Keep the existing static preflight CLI's `ready` and exit-code behavior so current callers remain compatible; only the session receipt uses the more specific capability statuses.

- [ ] **Step 4: Implement receipt reuse**

Fingerprint executable path, CLI version, selected model, cache bytes, runner command shape and relevant non-secret environment identity. Reuse only when the receipt `expiresByFingerprint` equals the current capability fingerprint.

- [ ] **Step 5: Update preflight CLI output**

Keep existing static preflight exit behavior. Add an explicit `--probe` mode; without `--probe`, never claim `turn_ready`.

- [ ] **Step 6: Run focused tests and commit**

Run: `PYTHONPATH=. ./.venv/bin/pytest tests/test_review_runner_profile.py -q`

```bash
git add backend/agents/review_runner_profile.py scripts/codex_runner_preflight.py tests/test_review_runner_profile.py
git commit -m "feat: add live review runner capability probe"
```

## Task 4: Make Review batching resumable and session-bound

**Files:**
- Modify: `backend/agents/review_agent_service.py`
- Modify: `scripts/review_agent.py`
- Test: `tests/test_review_agent_service.py`

**Interfaces:**
- Consumes: `VerificationSession`、`EvidenceBundle`、bounded context/verification payload。
- Produces: `plan_review_batches(session, bundle) -> tuple[ReviewBatch, ...]`、`run_review_batch(batch, runner, ...) -> dict`、`merge_review_batches(reports, session_fingerprint=...) -> dict`。
- Invariant: each batch identity is `(sessionId, batchId, batchFingerprint)`; aggregator never calls LLM and never drops findings.

- [ ] **Step 1: Write failing resume/coverage tests**

```python
def test_same_batch_fingerprint_reuses_completed_report(tmp_path, bundle, fake_runner):
    session = _session()
    batches = plan_review_batches(session, bundle)
    first = run_review_batch(batches[0], fake_runner, runtime_root=tmp_path)
    second = run_review_batch(batches[0], fake_runner, runtime_root=tmp_path)
    assert first == second
    assert fake_runner.calls == 1

def test_aggregator_rejects_missing_batch_and_preserves_findings(bundle):
    session = _session()
    reports = [_passing_report(session, "batch-001")]
    with pytest.raises(ValueError, match="coverage"):
        merge_review_batches(reports, session_fingerprint=session.source_fingerprint,
                             expected_batch_ids=("batch-001", "batch-002"))
```

- [ ] **Step 2: Run focused tests to verify failure**

Run: `PYTHONPATH=. ./.venv/bin/pytest tests/test_review_agent_service.py -q`

Expected: FAIL because session-aware planner and resume interfaces do not exist.

- [ ] **Step 3: Implement deterministic batch planner**

Split by file group and the configured review input budget. Every batch stores the full session source fingerprint, declared files, patch fingerprint and bounded payload fingerprint. No batch may contain SQLite rows, Excel, secrets or full logs.

- [ ] **Step 4: Implement immutable batch result reuse**

Before invoking the runner, read an existing batch result only if schema, session ID, batch ID, batch fingerprint and result fingerprint all match. Otherwise run exactly once and store a bounded result.

- [ ] **Step 5: Implement coverage-safe deterministic aggregation**

Require every planned batch ID exactly once, merge unique findings by canonical fingerprint, preserve all severity levels, and output `context_overflow` if merged output exceeds the configured limit.

- [ ] **Step 6: Run focused tests and commit**

Run: `PYTHONPATH=. ./.venv/bin/pytest tests/test_review_agent_service.py -q`

```bash
git add backend/agents/review_agent_service.py scripts/review_agent.py tests/test_review_agent_service.py
git commit -m "feat: make strict review batches resumable"
```

## Task 5: Implement deterministic Verification Chain controller

**Files:**
- Create: `backend/agents/verification_chain.py`
- Test: `tests/test_verification_chain.py`

**Interfaces:**
- Consumes: session, evidence writer, Review runner, approved full pytest command, Hermes adapter。
- Produces: `VerificationChain.seal(...)`、`run_pre_review(...)`、`run_strict_review(...)`、`run_full_verification(...)`、`run_hermes(...)`、`attest(...)`。
- Invariant: state transitions are exact and monotonic; failed later gates never rewrite earlier PASS into complete; each terminal artifact is atomic.

- [ ] **Step 1: Write failing ordering and fallback tests**

```python
def test_full_verification_cannot_run_before_review_pass(tmp_path, chain):
    with pytest.raises(InvalidGateTransition):
        chain.run_full_verification()

def test_runner_failure_creates_new_transport_terminal_state(tmp_path, chain, failing_runner):
    result = chain.run_strict_review(runner=failing_runner)
    assert result.status == "blocked_runner_transport"
    assert result.session_id
    assert not chain.current_result_is_previous_session()

def test_completion_requires_review_full_and_hermes_pass(tmp_path, chain):
    result = chain.attest()
    assert result.status == "blocked"
    assert "strictReview" in result.diagnostics[0]
```

- [ ] **Step 2: Run focused tests to verify failure**

Run: `PYTHONPATH=. ./.venv/bin/pytest tests/test_verification_chain.py -q`

Expected: FAIL because `VerificationChain` and gate transition errors do not exist.

- [ ] **Step 3: Implement gate state machine**

Allow `created → sealed → pre_review_passed → review_running → review_passed → full_verification_passed → hermes_passed → complete`. Map runner capability/transport, review findings, context overflow, full test failure, Hermes failure and source drift to the exact terminal states from the spec.

- [ ] **Step 4: Implement source freshness at gate boundaries**

Recompute HEAD, brief, filtered worktree and diff fingerprints before and after every gate. If any differs from the session, write `stale_source` and do not run the next gate.

- [ ] **Step 5: Implement atomic gate and terminal artifacts**

Store `session.json`, `gate.json`, batch reports and `completion.json` below one session directory. Never overwrite a prior session report; write a new terminal result for every invocation.

- [ ] **Step 6: Implement completion attestation**

Require Strict Review PASS, full pytest PASS, Hermes PASS, matching source fingerprint and no unclassified dirty files. Generate `completion-attestation-v1` with artifact fingerprints and no LLM call.

- [ ] **Step 7: Run focused tests and commit**

Run: `PYTHONPATH=. ./.venv/bin/pytest tests/test_verification_chain.py -q`

```bash
git add backend/agents/verification_chain.py tests/test_verification_chain.py
git commit -m "feat: add deterministic verification chain controller"
```

## Task 6: Add operator CLI and Hermes/session binding

**Files:**
- Create: `scripts/verification_chain.py`
- Modify: `scripts/hermes_post_change_check.py`
- Test: `tests/test_strict_review_runner_acceptance.py`

**Interfaces:**
- Consumes: current repo、approved brief、runner profile、existing system manager/Hermes commands。
- Produces: CLI subcommands `seal`, `run-review`, `run-full`, `run-hermes`, `attest`, `status`，以及 session-scoped JSON output。
- Invariant: `run-full` refuses unless session state is `review_passed`; `run-hermes` refuses unless full verification passed; no command modifies SQLite or business runtime state。

- [ ] **Step 1: Write failing CLI acceptance tests**

```python
def test_cli_status_does_not_select_old_report(tmp_path):
    old = _write_session(tmp_path, session_id="old", status="context_overflow")
    new = _write_session(tmp_path, session_id="new", status="blocked_runner_transport")
    result = run_cli("status", "--session", new)
    assert result["sessionId"] == "new"
    assert result["status"] == "blocked_runner_transport"
```

- [ ] **Step 2: Run acceptance test to verify failure**

Run: `PYTHONPATH=. ./.venv/bin/pytest tests/test_strict_review_runner_acceptance.py -q`

Expected: FAIL because the session CLI does not exist.

- [ ] **Step 3: Implement CLI with explicit session paths**

Require `--brief`, `--base`, `--runner-profile` and `--session` where applicable. Resolve all output paths under `.nbs_agent_runtime/verification_sessions/`; return stable exit codes: `0 complete/pass`, `1 changes/failure`, `2 blocked capability`, `4 context overflow`, `5 invalid runtime/evidence`.

- [ ] **Step 4: Bind Hermes to explicit profile mode**

Add a session-aware input option while preserving no-profile behavior. Save whether Hermes used `primary-runtime` or `isolated-profile`; reject mixed evidence during attestation. Hermes remains read-only and emits results; the trusted controller alone records the bounded session gate artifact.

- [ ] **Step 5: Run acceptance tests**

Run: `PYTHONPATH=. ./.venv/bin/pytest tests/test_strict_review_runner_acceptance.py -q`

Expected: session CLI, old-report isolation, gate order and profile separation tests pass.

- [ ] **Step 6: Commit checkpoint**

```bash
git add scripts/verification_chain.py scripts/hermes_post_change_check.py tests/test_strict_review_runner_acceptance.py
git commit -m "feat: expose session verification chain CLI"
```

## Task 7: Document contract boundaries and Memory Hub integration

**Files:**
- Modify: `docs/agents/REVIEW_AGENT_CONTRACT.md`
- Modify: `NBS_HERMES_MONITORING.md`
- Test: `tests/test_agent_read_only_contract.py`

**Interfaces:**
- Consumes: implemented session/gate schema and existing Memory Hub observation contract。
- Produces: operator-readable contract for Review/session/Hermes ordering and read-only Memory Hub boundary。
- Invariant: documentation does not change business scope, baseline, acceptance thresholds or runner permissions。

- [ ] **Step 1: Add contract assertions**

```python
def test_docs_define_two_stage_review_chain():
    review = Path("docs/agents/REVIEW_AGENT_CONTRACT.md").read_text()
    hermes = Path("NBS_HERMES_MONITORING.md").read_text()
    assert "verification-session-v1" in review
    assert "Completion Attestation" in review
    assert "primary-runtime" in hermes
    assert "isolated-profile" in hermes
```

- [ ] **Step 2: Update Review contract**

State that Review consumes pre-review targeted evidence and source seal; it does not require full pytest/Hermes to issue code-level PASS; final completion still requires both later gates.

- [ ] **Step 3: Update Hermes contract**

State that Hermes is read-only system acceptance, must bind to session source fingerprint, and primary/isolated profile results cannot be mixed.

- [ ] **Step 4: Add Memory Hub boundary**

Document that Memory Hub hints are optional, bounded, fresh, non-authoritative observations; malformed/stale/consumer-mismatch hints become `ignored` and cannot change verdict or gate.

- [ ] **Step 5: Run documentation contract tests and commit**

Run: `PYTHONPATH=. ./.venv/bin/pytest tests/test_agent_read_only_contract.py -q`

```bash
git add docs/agents/REVIEW_AGENT_CONTRACT.md NBS_HERMES_MONITORING.md tests/test_agent_read_only_contract.py
git commit -m "docs: define session verification gate boundaries"
```

## Task 8: Full matrix, benchmark and rollout evidence

**Files:**
- Modify: `tests/test_verification_chain.py`
- Modify: `tests/test_strict_review_runner_acceptance.py`
- Create: `scripts/verification_chain_benchmark.py`
- Test: `tests/test_verification_chain_benchmark.py`

**Interfaces:**
- Consumes: completed session controller and deterministic fixtures; production database remains read-only。
- Produces: benchmark JSON containing per-gate duration, reused batch count, runner probe count, source fingerprint and final status。
- Invariant: benchmark never mutates SQLite, baseline, active export, trusted reference or formal business data。

- [ ] **Step 1: Add failure-injection matrix**

Cover:

- stale HEAD/worktree between gates → `stale_source`;
- static cache mismatch → `blocked_runner_capability`;
- live runner exit/timeout/invalid JSON → `blocked_runner_transport`;
- one Review batch failure → only that batch reruns;
- full pytest failure → `verification_failed`;
- Hermes failure → `hermes_failed`;
- old session report present → ignored by current session;
- Memory Hub malformed/stale observation → ignored;
- all gates pass → `completion-attestation-v1: complete`。

- [ ] **Step 2: Implement benchmark command**

Use `time.perf_counter()` around each gate, output only bounded metadata, and include source/gate/artifact fingerprints. Do not include raw rows, full logs, prompt contents or secrets.

- [ ] **Step 3: Run focused and benchmark tests**

Run: `PYTHONPATH=. ./.venv/bin/pytest tests/test_verification_chain.py tests/test_strict_review_runner_acceptance.py tests/test_verification_chain_benchmark.py -q`

Expected: all failure-injection, resume, benchmark and clean-session tests pass.

- [ ] **Step 4: Run full verification**

Run:

```bash
PYTHONPATH=. ./.venv/bin/pytest tests/ -q
PYTHONPATH=. ./.venv/bin/python scripts/hermes_post_change_check.py --skip-monitor --json
PYTHONPATH=. ./.venv/bin/python scripts/system_manager.py acceptance
git diff --check
```

Expected: full pytest PASS, Hermes `overallStatus: pass`, service acceptance PASS and clean diff check.

- [ ] **Step 5: Run Review and final attestation**

Create a fresh session against the current brief and current worktree. Run pre-review targeted evidence, live runner capability, all Review batches, full pytest, Hermes, then `attest`. Confirm final `status=complete`; if any gate fails, preserve the exact terminal state and do not claim completion.

- [ ] **Step 6: Commit checkpoint**

```bash
git add scripts/verification_chain_benchmark.py tests/test_verification_chain.py tests/test_strict_review_runner_acceptance.py tests/test_verification_chain_benchmark.py
git commit -m "test: validate strict review verification chain"
```

## Execution Checkpoints

每個 Task 完成後必須依序交付：

1. 實際 diff 與 implementation report。
2. targeted pytest 結果。
3. findings-first Review 結果。
4. 如 Review PASS，再進行下一個 Task；不要用 full pytest 或 Hermes 結果掩蓋 Review finding。

整個 plan 完成後必須另外交付：

- session manifest 與 source seal fingerprint。
- 各 gate 的 bounded evidence 與 status。
- runner static/live capability receipt。
- full pytest、Hermes、system acceptance 結果。
- `completion-attestation-v1`；若不是 `complete`，必須標示阻塞而不是宣稱完成。
