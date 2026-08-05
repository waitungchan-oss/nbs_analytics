# Memory Sidecar Task 1 Brief

Objective: define strict memory-candidate and memory-hints contracts for a non-authoritative sidecar.
Allowed files: backend/agents/memory_sidecar_models.py, backend/agents/memory_sidecar_hint_models.py, backend/agents/memory_sidecar_policy.py, agent_config/memory_sidecar_policy.json, tests/test_memory_sidecar_models.py, tests/test_memory_sidecar_policy.py.
Required evidence: existing evidence_models fingerprint/token helpers, evidence allowlist, Context Agent limits.
Forbidden: SQLite, baseline, runtime writes, Git operations, external Gateway, raw logs, secrets.
Recommended tests: schema validation, fingerprint stability, source fingerprint binding, byte caps, path safety, stale/freshness states.
Review batching note: each Review batch may contain only a subset of the approved files; assess supplied patches and do not report other approved files as missing evidence.
