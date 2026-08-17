# Verification Runtime Profile Task 5 Brief

## Objective

Make Short-term Offload persistence use the caller's deterministic clock when
one is supplied, while retaining real UTC time for production callers that do
not supply `now`.

## Allowed files

- `backend/agents/short_term_offload_store.py`
- `backend/agents/short_term_offload_service.py`
- `tests/test_short_term_offload_store.py`
- `tests/test_short_term_offload_service.py`
- this brief

## Required behavior

- `ShortTermOffloadStore.write(..., now=...)` evaluates expiry against `now`.
- Service persistence propagates its existing `now` argument to the store.
- Expired artifacts remain rejected unless `allow_expired=True`.
- No change to policy, TTL bounds, redaction, artifact schema, or runtime root.

## Verification

Focused store/service tests, py_compile, diff check, strict Review, full
verification, and Hermes remain separate gates.
