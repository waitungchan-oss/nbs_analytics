# Task 6 Report: Approved workflow pipeline and final gates

## Status

Completed locally on `codex/agent-orchestrator-phase1`.

## Scope delivered

- `WorkflowOrchestrator.approve(...)` preserves the existing authorization, immutable approval, Implementation, targeted-evidence, and Review gates.
- A Review `pass` now runs fixed final gates in this order, using exact argv and the repository virtualenv Python:
  1. `(python, "-m", "pytest", "-q")`
  2. `(python, "scripts/system_manager.py", "acceptance")`
  3. `(python, "scripts/hermes_post_change_check.py", "--skip-monitor", "--json")`
- `full-verification.json` persists bounded full-pytest evidence (`exitCode`, `stdoutTail`, `stderrTail`, and `payload`) plus the existing acceptance payload; `hermes.json` persists the Hermes payload.
- Full pytest failure or acceptance `status != "passed"` transitions to terminal `blocked` before Hermes starts.
- Hermes failure or `overallStatus != "pass"` transitions to terminal `blocked`; Hermes pass transitions to terminal `completed`.
- Terminal paths set `completedAt`; notifications cover Implementation completion, Review changes-required, blocked/failure, Hermes result, and workflow completion.
- The orchestration contains no service `start` or `stop` command.

## TDD evidence

RED:

```text
tests/test_workflow_orchestrator_start.py tests/test_workflow_orchestrator_approve.py
5 failed, 30 passed
```

The new final-gate tests correctly stopped at the former `review_running` behavior: no final commands, artifacts, or terminal mapping existed. A subsequent executor-level RED test also proved that successful text-only pytest output was rejected before the `require_json=False` allowance was added for this one fixed command.

GREEN:

```text
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest \
  tests/test_workflow_orchestrator_start.py tests/test_workflow_orchestrator_approve.py -q
37 passed in 1.78s

/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile \
  backend/agents/workflow_orchestrator.py tests/test_workflow_orchestrator_approve.py

git diff --check
```

## Task 6 final fix

Review found that successful text-only full pytest output was persisted as an empty payload. The focused approve regression now returns `35 passed in 1.58s` with `require_json=False` semantics and verifies the bounded terminal evidence is retained. The acceptance payload shape and final gate order remain unchanged; Task 7 is out of scope.

## Focused coverage

- exact final-gate argv and stage order;
- final-gate artifacts;
- full pytest and acceptance failures blocking before Hermes;
- Hermes payload and exit failures mapping to blocked;
- Hermes pass mapping to completed with `completedAt`;
- notifications and no service management calls.

## Scope boundary

No real full pytest, system acceptance, Hermes, service start, or service stop command was run by this implementation task. The focused tests use the existing fake `StageExecutor`; the production orchestrator invokes final gates only after a real Review pass.
