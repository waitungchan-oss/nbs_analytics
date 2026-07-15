# Task 4 Implementation Report

## Status

- status: completed
- task: retention policy, dry-run, apply, and compact metadata preservation
- changed files:
  - `backend/agents/workflow_retention.py`
  - `agent_config/workflow_retention.json`
  - `tests/test_workflow_retention.py`

## Implementation

- Added strict `RetentionPolicy`, `RetentionCandidate`, and `RetentionReport` dataclasses.
- Added deterministic `WorkflowRetention.plan(now)` with 90-day retention and latest-30 terminal protection.
- Non-terminal, recent, latest-terminal, blocked, failed, and changes-required runs are retained.
- Old completed runs can compact only allowlisted stage JSON files; manifest, status, approval, events, and archive summary remain.
- `apply()` supports dry-run without writes, writes `archive-summary.json` atomically before deleting listed stage files, and rejects unsafe or external paths.
- Retention scans only direct children of `.nbs_agent_runtime/runs`; unknown schemas, symlinks, malformed artifacts, and held locks are skipped with reasons.
- Added the required strict JSON policy with stage, run, and command-output caps.
- Hardened `apply()` with lock-scoped status, created-at, and latest-terminal revalidation so stale reports cannot compact newly nonterminal or protected runs.
- Enforced positive policy limits, single-directory run IDs, per-stage and per-run caps, and bounded command-output tails.
- Compacted completed-run `events.jsonl` into bounded `eventSummary` metadata before removal, including event types, error codes, risk surfaces, and output tails.
- Made repeated application of the same report idempotent when all listed artifacts are already absent.

## Verification

### RED

Command:

```text
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_workflow_retention.py -q
```

Result: the regression suite initially produced `6 failed, 13 passed`, covering stale eligibility, run ID validation, cap enforcement, event compaction, idempotent apply, and strict config validation.

### GREEN

Commands and results:

```text
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_workflow_retention.py -q
19 passed in 0.19s

/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_workflow_retention.py tests/test_workflow_models.py tests/test_workflow_store.py -q
35 passed in 0.10s

/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile backend/agents/workflow_retention.py tests/test_workflow_retention.py
passed

git diff --check
passed with no output
```

## Concerns

- Full repository pytest, Hermes, SQLite, backup, quarantine, and formal runtime acceptance were intentionally not run or modified for this focused Task 4 scope.
- Oversized stage artifacts are rejected from a retention candidate rather than rewritten; command-output tails are bounded while building the archive summary.
