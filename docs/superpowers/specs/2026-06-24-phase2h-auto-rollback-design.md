# Phase 2H Auto Rollback Design

## Goal

Automatically reject an upload that causes core revenue-scope drift, preserve the rejected database for investigation, restore the pre-upload database, rebuild caches, and verify the restored official state.

## Decision

Phase 2H uses the approved enhanced automatic rollback approach:

- freshness changes never trigger rollback;
- only `coreValidation.status == "drift"` triggers rollback;
- the drifted database is copied to a quarantine file before restoration;
- the backup is integrity-checked before replacing the live database;
- the restored database is integrity-checked and the dashboard cache is rebuilt;
- a second Stability Gate must return `2/2 matched`;
- the rejected batch is written to Phase 2G history only after restoration, so the audit survives the database replacement.

## Components

### Database restore

`database.py` owns backup validation, quarantine copy creation, atomic replacement, and SQLite sidecar cleanup. It returns explicit backup and quarantine paths.

### Rollback orchestration

`backend/services/upload_rollback_service.py` owns the state machine:

- `accepted`: no core drift;
- `rejected_rolled_back`: restoration and second verification succeeded;
- `rollback_failed`: restoration, cache rebuild, or second verification failed.

### Streamlit coordination

The upload flow serializes write operations with a process lock. After the initial Gate:

- matched uploads are accepted and recorded normally;
- drifted uploads run rollback, rebuild the cache, then record the rejected batch and both Gate payloads;
- rollback failures display a high-risk error and never report successful acceptance.

## Audit Data

Phase 2G history adds:

- rollback status;
- backup path;
- quarantine path;
- post-rollback Gate;
- rollback error.

Vue remains read-only and shows `accepted`, `rejected_rolled_back`, or `rollback_failed`.

## Verification

- temporary SQLite tests prove quarantine and exact restoration;
- orchestration tests cover accepted, successful rollback, and failed second verification;
- Streamlit source contracts confirm lock, rollback, cache reset, and post-restore history ordering;
- the existing official `HKD 12,057,968` baseline remains unchanged.
