# Task 2 Findings Fix Report

## RED/GREEN

- RED: added regression tests for command redaction, long-string bounding, and schema-named paths. Before the implementation fix: `5 failed, 17 passed`.
- GREEN: focused verification passed with `52 passed`.
- Controller supplement: after the original Task 2 commit, the controller had already run and reported `44 passed`; this fix did not rerun the full pytest suite.

## Changes

- `backend/agents/documentation_evidence.py`: `_bounded_text()` now bounds every converted value; `_collect_commands()` emits only a SHA-256 `commandId`, `exitCode`, and bounded safe summary, never command text or argv.
- `backend/agents/documentation_policy.py`: ADR routing now requires explicit protected risk surfaces or explicit database/migration path segments; `schema_utils.py` and ordinary paths containing `schema` do not trigger ADR.
- `tests/test_documentation_evidence.py`: long-string and command/argv redaction regressions.
- `tests/test_documentation_policy.py`: schema false-positive and explicit database/migration routing regressions.

## Verification

Command:

`/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_documentation_evidence.py tests/test_documentation_policy.py tests/test_workflow_store.py tests/test_workflow_models.py -q`

Result: `52 passed in 0.21s`

Also passed:

- `py_compile` for both changed implementation modules
- `git diff --check`

## Commits

- Implementation and regression tests: `73f3365eb1b023f76841e9bd7adc48bd98a55ddb`.
- Report commit: this report is committed separately after inserting the implementation hash.

Implementation commit: `73f3365eb1b023f76841e9bd7adc48bd98a55ddb`.

## Concerns

- No full pytest run was performed per Task 2 instructions; the known backend health failure is outside this task.
- No Task 3-8, Store, CLI, runner, SQLite/runtime, baseline, Obsidian vault, or product code was modified.
