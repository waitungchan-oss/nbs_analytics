# Documentation Agent Contract

版本：v1，附加 v2 target-bound workflow
模式：read-only documentation proposal；寫入只能由 trusted Controller 在獨立明確批准後執行

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

The Controller re-validates the proposal, checks the evidence and proposal fingerprints against the current files, resolves the target policy, and records a `documentation-application-v1` result. High-risk system map 與 ADR targets require explicit target approval. A preview or proposal is not an application authorization.

## v2 Fixed Handoff / Runbook Targets

v2 是新增的 target-bound contract，不取代或重寫上述 v1 schema、artifact 或歷史 run。v2 只允許下表五個 catalog ID；path、heading、operation、risk 與 required approval 都由本地固定 catalog 派生，CLI 不接受任意檔案路徑，也沒有 `--target-path` 選項。

| Target ID | 固定 repo path | 固定 section |
| --- | --- | --- |
| `handoff.current-conclusion` | `NBS_ANALYTICS_HANDOFF.md` | `## 1. 本輪交接結論` |
| `handoff.verification-snapshot` | `NBS_ANALYTICS_HANDOFF.md` | `### 5.3 最近驗證快照` |
| `runbook.pipeline-rollout-gate` | `docs/agents/ACCEPTANCE_PIPELINE_HARDENING_RUNBOOK.md` | `## Rollout handoff gate（Task 6）` |
| `runbook.shard-boundary` | `docs/agents/ACCEPTANCE_SHARD_ROLLOUT_RUNBOOK.md` | `## Boundary` |
| `runbook.parallel-rollout-boundary` | `docs/agents/ACCEPTANCE_PARALLEL_ROLLOUT_RUNBOOK.md` | `## Boundary` |

### Two-step CLI and approval

第一次呼叫只產生 target-bound evidence、proposal、preview，不寫入目標文件：

```bash
.venv/bin/python scripts/documentation_agent.py \
  --run-id <completed-run-id> \
  --target-id handoff.current-conclusion \
  --agent-command "codex"
```

檢查 preview 後，只有明確批准同一 target 才可 apply：

```bash
.venv/bin/python scripts/documentation_agent.py \
  --run-id <same-run-id> \
  --target-id handoff.current-conclusion \
  --approve-target-id handoff.current-conclusion
```

Apply 呼叫會重新收集 target evidence，核對 saved preview、proposal、commit、run ID、source fingerprint 與 section hash；不一致就 `blocked`，不會重新呼叫 runner 或改寫文件。`--approve-target-id` 必須與 `--target-id` 完全相同；批准一個 target 不會批准其他 target。Preview 本身不是批准。

### Artifact lineage and fail-closed outcomes

v2 artifacts 固定寫在 `.nbs_agent_runtime/runs/<run-id>/documentation/targets/<target-id>/`，檔名只允許 `documentation-evidence-v2.json`、`documentation-proposal-v2.json`、`documentation-preview-v2.json`、`documentation-application-v2.json`。Evidence 綁定 `runId`、`commitSha`、`sourceFingerprint`、source hashes、target ID、gate evidence 與原 section hash；proposal/preview/application 依序綁定前一層 fingerprint。Hermes 只唯讀檢查固定目錄與 artifact allowlist、schema/status/fingerprint、lineage、5 MiB cap 及 symlink/path permissions；回報 invalid/stale/over-cap，不 dispatch runner、不 preview/apply、不批准 target，也不寫 release-gate state。`documentation-hermes-report-v1` 不屬於正式 release gate。

Missing approved runner、unknown target、approval mismatch、source/section drift、missing or invalid saved preview、malformed schema/fingerprint、symlink/path violation 或 artifact over cap 都必須 blocked/fail closed。不得把 `blocked` 或 Hermes 的文件檢查結果解讀為 apply 成功。

Catalog 只提供可治理的 target identity；此契約更新不代表 handoff 或 runbook 內容已回填、已 Review 或已驗收。實際回填須逐 target 通過既有 Review、Full verification、Hermes 與 target approval 流程。
