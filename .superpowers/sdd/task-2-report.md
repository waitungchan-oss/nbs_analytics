# Task 2 Implementation Report

## Status

DONE

## Scope

- Modified `backend/services/agent_operations_service.py`.
- Modified `tests/test_agent_operations_service.py`.
- Created this report only.
- No Git add, commit, merge, retention apply, Hermes invocation, service operation, SQLite, baseline, runtime, or workflow artifact write was performed.

## RED Evidence

Command:

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_service.py -q
```

Result before the Task 2 service implementation: `10 failed, 6 passed`.

The failures were the expected missing Task 2 behavior: no `stages`, `findings`, `verification`, `hermes`, `tokenUsage`, `retentionState`, bounded event aggregation, unsafe artifact rejection, or run-scoped diagnostic code.

Self-review RED command after the initial GREEN implementation:

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_service.py -q
```

Result: `1 failed, 16 passed`. The failure proved that an unsafe `status.message` could still expose an absolute path and runner-related text. The minimal GREEN follow-up routes status messages through the same bounded sanitizer.

## GREEN Evidence

Focused Task 2 tests:

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_service.py -q
```

Result: `18 passed in 0.10s`.

Required Phase 1 regression command:

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_service.py tests/test_workflow_models.py tests/test_workflow_store.py tests/test_workflow_retention.py -q
```

Result: `67 passed in 0.60s`.

Syntax check:

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile backend/services/agent_operations_service.py tests/test_agent_operations_service.py
```

Result: exit `0`.

Diff whitespace check:

```bash
git diff --check
```

Result: exit `0`.

## Delivered Behavior

- Fixed allowlisted stage artifact names with containment, symlink, regular-file, size-cap, object-shape, and fail-closed run isolation checks.
- Compact stage availability/duration, review findings, verification status, Hermes status, archive retention state, and actual token telemetry only.
- `usage=None` when there is no valid non-negative input/output telemetry; no token estimates are generated.
- Retention policy loads once per snapshot; invalid or unknown configuration returns `{"status":"unavailable"}` and a bounded `retention_config_invalid` diagnostic.
- Reads at most the final 500 valid `WorkflowEvent` lines within the configured artifact cap and exposes derived timing only, never raw events.
- Bounds diagnostics and findings; sanitizes messages so paths, runner argv, stdout, stderr, prompts, and exception text are not returned.
- Covers malformed JSON, unknown manifest schema, oversize data, symlinks, invalid regular-file requirements, and one bad run being isolated from valid runs.

## Self-review

PASS. Reviewed for absolute-path and raw exception leakage, runner argv/stdout/prompt exposure, unsafe artifact traversal, permissive token handling, retention writes, and bounded output. The service performs read-only snapshot aggregation; it does not import or call `WorkflowRetention.apply`.

## Incomplete Items

None.

## Reviewer Fix Follow-up

### Important Findings Addressed

- `status.errorCode` now uses the same bounded sanitizer as all emitted free-text fields while preserving `null` when no error code exists. The sanitizer rejects quoted POSIX paths, `file://` URIs, runner-sensitive terms, Windows paths, and exception-class text.
- Event aggregation now reads `events.jsonl` from the file tail in fixed `64 KiB` binary chunks. It stops after the final 500 valid events for the run, retains no full-file line list, and skips an oversized event line rather than allowing an unbounded partial buffer.

### RED Evidence

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_service.py -q -k 'status_free_text_is_sanitized or event_reader_tails_from_end'
```

Result before the fix: `3 failed, 18 deselected`. The failures exposed quoted-path and `file://` leakage, plus full-file event iteration through `list(handle)`.

