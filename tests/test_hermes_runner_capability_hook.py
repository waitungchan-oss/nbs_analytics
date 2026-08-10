from __future__ import annotations

import json

import pytest

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.runner_capability_evidence import RunnerCapabilityRun
from scripts import hermes_runner_capability_hook as hook


HEAD = "a" * 40
TASK = "b" * 64
WORKSPACE = "f" * 64


def _prepare_args(*, mode: str = "off", sequence: str = "1", output: str = "runs/control/manifest.json") -> list[str]:
    return [
        "prepare", "--recall-mode", mode, "--sequence", sequence, "--git-head", HEAD,
        "--project-id", "nbs_analytics", "--workspace-kind", "isolated_worktree", "--workspace-fingerprint", WORKSPACE,
        "--task-fingerprint", TASK, "--brief-fingerprint", "c" * 64,
        "--allowed-files-fingerprint", "d" * 64, "--commands-fingerprint", "e" * 64,
        "--output", output,
    ]


def _prepare(tmp_path, monkeypatch, *, mode: str = "off", sequence: str = "1", output: str = "runs/control/manifest.json") -> dict:
    monkeypatch.setattr(hook, "_current_git_head", lambda project_root: HEAD)
    monkeypatch.setattr(hook, "_git_status_porcelain", lambda project_root: "")
    assert hook.main(_prepare_args(mode=mode, sequence=sequence, output=output), project_root=tmp_path) == 0
    return json.loads((tmp_path / ".nbs_agent_runtime" / output).read_text(encoding="utf-8"))


def _receipt(manifest: dict, *, run_id: str = "run-control-001", session_id: str = "session-control-001", activation_receipt: object = None) -> dict:
    return {
        "schemaVersion": hook.RECEIPT_SCHEMA,
        "manifestId": manifest["manifestId"], "runId": run_id, "sessionId": session_id,
        "provider": "hermes", "model": "deepseek-v4-flash", "recallMode": manifest["recallMode"],
        "reasoning": "medium",
        "sequence": manifest["sequence"], "status": "completed", "inputTokens": 1000,
        "outputTokens": 100, "p95Ms": 200, "provenanceCoverage": 1.0,
        "sensitiveCaptureCount": 0, "cacheReplayDetected": False, "writerDisabled": True,
        "baselineUnchanged": True, "formalScopeUnchanged": True, "reviewNoRegression": True,
        "hermesNoRegression": True, "activationReceipt": activation_receipt,
    }


def _activation(manifest: dict, *, run_id: str, session_id: str) -> dict:
    return {
        "schemaVersion": hook.ACTIVATION_SCHEMA,
        "activationId": canonical_fingerprint({"manifestId": manifest["manifestId"], "runId": run_id, "sessionId": session_id, "recallMode": "on", "status": "activated"}),
        "recallMode": "on",
        "status": "activated",
    }


def _record_args(manifest: str, receipt: str, output: str) -> list[str]:
    return ["record", "--manifest", manifest, "--receipt", receipt, "--output", output]


def _write(tmp_path, relative: str, payload: object) -> None:
    path = tmp_path / ".nbs_agent_runtime" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_prepare_binds_live_head_and_writes_bounded_manifest(tmp_path, monkeypatch):
    manifest = _prepare(tmp_path, monkeypatch)

    assert manifest["gitHead"] == HEAD
    assert manifest["provider"] == "hermes"
    assert manifest["model"] == "deepseek-v4-flash"
    assert manifest["reasoning"] == "medium"
    assert manifest["workspaceFingerprint"] == WORKSPACE
    assert len(manifest["manifestId"]) == 64


def test_prepare_rejects_head_mismatch_unsafe_output_and_invalid_mode_sequence(tmp_path, monkeypatch):
    monkeypatch.setattr(hook, "_current_git_head", lambda project_root: "0" * 40)
    monkeypatch.setattr(hook, "_git_status_porcelain", lambda project_root: "")
    assert hook.main(_prepare_args(), project_root=tmp_path) == 2
    monkeypatch.setattr(hook, "_current_git_head", lambda project_root: HEAD)
    monkeypatch.setattr(hook, "_git_status_porcelain", lambda project_root: " M backend/agents/example.py\n")
    assert hook.main(_prepare_args(), project_root=tmp_path) == 2
    monkeypatch.setattr(hook, "_git_status_porcelain", lambda project_root: "")
    assert hook.main(_prepare_args(output="../outside.json"), project_root=tmp_path) == 2
    with pytest.raises(SystemExit):
        hook.main(_prepare_args(mode="invalid"), project_root=tmp_path)
    with pytest.raises(SystemExit):
        hook.main(_prepare_args(sequence="3"), project_root=tmp_path)
    args = _prepare_args()
    workspace_index = args.index("--workspace-fingerprint")
    with pytest.raises(SystemExit):
        hook.main(args[:workspace_index] + args[workspace_index + 2:], project_root=tmp_path)
    assert hook.main(_prepare_args()[:-2] + ["--workspace-fingerprint", "short", "--output", "runs/control/manifest.json"], project_root=tmp_path) == 2


