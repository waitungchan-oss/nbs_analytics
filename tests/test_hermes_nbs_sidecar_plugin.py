from __future__ import annotations

import json

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.memory_sidecar_hint_models import MemoryHint, MemoryHints
from integrations.hermes_nbs_sidecar.plugin import NbsHermesSidecarProvider, activation_binding_fingerprint


HEAD = "a" * 40
TASK = "c" * 64
PROJECT_ID = "nbs_analytics"


def _hints(query: str, *, status: str = "ready") -> dict:
    hints = MemoryHints(
        query_fingerprint=canonical_fingerprint({"query": query}), status=status,
        hints=() if status != "ready" else (MemoryHint("d" * 64, "Use bounded verification evidence.", ("verification.json",), "fresh", "high", ("e" * 64,)),),
    )
    return hints.to_dict()


def _envelope(project_root, *, hints_path: str = "runs/run-sidecar/memory-hints.json", **changes: object) -> dict:
    value = {
        "schemaVersion": "hermes-nbs-sidecar-activation-v1", "manifestId": "f" * 64,
        "activationId": "", "sessionId": "session-1", "recallMode": "on", "gitHead": HEAD,
        "projectId": PROJECT_ID, "workspaceKind": "repo", "workspaceFingerprint": canonical_fingerprint({"projectRoot": str(project_root.resolve()), "projectId": PROJECT_ID, "workspaceKind": "repo"}),
        "taskFingerprint": TASK, "briefFingerprint": "d" * 64, "allowedFilesFingerprint": "e" * 64,
        "commandsFingerprint": "f" * 64, "provider": "hermes", "model": "deepseek-v4-flash",
        "reasoning": "medium", "hintsPath": hints_path, "writerDisabled": True,
    }
    value.update(changes)
    value["activationId"] = activation_binding_fingerprint(value)
    return value


def _provider(tmp_path, monkeypatch, envelope: dict | None, *, query: str = "review runtime") -> NbsHermesSidecarProvider:
    if envelope is not None:
        path = tmp_path / ".nbs_agent_runtime" / envelope["hintsPath"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_hints(query)), encoding="utf-8")
    provider = NbsHermesSidecarProvider(tmp_path, envelope)
    monkeypatch.setattr(provider, "_current_git_head", lambda: HEAD)
    monkeypatch.setattr(provider, "_git_status_porcelain", lambda: "")
    return provider


def test_provider_is_disabled_without_explicit_activation(tmp_path, monkeypatch):
    provider = _provider(tmp_path, monkeypatch, None)

    assert provider.is_available() is False
    assert provider.prefetch("review runtime") == ""


def test_valid_activation_prefetches_bounded_non_authoritative_hints(tmp_path, monkeypatch):
    provider = _provider(tmp_path, monkeypatch, _envelope(tmp_path))
    provider.initialize("session-1")

    assert provider.is_available() is True
    value = provider.prefetch("review runtime", session_id="session-1")
    assert "non_authoritative_memory" in value
    assert "Use bounded verification evidence." in value
    assert provider.sync_turn("session-1", "input", "output") is None


def test_activation_rejects_identity_model_reasoning_and_fingerprint_mismatches(tmp_path, monkeypatch):
    for changes in (
        {"gitHead": "0" * 40}, {"workspaceFingerprint": "0" * 64}, {"model": "other"},
        {"reasoning": "high"}, {"activationId": "0" * 64},
    ):
        envelope = _envelope(tmp_path)
        envelope.update(changes)
        provider = _provider(tmp_path, monkeypatch, envelope)
        assert provider.is_available() is False


def test_provider_rejects_hints_path_escape_symlink_oversize_malformed_and_stale(tmp_path, monkeypatch):
    escape = _provider(tmp_path, monkeypatch, _envelope(tmp_path, hints_path="../outside.json"))
    assert escape.is_available() is False
    envelope = _envelope(tmp_path)
    provider = _provider(tmp_path, monkeypatch, envelope)
    hints_path = tmp_path / ".nbs_agent_runtime" / envelope["hintsPath"]
    hints_path.unlink()
    hints_path.symlink_to(tmp_path / "outside.json")
    assert provider.is_available() is False
    oversized = _provider(tmp_path, monkeypatch, _envelope(tmp_path))
    (tmp_path / ".nbs_agent_runtime/runs/run-sidecar/memory-hints.json").write_bytes(b"x" * 6001)
    assert oversized.is_available() is False
    malformed = _provider(tmp_path, monkeypatch, _envelope(tmp_path))
    (tmp_path / ".nbs_agent_runtime/runs/run-sidecar/memory-hints.json").write_text("{", encoding="utf-8")
    assert malformed.is_available() is False
    stale = _provider(tmp_path, monkeypatch, _envelope(tmp_path))
    (tmp_path / ".nbs_agent_runtime/runs/run-sidecar/memory-hints.json").write_text(json.dumps(_hints("review runtime", status="timeout")), encoding="utf-8")
    assert stale.is_available() is False


def test_prefetch_is_bounded_and_returns_empty_for_unbounded_or_query_mismatch(tmp_path, monkeypatch):
    provider = _provider(tmp_path, monkeypatch, _envelope(tmp_path))
    provider.initialize("session-1")

    assert provider.prefetch("x" * 513) == ""
    assert provider.prefetch("different query") == ""


def test_provider_requires_matching_initialized_session_and_workspace(tmp_path, monkeypatch):
    provider = _provider(tmp_path, monkeypatch, _envelope(tmp_path))
    assert provider.is_available() is False
    provider.initialize("other-session")
    assert provider.is_available() is False
    provider.initialize("session-1")
    assert provider.is_available() is True
    workspace_mismatch = _provider(tmp_path, monkeypatch, _envelope(tmp_path, workspaceFingerprint="0" * 64))
    workspace_mismatch.initialize("session-1")
    assert workspace_mismatch.is_available() is False
