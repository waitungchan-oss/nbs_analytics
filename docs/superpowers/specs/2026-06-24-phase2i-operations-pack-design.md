# Phase 2I Operations Pack Design

## Goal

Provide one low-friction command that starts, verifies, monitors, and stops Streamlit, FastAPI, and Vue while exposing actionable system-health details.

## Architecture

### Cross-platform system manager

`scripts/system_manager.py` owns:

- dependency preflight for Python, Node.js, npm, required project files, and ports;
- service definitions for Streamlit `8502`, FastAPI `8601`, and Vue `5173`;
- detached process startup with per-service logs;
- bounded log rotation;
- PID state in `.nbs_runtime/services.json`;
- HTTP readiness polling before opening the browser;
- `start`, `status`, and `stop` commands.

The manager must not kill an unknown process occupying a required port. It reports the conflict and stops safely.

### Health monitoring

`backend/services/system_health_service.py` owns the operational health payload:

- SQLite existence, size, and integrity;
- latest acceptance-history record and rollback result;
- backup and quarantine counts and total bytes;
- runtime-cache file count and total bytes.

The API reports `ok`, `degraded`, or `critical` with explicit issues.
If AI cache is deferred, the health payload and UI should treat that as an
intentional performance trade-off, not as a startup failure; the full rebuild
still happens from the Streamlit `補算 AI` action.

### User entrypoints

- macOS and Windows launchers call the same Python manager.
- Dedicated stop launchers call `system_manager.py stop`.
- The Vue API Status section displays database integrity, latest Gate/rollback, and storage footprint.

## Error Handling

- Missing Node.js/npm or dependencies produces a preflight failure before spawning services.
- A process that exits before readiness is reported with its log path.
- Unknown occupied ports are never terminated automatically.
- Stale PID files are cleaned during status/start checks.

## Verification

- Unit tests cover service definitions, port-conflict reporting, bounded logs, health aggregation, and launcher contracts.
- Existing backend and Vue contracts remain green.
- Where the execution environment allows port binding, the manager must return all three endpoints ready.
