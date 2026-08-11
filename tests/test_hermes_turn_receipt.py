from __future__ import annotations

import json
from pathlib import Path

from backend.agents.evidence_models import canonical_fingerprint
from integrations.hermes_nbs_sidecar.plugin import activation_binding_fingerprint
from scripts import hermes_turn_receipt as producer
from scripts.hermes_runner_capability_hook import _activation_is_valid

HEAD = "0abf7965a6fb90cc6b6f76e07377e077bd1648f7"


def _turn_input(tmp_path, *, recall_mode: str = "on", sequence: int = 2, run_id: str = "run-treatment-live", session_id: str = "session-treatment-live") -> dict:
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    value = {
        "schemaVersion": "hermes-turn-receipt-input-v1",
        "manifestId": canonical_fingerprint({"a": 1}),
        "runId": run_id, "sessionId": session_id, "recallMode": recall_mode, "sequence": sequence,
        "gitHead": HEAD, "projectId": "nbs_analytics", "workspaceKind": "repo",
        "workspaceFingerprint": canonical_fingerprint({"projectRoot": str(root.resolve()), "projectId": "nbs_analytics", "workspaceKind": "repo"}),
        "taskFingerprint": "b" * 64, "briefFingerprint": "c" * 64,
        "allowedFilesFingerprint": "d" * 64, "commandsFingerprint": "e" * 64,
        "provider": "hermes", "model": "deepseek-v4-flash", "reasoningProfile": "max",
        "cleanWorktreeFingerprint": canonical_fingerprint({"gitHead": HEAD, "gitStatusPorcelain": ""}),
        "query": "review runtime", "sourceRefs": ["runs/live/memory-hints.json"],
        "activationReceipt": {
            "schemaVersion": "hermes-recall-activation-receipt-v1",
            "activationId": "", "recallMode": recall_mode,
            "status": "activated" if recall_mode == "on" else "disabled",
        },
    }
    value["activationReceipt"]["activationId"] = canonical_fingerprint({"manifestId": value["manifestId"], "runId": run_id, "sessionId": session_id, "recallMode": recall_mode, "status": value["activationReceipt"]["status"]})
    return value


def _write(root: Path, relative: str, value: dict) -> None:
    path = root / ".nbs_agent_runtime" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _fake_turn_runner(prompt_tokens: int, completion_tokens: int, latency_ms: int, content: str = "ok", *,
                      coverage: float = 1.0, source_count: int = 1, covered_count: int = 1,
                      sensitive: int = 0, response_id: str = "response-fake-001", prior_response_ids: list[str] | None = None):
    def runner(input_json, client_config):
        status = "activated" if input_json["recallMode"] == "on" else "disabled"
        return {
            "promptTokens": prompt_tokens, "outputTokens": completion_tokens, "latencyMs": latency_ms,
            "content": content, "provenanceCoverage": coverage, "provenanceSourceCount": source_count,
            "provenanceCoveredCount": covered_count, "sensitiveCaptureCount": sensitive,
            "responseId": response_id, "priorResponseIds": prior_response_ids or [],
            "activationReceipt": {
                "schemaVersion": "hermes-recall-activation-receipt-v1",
                "activationId": canonical_fingerprint({"manifestId": input_json["manifestId"], "runId": input_json["runId"], "sessionId": input_json["sessionId"], "recallMode": input_json["recallMode"], "status": status}),
                "recallMode": input_json["recallMode"], "status": status,
            },
        }
    return runner


def _args(turn: dict) -> list[str]:
    args = [
        "run",
        "--turn-input", "runs/live/turn-input.json",
        "--client-config", "runs/live/client-config.json",
        "--output", "runs/live/receipt.json",
    ]
    return [*args, "--sidecar-provider", "nbs_sidecar" if turn["recallMode"] == "on" else "disabled", *( ["--sidecar-envelope", "runs/live/activation-envelope.json", "--hints-path", "runs/live/memory-hints.json"] if turn["recallMode"] == "on" else [])]


