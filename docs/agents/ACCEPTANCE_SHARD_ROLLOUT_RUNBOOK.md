# Acceptance Shard Rollout Runbook

## Boundary

This is an advisory, diagnostic rollout. The serial `full-pytest`, Hermes, UI
acceptance and formal release aggregate jobs remain authoritative.

```text
ACCEPTANCE_SHARDS_ENABLED=false
formalReleaseEnabled=false
rolloutCandidate=ineligible
```

No shard artifact may be consumed as `full-pytest-gate-v1` or as a formal
`release-gate-result-v1`. Memory Hub, Memory Sidecar and Governance Graph may
provide bounded read-only context only; none can approve, dispatch or promote
a blocked result.

## Source-bound operator sequence

Run all commands from the repository root and use one fresh source seal for the
whole sequence. Replace placeholders only with values produced by the preceding
command; do not use historical PASS artifacts.

```bash
python scripts/verification_chain.py seal \
  --project-root . \
  --brief docs/agents/ACCEPTANCE_PIPELINE_HARDENING_RUNBOOK.md \
  --base HEAD --head WORKTREE \
  --runtime-root .nbs_agent_runtime/verification_sessions

python scripts/pytest_manifest.py \
  --project-root . \
  --commit-sha "$(git rev-parse HEAD)" \
  --source-fingerprint <source-seal-sha256> \
  --output .nbs_agent_runtime/rollout/pytest-manifest.json

ACCEPTANCE_SHARDS_ENABLED=false \
  python scripts/acceptance_shard_rollout_preflight.py \
  --project-root . \
  --manifest .nbs_agent_runtime/rollout/pytest-manifest.json \
  --commit-sha "$(git rev-parse HEAD)" \
  --source-fingerprint <source-seal-sha256> \
  --output .nbs_agent_runtime/rollout/rollout-preflight.json

python scripts/full_pytest_gate.py \
  --sandbox-preflight required \
  --commit-sha "$(git rev-parse HEAD)" \
  --source-fingerprint <source-seal-sha256> \
  --output .nbs_agent_runtime/rollout/serial-full-pytest.json

NBS_ACCEPTANCE_COMMIT_SHA="$(git rev-parse HEAD)" \
NBS_ACCEPTANCE_SOURCE_FINGERPRINT=<source-seal-sha256> \
ACCEPTANCE_SHARDS_ENABLED=true \
  python scripts/acceptance_shard_canary.py \
  --project-root . \
  --output .nbs_agent_runtime/rollout/canary-summary.json

# `full_pytest_shard_aggregate.py` exposes this bounded validator API; keep the
# manifest and shard paths below source-matched and complete.
EXPECTED_COMMIT_SHA="$(git rev-parse HEAD)" \
EXPECTED_SOURCE_FINGERPRINT=<source-seal-sha256> \
python -c 'import json, os; from pathlib import Path; from scripts.full_pytest_shard_aggregate import aggregate_pytest_shards; root=Path(".nbs_agent_runtime/rollout"); manifest=json.loads((root / "pytest-manifest.json").read_text(encoding="utf-8")); shards=[json.loads(path.read_text(encoding="utf-8")) for path in sorted(root.glob("shard-*.json"))]; result=aggregate_pytest_shards(manifest, shards, expected_commit_sha=os.environ["EXPECTED_COMMIT_SHA"], expected_source_fingerprint=os.environ["EXPECTED_SOURCE_FINGERPRINT"]); (root / "shard-aggregate.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8"); raise SystemExit(0 if result["status"] == "PASS" else 2)'

這個 aggregate invocation 只讀取 fresh、同 source 的
`.nbs_agent_runtime/rollout/pytest-manifest.json` 與完整
`.nbs_agent_runtime/rollout/shard-*.json`（canary summary 另存為
`.nbs_agent_runtime/rollout/canary-summary.json`），輸出
`.nbs_agent_runtime/rollout/shard-aggregate.json`。結果必須維持
`authority=prototype`、`formalReleaseEnabled=false`；它不能升格成正式
`full-pytest-gate-v1`。

python scripts/hermes_gate.py \
  --skip-system-acceptance \
  --commit-sha "$(git rev-parse HEAD)" \
  --source-fingerprint <source-seal-sha256> \
  --output .nbs_agent_runtime/hermes.json

python scripts/ui_acceptance_fixture_preflight.py \
  --db-path <temporary-fixture-db> \
  --cache-dir <temporary-fixture-cache> \
  --project-root . \
  --source-fingerprint <source-seal-sha256>

# The source-matched UI evidence must then run through the existing UI smoke
# boundary (for example, streamlit_ui_smoke.py) before any release decision.

python scripts/release_gate.py stage \
  --session <status-or-session-json> \
  --release <release-gate-result.json>
```

The shard aggregate command above is intentionally an operator boundary: use
the deterministic validator API or its bounded wrapper with the fresh manifest
and every shard artifact. It must reject duplicate, missing or unknown nodeids,
identity mismatch, failed child status and any `ui_fixture_cache_mismatch`.

## Handoff and retention

The handoff must record the canary artifact path, source fingerprint, commit
SHA, manifest fingerprint, runner fingerprint, serial parity result, the three
serial／shard timing ratios, UI fixture status, and the exact rollback command.
Keep only bounded JSON evidence under `.nbs_agent_runtime`; run the repository's
retention dry run first, then apply only the approved retention policy:

```bash
python scripts/agent_workflow.py prune --dry-run
python scripts/agent_workflow.py prune --apply
```

Never delete formal database, baseline or production artifacts as part of shard
retention. If any gate is missing, stale, blocked, mismatched or contaminated,
set `rolloutCandidate=ineligible` and do not create a formal release aggregate
from shard artifacts.

## Rollback

Rollback is immediate and does not require changing branch protection:

```bash
gh workflow run release-gates.yml -f enable_acceptance_shards=false
```

The serial path remains the release path while shard rollout is disabled. A
missing canary artifact, failed preflight, UI fixture cache mismatch or Hermes
failure must not be repaired by changing baseline data or by reusing a cached
PASS. Promotion beyond this diagnostic stage requires a separately approved
L3 spec.
