# NBS Short-term Offload Design Spec

## Status

- Status: proposed for review
- Date: 2026-08-14
- Scope: bounded short-term offload for long tool outputs during Inline Execution
- Related contracts: `docs/agents/MEMORY_SIDECAR_CONTRACT.md`, `docs/agents/NBS_AGENT_ARCHITECTURE.md`

## 1. Goal

在不增加主 Context token 壓力的前提下，將長工具輸出暫存為受控、短期、read-only artifact；Context 只保留精簡摘要、`ref_id`、內容 fingerprint 與 Mermaid task canvas node reference。需要細節時，runner 依 `ref_id` 做 bounded drill-down。

第一版不改變 canonical evidence、Review、Verification、Hermes、Governance Graph、approval 或 workflow control 的 authority。

## 2. Non-goals

- 不 offload prompt、secrets、API keys、`.env`、SQLite rows、Excel/CSV rows、完整 customer data 或內部推理。
- 不把 offload artifact 當成 canonical evidence、approval、Review PASS、Hermes PASS 或 Graph node/edge。
- 不修改正式 SQLite、baseline、revenue scope、business rules、export schema 或 Git。
- 不建立 Memory Hub、Wiki、CodeGraph、remote embedding、長駐 Gateway 或新的外部資料庫。
- 不讓 offload 取代既有 context bundle；artifact 不存在時必須回到原本 inline output／canonical fallback。
- 不在第一版自動開啟 default-on recall；offload 先採 explicit opt-in 或受控 runner policy。

## 3. Design principles

1. Canonical artifacts remain the source of truth.
2. Offload storage is disposable, bounded and read-only to consumers.
3. Every reference is content-addressed and bound to the current run/session.
4. Missing, stale, tampered, symlinked or over-cap artifacts fail closed.
5. The summary must remain useful without requiring a drill-down.
6. Cleanup is TTL-based and cannot delete canonical runtime evidence.

## 4. Architecture

```text
Long tool output
    │
    ├─> Sensitive-content scanner + size limiter
    │       │
    │       ├─ blocked -> redacted diagnostic + original workflow continues
    │       └─ accepted
    │
    ├─> Short-term Offload Store (isolated runtime directory)
    │       └─ offload artifact + SHA-256 + expiresAt
    │
    └─> Inline Context Projection
            ├─ compact summary
            ├─ ref_id
            ├─ artifact fingerprint
            └─ Mermaid task-canvas node
```

The store is separate from `.nbs_agent_runtime/runs`, canonical evidence, SQLite and the Obsidian vault. A run may reference an offload artifact, but the reference is an optimization hint and not evidence of task completion.

## 5. Artifact contract

Each artifact uses a strict `short-term-offload-v1` envelope:

```json
{
  "schemaVersion": "short-term-offload-v1",
  "refId": "offload_<run-safe-id>_<counter>",
  "runId": "bounded-run-id",
  "sessionId": "bounded-session-id",
  "sourceKind": "tool_output",
  "summary": "bounded non-sensitive summary",
  "content": "bounded non-sensitive output",
  "contentSha256": "lowercase-64-hex",
  "createdAt": "ISO-8601",
  "expiresAt": "ISO-8601",
  "sourceFingerprint": "lowercase-64-hex",
  "redactionStatus": "clean|redacted|blocked",
  "status": "ready|expired|blocked|missing"
}
```

Rules:

- Exact keys only; unknown keys are rejected.
- `refId`, `runId` and `sessionId` use bounded safe identifiers; no absolute paths or secrets.
- `contentSha256` is computed from the stored UTF-8 content.
- `expiresAt` must be after `createdAt` and within the configured short TTL; first version default is 30 minutes, maximum 24 hours.
- `content` is capped at 32,000 UTF-8 bytes per artifact; summary is capped at 2,048 bytes.
- A run may create at most 20 artifacts and 200,000 total stored bytes.
- Artifacts must be regular files below the isolated offload root; symlinks and path traversal are rejected.

