import subprocess

import pytest

from backend.agents.release_gate_models import ReleaseGateValidationError, validate_release_gate_evidence
from scripts.fast_acceptance_precheck import _working_tree_identity, run_fast_precheck, select_targeted_tests


COMMIT = "a" * 40
SOURCE = "b" * 64


def test_select_targeted_tests_maps_gate_changes_to_contract_tests():
    tests, mode = select_targeted_tests(["scripts/release_gate.py"])

    assert "tests/test_release_gate.py" in tests
    assert mode == "changed_file_mapping"


def test_unknown_scope_keeps_full_gate_required():
    tests, mode = select_targeted_tests(["backend/unknown_component.py"])

    assert tests
    assert mode == "contract_pack"


def test_fast_result_is_never_release_authority(monkeypatch, tmp_path):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "3 passed in 0.02s\n", ""),
    )

    result = run_fast_precheck(
        tmp_path,
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        changed_paths=["scripts/release_gate.py"],
    )

    assert result["schemaVersion"] == "acceptance-fast-precheck-v1"
    assert result["authority"] == "advisory"
    assert result["fullGateRequired"] is True
    assert result["status"] == "PASS"
    assert result["metadata"]["telemetry"]["durationSeconds"] >= 0


def test_fast_precheck_timeout_is_blocked(monkeypatch, tmp_path):
    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 120, output="partial")

    monkeypatch.setattr(subprocess, "run", timeout)

    result = run_fast_precheck(
        tmp_path,
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        changed_paths=["scripts/release_gate.py"],
    )

    assert result["status"] == "BLOCKED"
    assert result["metadata"]["telemetry"]["blockedReason"] == "timeout"


def test_fast_artifact_is_rejected_by_release_gate_validator():
    with pytest.raises(ReleaseGateValidationError):
        validate_release_gate_evidence(
            {
                "schemaVersion": "acceptance-fast-precheck-v1",
                "authority": "advisory",
            },
            COMMIT,
            SOURCE,
        )


def test_working_tree_identity_changes_when_untracked_content_changes(monkeypatch, tmp_path):
    untracked = tmp_path / "new.txt"
    untracked.write_text("first", encoding="utf-8")
    monkeypatch.setattr(
        "scripts.fast_acceptance_precheck._git",
        lambda project_root, *args: "status\n" if args[0] == "status" else "tracked diff",
    )

    first = _working_tree_identity(tmp_path, COMMIT, ["new.txt"])
    untracked.write_text("second", encoding="utf-8")
    second = _working_tree_identity(tmp_path, COMMIT, ["new.txt"])

    assert first != second
