# Phase 2J Operational Resilience Design

## Goal

Extend Phase 2I from point-in-time startup checks into an auditable operations
layer that records health, controls backup growth, proves recovery on an
isolated copy, and produces a privacy-safe diagnostic package.

## Scope

Phase 2J includes:

- full-stack HTTP acceptance for Streamlit, FastAPI, and Vue;
- bounded operational-health history outside the business database;
- a 3 GB backup-capacity warning;
- deterministic backup retention;
- an isolated restore drill that never replaces the live SQLite database;
- a one-command diagnostic ZIP without business-detail rows.

Vue upload, export migration, GMV migration, alert delivery, and production
deployment remain outside this phase.
AI cache deferment after upload is allowed; operational health should surface it
as a cache state, while the explicit `補算 AI` action remains a Streamlit UI
responsibility.

## Architecture

### Operational snapshots

`backend/services/operational_monitor_service.py` builds and persists compact
JSON snapshots under `.nbs_runtime/health_history.jsonl`. Each snapshot records:

- timestamp and overall status;
- SQLite integrity and latest acceptance/rollback summary;
- latest business-data date when available;
- backup, quarantine, and runtime-cache footprint;
- endpoint readiness and response time when probes are supplied.

History is capped by record count so monitoring cannot grow without bound.
No transaction-level or customer-level data is stored.

### Backup retention

`backend/services/backup_retention_service.py` owns classification, planning,
and application of retention:

- keep all backups from the latest 7 calendar days;
- keep one newest backup per ISO week for the latest 4 weeks;
- keep one newest backup per calendar month for the latest 6 months;
- preserve explicitly protected backup paths referenced by acceptance history;
- never delete quarantine files;
- report a warning when retained backup bytes exceed 3 GB.

Retention supports dry-run and apply modes. Only files matching
`nbs_marketing_data.db.backup_YYYYMMDD_HHMMSS` are eligible for deletion.

### Restore drill

`backend/services/restore_drill_service.py` copies the selected backup and the
live database into a temporary workspace. It validates backup integrity,
restores into an isolated target, validates the restored target, and runs the
Phase 2 baseline checks against that target. It never calls the live-database
replacement function.

The drill report includes integrity, baseline checks, duration, selected backup,
and pass/fail status. Temporary files are removed after the report is written.

### Diagnostic package

`backend/services/diagnostics_service.py` creates a timestamped ZIP under
`.nbs_runtime/diagnostics/` containing:

- system status;
- current health;
- compact health history;
- latest acceptance summary;
- manager state;
- bounded service-log tails;
- environment and version metadata;
- retention and restore-drill reports when present.

The package excludes the SQLite database, uploaded source files, exports,
caches, and transaction-level records.

### Operations commands

`scripts/system_manager.py` adds:

- `monitor`: probe endpoints and append one compact health snapshot;
- `retention [--apply]`: preview or apply backup retention;
- `drill`: run isolated restore verification;
- `diagnose`: produce the diagnostic ZIP;
- `acceptance`: start services when needed and verify all three HTTP endpoints.

Existing `start`, `status`, and `stop` behavior remains compatible.

## Error Handling

- Monitoring records unavailable endpoints as issues without crashing history.
- A malformed health-history line is skipped during reads.
- Retention refuses unknown filenames and never touches quarantine files.
- Restore drill fails closed when no valid backup exists or any baseline check
  fails.
- Diagnostic generation records unavailable optional inputs in a manifest
  instead of failing the whole ZIP.

## Verification

- Unit tests cover history bounds, compact payloads, 3 GB warnings, retention
  tiers, protected backups, quarantine safety, isolated restore, diagnostic ZIP
  exclusions, and manager command contracts.
- Existing Phase 2 tests remain green.
- Vue production build remains green.
- A real local run verifies Streamlit `8502`, API `8601`, Vue `5173`, health,
  duplicate-start protection, and clean shutdown.
