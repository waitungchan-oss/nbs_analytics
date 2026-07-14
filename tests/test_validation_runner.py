import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.agents.implementation_models import ValidationResult
from backend.agents.validation_runner import CommandRejected, ValidationRunner


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def completed(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


@pytest.mark.parametrize("value", ["; rm -rf .", "$(touch hacked)", "| cat .env", "../outside.py"])
def test_runner_rejects_shell_and_path_escape(value):
    with pytest.raises(CommandRejected):
        ValidationRunner(PROJECT_ROOT).run("pytest_targeted", (value,))


def test_runner_rejects_unknown_command():
    with pytest.raises(CommandRejected, match="not allowlisted"):
        ValidationRunner(PROJECT_ROOT).run("system_manager_start", ())


def test_runner_uses_shell_false(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return completed()

    monkeypatch.setattr("backend.agents.validation_runner.subprocess.run", fake_run)
    result = ValidationRunner(PROJECT_ROOT).run("py_compile", ("app.py",))

    assert calls[0][1]["shell"] is False
    assert result.exit_code == 0


def test_runner_resolves_project_local_interpreter_first(tmp_path):
    interpreter = tmp_path / ".venv/bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b"python")
    interpreter.chmod(0o755)

    runner = object.__new__(ValidationRunner)
    runner.project_root = tmp_path.resolve()

    assert runner._resolve_interpreter(".venv/bin/python") == interpreter.resolve()


def test_runner_rejects_worktree_interpreter_symlink_outside_approved_root(tmp_path):
    repository = tmp_path / "repository"
    worktree = repository / ".worktrees/implementation-agent"
    outside = tmp_path / "outside/python"
    (worktree / ".venv/bin").mkdir(parents=True)
    outside.parent.mkdir()
    outside.write_bytes(b"python")
    outside.chmod(0o755)
    (worktree / ".venv/bin/python").symlink_to(outside)

    runner = object.__new__(ValidationRunner)
    runner.project_root = worktree.resolve()

    with pytest.raises(CommandRejected, match="approved virtualenv"):
        runner._resolve_interpreter(".venv/bin/python")


def test_runner_rejects_missing_allowlisted_interpreter(tmp_path):
    runner = object.__new__(ValidationRunner)
    runner.project_root = tmp_path.resolve()

    with pytest.raises(CommandRejected, match="approved virtualenv"):
        runner._resolve_interpreter(".venv/bin/python")


def test_runner_real_allowlisted_python_invocation_returns_result():
    result = ValidationRunner(PROJECT_ROOT).run(
        "py_compile", ("backend/agents/validation_runner.py",)
    )

    assert isinstance(result, ValidationResult)
    assert result.argv[0] == str(
        (PROJECT_ROOT.parent.parent / ".venv/bin/python").resolve()
    )
    assert result.exit_code == 0
    assert result.timed_out is False


def test_runner_reports_timeout_without_retry(monkeypatch):
    calls = []

    def raise_timeout(*args, **kwargs):
        calls.append((args, kwargs))
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr("backend.agents.validation_runner.subprocess.run", raise_timeout)
    result = ValidationRunner(PROJECT_ROOT).run("py_compile", ("app.py",))

    assert len(calls) == 1
    assert result.timed_out is True
    assert result.exit_code == 124


def test_runner_caps_each_output_stream_and_returns_stable_result(monkeypatch):
    monkeypatch.setattr(
        "backend.agents.validation_runner.subprocess.run",
        lambda *args, **kwargs: completed("o" * 40000, "e" * 40000, 3),
    )

    result = ValidationRunner(PROJECT_ROOT).run("py_compile", ("app.py",))

    assert isinstance(result, ValidationResult)
    assert len(result.stdout) == 32000
    assert len(result.stderr) == 32000
    assert result.command_id == "py_compile"
    assert result.argv == (
        str((PROJECT_ROOT.parent.parent / ".venv/bin/python").resolve()),
        "-m", "py_compile", "app.py"
    )
    assert result.exit_code == 3
    assert result.duration_ms >= 0


def test_runner_accepts_pytest_targets_and_integer_maxfail(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "backend.agents.validation_runner.subprocess.run",
        lambda *args, **kwargs: calls.append((args, kwargs)) or completed(),
    )

    ValidationRunner(PROJECT_ROOT).run(
        "pytest_targeted", ("tests/test_implementation_models.py", "-q", "--maxfail=2")
    )

    assert calls[0][0][0] == [
        str((PROJECT_ROOT.parent.parent / ".venv/bin/python").resolve()),
        "-m", "pytest", "tests/test_implementation_models.py", "-q", "--maxfail=2"
    ]


@pytest.mark.parametrize(
    "command_id, arguments",
    [
        ("pytest_targeted", ("backend/agents/implementation_models.py",)),
        ("pytest_targeted", ("tests/test_implementation_models.py", "--maxfail=x")),
        ("pytest_targeted", ("tests/test_implementation_models.py", "--no-header")),
        ("py_compile", ("README.md",)),
        ("py_compile", ("../outside.py",)),
        ("vue_verify", ("frontend",)),
    ],
)
def test_runner_rejects_invalid_targets_options_or_suffix(command_id, arguments):
    with pytest.raises(CommandRejected):
        ValidationRunner(PROJECT_ROOT).run(command_id, arguments)
