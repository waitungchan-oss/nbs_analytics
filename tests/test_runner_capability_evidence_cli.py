from __future__ import annotations

import json

import pytest

from backend.agents.evidence_models import canonical_fingerprint
from scripts.runner_capability_evidence import MAX_INPUT_BYTES, main


GIT_HEAD = "a" * 40
TASK_FINGERPRINT = "b" * 64


def _run(*, run_id: str, sequence: int, recall_mode: str, input_tokens: int = 1000) -> dict:
    raw = {
        "runId": run_id, "sequence": sequence, "recallMode": recall_mode, "gitHead": GIT_HEAD,
        "projectId": "nbs_analytics", "workspaceKind": "isolated_worktree", "workspaceFingerprint": "c" * 64,
        "taskFingerprint": TASK_FINGERPRINT, "briefFingerprint": "d" * 64, "allowedFilesFingerprint": "e" * 64,
        "commandsFingerprint": "f" * 64, "provider": "hermes", "model": "deepseek-v4-flash",
        "status": "completed", "cacheReplayDetected": False, "inputTokens": input_tokens, "outputTokens": 100,
        "p95Ms": 200, "provenanceCoverage": 1.0, "sensitiveCaptureCount": 0, "writerDisabled": True,
        "baselineUnchanged": True, "formalScopeUnchanged": True, "reviewNoRegression": True,
        "hermesNoRegression": True,
    }
    return {**raw, "runFingerprint": canonical_fingerprint(raw)}


def _write(root, relative: str, payload: object) -> None:
    path = root / ".nbs_agent_runtime" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _args(control: str = "runs/control/capability-input.json", treatment: str = "runs/treatment/capability-input.json", output: str = "runs/evidence/runner-capability-evidence.json") -> list[str]:
    return ["--control", control, "--treatment", treatment, "--git-head", GIT_HEAD, "--task-fingerprint", TASK_FINGERPRINT, "--output", output]


def _valid_pair(root) -> None:
    _write(root, "runs/control/capability-input.json", _run(run_id="control-001", sequence=1, recall_mode="off"))
    _write(root, "runs/treatment/capability-input.json", _run(run_id="treatment-002", sequence=2, recall_mode="on", input_tokens=700))


def test_cli_writes_stable_capability_evidence_only_to_requested_runtime_path(tmp_path):
    _valid_pair(tmp_path)

    assert main(_args(), project_root=tmp_path) == 0
    output = tmp_path / ".nbs_agent_runtime/runs/evidence/runner-capability-evidence.json"
    first = json.loads(output.read_text(encoding="utf-8"))
    assert first["result"] == "ready"
    assert main(_args(), project_root=tmp_path) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["evidenceId"] == first["evidenceId"]
    assert sorted(path.relative_to(tmp_path / ".nbs_agent_runtime").as_posix() for path in (tmp_path / ".nbs_agent_runtime").rglob("*") if path.is_file()) == [
        "runs/control/capability-input.json", "runs/evidence/runner-capability-evidence.json", "runs/treatment/capability-input.json"
    ]


@pytest.mark.parametrize("control", ["runs/missing.json", "/tmp/outside.json", "../outside.json"])
def test_cli_rejects_missing_absolute_and_out_of_root_inputs(tmp_path, control: str):
    _valid_pair(tmp_path)

    assert main(_args(control=control), project_root=tmp_path) == 2


def test_cli_rejects_symlinked_input_and_output(tmp_path):
    _valid_pair(tmp_path)
    runtime = tmp_path / ".nbs_agent_runtime"
    (runtime / "runs/link.json").symlink_to(runtime / "runs/control/capability-input.json")
    assert main(_args(control="runs/link.json"), project_root=tmp_path) == 2
    output = runtime / "runs/evidence/link.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.symlink_to(tmp_path / "outside.json")
    assert main(_args(output="runs/evidence/link.json"), project_root=tmp_path) == 2


def test_cli_rejects_oversized_malformed_and_unknown_schema_inputs(tmp_path):
    _valid_pair(tmp_path)
    runtime = tmp_path / ".nbs_agent_runtime"
    oversized = runtime / "runs/oversized.json"
    oversized.write_bytes(b"x" * (MAX_INPUT_BYTES + 1))
    assert main(_args(control="runs/oversized.json"), project_root=tmp_path) == 2
    malformed = runtime / "runs/malformed.json"
    malformed.write_text("{", encoding="utf-8")
    assert main(_args(control="runs/malformed.json"), project_root=tmp_path) == 2
    unknown = _run(run_id="unknown-001", sequence=1, recall_mode="off")
    unknown["rawPrompt"] = "must not be accepted"
    _write(tmp_path, "runs/unknown.json", unknown)
    assert main(_args(control="runs/unknown.json"), project_root=tmp_path) == 2


def test_cli_rejects_output_outside_runtime_root_without_creating_it(tmp_path):
    _valid_pair(tmp_path)
    outside = tmp_path / "outside.json"

    assert main(_args(output="../outside.json"), project_root=tmp_path) == 2
    assert not outside.exists()
