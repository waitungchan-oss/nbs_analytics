from pathlib import Path

import pytest

from backend.agents.agent_runtime import resolve_implementation_runtime_path
from backend.agents.evidence_models import (
    ALLOWED_IMPLEMENTATION_STATUSES,
    AgentReportEnvelope,
    load_json_config,
)
from backend.agents.implementation_models import (
    CarryForwardFile,
    ImplementationRunReport,
    ImplementationTaskContract,
    ValidationResult,
    load_implementation_policy,
    validate_carry_forward_approval,
)
from backend.agents.memory_hub_integration_models import build_memory_hub_integration_evidence


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


def test_contract_memory_context_is_opt_in_and_fingerprint_bound():
    payload = valid_contract_payload()
    contract = ImplementationTaskContract.from_dict(payload)
    assert contract.memory_context_allowed is False
    assert contract.expected_memory_evidence_fingerprint is None
    payload["memoryContextAllowed"] = True
    payload["expectedMemoryEvidenceFingerprint"] = "d" * 64
    opted_in = ImplementationTaskContract.from_dict(payload)
    assert opted_in.memory_context_allowed is True
    assert opted_in.expected_memory_evidence_fingerprint == "d" * 64


def test_contract_round_trips_hash_pinned_carry_forward_files_without_changing_write_scope():
    payload = valid_contract_payload()
    payload["carryForwardFiles"] = [
        {"path": "tests/test_legacy.py", "sha256": "c" * 64},
    ]

    contract = ImplementationTaskContract.from_dict(payload)

    assert contract.to_dict()["carryForwardFiles"] == payload["carryForwardFiles"]
    assert contract.allowed_write_paths == tuple(payload["allowedWritePaths"])


@pytest.mark.parametrize("path", ["Summay/private.md", "docs/sUmMaY/private.md"])
def test_carry_forward_rejects_summary_root_paths(path):
    with pytest.raises(ValueError, match="carryForwardFiles"):
        CarryForwardFile(path=path, sha256="c" * 64)


@pytest.mark.parametrize("control", ["\n", "\t", "\x1f"])
def test_carry_forward_contract_rejects_control_characters(control):
    with pytest.raises(ValueError, match="carryForwardFiles path is invalid"):
        CarryForwardFile(path=f"docs/legacy{control}marker.md", sha256="c" * 64)


def test_contract_direct_construction_rejects_over_limit_carry_forward_inventory():
    from dataclasses import replace

    contract = ImplementationTaskContract.from_dict(valid_contract_payload())
    entries = tuple(
        CarryForwardFile(path=f"docs/legacy/{index}.md", sha256="c" * 64)
        for index in range(51)
    )

    with pytest.raises(ValueError, match="carryForwardFiles"):
        replace(contract, carry_forward_files=entries)


@pytest.mark.parametrize(
    "entries",
    [
        [{"path": "../outside.py", "sha256": "c" * 64}],
        [{"path": "/absolute.py", "sha256": "c" * 64}],
        [{"path": ".", "sha256": "c" * 64}],
        [{"path": "tests/test_legacy.py", "sha256": "bad"}],
        [{"path": "tests/test_legacy.py", "sha256": "c" * 64, "extra": True}],
        [
            {"path": "tests/test_legacy.py", "sha256": "c" * 64},
            {"path": "tests/test_legacy.py", "sha256": "d" * 64},
        ],
    ],
)
def test_contract_rejects_malformed_carry_forward_entries(entries):
    payload = valid_contract_payload()
    payload["carryForwardFiles"] = entries

    with pytest.raises(ValueError, match="carryForwardFiles"):
        ImplementationTaskContract.from_dict(payload)


@pytest.mark.parametrize(
    ("write_path", "carry_forward_path"),
    [
        ("tests", "tests/test_legacy.py"),
        ("Docs/Agents", "docs/agents/legacy.md"),
    ],
)
def test_contract_rejects_carry_forward_path_overlapping_write_allowlist(write_path, carry_forward_path):
    payload = valid_contract_payload()
    payload["allowedWritePaths"] = [write_path]
    payload["carryForwardFiles"] = [
        {"path": carry_forward_path, "sha256": "c" * 64},
    ]

    with pytest.raises(ValueError, match="carryForwardFiles"):
        ImplementationTaskContract.from_dict(payload)


