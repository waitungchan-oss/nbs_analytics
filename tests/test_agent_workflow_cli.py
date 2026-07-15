import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_parser_exposes_required_workflow_commands_and_flags():
    from scripts.agent_workflow import _parser

    parser = _parser()
    run = parser.parse_args(["run", "--brief", "docs/agents/CODEX_AGENT_DISPATCH.md"])
    approve = parser.parse_args([
        "approve", "--run-id", "run-1", "--contract", "task.json",
        "--implementation-agent-command", "codex exec --json",
        "--review-agent-command", "reviewer --json",
    ])

    assert run.command == "run"
    assert run.context_agent_command is None
    assert run.no_notify is False
    assert approve.command == "approve"
    assert approve.run_id == "run-1"
    assert approve.no_notify is False
    assert parser.parse_args(["status", "--run-id", "run-1"]).command == "status"
    assert parser.parse_args(["start", "--brief", "docs/agents/CODEX_AGENT_DISPATCH.md"]).command == "start"
    assert parser.parse_args(["list"]).command == "list"
    assert parser.parse_args(["prune", "--dry-run"]).dry_run is True
    assert parser.parse_args(["prune", "--apply"]).apply is True


def test_cli_parse_errors_and_runtime_errors_emit_one_redacted_json_document(capsys, monkeypatch):
    import scripts.agent_workflow as cli

    assert cli.main(["run"]) == 2
    parsed = capsys.readouterr()
    assert json.loads(parsed.out)["status"] == "blocked"
    assert parsed.out.count("{") == 1

    monkeypatch.setenv("WORKFLOW_CLI_SECRET", "do-not-leak")
    monkeypatch.setattr(cli, "_run", lambda _args: (_ for _ in ()).throw(RuntimeError("/private/tmp/do-not-leak")))
    assert cli.main(["list"]) == 5
    failed = capsys.readouterr()
    assert json.loads(failed.out)["status"] == "runtime_error"
    assert "/private/tmp" not in failed.out
    assert "do-not-leak" not in failed.err


def test_cli_maps_workflow_statuses_to_required_exit_codes():
    from scripts.agent_workflow import _exit_code

    assert _exit_code("completed") == 0
    assert _exit_code("awaiting_authorization") == 0
    assert _exit_code("changes_required") == 1
    assert _exit_code("blocked") == 2
    assert _exit_code("context_overflow") == 4
    assert _exit_code("invalid_agent_output") == 4
    assert _exit_code("failed") == 5


def test_hermes_workflow_coverage_is_read_only_and_includes_task_seven_tests():
    from scripts.hermes_post_change_check import build_check_plan

    plan = build_check_plan(include_monitor=False, include_tests=True)
    targeted = next(step for step in plan if step.label == "targeted-tests")
    artifacts = next(step for step in plan if step.label == "workflow-artifact-retention-report")

    for test_name in [
        "tests/test_workflow_models.py", "tests/test_workflow_store.py",
        "tests/test_workflow_notifications.py", "tests/test_workflow_retention.py",
        "tests/test_workflow_orchestrator_start.py", "tests/test_workflow_orchestrator_approve.py",
        "tests/test_agent_workflow_cli.py", "tests/test_agent_workflow_integration.py",
    ]:
        assert test_name in targeted.command
    assert artifacts.required is False
    assert "unlink" not in artifacts.command[2]
    assert "write_text" not in artifacts.command[2]


def test_status_list_and_prune_render_json_and_wire_retention(tmp_path, monkeypatch, capsys):
    import scripts.agent_workflow as cli
    from backend.agents.workflow_models import MANIFEST_SCHEMA, STATUS_SCHEMA, WorkflowManifest, WorkflowStatus
    from backend.agents.workflow_store import WorkflowStore

    store = WorkflowStore(tmp_path)
    for run_id, created_at in [("run-old", "2026-07-14T00:00:00+00:00"), ("run-new", "2026-07-15T00:00:00+00:00")]:
        store.create_run(
            WorkflowManifest(MANIFEST_SCHEMA, run_id, "docs/agents/CODEX_AGENT_DISPATCH.md", "a" * 64, "codex/test", "b" * 40, (), created_at, "c" * 64),
            WorkflowStatus(STATUS_SCHEMA, run_id, "context", "awaiting_authorization", created_at, created_at, None, "ready", None, 0),
        )
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)

    assert cli.main(["status", "--run-id", "run-new"]) == 0
    assert json.loads(capsys.readouterr().out)["runId"] == "run-new"
    assert cli.main(["list"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [item["runId"] for item in listed["runs"]] == ["run-new", "run-old"]

    calls = []

    class FakeRetention:
        def __init__(self, root):
            assert root == tmp_path

        def plan(self):
            calls.append("plan")
            return {"report": "planned"}

        def apply(self, report, *, dry_run):
            calls.append((report, dry_run))
            return report

    monkeypatch.setattr(cli, "WorkflowRetention", FakeRetention)
    assert cli.main(["prune", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["dryRun"] is True
    assert calls == ["plan", ({"report": "planned"}, True)]


@pytest.mark.parametrize("command", ["run", "approve"])
def test_cli_requires_explicit_approval_inputs(command, capsys):
    import scripts.agent_workflow as cli

    argv = [command, "--brief", "docs/agents/CODEX_AGENT_DISPATCH.md"] if command == "run" else [command, "--run-id", "run-1", "--contract", "task.json"]
    assert cli.main(argv) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"
