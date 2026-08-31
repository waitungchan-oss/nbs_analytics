from __future__ import annotations

import json
from pathlib import Path
import sys

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.memory_sidecar_hint_models import MemoryHints
from integrations.hermes_nbs_sidecar.plugin import activation_binding_fingerprint
from scripts.hermes_isolated_profile import IsolatedHermesProfile


HEAD = "0abf7965a6fb90cc6b6f76e07377e077bd1648f7"


def _manifest(project_root: Path) -> dict[str, object]:
    unsigned = {
        "schemaVersion": "hermes-runner-capability-manifest-v1", "recallMode": "off", "sequence": 1,
        "gitHead": HEAD, "projectId": "nbs_analytics", "workspaceKind": "repo",
        "workspaceFingerprint": canonical_fingerprint({"projectRoot": str(project_root.resolve()), "projectId": "nbs_analytics", "workspaceKind": "repo"}), "taskFingerprint": "b" * 64, "briefFingerprint": "c" * 64,
        "allowedFilesFingerprint": "d" * 64, "commandsFingerprint": "e" * 64,
        "provider": "hermes", "model": "deepseek-v4-flash", "reasoningProfile": "max",
        "cleanWorktreeFingerprint": canonical_fingerprint({"gitHead": HEAD, "gitStatusPorcelain": ""}),
        "writerDisabled": True,
    }
    return {**unsigned, "manifestId": canonical_fingerprint(unsigned)}


def _profile(tmp_path: Path) -> IsolatedHermesProfile:
    home = tmp_path / ".nbs_agent_runtime" / "live-ab" / "acceptance-1" / "hermes-home"
    home.mkdir(parents=True)
    (home / "plugins" / "nbs_sidecar").mkdir(parents=True)
    (home / "config.yaml").write_text('{"memory":{"provider":"nbs_sidecar","loaderPath":"plugins/nbs_sidecar/plugin.py"}}')
    source = tmp_path / ".nbs_agent_runtime" / "live-ab" / "acceptance-1" / "source.json"
    source.write_text('{"source":"bounded"}')
    return IsolatedHermesProfile("ready", "", home, home / "config.yaml", home / "plugins" / "nbs_sidecar", ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"), "f" * 64, "e" * 64)


def _hermes_source(tmp_path: Path) -> Path:
    root = tmp_path / "hermes-source"
    agent = root / "agent"
    agent.mkdir(parents=True)
    (agent / "memory_provider.py").write_text("class MemoryProvider: pass\n")
    return root


def test_run_live_ab_builds_exactly_two_identical_inputs_with_distinct_lifecycle_arms(tmp_path, monkeypatch):
    from scripts import hermes_live_ab_runner as subject

    observed: list[dict] = []
    monkeypatch.setattr(subject, "_immutable_identity", lambda root: (HEAD, canonical_fingerprint({"gitHead": HEAD, "gitStatusPorcelain": ""})))

    def child(command, *, env, timeout):
        turn = json.loads((tmp_path / ".nbs_agent_runtime" / command[command.index("--turn-input") + 1]).read_text())
        observed.append({"command": command, "turn": turn, "env": env, "timeout": timeout})
        output = tmp_path / ".nbs_agent_runtime" / command[command.index("--output") + 1]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"status": "completed", "runId": turn["runId"], "sessionId": turn["sessionId"], "recallMode": turn["recallMode"], "sequence": turn["sequence"], "activationReceipt": turn["activationReceipt"], **{key: turn[key] for key in ("gitHead", "projectId", "workspaceKind", "workspaceFingerprint", "taskFingerprint", "briefFingerprint", "allowedFilesFingerprint", "commandsFingerprint", "provider", "model", "reasoningProfile", "cleanWorktreeFingerprint")}}))
        return 0, "safe stdout", "safe stderr"

    profile = _profile(tmp_path)
    hermes_source = _hermes_source(tmp_path)
    result = subject.run_live_ab(profile, _manifest(tmp_path), "bounded question", ["live-ab/acceptance-1/source.json"], project_root=tmp_path, env={"DEEPSEEK_API_KEY": "super-secret", "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1", "HERMES_SOURCE_ROOT": str(hermes_source)}, child_runner=child, short_term_offload="on")
    assert result.status == "completed"
    assert len(observed) == 2
    control, treatment = (item["turn"] for item in observed)
    assert (control["recallMode"], control["sequence"], control["activationReceipt"]["status"]) == ("off", 1, "disabled")
    assert (treatment["recallMode"], treatment["sequence"], treatment["activationReceipt"]["status"]) == ("on", 2, "activated")
    assert control["sessionId"] != treatment["sessionId"]
    for key in ("gitHead", "workspaceFingerprint", "taskFingerprint", "briefFingerprint", "allowedFilesFingerprint", "commandsFingerprint", "cleanWorktreeFingerprint", "query", "sourceRefs"):
        assert control[key] == treatment[key]
    assert all(item["env"]["HERMES_HOME"] == str(profile.home_dir) for item in observed)
    assert observed[0]["env"]["HERMES_MEMORY_PROVIDER"] == "disabled"
    assert observed[1]["env"]["HERMES_MEMORY_PROVIDER"] == "nbs_sidecar"
    assert all(item["env"]["HERMES_CONFIG"] == str(profile.config_path) for item in observed)
    assert all(item["command"][item["command"].index("--hermes-source-root") + 1] == str(hermes_source) for item in observed)
    assert "--sidecar-envelope" not in observed[0]["command"] if "command" in observed[0] else True
    assert "--sidecar-envelope" in observed[1]["command"] if "command" in observed[1] else False
    envelope_ref = observed[1]["command"][observed[1]["command"].index("--sidecar-envelope") + 1]
    hints_ref = observed[1]["command"][observed[1]["command"].index("--hints-path") + 1]
    envelope = json.loads((tmp_path / ".nbs_agent_runtime" / envelope_ref).read_text())
    assert envelope["provider"] == "hermes" and envelope["recallMode"] == "on"
    assert envelope["activationId"] == activation_binding_fingerprint(envelope)
    assert MemoryHints.from_dict(json.loads((tmp_path / ".nbs_agent_runtime" / hints_ref).read_text())).status == "ready"
    assert all("super-secret" not in json.dumps(item["turn"]) for item in observed)
    offload_files = list((tmp_path / ".nbs_agent_runtime" / "short-term-offload").rglob("*.json"))
    assert len(offload_files) == 2


