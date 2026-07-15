import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DISPATCH_PATH = ROOT / "docs/agents/CODEX_AGENT_DISPATCH.md"
AGENTS_PATH = ROOT / "AGENTS.md"
ARCHITECTURE_PATH = ROOT / "docs/agents/NBS_AGENT_ARCHITECTURE.md"
IMPLEMENTATION_CONTRACT_PATH = ROOT / "docs/agents/IMPLEMENTATION_AGENT_CONTRACT.md"


def load_dispatch_rules():
    text = DISPATCH_PATH.read_text(encoding="utf-8")
    match = re.search(r"```json\n(.*?)\n```", text, re.S)
    assert match
    return json.loads(match.group(1))


def test_dispatch_document_contains_machine_readable_rules():
    rules = load_dispatch_rules()
    assert rules["context"]["anyOf"]["changedCodeFilesGte"] == 2
    assert "upload" in rules["context"]["riskSurfaces"]
    assert rules["review"]["before"] == ["commit", "merge", "hermes"]


def test_implementation_dispatch_rules_exactly_match_brief():
    rules = load_dispatch_rules()
    expected = {
        "requiresApprovedPlan": True,
        "requiresExplicitAuthorization": True,
        "requiresIsolatedWorktree": True,
        "requiredBranchPrefix": "codex/",
        "maxTasksPerRun": 1,
        "allowedTaskTypes": ["behavior", "refactor", "test", "documentation", "configuration"],
        "deniedRiskSurfaces": [
            "upload",
            "sqlite",
            "baseline",
            "rollback",
            "revenue",
            "business_rules",
            "export_schema",
        ],
        "after": ["review_agent", "full_verification", "hermes"],
        "never": ["commit", "merge", "push", "service_management", "dependency_install"],
    }
    assert rules["implementation"] == expected


def test_root_agents_links_all_governance_contracts():
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for path in [
        "docs/agents/NBS_AGENT_ARCHITECTURE.md",
        "docs/agents/CONTEXT_AGENT_CONTRACT.md",
        "docs/agents/REVIEW_AGENT_CONTRACT.md",
        "docs/agents/IMPLEMENTATION_AGENT_CONTRACT.md",
        "docs/agents/CODEX_AGENT_DISPATCH.md",
        "NBS_HERMES_MONITORING.md",
    ]:
        assert path in text


def test_codex_owns_post_implementation_governance():
    text = DISPATCH_PATH.read_text(encoding="utf-8")
    for phrase in [
        "Codex 建立並批准 implementation Task contract",
        "Codex 檢查 final implementation report 與實際 diff",
        "處理 findings",
        "完成完整驗證",
        "最後呼叫 Hermes",
    ]:
        assert phrase in text


def test_review_findings_return_to_codex_before_single_task_redispatch():
    text = ARCHITECTURE_PATH.read_text(encoding="utf-8")
    assert 'RA -->|"Changes Required"| CP' in text
    assert 'CD -->|"Authorize one Task"| IA' in text
    assert 'RA -->|"Changes Required"| IA' not in text


def test_contract_distinguishes_product_agent_from_sdd_worker():
    text = IMPLEMENTATION_CONTRACT_PATH.read_text(encoding="utf-8")
    assert "不包含 Codex Superpowers SDD worker" in text
    assert "Task commit 由 Codex 編排流程持有" in text


def test_product_agent_reports_head_boundaries_instead_of_commit_sha():
    text = IMPLEMENTATION_CONTRACT_PATH.read_text(encoding="utf-8")
    assert "startHead、endHead" in text
    assert "commit SHA" not in text


def test_product_agent_git_prohibition_has_no_authorization_exception():
    text = IMPLEMENTATION_CONTRACT_PATH.read_text(encoding="utf-8")
    assert "產品 Implementation Agent 不得 commit、merge、push。" in text
    assert "除非 Codex" not in text
    assert "Codex orchestration 只可在獨立授權後進行 Task commit。" in text


def test_implementation_agent_cannot_choose_next_task_or_use_prohibited_operations():
    text = IMPLEMENTATION_CONTRACT_PATH.read_text(encoding="utf-8")
    for phrase in [
        "不得自行決定下一 Task",
        "產品 Implementation Agent 不得 commit、merge、push",
        "不得管理服務或安裝 dependency",
        "不得修改正式 SQLite、baseline、rollback、revenue、business rules 或 export schema",
        "不得自行進行 full verification 或 Hermes",
    ]:
        assert phrase in text


def test_repo_instructions_forbid_agent_git_and_formal_state_writes():
    text = AGENTS_PATH.read_text(encoding="utf-8")
    for phrase in ["不得 commit 或 merge", "不得修改正式 SQLite", "必須交給 Review Agent"]:
        assert phrase in text
