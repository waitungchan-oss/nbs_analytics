from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_governance_docs() -> str:
    paths = [
        ROOT / "AGENTS.md",
        ROOT / "docs/agents/NBS_AGENT_ARCHITECTURE.md",
        ROOT / "docs/agents/CODEX_AGENT_DISPATCH.md",
        ROOT / "docs/agents/DOCUMENTATION_AGENT_CONTRACT.md",
        ROOT / "docs/agents/VERIFIED_DOCUMENTATION_BACKFILL.md",
        ROOT / "NBS_HERMES_MONITORING.md",
        ROOT / "NBS_ANALYTICS_SYSTEM_MAP.md",
    ]
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def test_governance_requires_independent_documentation_agent():
    combined = read_governance_docs()

    assert "documentation-evidence-v1" in combined
    assert "documentation-proposal-v1" in combined
    assert "不得由主 Codex LLM 靜默代寫" in combined
    assert "system map 與 ADR" in combined
    assert "明確" in combined and "approval" in combined
    assert "Hermes" in combined and "read-only" in combined


def test_governance_keeps_documentation_fail_closed_and_non_mutating():
    combined = read_governance_docs()

    assert "blocked_missing_runner" in combined
    assert "不得 auto-apply" in combined or "不得自動套用" in combined
    assert "不得批准" in combined
    assert "不得修改 SQLite、baseline、runtime、Git" in combined


def test_v2_documentation_uses_fixed_targets_and_explicit_target_approval():
    combined = read_governance_docs()

    target_ids = (
        "handoff.current-conclusion",
        "handoff.verification-snapshot",
        "runbook.pipeline-rollout-gate",
        "runbook.shard-boundary",
        "runbook.parallel-rollout-boundary",
    )
    assert all(f"`{target_id}`" in combined for target_id in target_ids)
    assert "--target-id" in combined
    assert "--approve-target-id" in combined
    assert "不接受任意檔案路徑" in combined
    assert "也沒有 `--target-path` 選項" in combined
def test_verified_backfill_docs_lock_exact_sequence_and_boundaries():
    combined = read_governance_docs()
    procedure = (ROOT / "docs/agents/VERIFIED_DOCUMENTATION_BACKFILL.md").read_text(encoding="utf-8")

    sequence = [
        "1. **Backfill create**",
        "2. **Proposal**",
        "3. **Preview inspection**",
        "4. **Review**",
        "5. **Controlled apply**",
        "6. **Hermes**",
    ]
    legacy_procedure = procedure.split("## Legacy v1 Exact Operator Sequence", 1)[1]
    positions = [legacy_procedure.index(item) for item in sequence]
    assert positions == sorted(positions)
    assert "--apply-brief" in procedure
    assert "--approve-target system_map" in procedure
    assert "byte-identical" in combined
    assert "temporary vault" in combined
    assert "vault absolute path" in combined
    assert "Review Agent" in combined and "Hermes 是最後的 read-only acceptance gate" in combined
    assert "不得執行 preview/apply" in combined