def test_contract_rejects_case_equivalent_duplicate_carry_forward_paths():
    payload = valid_contract_payload()
    payload["carryForwardFiles"] = [
        {"path": "docs/legacy.md", "sha256": "c" * 64},
        {"path": "Docs/Legacy.md", "sha256": "d" * 64},
    ]

    with pytest.raises(ValueError, match="carryForwardFiles"):
        ImplementationTaskContract.from_dict(payload)


def test_carry_forward_manifest_must_match_the_fingerprinted_approved_plan(tmp_path):
    from hashlib import sha256
    import json

    manifest_files = [{"path": "tests/test_legacy.py", "sha256": "c" * 64}]
    plan = tmp_path / "plan.md"
    plan.write_text(
        "<!-- review-carry-forward-v1:start -->\n"
        "```json\n"
        + json.dumps({"schemaVersion": "review-carry-forward-v1", "files": manifest_files})
        + "\n```\n<!-- review-carry-forward-v1:end -->\n",
        encoding="utf-8",
    )
    plan_fingerprint = sha256(plan.read_bytes()).hexdigest()
    payload = valid_contract_payload()
    payload.update({
        "planPath": "plan.md",
        "planFingerprint": plan_fingerprint,
        "carryForwardFiles": manifest_files,
    })
    contract = ImplementationTaskContract.from_dict(payload)

    validate_carry_forward_approval(
        contract, project_root=tmp_path, brief_path="plan.md",
        expected_brief_fingerprint=plan_fingerprint,
    )


def test_carry_forward_approval_rejects_symlinked_project_root(tmp_path):
    from hashlib import sha256
    import json

    real_root = tmp_path / "real-repository"
    real_root.mkdir()
    manifest_files = [{"path": "tests/test_legacy.py", "sha256": "c" * 64}]
    brief = real_root / "plan.md"
    brief.write_text(
        "<!-- review-carry-forward-v1:start -->\n"
        + json.dumps({"schemaVersion": "review-carry-forward-v1", "files": manifest_files})
        + "\n<!-- review-carry-forward-v1:end -->\n",
        encoding="utf-8",
    )
    plan_fingerprint = sha256(brief.read_bytes()).hexdigest()
    payload = valid_contract_payload()
    payload.update({
        "planPath": "plan.md",
        "planFingerprint": plan_fingerprint,
        "carryForwardFiles": manifest_files,
    })
    contract = ImplementationTaskContract.from_dict(payload)
    linked_root = tmp_path / "linked-repository"
    linked_root.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(ValueError, match="unavailable or unsafe"):
        validate_carry_forward_approval(
            contract,
            project_root=linked_root,
            brief_path="plan.md",
            expected_brief_fingerprint=plan_fingerprint,
        )


def test_carry_forward_approval_rejects_oversized_plan_manifest_before_entry_conversion(tmp_path):
    from hashlib import sha256
    import json

    approved = [{"path": "tests/test_legacy.py", "sha256": "c" * 64}]
    manifest_files = [
        {"path": f"tests/test_{index}.py", "sha256": "d" * 64}
        for index in range(51)
    ]
    plan = tmp_path / "plan.md"
    plan.write_text(
        "<!-- review-carry-forward-v1:start -->\n"
        + json.dumps({"schemaVersion": "review-carry-forward-v1", "files": manifest_files})
        + "\n<!-- review-carry-forward-v1:end -->\n",
        encoding="utf-8",
    )
    plan_fingerprint = sha256(plan.read_bytes()).hexdigest()
    payload = valid_contract_payload()
    payload.update({
        "planPath": "plan.md",
        "planFingerprint": plan_fingerprint,
        "carryForwardFiles": approved,
    })
    contract = ImplementationTaskContract.from_dict(payload)

    with pytest.raises(ValueError, match="exceeds the bounded entry limit"):
        validate_carry_forward_approval(
            contract, project_root=tmp_path, brief_path="plan.md",
            expected_brief_fingerprint=plan_fingerprint,
        )


