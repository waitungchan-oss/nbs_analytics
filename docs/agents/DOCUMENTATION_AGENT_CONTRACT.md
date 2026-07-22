# Documentation Agent Contract

版本：v1
模式：read-only documentation proposal

本契約對應已批准的 documentation-agent design spec：
`docs/superpowers/specs/2026-07-18-documentation-agent-contract-design.md`。

## Boundary

Documentation Agent 只讀取已提供的 context/evidence，先產生受限的
`documentation-draft-v1`，再由 trusted `DocumentationAgentService` 正規化為嚴格的
`documentation-proposal-v1`。外部 sidecar 的最終輸出仍是
`documentation-proposal-v1`；Runner 不提供 tools，不能寫入 repo、Obsidian vault、SQLite、
runtime、baseline、Git index 或 Git history；不得執行 upload、upsert、rollback、promotion
或服務控制。Agent 不得使用主 Codex LLM fallback。

Agent 只可使用 `brief_backfill`、`system_map`、`adr` 三種 target kind，以及 policy 中列明的 operation。實際檔案更新由 Controller 在 proposal 通過 target approval、scope 與 fingerprint checks 後執行；Agent 永遠不直接 apply。

## Token Contract

- Input 上限：8,000 estimated tokens。
- Output 上限：1,500 tokens。
- 超過 input 上限時只回傳 `context_overflow`，不可截斷後猜測。

## Required Input

```json
{
  "schemaVersion": "documentation-evidence-v1",
  "taskId": "task-1",
  "generatedAt": "2026-07-18T12:00:00+08:00",
  "sources": [{"path": "docs/briefs/example.md", "sha256": "lowercase-sha256"}],
  "guardrails": {
    "revenueScope": "不含掛賬核銷與TT退款轉團款",
    "mayBaseline": "HKD 12,057,968"
  },
  "evidenceFingerprint": "lowercase-sha256"
}
```

## Required Output

The read-only Codex runner output is an internal `documentation-draft-v1` object. Each draft
item contains only `targetKind` and Markdown `content`; it must not choose paths, operations,
hashes, vault identities, or proposal fingerprints. The trusted service verifies the draft
against classifier-required targets, derives safe identities and hashes, and emits the final
`documentation-proposal-v1`, which must validate with `DocumentationProposal.from_dict()`.
Each final proposal has a unique `targetIdentity`, an allowed `targetKind`, an allowed
`operation`, and a lowercase SHA-256 `contentSha256`.

```json
{
  "schemaVersion": "documentation-draft-v1",
  "evidenceFingerprint": "lowercase-sha256",
  "status": "ready",
  "proposals": [{"targetKind": "brief_backfill", "content": "Markdown fragment"}]
}
```

When documentation is required, the ready draft target kinds must exactly match the
classifier-required set. The service maps an evidence-approved Brief source to the validator's
`docs/briefs/<basename>.md` root and limits System Map normalization to the existing
`## 2A. Agent Evidence Pipeline` section. ADR normalization remains blocked until its
create-only identity policy is implemented.

```json
{
  "schemaVersion": "documentation-proposal-v1",
  "taskId": "task-1",
  "generatedAt": "2026-07-18T12:00:00+08:00",
  "evidence": {},
  "evidenceFingerprint": "lowercase-sha256",
  "status": "ready",
  "proposals": [],
  "proposalFingerprint": "lowercase-sha256"
}
```

Allowed proposal statuses are `ready`, `no_documentation_needed`, `blocked`, `context_overflow`, and `invalid_agent_output`. Controller application statuses are `preview_ready`, `awaiting_target_approval`, `applied`, `partially_applied`, and `blocked`.

## Protected Governance

The policy is tracked in `agent_config/documentation_policies.json`. The formal revenue scope remains `不含掛賬核銷與TT退款轉團款`; the protected baseline text is `HKD 12,057,968`. Documentation work must not rewrite, normalize, reinterpret, or hide either value.

## Controller Apply

The Controller re-validates the proposal, checks the evidence and proposal fingerprints against the current files, resolves the target policy, and records a `documentation-application-v1` result. High-risk `system_map` and `adr` targets require explicit target approval. A preview or proposal is not an application authorization.