def _prepare(root, monkeypatch, turn: dict, runner) -> None:
    _write(root, "runs/live/turn-input.json", turn)
    _write(root, "runs/live/memory-hints.json", {"schemaVersion": "test-source-ref"})
    _write(root, "runs/live/client-config.json", {
        "model": turn["model"], "timeout": 30, "prior_response_ids": [],
    })
    monkeypatch.setattr(producer, "_current_git_head", lambda project_root: HEAD)
    monkeypatch.setattr(producer, "_git_status_porcelain", lambda project_root: "")
    monkeypatch.setattr(producer, "_observed_activation", lambda turn_input, args, project_root: turn_input["activationReceipt"])


def test_client_config_rejects_artifact_credentials_and_non_live_endpoint():
    for config in (
        {"model": "deepseek-v4-flash", "timeout": 30, "prior_response_ids": [], "api_key": "forbidden"},
        {"model": "deepseek-v4-flash", "timeout": 30, "prior_response_ids": [], "base_url": "https://example.invalid"},
    ):
        try:
            producer._validate_client_config(config)
        except producer.RunnerCapabilityEvidenceError:
            pass
        else:
            raise AssertionError("credential-bearing client config must fail closed")


def test_receipt_producer_uses_real_usage_and_writes_canonical_bound_receipt(tmp_path, monkeypatch):
    turn = _turn_input(tmp_path)
    root = tmp_path / "proj"
    _prepare(root, monkeypatch, turn, _fake_turn_runner(1000, 200, 150))
    # Import the transport-level real path too, but use the injected fake turn runner.
    assert producer.main(_args(turn), project_root=root, turn_runner=_fake_turn_runner(1000, 200, 150)) == 0
    receipt = json.loads((root / ".nbs_agent_runtime/runs/live/receipt.json").read_text(encoding="utf-8"))
    assert receipt["schemaVersion"] == "hermes-runner-capability-receipt-v1"
    assert receipt["provider"] == "hermes" and receipt["model"] == "deepseek-v4-flash"
    assert receipt["inputTokens"] == 1000 and receipt["outputTokens"] == 200 and receipt["p95Ms"] == 150
    assert receipt["provenanceCoverage"] == 1.0 and receipt["sensitiveCaptureCount"] == 0
    assert receipt["cacheReplayDetected"] is False
    assert receipt["reasoningProfile"] == "max"
    assert receipt["cleanWorktreeFingerprint"] == turn["cleanWorktreeFingerprint"]
    for key in ("gitHead", "projectId", "workspaceKind", "workspaceFingerprint", "taskFingerprint", "briefFingerprint", "allowedFilesFingerprint", "commandsFingerprint"):
        assert receipt[key] == turn[key]
    assert receipt["writerDisabled"] is True
    # activationReceipt must be canonical-bound.
    expected_id = canonical_fingerprint({"manifestId": receipt["manifestId"], "runId": receipt["runId"], "sessionId": receipt["sessionId"], "recallMode": "on", "status": "activated"})
    assert receipt["activationReceipt"]["activationId"] == expected_id
    assert receipt["activationReceipt"]["schemaVersion"] == "hermes-recall-activation-receipt-v1"
    assert receipt["activationReceipt"]["status"] == "activated"


