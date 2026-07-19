from __future__ import annotations


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
