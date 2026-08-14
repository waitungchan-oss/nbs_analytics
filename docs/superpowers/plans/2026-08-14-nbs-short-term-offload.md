# NBS Short-term Offload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Inline Execution 中提供 opt-in、短 TTL、read-only 的長工具輸出 offload，讓主 Context 只保留摘要與可驗證 reference。

**Architecture:** 以獨立 `short_term_offload_*` modules 實作 strict artifact model、敏感內容掃描、隔離 store、bounded drill-down 與 projection。runner 只在明確 `short_term_offload=on` 或 trusted task descriptor 下建立 artifact；所有缺失、竄改、過期、symlink 與超限情況都回到 inline/canonical fallback。此計畫不改 canonical evidence、SQLite、baseline、Governance Graph、approval、Review、Hermes 或 workflow authority。

**Tech Stack:** Python 3 dataclasses、strict JSON、SHA-256、pytest、既有 runner／Context Agent／Hermes read-only patterns；不新增外部 service、database 或 remote dependency。

**Execution status (2026-08-14):** Tasks 1–5 completed on `codex/short-term-offload`; focused findings-first Reviews PASS, full pytest `1802 passed`, system acceptance PASS, and Hermes post-change `overallStatus=pass`. Offload remains explicit opt-in; ordinary workflow and Memory Sidecar recall defaults are unchanged.

## Global Constraints

- Artifact schema literal is `short-term-offload-v1`; unknown keys are rejected.
- Default TTL is 30 minutes; maximum TTL is 24 hours.
- Content cap is 32,000 UTF-8 bytes per artifact; summary cap is 2,048 bytes.
- Each run may create at most 20 artifacts and 200,000 total stored bytes.
- Storage is isolated from `.nbs_agent_runtime/runs`, canonical evidence, SQLite and Obsidian vault.
- Secrets, API keys, `.env`, auth config, SQLite/Excel/CSV rows, prompts and internal reasoning are never persisted.
- Missing, stale, tampered, symlinked, traversal or permission-denied artifacts fail closed and preserve inline/canonical workflow behavior.
- Offload is opt-in; ordinary workflow and Memory Sidecar recall defaults remain unchanged.
- Projection is read-only; it cannot create approval, dispatch, Graph edge, snapshot or workflow state.
- Cleanup may delete only expired offload artifacts below its own isolated root.
- Every Task follows TDD RED→GREEN, focused tests, `py_compile`, `git diff --check`, findings-first Review, then checkpoint.

## File Map

- `backend/agents/short_term_offload_models.py`: strict artifact, reference and result dataclasses; canonical fingerprints.
- `backend/agents/short_term_offload_policy.py`: exact caps, safe identifiers, TTL and opt-in policy validation.
- `backend/agents/short_term_offload_sanitizer.py`: sensitive-content detection, redaction and bounded summary generation.
- `backend/agents/short_term_offload_store.py`: isolated artifact write/read/list/expiry cleanup with symlink and root containment checks.
- `backend/agents/short_term_offload_projection.py`: read-only inline projection and Mermaid node mapping.
- `backend/agents/short_term_offload_service.py`: bounded write, drill-down and cleanup orchestration.
- `scripts/short_term_offload.py`: explicit opt-in CLI for inspect and cleanup; no approval or dispatch operation.
- `tests/test_short_term_offload_models.py`, `tests/test_short_term_offload_policy.py`, `tests/test_short_term_offload_sanitizer.py`, `tests/test_short_term_offload_store.py`, `tests/test_short_term_offload_projection.py`, `tests/test_short_term_offload_service.py`, `tests/test_short_term_offload_cli.py`.
- `scripts/hermes_post_change_check.py`: only add read-only artifact report for schema, caps, permissions and fallback diagnostics.

---

### Task 1: Strict artifact contracts and policy

**Files:**
- Create: `backend/agents/short_term_offload_models.py`
- Create: `backend/agents/short_term_offload_policy.py`
- Create: `tests/test_short_term_offload_models.py`
- Create: `tests/test_short_term_offload_policy.py`

**Interfaces:**
- `ShortTermOffloadArtifact.from_dict(payload: Mapping[str, object]) -> ShortTermOffloadArtifact`
- `ShortTermOffloadArtifact.to_dict() -> dict[str, object]`
- `ShortTermOffloadReference.from_artifact(artifact: ShortTermOffloadArtifact) -> ShortTermOffloadReference`
- `ShortTermOffloadPolicy.validate_ref_id(value: str) -> None`
- `ShortTermOffloadPolicy.validate_ttl(created_at: datetime, expires_at: datetime) -> None`
- `ShortTermOffloadPolicy.fingerprint() -> str`

