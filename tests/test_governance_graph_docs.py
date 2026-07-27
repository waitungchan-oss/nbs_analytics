from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_graph_contract_preserves_required_boundaries():
    text = (ROOT / "docs/agents/NBS_GOVERNANCE_GRAPH_CONTRACT.md").read_text(encoding="utf-8")
    for value in (
        "nbs-governance-graph-v1",
        "canonical artifacts",
        "not a control input",
        "R0",
        "R1",
        "R2",
        "blocked_missing_runner",
        "protected_incident",
        "不含掛賬核銷與TT退款轉團款",
        "HKD 12,057,968",
    ):
        assert value in text


def test_governance_documents_cross_reference_the_contract():
    contract_link = "NBS_GOVERNANCE_GRAPH_CONTRACT.md"
    for relative in (
        "docs/agents/NBS_AGENT_ARCHITECTURE.md",
        "docs/agents/CODEX_AGENT_DISPATCH.md",
        "NBS_HERMES_MONITORING.md",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert contract_link in text


def test_contract_keeps_graph_out_of_control_plane():
    text = (ROOT / "docs/agents/NBS_GOVERNANCE_GRAPH_CONTRACT.md").read_text(encoding="utf-8")
    for value in (
        "不會 approve",
        "不會 dispatch",
        "不會寫入 SQLite",
        "Agent Operations Phase B",
        "Hermes",
    ):
        assert value in text
