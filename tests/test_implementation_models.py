from pathlib import Path

import pytest

from backend.agents.agent_runtime import resolve_implementation_runtime_path
from backend.agents.evidence_models import (
    ALLOWED_IMPLEMENTATION_STATUSES,
    AgentReportEnvelope,
    load_json_config,
)
from backend.agents.implementation_models import (
    ImplementationRunReport,
    ImplementationTaskContract,
    ValidationResult,
    load_implementation_policy,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def valid_contract_payload() -> dict:
    return {
        "schemaVersion": "implementation-task-v1",
        "taskId": "task-1",
        "planPath": ".superpowers/sdd/task-1-brief.md",
        "planFingerprint": "a" * 64,
        "objective": "Define implementation agent contracts",
        "approvedBaseSha": "b" * 40,
        "approvedWorktree": str(PROJECT_ROOT),
        "allowedWritePaths": ["backend/agents/implementation_models.py", "tests/test_implementation_models.py"],
        "validationCommands": ["pytest_targeted", "py_compile"],
        "riskSurfaces": [],
        "maxChangedFiles": 8,
        "maxDiffLines": 800,
        "maxRepairLoops": 2,
    }


def test_contract_rejects_unversioned_or_multi_task_payload():
    payload = valid_contract_payload()
    payload["schemaVersion"] = ""
    with pytest.raises(ValueError, match="schemaVersion"):
        ImplementationTaskContract.from_dict(payload)

    payload = valid_contract_payload()
    payload["taskId"] = "task-1,task-2"
    with pytest.raises(ValueError, match="taskId"):
        ImplementationTaskContract.from_dict(payload)


def test_contract_fingerprint_is_order_independent():
    left = ImplementationTaskContract.from_dict(valid_contract_payload())
    right = ImplementationTaskContract.from_dict(dict(reversed(list(valid_contract_payload().items()))))
    assert left.fingerprint == right.fingerprint


def test_contract_round_trips_and_nested_models_serialize():
    contract = ImplementationTaskContract.from_dict(valid_contract_payload())
    assert ImplementationTaskContract.from_dict(contract.to_dict()) == contract
    validation = ValidationResult(
        command_id="pytest_targeted",
        argv=(".venv/bin/python", "-m", "pytest"),
        exit_code=0,
        stdout="1 passed",
        stderr="",
        duration_ms=12,
    )
    report = ImplementationRunReport(
        schema_version="implementation-run-report-v1",
        status="completed",
        task_id=contract.task_id,
        contract_fingerprint=contract.fingerprint,
        start_head="a" * 40,
        end_head="b" * 40,
        changed_files=("backend/agents/implementation_models.py",),
        diff_stat={"files": 1, "insertions": 1, "deletions": 0},
        red_evidence=(validation,),
        green_evidence=(validation,),
        findings=(),
    )
    payload = report.to_dict()
    assert payload["redEvidence"][0]["commandId"] == "pytest_targeted"
    assert payload["status"] == "completed"


def test_policy_denies_formal_state_and_caps_work():
    policy = load_implementation_policy(PROJECT_ROOT)
    assert "*.db" in policy["deniedWritePatterns"]
    assert policy["limits"] == {
        "maxChangedFiles": 8,
        "maxDiffLines": 800,
        "maxRepairLoops": 2,
    }


def test_configs_and_budget_are_exactly_scoped():
    commands = load_json_config(PROJECT_ROOT, "agent_config/implementation_commands.json")
    assert commands["commands"]["pytest_targeted"]["prefix"] == [".venv/bin/python", "-m", "pytest"]
    assert all("command" not in value for value in commands["commands"].values())
    budget = load_json_config(PROJECT_ROOT, "agent_config/token_budgets.json")["implementation"]
    assert budget == {"inputTokens": 12000, "outputTokens": 2000, "maxRepairLoops": 2}


def test_implementation_statuses_do_not_change_existing_envelope_sets():
    assert "completed" in ALLOWED_IMPLEMENTATION_STATUSES
    assert AgentReportEnvelope(schema_version="implementation-run-report-v1", status="completed", payload={}).to_dict()["status"] == "completed"


def test_runtime_helper_is_confined_to_implementation_subdirectory(tmp_path):
    allowed = resolve_implementation_runtime_path(tmp_path, "reports/run.json")
    assert allowed == (tmp_path / ".nbs_agent_runtime/implementation/reports/run.json").resolve()
    with pytest.raises(PermissionError, match="implementation"):
        resolve_implementation_runtime_path(tmp_path, "../reports/run.json")