def test_record_emits_exact_runner_capability_run_with_stable_identity(tmp_path, monkeypatch):
    manifest = _prepare(tmp_path, monkeypatch)
    receipt = _receipt(manifest)
    _write(tmp_path, "runs/control/receipt.json", receipt)

    assert hook.main(_record_args("runs/control/manifest.json", "runs/control/receipt.json", "runs/control/capability-input.json"), project_root=tmp_path) == 0
    payload = json.loads((tmp_path / ".nbs_agent_runtime/runs/control/capability-input.json").read_text(encoding="utf-8"))
    run = RunnerCapabilityRun.from_dict(payload)
    assert run.run_id == "run-control-001"
    assert run.workspace_fingerprint == WORKSPACE
    assert run.to_dict() == payload
    assert hook.main(_record_args("runs/control/manifest.json", "runs/control/receipt.json", "runs/control/capability-input.json"), project_root=tmp_path) == 0
    assert json.loads((tmp_path / ".nbs_agent_runtime/runs/control/capability-input.json").read_text(encoding="utf-8")) == payload
    assert not {"rawPrompt", "rawModelOutput", "credentials", "absolutePath", "runnerCommand", "rawHints"} & set(payload)


def test_record_blocks_recall_on_without_explicit_activation_receipt(tmp_path, monkeypatch):
    manifest = _prepare(tmp_path, monkeypatch, mode="on", sequence="2", output="runs/treatment/manifest.json")
    _write(tmp_path, "runs/treatment/receipt.json", _receipt(manifest, run_id="run-treatment-002", session_id="session-treatment-002"))

    assert hook.main(_record_args("runs/treatment/manifest.json", "runs/treatment/receipt.json", "runs/treatment/capability-input.json"), project_root=tmp_path) == 0
    payload = json.loads((tmp_path / ".nbs_agent_runtime/runs/treatment/capability-input.json").read_text(encoding="utf-8"))
    assert payload["status"] == "incomplete"


def test_record_blocks_forged_activation_and_accepts_canonical_activation(tmp_path, monkeypatch):
    manifest = _prepare(tmp_path, monkeypatch, mode="on", sequence="2", output="runs/treatment/manifest.json")
    run_id, session_id = "run-treatment-002", "session-treatment-002"
    forged = _receipt(manifest, run_id=run_id, session_id=session_id, activation_receipt={"schemaVersion": hook.ACTIVATION_SCHEMA, "activationId": "0" * 64, "recallMode": "on", "status": "activated"})
    _write(tmp_path, "runs/treatment/receipt.json", forged)
    assert hook.main(_record_args("runs/treatment/manifest.json", "runs/treatment/receipt.json", "runs/treatment/capability-input.json"), project_root=tmp_path) == 0
    assert json.loads((tmp_path / ".nbs_agent_runtime/runs/treatment/capability-input.json").read_text(encoding="utf-8"))["status"] == "incomplete"
    _write(tmp_path, "runs/treatment/receipt.json", _receipt(manifest, run_id=run_id, session_id=session_id, activation_receipt=_activation(manifest, run_id=run_id, session_id=session_id)))
    assert hook.main(_record_args("runs/treatment/manifest.json", "runs/treatment/receipt.json", "runs/treatment/capability-input.json"), project_root=tmp_path) == 0
    assert json.loads((tmp_path / ".nbs_agent_runtime/runs/treatment/capability-input.json").read_text(encoding="utf-8"))["status"] == "completed"


def test_record_rejects_malformed_raw_or_mismatched_receipt_and_symlink_paths(tmp_path, monkeypatch):
    manifest = _prepare(tmp_path, monkeypatch)
    _write(tmp_path, "runs/control/receipt.json", "{")
    assert hook.main(_record_args("runs/control/manifest.json", "runs/control/receipt.json", "runs/control/out.json"), project_root=tmp_path) == 2
    bad = _receipt(manifest)
    bad["rawPrompt"] = "not allowed"
    _write(tmp_path, "runs/control/receipt.json", bad)
    assert hook.main(_record_args("runs/control/manifest.json", "runs/control/receipt.json", "runs/control/out.json"), project_root=tmp_path) == 2
    link = tmp_path / ".nbs_agent_runtime/runs/control/link.json"
    link.symlink_to(tmp_path / ".nbs_agent_runtime/runs/control/manifest.json")
    assert hook.main(_record_args("runs/control/link.json", "runs/control/receipt.json", "runs/control/out.json"), project_root=tmp_path) == 2


def test_record_rejects_out_of_bounds_metrics_and_wrong_identity(tmp_path, monkeypatch):
    manifest = _prepare(tmp_path, monkeypatch)
    receipt = _receipt(manifest)
    receipt["inputTokens"] = 10_000_001
    _write(tmp_path, "runs/control/receipt.json", receipt)
    assert hook.main(_record_args("runs/control/manifest.json", "runs/control/receipt.json", "runs/control/out.json"), project_root=tmp_path) == 2


def test_record_rejects_reasoning_mismatch(tmp_path, monkeypatch):
    manifest = _prepare(tmp_path, monkeypatch)
    receipt = _receipt(manifest)
    receipt["reasoning"] = "high"
    _write(tmp_path, "runs/control/receipt.json", receipt)

    assert hook.main(_record_args("runs/control/manifest.json", "runs/control/receipt.json", "runs/control/out.json"), project_root=tmp_path) == 2
    receipt = _receipt(manifest)
    receipt["manifestId"] = canonical_fingerprint({"wrong": True})
    _write(tmp_path, "runs/control/receipt.json", receipt)
    assert hook.main(_record_args("runs/control/manifest.json", "runs/control/receipt.json", "runs/control/out.json"), project_root=tmp_path) == 2
