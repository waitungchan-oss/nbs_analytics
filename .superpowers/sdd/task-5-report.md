# Task 5 Report: Start run, collect Context, and stop at authorization

## Status

COMPLETED

## Scope

- Added `backend/agents/workflow_orchestrator.py` with the injected `StageExecutor` contract, bounded UTF-8 subprocess executor, read-only Git identity capture, EvidencePolicy Brief validation, Context CLI dispatch, workflow manifest/status/event persistence, safe notification degradation, and optional housekeeping warning handling.
- Added `tests/test_workflow_orchestrator_start.py` covering successful start, manifest identity/fingerprints, collect-only/default dispatch, supplied runner forwarding without persistence, missing Brief blocking, Context failure status recording, authorization stop, event/notification emission, and warning-only housekeeping/notification failures.
- Did not modify SQLite, baseline, Hermes, formal runtime, or other Task files.

## TDD Evidence

The focused RED run failed during collection with the expected:

```text
ModuleNotFoundError: No module named 'backend.agents.workflow_orchestrator'
```

After the minimal implementation and one argv expectation correction, focused tests passed.

## Verification

```text
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_workflow_orchestrator_start.py tests/test_workflow_models.py tests/test_workflow_store.py tests/test_workflow_notifications.py -q
43 passed in 0.25s

/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile backend/agents/workflow_orchestrator.py tests/test_workflow_orchestrator_start.py
passed

git diff --check
passed with no output
```

## Concerns

- Missing or denied Brief returns a blocked `WorkflowStatus` before Context dispatch; because no valid Brief bytes exist, no manifest is created for that rejected input.
- This Task intentionally stops at `awaiting_authorization`; it does not execute implementation, commit, merge, SQLite operations, baseline changes, formal runtime actions, or Hermes checks.

## Review Fix: bounded subprocess output

- Replaced `subprocess.run(capture_output=True)` with `Popen` and two continuously draining reader threads.
- Each reader retains only the final 12,000 UTF-8 bytes for its stream, preventing unbounded stdout/stderr accumulation while avoiding pipe backpressure deadlocks.
- Added an oversized stdout/stderr regression test that forbids the old `subprocess.run` path and verifies bounded tails.

Review-fix verification:

```text
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_workflow_orchestrator_start.py -q
9 passed in 0.22s

/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile backend/agents/workflow_orchestrator.py tests/test_workflow_orchestrator_start.py
passed

git diff --check
passed with no output
```