def test_run_live_ab_offload_store_failure_preserves_completed_status(tmp_path, monkeypatch):
    from scripts import hermes_live_ab_runner as subject

    monkeypatch.setattr(subject, "_persist_child_output", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("store unavailable")))
    monkeypatch.setattr(subject, "_immutable_identity", lambda root: (HEAD, canonical_fingerprint({"gitHead": HEAD, "gitStatusPorcelain": ""})))
    def child(command, **kwargs):
        turn = json.loads((tmp_path / ".nbs_agent_runtime" / command[command.index("--turn-input") + 1]).read_text())
        output = tmp_path / ".nbs_agent_runtime" / command[command.index("--output") + 1]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"status": "completed", "runId": turn["runId"], "sessionId": turn["sessionId"], "recallMode": turn["recallMode"], "sequence": turn["sequence"], "activationReceipt": turn["activationReceipt"], **{key: turn[key] for key in ("gitHead", "projectId", "workspaceKind", "workspaceFingerprint", "taskFingerprint", "briefFingerprint", "allowedFilesFingerprint", "commandsFingerprint", "provider", "model", "reasoningProfile", "cleanWorktreeFingerprint")}}))
        return 0, "safe stdout", ""
    result = subject.run_live_ab(_profile(tmp_path), _manifest(tmp_path), "bounded question", ["live-ab/acceptance-1/source.json"], project_root=tmp_path, env={"DEEPSEEK_API_KEY": "x", "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1", "HERMES_SOURCE_ROOT": str(_hermes_source(tmp_path))}, child_runner=child, short_term_offload="on")
    assert result.status == "completed"


