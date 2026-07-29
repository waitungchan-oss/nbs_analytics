from __future__ import annotations

import json
import io
from pathlib import Path

import scripts.governance_graph as cli
from backend.agents.workflow_models import MANIFEST_SCHEMA, STATUS_SCHEMA, WorkflowManifest, WorkflowStatus
from backend.agents.workflow_store import WorkflowStore
from backend.agents.governance_graph_service import GovernanceGraphBuilder
from backend.agents.governance_graph_comparison_models import GovernanceGraphComparisonResult


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _comparison_payload() -> dict:
    identity = {"runId": "run-left", "graphFingerprint": "a" * 64, "generatedAt": "2026-07-29T00:00:00+00:00", "freshness": "fresh"}
    summary = {"addedNodes": 0, "removedNodes": 0, "changedNodes": 0, "unchangedNodes": 0, "addedEdges": 0, "removedEdges": 0, "changedEdges": 0, "addedEvidenceRefs": 0, "removedEvidenceRefs": 0, "changedEvidenceRefs": 0}
    return GovernanceGraphComparisonResult.from_parts(
        status="available", left_reference={"runId": "run-left"}, right_reference={"runId": "run-right"},
        left_snapshot=identity, right_snapshot={**identity, "runId": "run-right"}, summary=summary,
        node_changes=(), edge_changes=(), evidence_changes=(), diagnostics=(),
    ).to_dict()


def test_parser_exposes_only_projection_commands():
    parser = cli._parser()

    assert parser.parse_args(["build", "--run-id", "run-123"]).command == "build"
    assert parser.parse_args(["validate", "--run-id", "run-123"]).command == "validate"
    assert parser.parse_args(["status", "--run-id", "run-123"]).command == "status"
    assert parser.parse_args(["risk-summary"]).command == "risk-summary"


def test_parser_exposes_query_with_exact_filters():
    args = cli._parser().parse_args([
        "query", "--run-id", "run-123", "--node-type", "task_gate",
        "--node-status", "invalid",
    ])

    assert args.command == "query"
    assert args.node_type == "task_gate"
    assert args.node_status == "invalid"


def test_parser_exposes_compare_with_explicit_sides():
    args = cli._parser().parse_args([
        "compare", "--left-run-id", "run-before", "--right-run-id", "run-after",
    ])

    assert (args.command, args.left_run_id, args.right_run_id) == (
        "compare", "run-before", "run-after",
    )
    assert args.left_snapshot_fingerprint is None


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


def test_query_emits_read_only_query_envelope(tmp_path, monkeypatch, capsys):
    store = WorkflowStore(tmp_path)
    manifest = WorkflowManifest(
        MANIFEST_SCHEMA, "run-123", "docs/brief.md", "a" * 64, "main", "b" * 40,
        (), "2026-07-27T10:00:00+00:00", "c" * 64,
    )
    status = WorkflowStatus(
        STATUS_SCHEMA, "run-123", "created", "created",
        "2026-07-27T10:00:00+00:00", "2026-07-27T10:00:00+00:00", None,
        "fixture", None, 0,
    )
    store.create_run(manifest, status)
    GovernanceGraphBuilder(tmp_path).persist("run-123")
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    before = _tree_bytes(tmp_path)

    assert cli.main(["query", "--run-id", "run-123", "--node-type", "risk"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["schemaVersion"] == "nbs-governance-graph-cli-v1"
    assert payload["command"] == "query"
    assert payload["result"]["schemaVersion"] == "governance-graph-query-v1"
    assert _tree_bytes(tmp_path) == before


def test_query_schema_violation_is_invalid_result_envelope(tmp_path, monkeypatch, capsys):
    WorkflowStore(tmp_path)
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)

    assert cli.main(["query", "--run-id", "run-123", "--node-type", "made_up"]) == 2
    payload = json.loads(capsys.readouterr().out)

    assert payload["result"]["schemaVersion"] == "governance-graph-query-v1"
    assert payload["result"]["status"] == "invalid"


def test_compare_emits_read_only_comparison_envelope(tmp_path, monkeypatch, capsys):
    store = WorkflowStore(tmp_path)
    for run_id in ("run-left", "run-right"):
        store.create_run(
            WorkflowManifest(
                MANIFEST_SCHEMA, run_id, "docs/brief.md", "a" * 64, "main", "b" * 40,
                (), "2026-07-27T10:00:00+00:00", "c" * 64,
            ),
            WorkflowStatus(
                STATUS_SCHEMA, run_id, "created", "created",
                "2026-07-27T10:00:00+00:00", "2026-07-27T10:00:00+00:00", None,
                "fixture", None, 0,
            ),
        )
        GovernanceGraphBuilder(tmp_path).persist(run_id)
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    before = _tree_bytes(tmp_path)

    assert cli.main([
        "compare", "--left-run-id", "run-left", "--right-run-id", "run-right",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["schemaVersion"] == "nbs-governance-graph-cli-v1"
    assert payload["command"] == "compare"
    assert payload["result"]["schemaVersion"] == "governance-graph-comparison-v1"
    assert _tree_bytes(tmp_path) == before


def test_compare_missing_side_is_unavailable_and_does_not_write(tmp_path, monkeypatch, capsys):
    store = WorkflowStore(tmp_path)
    store.create_run(
        WorkflowManifest(
            MANIFEST_SCHEMA, "run-left", "docs/brief.md", "a" * 64, "main", "b" * 40,
            (), "2026-07-27T10:00:00+00:00", "c" * 64,
        ),
        WorkflowStatus(
            STATUS_SCHEMA, "run-left", "created", "created",
            "2026-07-27T10:00:00+00:00", "2026-07-27T10:00:00+00:00", None,
            "fixture", None, 0,
        ),
    )
    GovernanceGraphBuilder(tmp_path).persist("run-left")
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    before = _tree_bytes(tmp_path)

    assert cli.main([
        "compare", "--left-run-id", "run-left", "--right-run-id", "run-missing",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["result"]["status"] == "unavailable"
    assert _tree_bytes(tmp_path) == before


def test_compare_invalid_snapshot_fingerprint_returns_invalid_exit(tmp_path, monkeypatch, capsys):
    store = WorkflowStore(tmp_path)
    store.create_run(
        WorkflowManifest(
            MANIFEST_SCHEMA, "run-left", "docs/brief.md", "a" * 64, "main", "b" * 40,
            (), "2026-07-27T10:00:00+00:00", "c" * 64,
        ),
        WorkflowStatus(
            STATUS_SCHEMA, "run-left", "created", "created",
            "2026-07-27T10:00:00+00:00", "2026-07-27T10:00:00+00:00", None,
            "fixture", None, 0,
        ),
    )
    GovernanceGraphBuilder(tmp_path).persist("run-left")
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)

    assert cli.main([
        "compare", "--left-run-id", "run-left", "--right-run-id", "run-left",
        "--left-snapshot-fingerprint", "0" * 64,
    ]) == 2
    payload = json.loads(capsys.readouterr().out)

    assert payload["result"]["status"] == "invalid"


def test_risk_summary_reads_bridge_result_from_stdin(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(_comparison_payload())))

    assert cli.main(["risk-summary"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["schemaVersion"] == "nbs-governance-graph-cli-v1"
    assert payload["result"]["schemaVersion"] == "governance-graph-risk-summary-v1"


def test_risk_summary_rejects_control_plane_flags():
    try:
        cli._parser().parse_args(["risk-summary", "--run-id", "run-1"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("risk-summary accepted a control-plane flag")