def test_carry_forward_approval_rejects_brief_over_byte_budget_before_accumulating(tmp_path, monkeypatch):
    from hashlib import sha256
    import backend.agents.implementation_models as implementation_models

    monkeypatch.setattr(implementation_models, "MAX_CARRY_FORWARD_BRIEF_BYTES", 64)
    plan = tmp_path / "plan.md"
    plan.write_bytes(b"x" * 65)
    plan_fingerprint = sha256(plan.read_bytes()).hexdigest()
    payload = valid_contract_payload()
    payload.update({
        "planPath": "plan.md",
        "planFingerprint": plan_fingerprint,
        "carryForwardFiles": [{"path": "tests/test_legacy.py", "sha256": "c" * 64}],
    })
    contract = ImplementationTaskContract.from_dict(payload)

    with pytest.raises(ValueError, match="exceeds the bounded byte limit"):
        validate_carry_forward_approval(
            contract,
            project_root=tmp_path,
            brief_path="plan.md",
            expected_brief_fingerprint=plan_fingerprint,
        )


def test_carry_forward_approval_reads_sealed_brief_through_pinned_directory_fd(tmp_path, monkeypatch):
    import json
    import os
    from hashlib import sha256

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    brief = docs_dir / "plan.md"
    manifest_files = [{"path": "tests/test_legacy.py", "sha256": "c" * 64}]
    original_text = (
        "<!-- review-carry-forward-v1:start -->\n"
        + json.dumps({"schemaVersion": "review-carry-forward-v1", "files": manifest_files})
        + "\n<!-- review-carry-forward-v1:end -->\n"
    )
    brief.write_text(original_text, encoding="utf-8")
    plan_fingerprint = sha256(brief.read_bytes()).hexdigest()
    external_dir = tmp_path.parent / f"{tmp_path.name}-external-docs"
    external_dir.mkdir()
    (external_dir / "plan.md").write_text("external replacement\n", encoding="utf-8")
    moved_dir = tmp_path / "docs-held"
    swapped = False
    candidate = brief
    real_resolve = Path.resolve
    real_read_bytes = Path.read_bytes
    real_open = os.open

    def swap_docs():
        nonlocal swapped
        os.rename(docs_dir, moved_dir)
        docs_dir.symlink_to(external_dir, target_is_directory=True)
        swapped = True

    def restore_docs():
        if docs_dir.is_symlink():
            docs_dir.unlink()
            os.rename(moved_dir, docs_dir)

    def racing_resolve(path, *args, **kwargs):
        result = real_resolve(path, *args, **kwargs)
        if Path(path) == candidate and not swapped:
            swap_docs()
        return result

    def restoring_read_bytes(path):
        try:
            return real_read_bytes(path)
        finally:
            if Path(path) == candidate:
                restore_docs()

    def swapping_open(path, flags, *args, **kwargs):
        if Path(path).name == "plan.md" and kwargs.get("dir_fd") is not None and not swapped:
            swap_docs()
            try:
                return real_open(path, flags, *args, **kwargs)
            finally:
                restore_docs()
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", racing_resolve)
    monkeypatch.setattr(Path, "read_bytes", restoring_read_bytes)
    monkeypatch.setattr(os, "open", swapping_open)
    contract_payload = {
        **valid_contract_payload(),
        "planPath": "docs/plan.md",
        "planFingerprint": plan_fingerprint,
        "carryForwardFiles": manifest_files,
    }
    contract = ImplementationTaskContract.from_dict(contract_payload)

    validate_carry_forward_approval(
        contract,
        project_root=tmp_path,
        brief_path="docs/plan.md",
        expected_brief_fingerprint=plan_fingerprint,
    )

    assert swapped
    assert not docs_dir.is_symlink()


