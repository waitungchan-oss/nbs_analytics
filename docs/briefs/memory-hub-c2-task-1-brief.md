# C2 Task 1 Review Brief: Team Catalog

## Scope

Review only the immutable Team Catalog contract and loader:

- `backend/agents/memory_hub_team_catalog.py`
- `tests/test_memory_hub_team_catalog.py`

## Contract

The implementation must parse `memory-team-catalog-v1` with exact keys, validate
project-bound team identities, unique deterministic memberships and scopes, and
re-derive record/catalog SHA-256 fingerprints. Loading is read-only and must
reject missing, outside-runtime, symlinked or otherwise unsafe paths.

## Forbidden scope

Do not modify SQLite, baseline, revenue rules, export schema, runtime state,
catalog files, policy service, UI, network, Git history or dispatch behavior.

## Verification evidence

- Focused pytest: `tests/test_memory_hub_team_catalog.py` (14 passed)
- `py_compile` passed for implementation and tests.
- `git diff --check` passed.
