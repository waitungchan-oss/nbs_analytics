# NBS Verification Runtime Profile Design Spec

**Status:** approved direction, implementation pending  
**Date:** 2026-08-17  
**Scope:** pre-merge verification for isolated Git worktrees

## 1. Problem

An isolated worktree currently derives `nbs_marketing_data.db`, `.nbs_runtime`,
and `.nbs_runtime_cache` from its own checkout root. A fresh worktree therefore
has an empty SQLite file or no runtime evidence, while `system_manager.py`
still probes fixed ports owned by the primary checkout. This can produce the
contradictory result “baseline HKD 0, no PID, but HTTP acceptance passed”.

Short-term Offload tests also construct artifacts at a historical fixed time,
while the store validates expiry against the real wall clock. Those tests are
not hermetic and fail as the calendar advances.

## 2. Goals

1. Make isolated verification explicit, immutable, reproducible, and bound to
   one Git HEAD.
2. Verify data-dependent tests against a governed read-only SQLite snapshot,
   never an automatically-created empty production-named database.
3. Prove that a service endpoint belongs to the expected worktree, process and
   profile before accepting HTTP readiness.
4. Make runtime cache/generation evidence explicit without copying the entire
   multi-gigabyte production cache.
5. Make time-sensitive Short-term Offload tests deterministic through an
   injected clock.
6. Preserve the canonical revenue scope, frozen 2026-05 baseline,
   Monthly Baseline Governance, formal SQLite, rollback, exports, and business
   rules byte-for-byte unless a separately approved task says otherwise.

## 3. Non-goals and hard boundaries

- No baseline promotion, registry value change, or Monthly Baseline mode change.
- No write to the formal `nbs_marketing_data.db`, its backup/quarantine files,
  or the primary `.nbs_runtime` / `.nbs_runtime_cache`.
- No cache copy of the full production cache; only bounded inventory and
  fingerprint evidence may be captured.
- No new approval, dispatch, runtime control, or data authority.
- No acceptance based only on a reachable fixed port.
- A missing or mismatched profile is `blocked`, never silently downgraded to
  an empty database or borrowed service.

## 4. Proposed architecture

```text
canonical primary runtime (read-only source)
        |
        v
Verification Profile Builder
  - immutable SQLite snapshot
  - baseline/registry identity
  - cache/generation inventory
  - Git HEAD and worktree fingerprint
        |
        v
isolated verification runtime
  - profile-bound DB path (SQLite immutable/read-only)
  - profile-bound runtime evidence root
  - dynamic service ports
  - service identity handshake
        |
        v
Hermes profile acceptance
  - code tests
  - data/baseline checks
  - service identity + HTTP checks
  - explicit blocked reasons
```

The profile is a derived verification artifact, not a canonical business
artifact. It is stored under an ignored `.nbs_agent_runtime/verification/`
directory and contains relative artifact references plus SHA-256 identities;
absolute paths and secrets are not serialized into reports.

### 4.1 Profile contract

`verification-runtime-profile-v1` has exact top-level fields:

```json
{
  "schemaVersion": "verification-runtime-profile-v1",
  "profileId": "...",
  "projectId": "nbs_analytics",
  "gitHead": "...",
  "worktreeFingerprint": "...",
  "database": {
    "snapshotRef": "verification/<profileId>/database.sqlite",
    "sourceFingerprint": "sha256",
    "snapshotFingerprint": "sha256",
    "readOnly": true
  },
  "baseline": {
    "registryFingerprint": "sha256",
    "requiredMay2026Total": "HKD 12,057,968"
  },
  "runtime": {
    "generationRef": "verification/<profileId>/generation.json",
    "cacheInventory": {"fileCount": 0, "totalBytes": 0, "fingerprint": "sha256"}
  },
  "services": {
    "profileNamespace": "...",
    "ports": {"api": 0, "streamlit": 0, "vue": 0}
  },
  "createdAt": "...",
  "profileFingerprint": "sha256"
}
```

The loader requires exact keys, matching `gitHead` and worktree fingerprint,
read-only database metadata, valid relative references, and a self-consistent
profile fingerprint. Any mismatch returns `blocked_runner_capability`.

### 4.2 Database snapshot

The builder uses SQLite backup/read-only inspection to create a disposable
snapshot under the verification root. The source database is opened read-only;
the snapshot is never assigned to `config.DB_FILE` globally. Data-dependent
services and checks receive `db_path` explicitly. A missing source or failed
integrity check blocks profile creation before a test can create an empty DB.

### 4.3 Runtime evidence

The profile records `data_generation.json` and cache inventory identities from
the approved source runtime. It does not copy cache contents. A cache-related
change requires matching generation/signature evidence; a code-only change may
use the bounded inventory and report cache as `not_required` rather than
fabricating a ready cache.

### 4.4 Service identity

`system_manager.py` must bind each service to the profile namespace and dynamic
port. Status is ready only when all of the following hold:

- process PID exists and is alive;
- process command and working directory match the expected worktree;
- endpoint is reachable on the profile port;
- endpoint identity matches `projectId`, `gitHead`, and profile namespace.

The acceptance command fails closed when a port is occupied by another
checkout, when the state file is absent, or when PID ownership is unknown.

### 4.5 Deterministic clock

Short-term Offload store write/expiry validation accepts an explicit `now`
value (or a profile-owned clock), and all service calls pass the same value
through. Production defaults continue to use the real UTC clock. Tests must
not depend on the host calendar.

## 5. Failure behavior

| Condition | Result | Must not do |
|---|---|---|
| profile missing/invalid | `blocked_runner_capability` | create empty DB or borrow service |
| source DB missing/invalid | `blocked_runner_capability` | repair or replace formal DB |
| DB snapshot fingerprint drift | `blocked_runner_capability` | continue with stale snapshot |
| service PID/identity mismatch | `blocked_runner_capability` | treat endpoint readiness as PASS |
| cache inventory unavailable for non-cache task | `not_required` with evidence | copy production cache |
| cache evidence required but stale | `blocked_runner_capability` | refresh production cache |
| clock/expiry mismatch | deterministic test failure | extend TTL silently |

## 6. Acceptance criteria

- Fresh isolated worktree cannot create or silently use an empty formal-named
  database during verification.
- A profile built from the current primary runtime reports the same 2026-05
  baseline and Monthly Baseline Governance result as the source, without any
  source mutation.
- Fixed-port services from another checkout are rejected, even when HTTP 200.
- A service launched for the profile is accepted only with matching identity.
- `system_manager.py acceptance` cannot report PASS when PID is missing.
- Short-term Offload focused tests pass on dates after the original fixture
  date and still reject expired artifacts.
- Hermes reports explicit profile identity and blocked reasons.
- Existing baseline/Monthly Baseline tests remain unchanged and pass against
  the governed snapshot.

## 7. Verification

Per task: RED focused test, GREEN focused test, `py_compile`, and
`git diff --check`. At the end: full pytest, `scripts/system_manager.py
acceptance`, `scripts/hermes_post_change_check.py`, and an explicit source
database signature comparison before/after. Hermes PASS is separate from
Review PASS.

