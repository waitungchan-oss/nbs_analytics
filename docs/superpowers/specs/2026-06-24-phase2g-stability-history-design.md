# Phase 2G Stability History Design

## Goal

Persist every completed upload acceptance result so operators can identify when core scope drift or freshness movement first appeared.

## Architecture

- Store audit records in a dedicated `stability_gate_history` SQLite table.
- Keep the existing revenue tables unchanged.
- Save the complete Gate payload as JSON plus searchable summary columns.
- Expose a read-only `GET /api/stability/history?limit=20` endpoint.
- Show recent acceptance records in the Vue cockpit without moving upload, export, or GMV features.

## Stored Record

Each record contains:

- creation timestamp and upload result
- upload message and source filenames
- core gate status, baseline month, expected and actual revenue
- core matched/drift counts
- freshness status and update count
- latest observed data date
- batch and upsert summaries
- complete Gate payload for future audit expansion

## Failure Handling

History persistence is secondary to the completed data upsert. A history-write failure is recorded in the Streamlit feedback and does not undo or hide a successful upload.

## Verification

- SQLite repository tests use a temporary database.
- API contract tests fix the response field set and limit behavior.
- Streamlit source contract verifies persistence occurs after Gate construction.
- Vue contract and browser verification confirm the history panel is readable.