def test_receipt_producer_writes_a_valid_runner_capability_run(tmp_path, monkeypatch):
    turn = _turn_input(tmp_path)
    root = tmp_path / "proj"
    _prepare(root, monkeypatch, turn, _fake_turn_runner(1000, 200, 150))
    assert producer.main(_args(turn), project_root=root, turn_runner=_fake_turn_runner(1000, 200, 150)) == 0
    receipt = json.loads((root / ".nbs_agent_runtime/runs/live/receipt.json").read_text(encoding="utf-8"))
    run = producer.build_run(receipt, turn)
    # The run round-trips through RunnerCapabilityRun (bounded, canonically fingerprinted).
    from backend.agents.runner_capability_evidence import RunnerCapabilityRun
    loaded = RunnerCapabilityRun(**{
        "run_id": run["runId"], "sequence": run["sequence"], "recall_mode": run["recallMode"],
        "git_head": run["gitHead"], "project_id": run["projectId"], "workspace_kind": run["workspaceKind"],
        "workspace_fingerprint": run["workspaceFingerprint"], "task_fingerprint": run["taskFingerprint"],
        "brief_fingerprint": run["briefFingerprint"], "allowed_files_fingerprint": run["allowedFilesFingerprint"],
        "commands_fingerprint": run["commandsFingerprint"], "provider": run["provider"], "model": run["model"],
        "reasoning_profile": run["reasoningProfile"], "clean_worktree_fingerprint": run["cleanWorktreeFingerprint"],
        "status": run["status"], "cache_replay_detected": run["cacheReplayDetected"], "input_tokens": run["inputTokens"],
        "output_tokens": run["outputTokens"], "p95_ms": run["p95Ms"], "provenance_coverage": run["provenanceCoverage"],
        "sensitive_capture_count": run["sensitiveCaptureCount"], "writer_disabled": run["writerDisabled"],
        "baseline_unchanged": run["baselineUnchanged"], "formal_scope_unchanged": run["formalScopeUnchanged"],
        "review_no_regression": run["reviewNoRegression"], "hermes_no_regression": run["hermesNoRegression"],
    })
    assert loaded.status == "completed"


def test_receipt_producer_fails_closed_on_missing_usage(tmp_path, monkeypatch):
    turn = _turn_input(tmp_path)
    root = tmp_path / "proj"
    _prepare(root, monkeypatch, turn, _fake_turn_runner(0, 0, 150))
    assert producer.main(_args(turn), project_root=root, turn_runner=_fake_turn_runner(0, 0, 150)) == 2
    assert not (root / ".nbs_agent_runtime/runs/live/receipt.json").exists()


def test_receipt_producer_fails_closed_on_zero_prompt_tokens(tmp_path, monkeypatch):
    turn = _turn_input(tmp_path)
    root = tmp_path / "proj"
    _prepare(root, monkeypatch, turn, _fake_turn_runner(0, 200, 150))
    assert producer.main(_args(turn), project_root=root, turn_runner=_fake_turn_runner(0, 200, 150)) == 2
    assert not (root / ".nbs_agent_runtime/runs/live/receipt.json").exists()


def test_receipt_producer_fails_closed_on_empty_content(tmp_path, monkeypatch):
    turn = _turn_input(tmp_path)
    root = tmp_path / "proj"
    _prepare(root, monkeypatch, turn, _fake_turn_runner(1000, 200, 150, content=""))
    assert producer.main(_args(turn), project_root=root, turn_runner=_fake_turn_runner(1000, 200, 150, content="")) == 2
    assert not (root / ".nbs_agent_runtime/runs/live/receipt.json").exists()


def test_receipt_producer_fails_closed_on_unmeasurable_latency(tmp_path, monkeypatch):
    turn = _turn_input(tmp_path)
    root = tmp_path / "proj"
    _prepare(root, monkeypatch, turn, _fake_turn_runner(1000, 200, -1))
    assert producer.main(_args(turn), project_root=root, turn_runner=_fake_turn_runner(1000, 200, -1)) == 2
    assert not (root / ".nbs_agent_runtime/runs/live/receipt.json").exists()


def test_receipt_producer_rejects_out_of_root_path(tmp_path, monkeypatch):
    turn = _turn_input(tmp_path)
    root = tmp_path / "proj"
    _prepare(root, monkeypatch, turn, _fake_turn_runner(1000, 200, 150))
    bad_args = _args(turn)
    bad_args[bad_args.index("--output") + 1] = "../outside.json"
    assert producer.main(bad_args, project_root=root, turn_runner=_fake_turn_runner(1000, 200, 150)) == 2