## 6. Projection and drill-down

The inline projection stores only:

```json
{
  "refId": "offload_run123_004",
  "summary": "pytest completed; 31 tests passed",
  "contentSha256": "...",
  "expiresAt": "...",
  "nodeId": "tool-output-004"
}
```

Drill-down must validate `runId`, `sessionId`, `refId`, `contentSha256`, TTL and isolated-root containment before returning content. It returns a bounded slice, not an unbounded file read. A failed drill-down returns `missing`, `expired`, `blocked` or `fingerprint_mismatch` and never silently substitutes unrelated output.

Mermaid task canvas is a projection only. It may show `produces`, `references` and `blocked_by` relationships already present in the run evidence, but it must not invent dependencies or become a new approval/dispatch input.

## 7. Sensitive-content and redaction policy

Before persistence, the writer rejects or redacts:

- API keys, bearer tokens, cookies, private keys and credential-like environment values.
- `.env` contents, auth configuration, database connection strings and absolute home paths.
- SQLite/Excel/CSV raw rows and large serialized payloads.
- Prompt text or internal reasoning markers.

The scanner is fail-closed for uncertain credential-like matches. A `blocked` artifact retains only a bounded diagnostic reason and never stores the original content. Redaction is explicit in `redactionStatus`; it is not presented as lossless evidence.

## 8. Lifecycle and cleanup

- Creation requires explicit runner policy `short_term_offload=on` or an approved task descriptor; ordinary workflow defaults remain unchanged until separately enabled.
- Read access is limited to the same run/session and a read-only drill-down service.
- TTL cleanup runs only against the isolated offload root and only for expired offload artifacts.
- Cleanup must never traverse symlinks or delete canonical runtime evidence, workflow artifacts, SQLite, backups, quarantine or cache directories.
- Cleanup failures produce bounded telemetry and do not block the main workflow.
- No automatic cleanup command may be routed through Streamlit, Governance Graph or Hermes.

## 9. Failure behavior

| Condition | Behavior |
|---|---|
| Sensitive or uncertain content | `blocked`, diagnostic only, workflow continues |
| Size/counter cap exceeded | Do not persist; keep compact inline summary |
| Path/symlink violation | `blocked`, no file read/write |
| Missing/expired artifact | bounded drill-down status, no substitute content |
| Fingerprint mismatch | `fingerprint_mismatch`, fail closed |
| Store unavailable/permission denied | canonical inline output continues |
| Cleanup failure | telemetry warning, no canonical impact |

No failure may change approval, Review, Verification, Hermes or baseline status.

## 10. Acceptance gates

1. Exact envelope, safe identifiers, byte caps and TTL validation tests pass.
2. Sensitive-content tests prove secrets, raw rows, prompts and absolute paths are never persisted.
3. Symlink and traversal tests prove the store cannot escape its isolated root.
4. Drill-down rejects missing, expired, tampered and cross-run references.
5. Counter/byte caps prevent unbounded accumulation.
6. Mermaid projection remains read-only and does not create inferred edges.
7. Cleanup removes only expired offload artifacts and preserves canonical/runtime evidence.
8. Existing Memory Sidecar, Context Agent, runner, Governance Graph and Hermes tests remain green.
9. Full pytest, system acceptance and Hermes post-change check pass.
10. No project-wide token reduction claim is made until repeated real run evidence exists.

## 11. Rollback

Set `short_term_offload=off` or remove the explicit runner policy. Existing inline output and canonical evidence paths remain unchanged. Expired offload artifacts may be discarded; no rollback of canonical data is required.

## 12. Implementation boundary

This document is a design only. A separate implementation plan must define the exact allowlisted files, TDD tasks, runner hook, offload store, drill-down service, cleanup command and review checkpoints. No implementation is authorized by this document alone.
