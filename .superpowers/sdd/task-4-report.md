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

## Verification

### RED

Command:

```text
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_workflow_retention.py -q
```

Result: collection failed with the expected `ModuleNotFoundError` because `workflow_retention.py` was not present.

### GREEN

Commands and results:

```text
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_workflow_retention.py -q
7 passed in 0.07s

/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_workflow_retention.py tests/test_workflow_models.py tests/test_workflow_store.py -q
35 passed in 0.10s

/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile backend/agents/workflow_retention.py tests/test_workflow_retention.py
passed

git diff --check
passed with no output
```

## Concerns

- Full repository pytest, Hermes, SQLite, backup, quarantine, and formal runtime acceptance were intentionally not run or modified for this focused Task 4 scope.
- Retention does not rewrite oversized stage artifacts; the configured caps are loaded and exposed for the producing workflow to enforce. It only removes eligible stage files during compaction.