def test_receipt_producer_accepts_recall_off_control_arm_as_real_turn(tmp_path, monkeypatch):
    turn = _turn_input(tmp_path, recall_mode="off", sequence=1, run_id="run-control-live", session_id="session-control-live")
    root = tmp_path / "proj"
    _prepare(root, monkeypatch, turn, _fake_turn_runner(1200, 120, 180))
    assert producer.main(_args(turn), project_root=root, turn_runner=_fake_turn_runner(1200, 120, 180)) == 0
    receipt = json.loads((root / ".nbs_agent_runtime/runs/live/receipt.json").read_text(encoding="utf-8"))
    assert receipt["recallMode"] == "off" and receipt["sequence"] == 1
    assert receipt["activationReceipt"]["recallMode"] == "off"
    assert receipt["activationReceipt"]["status"] == "disabled"


def test_receipt_producer_fails_closed_on_invalid_identity_or_sequence(tmp_path, monkeypatch):
    turn = _turn_input(tmp_path)
    bad = dict(turn)
    bad["model"] = "some-other-model"
    root = tmp_path / "proj"
    _write(root, "runs/live/turn-input.json", bad)
    _write(root, "runs/live/client-config.json", {"model": turn["model"], "timeout": 30, "prior_response_ids": []})
    monkeypatch.setattr(producer, "_current_git_head", lambda project_root: HEAD)
    monkeypatch.setattr(producer, "_git_status_porcelain", lambda project_root: "")
    assert producer.main(_args(turn), project_root=root, turn_runner=_fake_turn_runner(1000, 200, 150)) == 2
    # sequence out of range
    turn2 = dict(_turn_input(tmp_path))
    turn2["sequence"] = 3
    _write(root, "runs/live/turn-input.json", turn2)
    assert producer.main(_args(turn2), project_root=root, turn_runner=_fake_turn_runner(1000, 200, 150)) == 2
    assert not (root / ".nbs_agent_runtime/runs/live/receipt.json").exists()


def _bare_runner(extra: dict):
    def runner(input_json, client_config):
        return {"promptTokens": 1000, "outputTokens": 200, "latencyMs": 150, "content": "ok", **extra}
    return runner


def test_receipt_producer_fails_closed_when_provenance_evidence_missing(tmp_path, monkeypatch):
    turn = _turn_input(tmp_path)
    root = tmp_path / "proj"
    _prepare(root, monkeypatch, turn, _bare_runner({}))
    assert producer.main(_args(turn), project_root=root, turn_runner=_bare_runner({})) == 2
    assert not (root / ".nbs_agent_runtime/runs/live/receipt.json").exists()


def test_receipt_producer_fails_closed_without_lifecycle_activation_or_resolvable_source(tmp_path, monkeypatch):
    turn = _turn_input(tmp_path)
    root = tmp_path / "proj"
    _prepare(root, monkeypatch, turn, _bare_runner({"provenanceCoverage": 1.0, "provenanceSourceCount": 1, "provenanceCoveredCount": 1, "sensitiveCaptureCount": 0, "responseId": "response-1", "priorResponseIds": []}))
    assert producer.main(_args(turn), project_root=root, turn_runner=_bare_runner({"provenanceCoverage": 1.0, "provenanceSourceCount": 1, "provenanceCoveredCount": 1, "sensitiveCaptureCount": 0, "responseId": "response-1", "priorResponseIds": []})) == 2
    (root / ".nbs_agent_runtime/runs/live/memory-hints.json").unlink()
    assert producer.main(_args(turn), project_root=root, turn_runner=_fake_turn_runner(1000, 200, 150)) == 2


def test_receipt_producer_rejects_invalid_lifecycle_binding_before_turn_runner(tmp_path, monkeypatch):
    turn = _turn_input(tmp_path)
    turn["activationReceipt"]["activationId"] = "0" * 64
    root = tmp_path / "proj"
    _prepare(root, monkeypatch, turn, _fake_turn_runner(1000, 200, 150))
    called = False

    def must_not_run(input_json, client_config):
        nonlocal called
        called = True
        raise AssertionError("transport must not be invoked")

    assert producer.main(_args(turn), project_root=root, turn_runner=must_not_run) == 2
    assert called is False