def test_run_live_ab_blocks_before_child_when_identity_changes(tmp_path, monkeypatch):
    from scripts import hermes_live_ab_runner as subject

    calls = 0
    identities = iter([(HEAD, canonical_fingerprint({"gitHead": HEAD, "gitStatusPorcelain": ""})), ("f" * 40, canonical_fingerprint({"gitHead": "f" * 40, "gitStatusPorcelain": ""}))])
    monkeypatch.setattr(subject, "_immutable_identity", lambda root: next(identities))

    def child(command, **kwargs):
        nonlocal calls
        calls += 1
        turn = json.loads((tmp_path / ".nbs_agent_runtime" / command[command.index("--turn-input") + 1]).read_text())
        output = tmp_path / ".nbs_agent_runtime" / command[command.index("--output") + 1]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"status": "completed", "runId": turn["runId"], "sessionId": turn["sessionId"], "recallMode": turn["recallMode"], "sequence": turn["sequence"], "activationReceipt": turn["activationReceipt"], **{key: turn[key] for key in ("gitHead", "projectId", "workspaceKind", "workspaceFingerprint", "taskFingerprint", "briefFingerprint", "allowedFilesFingerprint", "commandsFingerprint", "provider", "model", "reasoningProfile", "cleanWorktreeFingerprint")}}))
        return 0, "", ""

    result = subject.run_live_ab(_profile(tmp_path), _manifest(tmp_path), "bounded question", ["live-ab/acceptance-1/source.json"], project_root=tmp_path, env={"DEEPSEEK_API_KEY": "x", "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1", "HERMES_SOURCE_ROOT": str(_hermes_source(tmp_path))}, child_runner=child)
    assert result.status == "blocked_runner_capability"
    assert result.reason == "identity_mismatch"
    assert calls == 1
    assert result.control_receipt_path is None and result.treatment_receipt_path is None


def test_run_live_ab_timeout_or_missing_completed_receipt_fails_closed_and_redacts_diagnostics(tmp_path, monkeypatch):
    from scripts import hermes_live_ab_runner as subject

    monkeypatch.setattr(subject, "_immutable_identity", lambda root: (HEAD, canonical_fingerprint({"gitHead": HEAD, "gitStatusPorcelain": ""})))
    result = subject.run_live_ab(_profile(tmp_path), _manifest(tmp_path), "bounded question", ["live-ab/acceptance-1/source.json"], project_root=tmp_path, env={"DEEPSEEK_API_KEY": "very-secret", "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1", "HERMES_SOURCE_ROOT": str(_hermes_source(tmp_path))}, child_runner=lambda *args, **kwargs: (124, "very-secret output", "very-secret stderr"))
    assert result.status == "blocked_runner_capability"
    assert result.reason == "completion_missing"
    assert result.control_receipt_path is None and result.treatment_receipt_path is None
    diagnostic = (tmp_path / ".nbs_agent_runtime/live-ab/acceptance-1/blocked.json").read_text()
    assert "very-secret" not in diagnostic


def test_run_live_ab_blocks_missing_hermes_source_before_child_launch(tmp_path, monkeypatch):
    from scripts import hermes_live_ab_runner as subject

    monkeypatch.setattr(subject, "_immutable_identity", lambda root: (HEAD, canonical_fingerprint({"gitHead": HEAD, "gitStatusPorcelain": ""})))
    result = subject.run_live_ab(_profile(tmp_path), _manifest(tmp_path), "bounded question", ["live-ab/acceptance-1/source.json"], project_root=tmp_path, env={"DEEPSEEK_API_KEY": "x", "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1"}, child_runner=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("child must not launch")))
    assert result.status == "blocked_runner_capability"
    assert result.reason == "isolated_home_unavailable"
    assert (tmp_path / ".nbs_agent_runtime/live-ab/acceptance-1/blocked.json").is_file()


