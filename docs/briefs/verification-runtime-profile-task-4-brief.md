# Verification Runtime Profile Task 4 Brief

## Objective

Bind service status and HTTP acceptance to the verification profile's dynamic
ports and process ownership. A reachable endpoint alone is not acceptance.

## Allowed files

- `scripts/system_manager.py`
- `backend/routers/health.py`
- `tests/test_system_manager.py`
- `tests/test_system_health_service.py`
- this brief

## Required behavior

- `build_service_specs` accepts profile ports and profile identity.
- `service_status` and `run_http_acceptance` expose bounded alive/ready/owner/
  identity fields and require all four for profile acceptance.
- Missing PID, unrelated process, wrong port, or identity mismatch is rejected.
- No service is started or stopped by this task.
- No SQLite, baseline, revenue rule, export schema, or runtime cache writes.

## Verification

Focused system-manager and health tests, py_compile, diff check, strict Review,
full verification, and Hermes remain separate gates.