- [ ] **Step 1: Write RED tests** for exact keys, schema version, safe identifier regex, lowercase SHA-256, source kind, status/redaction enums, 30-minute default TTL, 24-hour maximum, byte caps and unknown-key rejection.
- [ ] **Step 2: Run:** `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_short_term_offload_models.py tests/test_short_term_offload_policy.py -q -p no:cacheprovider` and confirm collection/behavior failure because the modules do not exist.
- [ ] **Step 3: Implement minimal frozen dataclasses and policy validator.** Compute `contentSha256` from UTF-8 content; reject non-finite timestamps, unsafe IDs, over-cap summary/content and TTL outside the exact bounds.
- [ ] **Step 4: Run focused tests, compile and diff check.**

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_short_term_offload_models.py tests/test_short_term_offload_policy.py -q -p no:cacheprovider
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile backend/agents/short_term_offload_models.py backend/agents/short_term_offload_policy.py
git diff --check
```

- [ ] **Step 5: Stop for findings-first Review of Task 1 files.**

### Task 2: Sensitive scanner and isolated artifact store

**Files:**
- Create: `backend/agents/short_term_offload_sanitizer.py`
- Create: `backend/agents/short_term_offload_store.py`
- Create: `tests/test_short_term_offload_sanitizer.py`
- Create: `tests/test_short_term_offload_store.py`

**Interfaces:**
- `sanitize_tool_output(*, content: str, source_fingerprint: str, policy: ShortTermOffloadPolicy) -> SanitizedOffload`
- `ShortTermOffloadStore.write(artifact: ShortTermOffloadArtifact) -> StoreResult`
- `ShortTermOffloadStore.read(*, run_id: str, session_id: str, ref_id: str) -> StoreResult`
- `ShortTermOffloadStore.cleanup_expired(*, now: datetime) -> CleanupResult`

- [ ] **Step 1: Write RED tests** for API key/bearer/private-key/.env detection, absolute home-path detection, prompt/internal-reasoning markers, SQLite/CSV/Excel row rejection, redaction diagnostics without original content, size/counter caps, symlink/traversal rejection and isolated-root containment.
- [ ] **Step 2: Run:** `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_short_term_offload_sanitizer.py tests/test_short_term_offload_store.py -q -p no:cacheprovider` and confirm expected missing-module failure.
- [ ] **Step 3: Implement fail-closed sanitizer.** On uncertain credential-like matches return `blocked` with a bounded reason; never write the input. Store regular files only below `<runtime_root>/short-term-offload/<run_id>/<session_id>/` and reject symlinks at every path component.
- [ ] **Step 4: Implement atomic JSON writes and bounded reads.** Write a temporary file within the same isolated directory, replace atomically, revalidate exact envelope/fingerprint on read, and return `missing`, `expired`, `blocked` or `fingerprint_mismatch` without substituting another artifact.
- [ ] **Step 5: Run focused tests, compile and diff check.**

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_short_term_offload_sanitizer.py tests/test_short_term_offload_store.py -q -p no:cacheprovider
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile backend/agents/short_term_offload_sanitizer.py backend/agents/short_term_offload_store.py
git diff --check
```

- [ ] **Step 6: Stop for findings-first Review of Task 2 files.**

### Task 3: Read-only drill-down and Mermaid projection

**Files:**
- Create: `backend/agents/short_term_offload_projection.py`
- Create: `backend/agents/short_term_offload_service.py`
- Create: `tests/test_short_term_offload_projection.py`
- Create: `tests/test_short_term_offload_service.py`

**Interfaces:**
- `ShortTermOffloadService.persist_tool_output(*, run_id: str, session_id: str, content: str, source_fingerprint: str, policy: ShortTermOffloadPolicy) -> ShortTermOffloadReference`
- `ShortTermOffloadService.drill_down(*, run_id: str, session_id: str, ref_id: str, expected_sha256: str, offset: int = 0, limit: int = 4096) -> DrillDownResult`
- `ShortTermOffloadService.cleanup(*, now: datetime) -> CleanupResult`
- `project_offload_reference(reference: ShortTermOffloadReference) -> dict[str, object]`
- `project_mermaid_node(reference: ShortTermOffloadReference) -> dict[str, object]`