def test_run_live_ab_rejects_child_receipt_with_immutable_identity_drift(tmp_path, monkeypatch):
    from scripts import hermes_live_ab_runner as subject

    monkeypatch.setattr(subject, "_immutable_identity", lambda root: (HEAD, canonical_fingerprint({"gitHead": HEAD, "gitStatusPorcelain": ""})))
    def child(command, **kwargs):
        turn = json.loads((tmp_path / ".nbs_agent_runtime" / command[command.index("--turn-input") + 1]).read_text())
        output = tmp_path / ".nbs_agent_runtime" / command[command.index("--output") + 1]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"status": "completed", "runId": turn["runId"], "sessionId": turn["sessionId"], "recallMode": turn["recallMode"], "sequence": turn["sequence"], "activationReceipt": turn["activationReceipt"], "gitHead": "f" * 40, "projectId": turn["projectId"], "workspaceKind": turn["workspaceKind"], "workspaceFingerprint": turn["workspaceFingerprint"], "taskFingerprint": turn["taskFingerprint"], "briefFingerprint": turn["briefFingerprint"], "allowedFilesFingerprint": turn["allowedFilesFingerprint"], "commandsFingerprint": turn["commandsFingerprint"], "provider": turn["provider"], "model": turn["model"], "reasoningProfile": turn["reasoningProfile"], "cleanWorktreeFingerprint": turn["cleanWorktreeFingerprint"]}))
        return 0, "", ""
    result = subject.run_live_ab(_profile(tmp_path), _manifest(tmp_path), "bounded question", ["live-ab/acceptance-1/source.json"], project_root=tmp_path, env={"DEEPSEEK_API_KEY": "x", "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1", "HERMES_SOURCE_ROOT": str(_hermes_source(tmp_path))}, child_runner=child)
    assert (result.status, result.reason) == ("blocked_runner_capability", "completion_missing")


def test_run_live_ab_blocks_symlink_arm_or_source_traversal_with_marker(tmp_path, monkeypatch):
    from scripts import hermes_live_ab_runner as subject

    monkeypatch.setattr(subject, "_immutable_identity", lambda root: (HEAD, canonical_fingerprint({"gitHead": HEAD, "gitStatusPorcelain": ""})))
    profile = _profile(tmp_path)
    arm = profile.home_dir.parent / "control"
    arm.symlink_to(tmp_path, target_is_directory=True)
    result = subject.run_live_ab(profile, _manifest(tmp_path), "bounded question", ["../escape.json"], project_root=tmp_path, env={"DEEPSEEK_API_KEY": "x", "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1", "HERMES_SOURCE_ROOT": str(_hermes_source(tmp_path))})
    assert result.status == "blocked_runner_capability"
    assert result.diagnostic_path == "live-ab/acceptance-1/blocked.json"


def test_run_local_cli_transport_is_explicit_and_writes_bound_receipt(tmp_path):
    from backend.agents.hermes_cli_transport import CliInvokeRequest
    from backend.agents.runner_identity import RunnerIdentity
    from scripts import hermes_live_ab_runner as subject

    executable = tmp_path / "hermes-cli"
    executable.write_text(f"#!{sys.executable}\nimport json; print(json.dumps({{'model': 'deepseek-v4-flash', 'response': 'ok'}}))\n")
    executable.chmod(0o700)
    identity = RunnerIdentity.from_legacy_local_cli(runner_id="hermes-cli", provider="hermes", model="deepseek-v4-flash", profile="max", execution_environment="hermes-local")
    request = CliInvokeRequest(identity=identity, executable=executable, argv=("run", "--json"), cwd=tmp_path, source_fingerprint="a" * 64, turn_fingerprint="b" * 64, manifest_fingerprint="c" * 64, command_shape_fingerprint="d" * 64)
    result = subject.run_local_cli_transport(request, output_path=tmp_path / "receipt.json")
    assert result.status == "ready"
    payload = json.loads((tmp_path / "receipt.json").read_text())
    assert payload["runnerIdentityFingerprint"] == identity.identity_fingerprint


def test_existing_live_ab_default_does_not_select_local_cli(tmp_path):
    from scripts import hermes_live_ab_runner as subject

    assert "transport" not in subject.run_live_ab.__kwdefaults__
