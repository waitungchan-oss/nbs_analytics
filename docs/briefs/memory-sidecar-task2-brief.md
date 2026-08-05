# Memory Sidecar Task 2 Brief

Objective: add a provider-neutral fake adapter and bounded fail-open recall service for non-authoritative memory hints.
Allowed files: backend/agents/memory_sidecar_adapter.py, backend/agents/memory_sidecar_service.py, tests/test_memory_sidecar_adapter.py, tests/test_memory_sidecar_service.py.
Required evidence: memory-hints-v1 source fingerprints, policy caps, canonical query fingerprint, and fail-open semantics.
Forbidden: SQLite, baseline, runtime writes, Git operations, external Gateway, network access, raw logs, secrets, Context Agent integration.
Recommended tests: ready/empty/timeout/degraded/invalid/stale/mismatch results, max items/bytes/timeout propagation, no retry loop, no file or network writes.
Review batching note: each Review batch may contain only a subset of approved files; assess supplied patches and do not report other approved files as missing evidence.
