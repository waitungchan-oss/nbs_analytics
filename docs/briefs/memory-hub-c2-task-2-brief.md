# C2 Task 2 Review Brief: Agent Policy Catalog

## Scope

Review only the immutable Agent Policy Catalog contract and loader:

- `backend/agents/memory_hub_agent_policy_catalog.py`
- `tests/test_memory_hub_agent_policy_catalog.py`

## Contract

The implementation must parse `memory-agent-policy-catalog-v1` with exact keys,
fixed `defaultDecision=deny`, deterministic agent/team/kind/scope/rule ordering,
re-derived rule/record/catalog fingerprints, and exact Team Catalog membership
references. Loading is read-only and must reject missing, outside-runtime,
symlinked or malformed catalog files.

## Forbidden scope

Do not modify SQLite, baseline, revenue rules, export schema, runtime state,
Team Catalog contents, policy service, UI, network, dispatch behavior or Git
history.

## Verification evidence

- Focused pytest: `tests/test_memory_hub_agent_policy_catalog.py tests/test_memory_hub_team_catalog.py` (26 passed)
- `py_compile` passed for implementation and tests.
- `git diff --check` passed.
