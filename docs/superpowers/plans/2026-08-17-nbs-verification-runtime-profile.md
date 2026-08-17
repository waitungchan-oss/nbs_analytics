# Verification Runtime Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with review checkpoints.

**Goal:** Make isolated worktree verification reproducible and fail-closed without changing the formal database, frozen baseline, or Monthly Baseline Governance.

**Architecture:** Add a typed `verification-runtime-profile-v1` manifest and a disposable SQLite snapshot under ignored `.nbs_agent_runtime/verification/`. Route data-dependent checks through explicit profile paths, bind services to dynamic profile ports and process identity, and inject a deterministic clock into Short-term Offload persistence. Hermes consumes the profile and rejects missing, stale, cross-worktree, or identity-mismatched evidence.

**Tech Stack:** Python 3.11+, dataclasses, pathlib, SQLite read-only/backup APIs, SHA-256 canonical fingerprints, pytest, existing `system_manager.py` and Hermes post-change check.

## Global Constraints

- Formal scope remains `不含掛賬核銷與TT退款轉團款`.
- Frozen 2026-05 baseline remains `HKD 12,057,968`.
- Do not write the formal `nbs_marketing_data.db`, backup/quarantine files, primary `.nbs_runtime`, or primary `.nbs_runtime_cache`.
- Do not change `data/monthly_revenue_baselines.json`, baseline modes, promotion history, rollback, revenue rules, or export schema.
- Verification artifacts may be written only under ignored `.nbs_agent_runtime/verification/<profileId>/`.
- Every behavior change starts with a RED test and ends with focused GREEN tests, `py_compile`, and `git diff --check`.
- Missing or mismatched profile evidence is `blocked_runner_capability`; never create an empty database or borrow another checkout's service.
- Implementation stays in branch `codex/verification-runtime-profile` and does not commit, merge, push, or start production services.

---

### Task 1: Immutable verification profile model and loader

**Files:**
- Create: `backend/services/verification_runtime_profile.py`
- Test: `tests/test_verification_runtime_profile.py`

**Interfaces:**
- `VERIFICATION_PROFILE_SCHEMA = "verification-runtime-profile-v1"`
- `VerificationRuntimeProfile.from_dict(payload, *, expected_git_head=None) -> VerificationRuntimeProfile`
- `VerificationRuntimeProfile.load(path, *, expected_git_head=None) -> VerificationRuntimeProfile`
- `VerificationRuntimeProfile.to_dict() -> dict`
- `VerificationRuntimeProfile.fingerprint() -> str`
- `VerificationRuntimeProfileError` for invalid, stale, unsafe, or mismatched evidence.

- [ ] **Step 1: Write the failing tests**

Cover exact top-level keys, profile self-fingerprint, Git HEAD binding, relative snapshot/generation references, non-zero dynamic ports, `readOnly=true`, path traversal rejection, symlink rejection, and frozen dataclass immutability.

- [ ] **Step 2: Run test to verify RED**

Run `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest -q tests/test_verification_runtime_profile.py`. Expected: collection fails because the module does not exist.

- [ ] **Step 3: Write minimal implementation**

Use frozen dataclasses and the repository canonical JSON fingerprint helper. Require exact fields from the Spec, validate IDs/ports/relative references, and recompute the profile fingerprint before accepting a payload. Loading never resolves or writes a source path.

- [ ] **Step 4: Verify GREEN and static checks**

Run the focused pytest, `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile backend/services/verification_runtime_profile.py tests/test_verification_runtime_profile.py`, and `git diff --check`.

- [ ] **Step 5: Record report**

Write `.nbs_agent_runtime/review_inputs/verification-profile-task-1-report.md` with RED/GREEN output, allowed files, and no-production-write evidence.

### Task 2: Read-only SQLite snapshot and profile builder

**Files:**
- Create: `backend/services/verification_runtime_snapshot.py`
- Create: `scripts/build_verification_runtime_profile.py`
- Test: `tests/test_verification_runtime_snapshot.py`
- Test: `tests/test_build_verification_runtime_profile.py`

