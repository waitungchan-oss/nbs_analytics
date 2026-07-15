import hashlib
import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path("/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python")


def _head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True,
    ).stdout.strip()


@pytest.fixture
def contract_path(tmp_path: Path) -> Path:
    plan = ROOT / ".superpowers/sdd/implementation-agent/task-6-brief.md"
    contract = {
        "schemaVersion": "implementation-task-v1",
        "taskId": "Task-6",
        "planPath": ".superpowers/sdd/implementation-agent/task-6-brief.md",
        "planFingerprint": hashlib.sha256(plan.read_bytes()).hexdigest(),
        "objective": "Expose the implementation agent JSON CLI",
        "approvedBaseSha": _head(),
        "approvedWorktree": str(ROOT),
        "allowedWritePaths": ["scripts/implementation_agent.py", "tests/test_implementation_agent_cli.py"],
        "validationCommands": ["vue_verify"],
        "riskSurfaces": [],
        "maxChangedFiles": 8,
        "maxDiffLines": 800,
        "maxRepairLoops": 2,
        "taskType": "behavior",
        "redCommands": [],
        "greenCommands": ["vue_verify"],
    }
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
    return path


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(PYTHON), "scripts/implementation_agent.py", *args],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def test_collect_only_emits_bundle_and_does_not_invoke_runner(contract_path: Path):
    result = run_cli("--contract", str(contract_path), "--collect-only")
    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert payload["schemaVersion"] == "evidence-bundle-v1"
    assert result.stdout.count("{") >= 1
    assert "Traceback" not in result.stderr


def test_cli_requires_explicit_runner_for_execution(contract_path: Path):
    result = run_cli("--contract", str(contract_path))
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "blocked_invalid_contract"
    assert "Traceback" not in result.stdout


def test_cli_parse_failure_emits_single_json_document():
    result = run_cli("--unknown-argument")

    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "blocked_invalid_contract"
    assert result.stdout.strip().endswith("}")
    assert "usage:" in result.stderr


def test_cli_subprocess_redacts_embedded_external_path():
    external_contract = Path("/private/tmp/unique_task6_external_path_9f3c2b.json")
    result = run_cli("--contract", str(external_contract))

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "blocked_invalid_contract"
    assert str(external_contract) not in result.stdout
    assert "/private/tmp" not in result.stdout


def test_cli_maps_validation_failure_to_nonzero_exit(monkeypatch, contract_path: Path, capsys):
    import scripts.implementation_agent as cli

    monkeypatch.setattr(cli, "_load_agent_runner", lambda _argv: lambda _request: {
        "schemaVersion": "implementation-response-v1",
        "status": "completed",
        "summary": "done",
        "requestedValidationCommandIds": ["vue_verify"],
    })
    monkeypatch.setattr(cli.ImplementationAgentService, "execute", lambda self, contract, runner: {
        "schemaVersion": "implementation-run-report-v1",
        "status": "validation_failed",
    })

    exit_code = cli.main(["--contract", str(contract_path), "--agent-command", "codex"])
    captured = capsys.readouterr()
    assert exit_code == 3
    assert json.loads(captured.out)["status"] == "validation_failed"
    assert captured.err == ""


def test_cli_redacts_external_paths_and_environment_values(monkeypatch, contract_path: Path, capsys):
    import scripts.implementation_agent as cli

    monkeypatch.setenv("IMPLEMENTATION_AGENT_SECRET", "do-not-leak")
    monkeypatch.setattr(cli, "_load_agent_runner", lambda _argv: lambda _request: None)
    monkeypatch.setattr(cli.ImplementationAgentService, "execute", lambda self, contract, runner: {
        "schemaVersion": "implementation-run-report-v1",
        "status": "runtime_error",
        "finding": {"message": f"{contract_path} do-not-leak"},
    })

    assert cli.main(["--contract", str(contract_path), "--agent-command", "codex"]) == 5
    captured = capsys.readouterr()
    assert str(contract_path) not in captured.out
    assert "do-not-leak" not in captured.out


def test_cli_redacts_external_paths_and_environment_values_from_stderr(
    monkeypatch, contract_path: Path, capsys,
):
    import scripts.implementation_agent as cli

    secret = "stderr-secret-value"
    external = "/private/tmp/implementation-agent-secret.json"
    monkeypatch.setenv("IMPLEMENTATION_AGENT_SECRET", secret)
    monkeypatch.setattr(cli, "_load_contract", lambda _path: (_ for _ in ()).throw(ValueError(f"{external} {secret}")))

    assert cli.main(["--contract", str(contract_path), "--collect-only"]) == 2
    captured = capsys.readouterr()
    assert external not in captured.err
    assert secret not in captured.err
    assert "[REDACTED" in captured.err


def test_cli_maps_wrong_worktree_to_blocked_exit():
    import scripts.implementation_agent as cli

    assert cli._exit_code("blocked_wrong_worktree") == 2
