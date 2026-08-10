from __future__ import annotations

import json

from backend.agents.evidence_models import canonical_fingerprint
from integrations.hermes_nbs_sidecar.plugin import activation_binding_fingerprint
from backend.agents.memory_sidecar_hint_models import MemoryHint, MemoryHints
from scripts import hermes_sidecar_activation as bridge


HEAD = "a" * 40


def _manifest(root) -> dict:
    value = {
        "schemaVersion": "hermes-runner-capability-manifest-v1", "recallMode": "on", "sequence": 2,
        "gitHead": HEAD, "projectId": "nbs_analytics", "workspaceKind": "repo", "workspaceFingerprint": canonical_fingerprint({"projectRoot": str(root.resolve()), "projectId": "nbs_analytics", "workspaceKind": "repo"}),
        "taskFingerprint": "c" * 64, "briefFingerprint": "d" * 64, "allowedFilesFingerprint": "e" * 64,
        "commandsFingerprint": "f" * 64, "provider": "hermes", "model": "deepseek-v4-flash",
        "reasoning": "medium", "writerDisabled": True,
    }
    return {**value, "manifestId": canonical_fingerprint(value)}


def _receipt(manifest: dict) -> dict:
    run_id, session_id = "run-treatment-002", "session-treatment-002"
    return {
        "schemaVersion": "hermes-runner-capability-receipt-v1", "manifestId": manifest["manifestId"],
        "runId": run_id, "sessionId": session_id, "provider": "hermes", "model": "deepseek-v4-flash",
        "reasoning": "medium", "recallMode": "on", "sequence": 2, "status": "completed",
        "inputTokens": 700, "outputTokens": 100, "p95Ms": 200, "provenanceCoverage": 1.0,
        "sensitiveCaptureCount": 0, "cacheReplayDetected": False, "writerDisabled": True,
        "baselineUnchanged": True, "formalScopeUnchanged": True, "reviewNoRegression": True,
        "hermesNoRegression": True,
        "activationReceipt": {"schemaVersion": "hermes-recall-activation-receipt-v1", "activationId": canonical_fingerprint({"manifestId": manifest["manifestId"], "runId": run_id, "sessionId": session_id, "recallMode": "on", "status": "activated"}), "recallMode": "on", "status": "activated"},
    }


def _write(root, relative: str, value: dict) -> None:
    path = root / ".nbs_agent_runtime" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _args() -> list[str]:
    return ["create", "--manifest", "runs/run-treatment/manifest.json", "--receipt", "runs/run-treatment/receipt.json", "--query", "review runtime", "--hints-output", "runs/run-treatment/memory-hints.json", "--output", "runs/run-treatment/sidecar-activation.json"]


def _prepare(root, monkeypatch) -> None:
    manifest = _manifest(root)
    _write(root, "runs/run-treatment/manifest.json", manifest)
    _write(root, "runs/run-treatment/receipt.json", _receipt(manifest))
    monkeypatch.setattr(bridge, "_current_git_head", lambda project_root: HEAD)
    monkeypatch.setattr(bridge, "_git_status_porcelain", lambda project_root: "")


def test_create_writes_bound_envelope_and_bounded_hints(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)

    assert bridge.main(_args(), project_root=tmp_path) == 0
    envelope = json.loads((tmp_path / ".nbs_agent_runtime/runs/run-treatment/sidecar-activation.json").read_text(encoding="utf-8"))
    hints = json.loads((tmp_path / ".nbs_agent_runtime/runs/run-treatment/memory-hints.json").read_text(encoding="utf-8"))
    assert envelope["activationId"] == activation_binding_fingerprint(envelope)
    assert envelope["reasoning"] == "medium"
    assert hints["schemaVersion"] == "memory-hints-v1"
    assert len(json.dumps(hints).encode("utf-8")) <= 6000


