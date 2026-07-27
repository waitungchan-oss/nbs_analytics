from __future__ import annotations

import json
from pathlib import Path

import scripts.governance_graph as cli
from backend.agents.workflow_models import MANIFEST_SCHEMA, STATUS_SCHEMA, WorkflowManifest, WorkflowStatus
from backend.agents.workflow_store import WorkflowStore


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_parser_exposes_only_projection_commands():
    parser = cli._parser()

    assert parser.parse_args(["build", "--run-id", "run-123"]).command == "build"
    assert parser.parse_args(["validate", "--run-id", "run-123"]).command == "validate"
    assert parser.parse_args(["status", "--run-id", "run-123"]).command == "status"


def test_validate_and_status_do_not_write(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    before = _tree_bytes(tmp_path)

    assert cli.main(["status", "--run-id", "run-123"]) == 2
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["schemaVersion"] == "nbs-governance-graph-cli-v1"

    assert cli.main(["validate", "--run-id", "run-123"]) == 2
    validate_payload = json.loads(capsys.readouterr().out)
    assert validate_payload["schemaVersion"] == "nbs-governance-graph-cli-v1"
    assert _tree_bytes(tmp_path) == before


def test_cli_does_not_parse_control_or_runner_flags():
    parser = cli._parser()

    for forbidden in ("approve", "dispatch", "repair", "apply", "prune", "delete"):
        try:
            parser.parse_args([forbidden])
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError(f"forbidden command accepted: {forbidden}")


def test_build_emits_cli_envelope_and_only_writes_projection(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    store = WorkflowStore(tmp_path)
    manifest = WorkflowManifest(
        MANIFEST_SCHEMA, "run-123", "docs/brief.md", "a" * 64, "main", "b" * 40,
        (), "2026-07-27T10:00:00+00:00", "c" * 64,
    )
    status = WorkflowStatus(
        STATUS_SCHEMA, "run-123", "authorization", "created",
        "2026-07-27T10:00:00+00:00", "2026-07-27T10:00:00+00:00", None,
        "fixture", None, 0,
    )
    store.create_run(manifest, status)
    before = _tree_bytes(tmp_path)

    assert cli.main(["build", "--run-id", "run-123"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schemaVersion"] == "nbs-governance-graph-cli-v1"
    assert payload["command"] == "build"
    assert payload["snapshot"]["schemaVersion"] == "nbs-governance-graph-v1"
    after = _tree_bytes(tmp_path)
    assert set(after) == set(before) | {".nbs_agent_runtime/runs/run-123/governance-graph.json"}
