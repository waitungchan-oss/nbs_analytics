from types import SimpleNamespace

import pytest


class FakeValidationRunner:
    def __init__(self, results=None, error=None):
        self.results = results or {}
        self.error = error
        self.calls = []

    def run(self, command_id, arguments):
        self.calls.append((command_id, arguments))
        if self.error is not None:
            raise self.error
        return self.results.get(
            command_id,
            SimpleNamespace(
                command_id=command_id,
                argv=(".venv/bin/python", "-m", command_id),
                exit_code=0,
                stdout="1 passed",
                stderr="",
                timed_out=False,
            ),
        )


def _result(command_id, exit_code=0, stdout="ok", stderr="", timed_out=False):
    return SimpleNamespace(
        command_id=command_id,
        argv=(".venv/bin/python", "-m", command_id),
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
    )


def test_preflight_checks_normalize_validation_results_to_verification_v1():
    from backend.agents.strict_review_evidence_service import (
        build_verification_v1,
        run_preflight_checks,
    )

    runner = FakeValidationRunner({
        "pytest_targeted": _result("pytest_targeted", stdout="2 passed"),
        "py_compile": _result("py_compile", stdout=""),
    })
    results = run_preflight_checks(
        project_root=None,
        plan=(
            ("py_compile", ("backend/agents/strict_review_preflight.py",)),
            ("pytest_targeted", ("tests/test_strict_review_preflight.py", "-q")),
        ),
        source_fingerprint="a" * 64,
        runner=runner,
    )

    verification = build_verification_v1(results)
    assert [item["label"] for item in verification["commands"]] == [
        "python-compile", "targeted-tests"
    ]
    assert verification["commands"][0]["exitCode"] == 0


def test_preflight_returns_verification_failed_for_required_nonzero_result():
    from backend.agents.strict_review_evidence_service import (
        evaluate_check_results,
        run_preflight_checks,
    )

    runner = FakeValidationRunner({"pytest_targeted": _result("pytest_targeted", exit_code=1)})
    results = run_preflight_checks(
        project_root=None,
        plan=(("pytest_targeted", ("tests/test_strict_review_preflight.py",)),),
        source_fingerprint="a" * 64,
        runner=runner,
    )

    assert evaluate_check_results(results) == "verification_failed"


def test_preflight_rejects_unapproved_validation_command():
    from backend.agents.strict_review_evidence_service import run_preflight_checks

    with pytest.raises(ValueError, match="unsupported"):
        run_preflight_checks(
            project_root=None,
            plan=(("shell", ()),),
            source_fingerprint="a" * 64,
            runner=FakeValidationRunner(),
        )


def test_preflight_maps_runner_start_failure_to_blocked_error():
    from backend.agents.strict_review_evidence_service import run_preflight_checks

    with pytest.raises(RuntimeError, match="blocked"):
        run_preflight_checks(
            project_root=None,
            plan=(("py_compile", ("backend/agents/strict_review_preflight.py",)),),
            source_fingerprint="a" * 64,
            runner=FakeValidationRunner(error=OSError("runner unavailable")),
        )