**Interfaces:**
- `build_read_only_snapshot(source_db: Path, destination: Path) -> SnapshotEvidence`
- `build_verification_profile(*, project_root: Path, source_db: Path, source_runtime: Path, output_root: Path, git_head: str, ports: Mapping[str, int]) -> Path`
- `load_snapshot_read_only(snapshot: Path) -> sqlite3.Connection`
- `SnapshotEvidence` contains source/snapshot SHA-256, integrity result, and relative snapshot ref.

- [ ] **Step 1: Write RED tests**

Test that a valid source creates a disposable snapshot with matching data and fingerprints, the source is unchanged, missing/invalid source blocks before destination creation, destination symlinks and traversal are rejected, and the returned connection cannot write (`PRAGMA query_only=1` plus write rejection).

- [ ] **Step 2: Run RED**

Run both new test files; expected failure is missing snapshot/profile modules.

- [ ] **Step 3: Implement snapshot and builder**

Use SQLite backup from a read-only source connection into the ignored verification root. Validate integrity before and after backup. Copy only bounded generation JSON and cache inventory metadata; do not copy cache contents. Read the committed monthly registry for its fingerprint and embed the fixed May total exactly as an identity check, not as a replacement calculation.

- [ ] **Step 4: Verify GREEN and no formal writes**

Run focused tests, compile, diff check, and compare source DB SHA-256 plus mtime before/after. The test must assert the formal source file has not changed.

- [ ] **Step 5: Record evidence**

Write the task report with source/snapshot fingerprints and the exact output root.

### Task 3: Explicit DB/runtime path injection and empty-DB guard

**Files:**
- Modify: `backend/services/dashboard_service.py`
- Modify: `scripts/phase2j_baseline_check.py`
- Modify: `scripts/monthly_baseline_check.py`
- Modify: `backend/routers/health.py`
- Test: `tests/test_verification_runtime_paths.py`
- Test: `tests/test_phase2_precheck_acceptance.py` (only add profile-path coverage; preserve existing assertions)

**Interfaces:**
- `VerificationRuntimePaths` carries `db_path`, `runtime_dir`, `cache_path`, and `profile_path`.
- `resolve_verification_paths(profile: VerificationRuntimeProfile) -> VerificationRuntimePaths`.
- Baseline and health entrypoints accept explicit profile paths and reject an absent profile in verification mode.

- [ ] **Step 1: Write RED tests**

Add tests proving a linked worktree without a profile does not create `nbs_marketing_data.db`, baseline checks read the profile snapshot, and health reports profile identity instead of defaulting to checkout-local runtime. Assert formal DB mtime/fingerprint remains unchanged.

- [ ] **Step 2: Run RED**

Run the new path tests and the phase2 focused file; expected failures show current checkout-root fallback or missing path parameter.

- [ ] **Step 3: Implement explicit path routing**

Thread explicit paths through existing functions rather than mutating `config.DB_FILE` or global module constants. Add a verification-mode guard before any default connection can be opened. Keep normal primary-runtime defaults unchanged.

- [ ] **Step 4: Verify GREEN**

Run path tests, `tests/test_phase2_precheck_acceptance.py`, compile affected modules, and diff check. Confirm the profile snapshot returns the same May total and monthly governance checks as the source.

- [ ] **Step 5: Record report**

Include source-vs-snapshot baseline comparison and proof that no baseline registry or formal DB file changed.

### Task 4: Service identity, dynamic ports, and truthful acceptance

**Files:**
- Modify: `scripts/system_manager.py`
- Modify: `backend/routers/health.py`
- Test: `tests/test_system_manager.py`
- Test: `tests/test_system_health_service.py`

**Interfaces:**
- `build_service_specs(project_root: Path, python_bin: str, npm_bin: str, *, ports: Mapping[str, int] | None = None, profile_id: str | None = None) -> dict`
- `service_status(project_root: Path = PROJECT_ROOT, *, profile: VerificationRuntimeProfile | None = None) -> dict`
- `run_http_acceptance(project_root: Path = PROJECT_ROOT, *, profile: VerificationRuntimeProfile | None = None) -> dict`
- Status records include `alive`, `ready`, `ownerMatch`, `identityMatch`, and a bounded failure reason.

- [ ] **Step 1: Write RED tests**