def test_receipt_producer_fails_closed_when_coverage_not_full(tmp_path, monkeypatch):
    turn = _turn_input(tmp_path)
    root = tmp_path / "proj"
    _prepare(root, monkeypatch, turn, _fake_turn_runner(1000, 200, 150, coverage=0.5, source_count=2, covered_count=1))
    assert producer.main(_args(turn), project_root=root, turn_runner=_fake_turn_runner(1000, 200, 150, coverage=0.5, source_count=2, covered_count=1)) == 2
    assert not (root / ".nbs_agent_runtime/runs/live/receipt.json").exists()


def test_receipt_producer_fails_closed_when_coverage_mismatches_counts(tmp_path, monkeypatch):
    turn = _turn_input(tmp_path)
    root = tmp_path / "proj"
    _prepare(root, monkeypatch, turn, _fake_turn_runner(1000, 200, 150, coverage=1.0, source_count=2, covered_count=1))
    assert producer.main(_args(turn), project_root=root, turn_runner=_fake_turn_runner(1000, 200, 150, coverage=1.0, source_count=2, covered_count=1)) == 2
    assert not (root / ".nbs_agent_runtime/runs/live/receipt.json").exists()


def test_receipt_producer_fails_closed_when_sensitive_capture_nonzero(tmp_path, monkeypatch):
    turn = _turn_input(tmp_path)
    root = tmp_path / "proj"
    _prepare(root, monkeypatch, turn, _fake_turn_runner(1000, 200, 150, sensitive=1))
    assert producer.main(_args(turn), project_root=root, turn_runner=_fake_turn_runner(1000, 200, 150, sensitive=1)) == 2
    assert not (root / ".nbs_agent_runtime/runs/live/receipt.json").exists()


def test_receipt_producer_derives_replay_from_response_identity(tmp_path, monkeypatch):
    turn = _turn_input(tmp_path)
    root = tmp_path / "proj"
    _prepare(root, monkeypatch, turn, _fake_turn_runner(1000, 200, 150, prior_response_ids=["response-fake-001"]))
    assert producer.main(_args(turn), project_root=root, turn_runner=_fake_turn_runner(1000, 200, 150, prior_response_ids=["response-fake-001"])) == 2
    assert not (root / ".nbs_agent_runtime/runs/live/receipt.json").exists()


def test_receipt_producer_fails_closed_when_provenance_counts_absent(tmp_path, monkeypatch):
    turn = _turn_input(tmp_path)
    root = tmp_path / "proj"
    _prepare(root, monkeypatch, turn, _bare_runner({"provenanceCoverage": 1.0, "sensitiveCaptureCount": 0, "responseId": "response-1", "priorResponseIds": []}))
    assert producer.main(_args(turn), project_root=root, turn_runner=_bare_runner({"provenanceCoverage": 1.0, "sensitiveCaptureCount": 0, "responseId": "response-1", "priorResponseIds": []})) == 2


def test_receipt_producer_rejects_medium_or_wrong_clean_worktree_identity(tmp_path, monkeypatch):
    turn = _turn_input(tmp_path)
    root = tmp_path / "proj"
    turn["reasoningProfile"] = "medium"
    _prepare(root, monkeypatch, turn, _fake_turn_runner(1000, 200, 150))
    assert producer.main(_args(turn), project_root=root, turn_runner=_fake_turn_runner(1000, 200, 150)) == 2
    turn = _turn_input(tmp_path)
    turn["cleanWorktreeFingerprint"] = "0" * 64
    _prepare(root, monkeypatch, turn, _fake_turn_runner(1000, 200, 150))
    assert producer.main(_args(turn), project_root=root, turn_runner=_fake_turn_runner(1000, 200, 150)) == 2
    assert not (root / ".nbs_agent_runtime/runs/live/receipt.json").exists()
