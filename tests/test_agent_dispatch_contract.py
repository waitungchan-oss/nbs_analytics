import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DISPATCH_PATH = ROOT / "docs/agents/CODEX_AGENT_DISPATCH.md"
AGENTS_PATH = ROOT / "AGENTS.md"


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


def test_dispatch_contract_requires_approved_plan_and_single_task():
    text = DISPATCH_PATH.read_text(encoding="utf-8")
    assert '"requiresApprovedPlan": true' in text
    assert '"maxTasksPerRun": 1' in text


def test_repo_instructions_forbid_agent_git_and_formal_state_writes():
    text = AGENTS_PATH.read_text(encoding="utf-8")
    for phrase in ["不得 commit 或 merge", "不得修改正式 SQLite", "必須交給 Review Agent"]:
        assert phrase in text
