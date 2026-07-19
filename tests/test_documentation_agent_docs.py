from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_governance_docs() -> str:
    paths = [
        ROOT / "AGENTS.md",
        ROOT / "docs/agents/NBS_AGENT_ARCHITECTURE.md",
        ROOT / "docs/agents/CODEX_AGENT_DISPATCH.md",
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