### GREEN Evidence

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_service.py -q
```

Result after the fix: `21 passed in 0.15s`.

### Scope

- Only `backend/services/agent_operations_service.py` and `tests/test_agent_operations_service.py` changed for this reviewer-fix follow-up, plus this appended report entry.
- No Git add, commit, merge, DB, baseline, runtime, retention apply, Hermes, service, or workflow artifact operation was performed.

### Second Reviewer Fix Follow-up

#### Important Findings Addressed

- The bounded sanitizer now rejects POSIX paths introduced after `[` and `=` delimiters, including `detail=[/Users/analyst/secret]` and `path=/Users/analyst/secret`; `file://` URIs and exception text remain unavailable in all emitted free text.
- `runId` is fail-closed to the Phase 1 single-segment identifier format. Unsafe run directories are isolated without echoing their identity. `briefName`, `gitBranch`, and `stage` use bounded allowlists and return `value unavailable` when unsafe, while `codex/agent-orchestrator-phase1` and normal stages remain unchanged.
- `archive-summary.json` only marks a run `archived_summary` when it is an object with schema `agent-workflow-archive-summary-v1` and a matching run ID. Schema or run-ID mismatches remain `complete` with an `invalid_archive_summary` diagnostic; non-object summaries fail closed as invalid run artifacts.
- The already-passing tail-only `events.jsonl` reader was retained unchanged.

#### RED Evidence

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_service.py -q
```

Result before this fix: `9 failed, 22 passed`. The expected failures proved the missing `[` and `=` path delimiter handling, unsanitized artifact-derived identity fields, unsafe run-ID exposure, and archive schema/run-ID validation gap.

#### GREEN Evidence

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_service.py -q
```

Result: `31 passed in 0.22s`.

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_service.py tests/test_workflow_models.py tests/test_workflow_store.py tests/test_workflow_retention.py -q
```

Result: `80 passed in 0.70s`.

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile backend/services/agent_operations_service.py tests/test_agent_operations_service.py
git diff --check
```

Result: both exit `0`.

#### Scope

- Modified only `backend/services/agent_operations_service.py`, `tests/test_agent_operations_service.py`, and this appended Task 2 report.
- No Git add, commit, merge, DB, baseline, runtime, retention apply, Hermes, service, or workflow artifact operation was performed.

### Final Hardening Follow-up

#### Important Findings Addressed

- The bounded free-text sanitizer now fail-closes any general absolute POSIX path whose slash is not part of an identifier token. This covers `note:/Users/secret` and `value,/Users/secret` without enumerating delimiters, while preserving relative identifiers such as `codex/branch`. Existing `file://` URI, Windows path, runner-sensitive term, and exception-text protections remain in place.
- The `events.jsonl` tail reader now has both a fixed `MAX_EVENT_SCAN_BYTES` limit of 1 MiB and `MAX_EVENT_SCAN_LINES` limit of 10,000. It continues to use 64 KiB binary chunks, stops when either scan budget or the 500 matching-event limit is reached, and constrains its byte budget to the configured stage artifact hard cap.
- `manifest.json`, `status.json`, and `agent_config/workflow_retention.json` now use the same pre-`json.load` containment, symlink, regular-file, and hard-size gate as stage artifacts. Retention configuration is checked with the 5 MiB safe default before parsing; a valid policy then supplies the run artifact cap. Oversize files are isolated before opening and produce only bounded diagnostics.

#### RED Evidence

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_service.py -q -k 'any_delimiter or relative_branch_path or oversize_core_artifact or oversize_retention_config or fixed_scan_budget'
```

Result before this hardening: `6 failed, 1 passed, 31 deselected`. The failures proved delimiter-based POSIX path leakage, core/config files opened before cap enforcement, and tail scanning beyond the intended fixed byte budget.

#### GREEN Evidence

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_service.py -q -k 'any_delimiter or relative_branch_path or oversize_core_artifact or oversize_retention_config or fixed_scan_budget'
```

Result: `7 passed, 31 deselected in 0.07s`.

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_service.py -q
```

Result: `38 passed in 0.22s`.

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_service.py tests/test_workflow_models.py tests/test_workflow_store.py tests/test_workflow_retention.py -q
```

Result: `87 passed in 0.72s`.

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile backend/services/agent_operations_service.py tests/test_agent_operations_service.py
git diff --check
```

Result: both exit `0`.

#### Scope

- Modified only `backend/services/agent_operations_service.py`, `tests/test_agent_operations_service.py`, and this appended Task 2 report.
- No Git add, commit, merge, DB, baseline, runtime, retention apply, Hermes, service, or workflow artifact operation was performed.

### Final Sanitizer Finding Follow-up

#### Important Finding Addressed

- General free-text sanitization now detects any absolute multi-segment POSIX path substring directly, without enumerating prefix delimiters. `note-/Users/secret`, `value._/Users/secret`, `tag~/Users/secret`, and `. /Users/secret` all return bounded `finding detail unavailable` and do not expose the path.
- `file://` URI rejection remains in the general free-text sanitizer. Branch and other artifact identity fields continue to use their independent complete relative-identifier allowlists, so `codex/agent-orchestrator-phase1` remains unchanged and is not sent through the general path detector.
- Existing event/core-cap/archive/retention behavior remains unchanged. No Task 3 work was performed.

#### RED Evidence

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_service.py -q -k 'any_delimiter'
```

Result before the fix: `3 failed, 1 passed, 36 deselected in 0.13s`. The failures proved that `note-`, `value._`, and `tag~` prefixes could still leak `/Users/secret`; the existing `. ` case already failed closed.

#### GREEN Evidence

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_service.py -q -k 'any_delimiter or relative_branch_path'
```

Result: `5 passed, 35 deselected in 0.08s`.

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_service.py -q
```

Result: `40 passed in 0.42s`.

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_service.py tests/test_workflow_models.py tests/test_workflow_store.py tests/test_workflow_retention.py -q
```

Result: `89 passed in 0.93s`.

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile backend/services/agent_operations_service.py tests/test_agent_operations_service.py
git diff --check
```

Result: both commands exit `0`.

#### Scope

- Modified only `backend/services/agent_operations_service.py`, `tests/test_agent_operations_service.py`, and this appended Task 2 report.
- No Git add, commit, merge, DB, baseline, runtime, retention apply, Hermes, service, or Task 3 operation was performed.

### Final Review Findings Follow-up

#### Important Findings Addressed

- Stage artifacts now validate their real contract before snapshot aggregation: `context-evidence-v1` or `context-summary-v1`, `implementation-run-report-v1`, `review-report-v1`, complete targeted command records, a real full-verification command/status shape, and a real Hermes `overallStatus`. Unknown schemaVersion and fake-pass payloads fail the whole run closed with a bounded diagnostic, so their usage cannot be counted. Existing schema-less full-verification and Hermes artifacts remain compatible.
- Git branch display now rejects invalid refname forms including empty segments, slash boundary errors, dot components, `..`, trailing dot, `.lock`, `@{`, backslash, control characters, whitespace, colon, question mark, asterisk, and bracket. `codex/agent-orchestrator-phase1` remains accepted.
- General free text now rejects slash, backslash, URI delimiters, and case-insensitive stdout/stderr/prompt/argv/exception/traceback/runner/command/token/password/secret substrings. This covers the final `note=/Users//secret`, `stdoutTail=/Users/[secret]/file`, and `Exception: failed` cases.

#### Strict TDD Evidence

First RED: `pytest tests/test_agent_operations_service.py -q -k 'stage_artifact_schema_or_fake_pass or git_branch_rejects_invalid_refname_forms or general_free_text_fails_closed or general_free_text_rejects_relative_branch_path'` returned `17 failed, 9 passed, 41 deselected` before the first hardening change.

Second RED: `pytest tests/test_agent_operations_service.py -q -k 'stage_artifact_schema_or_fake_pass'` returned `2 failed, 6 passed, 61 deselected` before same-schema implementation/review fake reports were rejected.

GREEN: `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_service.py -q` returned `70 passed in 0.18s`.

#### Final Verification

`/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_service.py tests/test_workflow_models.py tests/test_workflow_store.py tests/test_workflow_retention.py tests/test_workflow_orchestrator_approve.py -q` returned `143 passed in 2.06s`.

`/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile backend/services/agent_operations_service.py tests/test_agent_operations_service.py` and `git diff --check` both exited `0`.

#### Scope

- Modified only `backend/services/agent_operations_service.py`, `tests/test_agent_operations_service.py`, and this appended Task 2 report.
- No Git add, commit, merge, DB, baseline, runtime, retention apply, Hermes, service, or Task 3 operation was performed.

### Real-Artifact Compatibility Follow-up

#### Delivered Behavior

- `full-verification.json` now accepts a nonempty, exact-key subset of `fullPytest` and `acceptance`, matching the orchestrator write order. A supplied `fullPytest` must have exactly `exitCode`, `stdoutTail`, `stderrTail`, and `payload`; a supplied `acceptance` must be an object with a string `status`. Unknown keys and fake shapes remain invalid.
- A partial or failed full-verification artifact remains visible: failed pytest or acceptance maps to `fail`; incomplete evidence maps to `blocked`; only pytest exit code `0` plus acceptance `passed` maps to `pass`.
- Hermes `warning` is a valid, visible artifact status alongside `pass`, `fail`, and the existing `blocked` contract status.
- Branch segments beginning with `.` are fail-closed, general free text rejects bounded no-slash URI schemes such as `mailto:`, `data:`, and `urn:`, and stage display uses the exact Phase 1 allowlist. `database_write` displays `value unavailable`.

#### TDD Evidence

RED before the service change:

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_service.py -q -k 'full_verification_real_partial_or_failed_artifacts_remain_available or full_verification_requires_exact_real_artifact_fields or hermes_warning_artifact_remains_available or general_free_text_rejects_no_slash_uri_schemes or stage_display_uses_exact_allowlist or stage_display_preserves_exact_allowlisted_values or git_branch_rejects_invalid_refname_forms'
```

Result: `11 failed, 29 passed, 55 deselected`. The failures showed partial artifacts and Hermes warnings being isolated, hidden branch segments accepted, no-slash URI schemes exposed, and non-allowlisted stages displayed.

An additional exact-shape RED after adding null artifact cases returned `2 failed, 5 passed, 91 deselected`; it proved that present-but-null `fullPytest` and `acceptance` values still needed rejection.

GREEN:

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_service.py -q
```

Result: `98 passed in 0.21s`.

#### Scope

- Modified only `backend/services/agent_operations_service.py`, `tests/test_agent_operations_service.py`, and this appended report.
- No Git add, commit, merge, DB, baseline, runtime, retention apply, Hermes, service, or Task 3 operation was performed.
