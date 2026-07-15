# Task 1 Report: Workflow schemas and legal state transitions

## Status

DONE

## Scope

- Added `backend/agents/workflow_models.py` with the four workflow dataclasses, exact schema versions, strict JSON validation, canonical SHA-256 fingerprints, workflow status sets, and legal transitions.
- Added `tests/test_workflow_models.py` covering authorization gating, terminal-state re-entry, exact status sets, canonical fingerprints, round trips, ISO-8601 timestamps, strict keys, unknown statuses, and illegal event transitions.
- Did not modify SQLite, baseline, runtime, Hermes, or unrelated files.

## TDD Evidence

The strict JSON regression test for tuple `dirtyFiles` was added before the implementation fix. The focused RED run was:

```text
11 collected, 1 failed, 10 passed
```

The failure was the expected pre-fix failure: tuple `dirtyFiles` was accepted by `WorkflowManifest.from_dict()`.

## Fix

- Added shared value-level validation in `__post_init__` so direct dataclass construction and `from_dict()` enforce the same necessary schema, status, hash, timestamp, metadata, and transition invariants.
- Normalized valid UTC `Z` timestamps to `+00:00`, which is parseable by Python 3.9 and remains stable through `to_dict()` / `from_dict()` round trips.
- Restricted JSON `dirtyFiles` validation to lists while preserving the existing tuple-backed direct constructor representation.
- Validated `WorkflowStatus.message` as a non-empty string and validated status transition values as strings before set membership checks, so malformed lists raise `WorkflowSchemaError` instead of `TypeError`.

## Verification

Controller-executed focused verification:

```text
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_workflow_models.py -q
8 passed
```

After adding the two review-fix regression cases, the same focused command was rerun:

```text
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_workflow_models.py -q
10 passed
```

After the strict JSON tuple fix, the focused command was rerun:

```text
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_workflow_models.py -q
11 passed
```

After the final review fixes, the focused command was rerun:

```text
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_workflow_models.py -q
13 passed
```

`git diff --check` was also executed successfully.

## Self-review

- `legal_transition()` defaults unknown or terminal source statuses to no outgoing transition.
- `WorkflowStatus` accepts only the exact workflow status set and validates timestamps, nullable completion/error fields, and non-negative artifact bytes.
- `WorkflowStatus` validates non-empty messages, and status transition values are type-checked before membership tests.
- All `from_dict()` methods reject missing or unknown keys and enforce their exact schema version.
- `WorkflowEvent` rejects illegal status transitions while allowing metadata-only events with both status fields null.
- `canonical_sha256()` uses sorted keys, compact JSON separators, UTF-8, and SHA-256.

## Concerns

- No SQLite, baseline, runtime, Hermes, or unrelated Task files were modified.
- Only the focused workflow model test file was executed; no broader test result is claimed here.
