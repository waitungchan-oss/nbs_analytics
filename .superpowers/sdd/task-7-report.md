# Task 7 Implementation Report

## Scope

- Added `scripts/agent_workflow.py` with JSON-only `run` / `start`, `approve`, `status`, `list`, and `prune` commands.
- Added CLI and fake-executor integration coverage in `tests/test_agent_workflow_cli.py` and `tests/test_agent_workflow_integration.py`.
- Extended Hermes targeted tests and added a read-only workflow artifact / retention report.
- Updated architecture, dispatch, and repository governance documentation for Phase 1 CLI behavior and Phase 2 Streamlit boundary.

No SQLite, baseline, formal runtime, business rule, revenue, export schema, or database file was modified.

## Behavior

- `run` only collects Context and stops at `awaiting_authorization`; `start` is an alias.
- `approve` requires the explicit run ID, approved contract, Implementation runner, and Review runner. Runner commands are not persisted in run artifacts.
- `status` and `list` are read-only; `list` is stable newest-first by `updatedAt` then run ID.
- `prune --dry-run` is non-writing; `prune --apply` applies the existing retention policy. `run` also wires the existing best-effort policy housekeeping hook.
- CLI stdout is one JSON document; diagnostics are redacted stderr. Exit mapping is 0 for completed / awaiting authorization / read-only commands, 1 for changes required, 2 for blocked, 4 for overflow or invalid agent output, and 5 for runtime/schema failure.
- Hermes reports workflow artifact and retention state with no prune, artifact write, or Review overlap.

## TDD Evidence

RED:

```text
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_workflow_cli.py tests/test_agent_workflow_integration.py -q
8 failed
```

The failures were the expected `ModuleNotFoundError: No module named 'scripts.agent_workflow'` before the CLI existed.

GREEN:

```text
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_workflow_cli.py tests/test_agent_workflow_integration.py tests/test_hermes_post_change_check.py tests/test_agent_dispatch_contract.py -q
26 passed in 0.09s

/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_workflow_models.py tests/test_workflow_store.py tests/test_workflow_notifications.py tests/test_workflow_retention.py tests/test_workflow_orchestrator_start.py tests/test_workflow_orchestrator_approve.py tests/test_agent_workflow_cli.py tests/test_agent_workflow_integration.py -q
99 passed in 2.06s
```

Additional checks:

```text
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile scripts/agent_workflow.py scripts/hermes_post_change_check.py tests/test_agent_workflow_cli.py tests/test_agent_workflow_integration.py
passed

/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python scripts/agent_workflow.py list
{"runs": []}

git diff --check
passed
```

## Existing Regression Note

The required existing Agent regression command produced `233 passed, 1 failed`. The failure is pre-existing branch evidence scope: `tests/test_agent_cli.py::test_review_cli_accepts_runtime_input_and_rejects_symlink_escape` invokes Review collection against `base=main`; this branch already contains tracked `.superpowers/sdd/task-1-report.md` changes, which are outside `EvidencePolicy` allowlist, so collection returns exit 3 before the test's runtime-input assertion. Task 7 does not modify that policy or those historical task reports, and the Task 7 focused diff contains none of them.

## Review

Findings-first self-review found and corrected one documentation inconsistency: retention is wired as best-effort housekeeping after `run`, so the governance docs now distinguish that from explicit `prune --apply` rather than claiming prune is the only compaction path.