Cover absent PID, a ready fixed port owned by the primary checkout, mismatched command/cwd/profile identity, matching dynamic-port service, and normal primary-runtime compatibility.

- [ ] **Step 2: Run RED**

Run `tests/test_system_manager.py tests/test_system_health_service.py`; expected failures demonstrate readiness-only acceptance.

- [ ] **Step 3: Implement identity binding**

Use existing command matching helpers plus profile namespace and expected Git HEAD. Add a bounded health identity payload from process-local environment/profile, without secrets or absolute paths. Require `alive && ready && ownerMatch && identityMatch` for acceptance.

- [ ] **Step 4: Verify GREEN**

Run focused tests, compile `scripts/system_manager.py` and the health router, and diff check. Confirm the main runtime remains ready and a worktree with no managed PID returns failed acceptance.

- [ ] **Step 5: Record report**

Store accepted and rejected identity cases in the task report; do not start or stop the user's services.

### Task 5: Deterministic Short-term Offload clock

**Files:**
- Modify: `backend/agents/short_term_offload_store.py`
- Modify: `backend/agents/short_term_offload_service.py` (only if propagation is required)
- Test: `tests/test_short_term_offload_store.py`
- Test: `tests/test_short_term_offload_service.py`

**Interfaces:**
- `ShortTermOffloadStore.write(artifact, *, allow_expired=False, now: datetime | None = None) -> None`
- Existing service `now` parameters propagate to `write`; `None` continues to mean real UTC time in production.

- [ ] **Step 1: Write RED regression tests**

Use a post-fixture date and assert ready artifacts remain writable when `now` is explicitly the fixture clock, while expired artifacts remain rejected. Add one service propagation test.

- [ ] **Step 2: Run RED**

Run both Short-term Offload files; expected failure is the store comparing against real wall-clock time.

- [ ] **Step 3: Implement explicit clock propagation**

Compute one effective UTC `now` per call and use it consistently for write/expiry checks. Do not extend TTLs or weaken expiry validation.

- [ ] **Step 4: Verify GREEN**

Run the two files, compile affected modules, and diff check. Confirm no production runtime files are touched.

- [ ] **Step 5: Record report**

Record deterministic clock evidence and the unchanged expiry boundary.

### Task 6: Hermes profile integration and final acceptance

**Files:**
- Modify: `scripts/hermes_post_change_check.py`
- Modify: `NBS_HERMES_MONITORING.md`
- Test: `tests/test_hermes_post_change_check.py`
- Test: `tests/test_verification_runtime_profile_integration.py`

**Interfaces:**
- Hermes accepts `--verification-profile <path>` and emits profile identity in JSON/Markdown.
- Required profile checks return `pass`, `blocked_runner_capability`, or `not_required` with bounded reasons.
- Existing no-profile primary-runtime invocation remains supported.

- [ ] **Step 1: Write RED integration tests**

Cover valid snapshot/profile acceptance, missing profile blocking in isolated mode, stale fingerprint blocking, primary-runtime compatibility, and source DB signature equality before/after Hermes.

- [ ] **Step 2: Run RED**

Run the new integration tests; expected failure is unsupported profile argument or absent profile evidence.

- [ ] **Step 3: Implement profile-aware Hermes checks**

Load and validate the profile before system checks. Route baseline and health checks to explicit snapshot/runtime paths, require truthful service identity, and keep Hermes read-only. Do not add cache copying, repair, prune, approval, or baseline promotion.

- [ ] **Step 4: Run targeted and full verification**

Run `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest -q`, `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python scripts/system_manager.py acceptance --verification-profile <profile>`, and `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python scripts/hermes_post_change_check.py --verification-profile <profile> --json`. Capture exact counts and classify unrelated failures instead of rewriting baseline data.

- [ ] **Step 5: Review and final checks**

Run strict findings-first Review against the immutable implementation commit, then compile/diff checks, full pytest, system acceptance, Hermes, and source DB fingerprint comparison. Stop if any required gate fails.

- [ ] **Step 6: Write final task report**

Include profile ID, Git HEAD, snapshot/source fingerprints, baseline/monthly results, service identity evidence, test counts, Hermes result, and confirmation that formal SQLite, baseline registry, and primary runtime were not modified.