def test_create_fails_closed_for_dirty_head_identity_or_activation_mismatch(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(bridge, "_git_status_porcelain", lambda project_root: " M file.py\n")
    assert bridge.main(_args(), project_root=tmp_path) == 2
    monkeypatch.setattr(bridge, "_git_status_porcelain", lambda project_root: "")
    monkeypatch.setattr(bridge, "_current_git_head", lambda project_root: "0" * 40)
    assert bridge.main(_args(), project_root=tmp_path) == 2
    monkeypatch.setattr(bridge, "_current_git_head", lambda project_root: HEAD)
    receipt = _receipt(_manifest(tmp_path))
    receipt["activationReceipt"]["activationId"] = "0" * 64
    _write(tmp_path, "runs/run-treatment/receipt.json", receipt)
    assert bridge.main(_args(), project_root=tmp_path) == 2


def test_create_rejects_path_escape_symlink_and_malformed_inputs(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)
    assert bridge.main(_args()[:-2] + ["--output", "../outside.json"], project_root=tmp_path) == 2
    receipt = tmp_path / ".nbs_agent_runtime/runs/run-treatment/receipt.json"
    receipt.unlink()
    receipt.symlink_to(tmp_path / "outside.json")
    assert bridge.main(_args(), project_root=tmp_path) == 2


def _probe_envelope(root, *, session_id: str = "session-probe") -> dict:
    value = {
        "schemaVersion": "hermes-nbs-sidecar-activation-v1", "manifestId": "a" * 64, "activationId": "",
        "sessionId": session_id, "recallMode": "on", "gitHead": HEAD, "projectId": "nbs_analytics",
        "workspaceKind": "repo", "workspaceFingerprint": canonical_fingerprint({"projectRoot": str(root.resolve()), "projectId": "nbs_analytics", "workspaceKind": "repo"}),
        "taskFingerprint": "b" * 64, "briefFingerprint": "c" * 64, "allowedFilesFingerprint": "d" * 64,
        "commandsFingerprint": "e" * 64, "provider": "hermes", "model": "deepseek-v4-flash", "reasoning": "medium",
        "hintsPath": "runs/run-probe/memory-hints.json", "writerDisabled": True,
    }
    value["activationId"] = activation_binding_fingerprint(value)
    return value


def _probe_files(root, envelope: dict) -> None:
    _write(root, "runs/run-probe/sidecar-activation.json", envelope)
    hints = MemoryHints(canonical_fingerprint({"query": "review runtime"}), "ready", (MemoryHint("f" * 64, "Bounded probe hint.", ("verification.json",), "fresh", "high", ("a" * 64,)),))
    _write(root, envelope["hintsPath"], hints.to_dict())


def _fake_hermes_source(root) -> str:
    # Mirror the real Hermes MemoryProvider ABC abstract surface (see
    # agent/memory_provider.py): name/is_available/initialize/get_tool_schemas
    # are abstract; prefetch/sync_turn are concrete.  A provider that drops any
    # of the abstract members must fail instantiation in the probe test.
    source = root / "hermes-source" / "agent"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("", encoding="utf-8")
    (source / "memory_provider.py").write_text(
        "from abc import ABC, abstractmethod\n"
        "class MemoryProvider(ABC):\n"
        "    @property\n"
        "    @abstractmethod\n"
        "    def name(self) -> str:\n"
        "        raise NotImplementedError\n"
        "    @abstractmethod\n"
        "    def is_available(self) -> bool:\n"
        "        raise NotImplementedError\n"
        "    @abstractmethod\n"
        "    def initialize(self, session_id: str, **kwargs) -> None:\n"
        "        raise NotImplementedError\n"
        "    @abstractmethod\n"
        "    def get_tool_schemas(self) -> list:\n"
        "        raise NotImplementedError\n"
        "    def prefetch(self, query: str, *, session_id: str = '') -> str:\n"
        "        return ''\n"
        "    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = '', messages=None) -> None:\n"
        "        return None\n",
        encoding="utf-8",
    )
    return str(source.parent)


def test_probe_loads_memory_provider_and_writes_bounded_telemetry(tmp_path, monkeypatch):
    envelope = _probe_envelope(tmp_path)
    _probe_files(tmp_path, envelope)
    monkeypatch.setattr(bridge, "_current_git_head", lambda project_root: HEAD)
    monkeypatch.setattr(bridge, "_git_status_porcelain", lambda project_root: "")

    assert bridge.main(["probe", "--envelope", "runs/run-probe/sidecar-activation.json", "--query", "review runtime", "--hermes-source-root", _fake_hermes_source(tmp_path), "--output", "runs/run-probe/probe.json"], project_root=tmp_path) == 0
    telemetry = json.loads((tmp_path / ".nbs_agent_runtime/runs/run-probe/probe.json").read_text(encoding="utf-8"))
    assert telemetry["provider"] == "hermes"
    assert telemetry["prefetchBytes"] > 0
    assert telemetry["writerDisabled"] is True


def test_probe_fails_closed_for_wrong_session_or_invalid_envelope(tmp_path, monkeypatch):
    envelope = _probe_envelope(tmp_path, session_id="session-probe")
    _probe_files(tmp_path, envelope)
    monkeypatch.setattr(bridge, "_current_git_head", lambda project_root: HEAD)
    monkeypatch.setattr(bridge, "_git_status_porcelain", lambda project_root: "")
    args = ["probe", "--envelope", "runs/run-probe/sidecar-activation.json", "--query", "review runtime", "--hermes-source-root", _fake_hermes_source(tmp_path), "--output", "runs/run-probe/probe.json", "--session-id", "wrong-session"]
    assert bridge.main(args, project_root=tmp_path) == 2
    envelope["activationId"] = "0" * 64
    _write(tmp_path, "runs/run-probe/sidecar-activation.json", envelope)
    assert bridge.main(args[:-2], project_root=tmp_path) == 2
