import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dispatch_document_contains_machine_readable_rules():
    text = (ROOT / "docs/agents/CODEX_AGENT_DISPATCH.md").read_text(encoding="utf-8")
    match = re.search(r"```json\n(.*?)\n```", text, re.S)
    assert match
    rules = json.loads(match.group(1))
    assert rules["context"]["anyOf"]["changedCodeFilesGte"] == 2
    assert "upload" in rules["context"]["riskSurfaces"]
    assert rules["review"]["before"] == ["commit", "merge", "hermes"]


def test_root_agents_links_all_governance_contracts():
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for path in [
        "docs/agents/NBS_AGENT_ARCHITECTURE.md",
        "docs/agents/CONTEXT_AGENT_CONTRACT.md",
        "docs/agents/REVIEW_AGENT_CONTRACT.md",
        "docs/agents/CODEX_AGENT_DISPATCH.md",
        "NBS_HERMES_MONITORING.md",
    ]:
        assert path in text
