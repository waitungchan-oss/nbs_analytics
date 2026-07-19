from __future__ import annotations

import json

import pytest


def test_documentation_parser_exposes_contract():
    from scripts.documentation_agent import _parser

    args = _parser().parse_args([
        "--run-id", "run-1", "--agent-command", "codex exec --json",
        "--obsidian-vault", "/tmp/vault", "--apply-brief",
        "--approve-target", "system_map", "--approve-target", "adr",
    ])
    assert args.run_id == "run-1"
    assert args.approve_target == ["system_map", "adr"]


def test_agent_workflow_parser_exposes_document_sidecar():
    from scripts.agent_workflow import _parser

    args = _parser().parse_args([
        "document", "--run-id", "run-1", "--agent-command", "codex exec --json",
    ])
    assert args.command == "document"
    assert args.run_id == "run-1"


def test_documentation_cli_emits_one_json_document_on_success(capsys, monkeypatch):
    import scripts.documentation_agent as cli

    class FakeWorkflow:
        def __init__(self, _project_root):
            pass

        def run(self, run_id, **kwargs):
            return {"status": "preview_ready", "runId": run_id}

    monkeypatch.setattr(cli, "DocumentationWorkflow", FakeWorkflow)

    assert cli.main(["--run-id", "run-1"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {"runId": "run-1", "status": "preview_ready"}
    assert captured.out.count("\n") == 1


def test_documentation_cli_redacts_external_paths_in_errors(capsys, monkeypatch):
    import scripts.documentation_agent as cli

    external_path = "/private/tmp/secret-documentation-source"

    class FakeWorkflow:
        def __init__(self, _project_root):
            pass

        def run(self, run_id, **kwargs):
            raise ValueError(f"invalid source: {external_path}")

    monkeypatch.setattr(cli, "DocumentationWorkflow", FakeWorkflow)

    assert cli.main(["--run-id", "run-1"]) == 2
    captured = capsys.readouterr()
    assert external_path not in captured.out
    assert external_path not in captured.err
    assert "[REDACTED_PATH]" in captured.out
    assert "[REDACTED_PATH]" in captured.err
    assert json.loads(captured.out)["status"] == "blocked"
    assert captured.out.count("\n") == 1


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("applied", 0),
        ("preview_ready", 0),
        ("no_documentation_needed", 0),
        ("awaiting_target_approval", 1),
        ("blocked", 2),
        ("context_overflow", 4),
        ("invalid_agent_output", 5),
    ],
)
def test_documentation_cli_uses_contract_exit_codes(status, expected):
    from scripts.documentation_agent import _exit_code

    assert _exit_code(status) == expected
