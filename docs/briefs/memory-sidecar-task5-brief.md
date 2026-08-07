# Memory Sidecar Task 5 Brief
Objective: add bounded telemetry, safe-off feature flags, and deterministic shadow A/B evidence without provider integration.
Allowed files: backend/agents/memory_sidecar_telemetry.py, tests/test_memory_sidecar_telemetry.py, backend/agents/agent_runtime.py, agent_config/memory_sidecar_policy.json.
Required evidence: existing ignored runtime telemetry rotation, MemoryHints limits, no raw query/summary/source content.
Forbidden: Gateway startup, network, LLM invocation, SQLite, baseline, runtime authority, approval, dispatch, workflow control, Git writes.
Acceptance: exact safe telemetry schema, bounded fields, cohort/p95 aggregation, disabled callback gates, and ten equal non-R2 shadow profiles.