- [ ] **Step 1: Write RED tests** for same-run/session drill-down, bounded slices, missing/expired/tampered/cross-run rejection, projection fields, Mermaid node-only output and no inferred edges.
- [ ] **Step 2: Run the focused projection/service tests and confirm missing-module failure.**
- [ ] **Step 3: Implement service orchestration.** Call sanitizer before store write; persist only `ready`, `redacted` or bounded `blocked` diagnostic artifacts; preserve inline summary when persistence is unavailable.
- [ ] **Step 4: Implement drill-down validation.** Validate safe IDs, expected SHA-256, TTL, run/session binding, root containment and slice bounds before reading content; never return unbounded content.
- [ ] **Step 5: Implement projection-only Mermaid mapping.** Emit `nodeId`, `refId`, summary, fingerprint and expiry; accept only relationships already supplied by run evidence and never synthesize dependency/approval edges.
- [ ] **Step 6: Run focused tests, compile and diff check.**

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_short_term_offload_projection.py tests/test_short_term_offload_service.py -q -p no:cacheprovider
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile backend/agents/short_term_offload_projection.py backend/agents/short_term_offload_service.py
git diff --check
```

- [ ] **Step 7: Stop for findings-first Review of Task 3 files.**

### Task 4: Explicit runner hook and cleanup CLI

**Files:**
- Create: `scripts/short_term_offload.py`
- Modify: `scripts/hermes_live_ab_runner.py` (narrow opt-in hook only; no global default change)
- Create: `tests/test_short_term_offload_cli.py`
- Modify: `tests/test_hermes_live_ab_runner.py`

**Interfaces:**
- CLI `python scripts/short_term_offload.py inspect --run-id ... --session-id ... --ref-id ... --sha256 ...`
- CLI `python scripts/short_term_offload.py cleanup --runtime-root ... --now ...`; the supplied root must resolve exactly to `<project_root>/.nbs_agent_runtime/short-term-offload`, never an arbitrary directory.
- Runner hook accepts only `short_term_offload=on|off`; absent value remains off.

- [ ] **Step 1: Write RED tests** for explicit opt-in, default-off behavior, safe CLI identifiers, inspect slice limits, cleanup root restriction and rejection of approval/dispatch/classification arguments.
- [ ] **Step 2: Run the CLI tests and confirm failure.**
- [ ] **Step 3: Implement the thin CLI.** It may inspect or clean only the isolated offload root; enforce exact project-root containment before any read/write. It cannot approve, dispatch, alter runner state, write canonical evidence or change Graph snapshots. Unknown flags exit non-zero without side effects.
- [ ] **Step 4: Add the narrow runner hook.** When offload is off or absent, preserve existing inline output. When on, pass only bounded output and run/session identity to `ShortTermOffloadService`; never pass secrets or full task prompts.
- [ ] **Step 5: Run focused CLI/runner tests, compile and diff check.**

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_short_term_offload_cli.py tests/test_hermes_isolated_profile.py tests/test_hermes_live_ab_runner.py -q -p no:cacheprovider
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile scripts/short_term_offload.py
git diff --check
```

- [ ] **Step 6: Stop for findings-first Review.**

### Task 5: Hermes read-only report and final acceptance

**Files:**
- Modify: `scripts/hermes_post_change_check.py`
- Create: `tests/test_short_term_offload_hermes_boundary.py`
- Modify: existing short-term offload tests only for final regressions

**Interfaces:**
- `short_term_offload_artifact_report() -> dict[str, object]` with schema `short-term-offload-hermes-report-v1`, `policy="read-only"`, bounded counts, cap warnings, invalid runs and `writes=0`.

- [ ] **Step 1: Write RED tests** for Hermes report schema, read-only policy, bounded artifact counts, symlink/permission diagnostics and zero writes/invocations.
- [ ] **Step 2: Implement only read-only report logic.** Hermes must not run cleanup, inspect arbitrary paths, invoke providers, change runtime state or treat offload artifacts as canonical evidence.
- [ ] **Step 3: Run complete targeted suite.**

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_short_term_offload_models.py tests/test_short_term_offload_policy.py tests/test_short_term_offload_sanitizer.py tests/test_short_term_offload_store.py tests/test_short_term_offload_projection.py tests/test_short_term_offload_service.py tests/test_short_term_offload_cli.py tests/test_short_term_offload_hermes_boundary.py tests/test_hermes_isolated_profile.py tests/test_hermes_live_ab_runner.py tests/test_hermes_post_change_check.py -q -p no:cacheprovider
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile backend/agents/short_term_offload_models.py backend/agents/short_term_offload_policy.py backend/agents/short_term_offload_sanitizer.py backend/agents/short_term_offload_store.py backend/agents/short_term_offload_projection.py backend/agents/short_term_offload_service.py scripts/short_term_offload.py scripts/hermes_post_change_check.py
git diff --check
```

- [ ] **Step 4: Run full verification and read-only acceptance.**

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest -q
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python scripts/system_manager.py acceptance
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python scripts/hermes_post_change_check.py --json
```

- [ ] **Step 5: Stop with an evidence report.** Report token/latency observations only when supported by repeated real run evidence; do not claim project-wide savings from fixtures.

## Rollback

Set `short_term_offload=off`; leave canonical and existing runtime evidence untouched. Cleanup is optional and may target only the isolated short-term offload root.
