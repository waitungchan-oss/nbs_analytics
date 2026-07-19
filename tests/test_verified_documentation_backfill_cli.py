import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import verified_documentation_backfill as cli


def test_direct_script_execution_resolves_project_imports():
    script = Path(__file__).resolve().parents[1] / "scripts" / "verified_documentation_backfill.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "Create a verified documentation backfill run" in completed.stdout


def test_cli_accepts_only_documented_options():
    args = cli.parse_args(["--source-commit", "a" * 40, "--reason", "backfill", "--no-notify"])
    assert args.source_commit == "a" * 40
    assert args.reason == "backfill"
    assert args.no_notify is True


def test_cli_rejects_arbitrary_command_option():
    with pytest.raises(SystemExit):
        cli.parse_args(["--source-commit", "a" * 40, "--reason", "backfill", "--command", "rm -rf /"])


def test_cli_emits_one_redacted_json_document(monkeypatch, capsys):
    class FakeService:
        def __init__(self, *args, **kwargs):
            pass

        def create(self, *, source_commit, reason):
            return {"status": "completed", "runId": "run-1", "reason": reason}

    monkeypatch.setattr(cli, "build_service", lambda no_notify: FakeService())
    assert cli.main(["--source-commit", "a" * 40, "--reason", "backfill", "--no-notify"]) == 0
    output = capsys.readouterr().out.strip().splitlines()
    assert len(output) == 1
    assert json.loads(output[0]) == {"status": "completed", "runId": "run-1", "reason": "backfill"}


def test_cli_emits_blocked_json_for_fail_closed_identity_result(monkeypatch, capsys):
    class FakeService:
        def __init__(self, *args, **kwargs):
            pass

        def create(self, *, source_commit, reason):
            return {"status": "blocked", "reason": "identity_check_failed"}

    monkeypatch.setattr(cli, "build_service", lambda no_notify: FakeService())

    assert cli.main(["--source-commit", "a" * 40, "--reason", "backfill"]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "blocked", "reason": "identity_check_failed"
    }


@pytest.mark.parametrize("error", [OSError("I/O failure"), subprocess.TimeoutExpired("artifact", 1)])
def test_cli_emits_one_blocked_json_document_for_expected_service_io(monkeypatch, capsys, error):
    class FakeService:
        def create(self, *, source_commit, reason):
            raise error

    monkeypatch.setattr(cli, "build_service", lambda no_notify: FakeService())

    assert cli.main(["--source-commit", "a" * 40, "--reason", "backfill"]) == 1
    output = capsys.readouterr().out.strip().splitlines()
    assert len(output) == 1
    assert json.loads(output[0]) == {"status": "blocked", "reason": "service_io_failed"}