@pytest.mark.parametrize("failure", ["list", "plan_fingerprint", "session_fingerprint", "markers"])
def test_carry_forward_manifest_rejects_unbound_or_different_approval(tmp_path, failure):
    from hashlib import sha256
    import json

    approved_files = [{"path": "tests/test_legacy.py", "sha256": "c" * 64}]
    plan_files = [{"path": "tests/test_legacy.py", "sha256": "d" * 64}] if failure == "list" else approved_files
    markers = "" if failure == "markers" else (
        "<!-- review-carry-forward-v1:start -->\n"
        + json.dumps({"schemaVersion": "review-carry-forward-v1", "files": plan_files})
        + "\n<!-- review-carry-forward-v1:end -->\n"
    )
    plan = tmp_path / "plan.md"
    plan.write_text(markers, encoding="utf-8")
    actual_fingerprint = sha256(plan.read_bytes()).hexdigest()
    contract_fingerprint = "e" * 64 if failure == "plan_fingerprint" else actual_fingerprint
    payload = valid_contract_payload()
    payload.update({
        "planPath": "plan.md",
        "planFingerprint": contract_fingerprint,
        "carryForwardFiles": approved_files,
    })
    contract = ImplementationTaskContract.from_dict(payload)
    session_fingerprint = "f" * 64 if failure == "session_fingerprint" else actual_fingerprint

    with pytest.raises(ValueError, match="carry-forward approval"):
        validate_carry_forward_approval(
            contract, project_root=tmp_path, brief_path="plan.md",
            expected_brief_fingerprint=session_fingerprint,
        )


def test_implementation_memory_context_requires_exact_authorized_evidence():
    from backend.agents.implementation_models import build_implementation_memory_context

    evidence = build_memory_hub_integration_evidence(
        project_id="nbs-analytics", consumer_id="context-agent", integration_mode="direct_query",
        status="ready", reason="ok", query_fingerprint="a" * 64, hints_fingerprint="b" * 64,
        policy_decision_fingerprints=("c" * 64,), source_refs=("docs/brief.md",), hint_count=1,
        generated_at="2026-08-18T00:00:00+00:00",
    ).to_dict()
    payload = valid_contract_payload()
    payload["memoryContextAllowed"] = True
    payload["expectedMemoryEvidenceFingerprint"] = evidence["evidenceFingerprint"]
    contract = ImplementationTaskContract.from_dict(payload)
    context = build_implementation_memory_context(contract, evidence)
    assert context["evidenceFingerprint"] == evidence["evidenceFingerprint"]
    assert context["hintCount"] == 1
    assert set(context) == {"schemaVersion", "status", "authority", "evidenceFingerprint", "hintCount", "sourceRefs"}
    payload["expectedMemoryEvidenceFingerprint"] = "e" * 64
    with pytest.raises(PermissionError, match="fingerprint"):
        build_implementation_memory_context(ImplementationTaskContract.from_dict(payload), evidence)


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
        changed_files=(
            "backend/agents/implementation_models.py",
            "tests/test_implementation_models.py",
        ),
        diff_stat={"files": 2, "insertions": 1, "deletions": 0},
        red_evidence=(validation,),
        green_evidence=(validation,),
        repair_loops_used=1,
        test_files_changed=("tests/test_implementation_models.py",),
        production_files_changed=("backend/agents/implementation_models.py",),
        findings=(),
    )
    payload = report.to_dict()
    assert payload["redEvidence"][0]["commandId"] == "pytest_targeted"
    assert payload["repairLoopsUsed"] == 1
    assert payload["testFilesChanged"] == ["tests/test_implementation_models.py"]
    assert payload["productionFilesChanged"] == ["backend/agents/implementation_models.py"]
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


def test_runtime_helper_rejects_symlinked_runtime_parent(tmp_path):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (project / ".nbs_agent_runtime").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PermissionError, match="symlink"):
        resolve_implementation_runtime_path(project, "reports/run.json")
