from __future__ import annotations

import json
from pathlib import Path

from scripts.hermes_isolated_profile import create_isolated_profile


def _manifest() -> dict[str, str]:
    from backend.agents.evidence_models import canonical_fingerprint

    unsigned = {
        "schemaVersion": "hermes-runner-capability-manifest-v1", "recallMode": "off", "sequence": 1,
        "gitHead": "a" * 40, "projectId": "nbs_analytics", "workspaceKind": "repo",
        "workspaceFingerprint": "b" * 64, "taskFingerprint": "c" * 64, "briefFingerprint": "d" * 64,
        "allowedFilesFingerprint": "e" * 64, "commandsFingerprint": "f" * 64,
        "provider": "hermes",
        "model": "deepseek-v4-flash",
        "reasoningProfile": "max",
        "cleanWorktreeFingerprint": canonical_fingerprint({"gitHead": "a" * 40, "gitStatusPorcelain": ""}),
        "writerDisabled": True,
    }
    return {**unsigned, "manifestId": canonical_fingerprint(unsigned)}


def _project(tmp_path: Path) -> Path:
    source = Path(__file__).resolve().parents[1] / "integrations" / "hermes_nbs_sidecar"
    destination = tmp_path / "integrations" / "hermes_nbs_sidecar"
    destination.mkdir(parents=True)
    for name in ("__init__.py", "plugin.py"):
        (destination / name).write_bytes((source / name).read_bytes())
    return tmp_path


def _hermes_source(tmp_path: Path) -> Path:
    root = tmp_path / "hermes-source"
    agent = root / "agent"
    agent.mkdir(parents=True)
    (agent / "__init__.py").write_text("", encoding="utf-8")
    (agent / "memory_provider.py").write_text(
        "from abc import ABC\nclass MemoryProvider(ABC):\n    pass\n",
        encoding="utf-8",
    )
    return root


def test_profile_is_confined_copies_allowlisted_plugin_and_redacts_credentials(tmp_path):
    project_root = _project(tmp_path)
    hermes_source = _hermes_source(tmp_path)
    profile = create_isolated_profile(
        project_root,
        "acceptance-1",
        _manifest(),
        {"DEEPSEEK_API_KEY": "secret-not-for-artifacts", "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1"},
        hermes_source_root=hermes_source,
    )

    assert profile.status == "ready"
    assert profile.home_dir == project_root / ".nbs_agent_runtime/live-ab/acceptance-1/hermes-home"
    assert sorted(path.name for path in profile.plugin_dir.iterdir()) == ["__init__.py", "plugin.py"]
    config = json.loads(profile.config_path.read_text(encoding="utf-8"))
    assert config == {"memory": {"provider": "nbs_sidecar", "loaderPath": "plugins/nbs_sidecar/plugin.py"}, "model": "deepseek-v4-flash", "reasoningProfile": "max", "plugins": ["plugins/nbs_sidecar"]}
    assert "api_key" not in config and "base_url" not in config
    assert profile.credential_env_names == ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL")
    assert "secret-not-for-artifacts" not in profile.config_path.read_text(encoding="utf-8")
    assert profile.plugin_checksum


def test_profile_blocks_loader_discovery_without_a_hermes_source_root(tmp_path):
    project_root = _project(tmp_path)
    profile = create_isolated_profile(project_root, "acceptance-no-hermes", _manifest(), {"DEEPSEEK_API_KEY": "x", "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1"})

    assert (profile.status, profile.reason) == ("blocked_runner_capability", "isolated_home_unavailable")
    assert not (project_root / ".nbs_agent_runtime/live-ab/acceptance-no-hermes").exists()


def test_profile_blocks_invalid_endpoint_collisions_and_unsafe_acceptance_paths(tmp_path):
    project_root = _project(tmp_path)
    hermes_source = _hermes_source(tmp_path)
    invalid = create_isolated_profile(project_root, "acceptance-2", _manifest(), {"DEEPSEEK_API_KEY": "x", "DEEPSEEK_BASE_URL": "https://other.example/v1"})
    assert (invalid.status, invalid.reason) == ("blocked_runner_capability", "live_identity_missing")
    assert not (project_root / ".nbs_agent_runtime/live-ab/acceptance-2").exists()

    ready = create_isolated_profile(project_root, "acceptance-3", _manifest(), {"DEEPSEEK_API_KEY": "x", "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1"}, hermes_source_root=hermes_source)
    assert ready.status == "ready"
    collision = create_isolated_profile(project_root, "acceptance-3", _manifest(), {"DEEPSEEK_API_KEY": "x", "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1"}, hermes_source_root=hermes_source)
    traversal = create_isolated_profile(project_root, "../escape", _manifest(), {"DEEPSEEK_API_KEY": "x", "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1"}, hermes_source_root=hermes_source)
    assert (collision.status, collision.reason) == ("blocked_runner_capability", "isolated_home_unavailable")
    assert (traversal.status, traversal.reason) == ("blocked_runner_capability", "isolated_home_unavailable")


def test_profile_rejects_a_symlinked_runtime_root(tmp_path):
    project_root = _project(tmp_path)
    hermes_source = _hermes_source(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (project_root / ".nbs_agent_runtime").symlink_to(outside, target_is_directory=True)

    profile = create_isolated_profile(project_root, "acceptance-4", _manifest(), {"DEEPSEEK_API_KEY": "x", "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1"}, hermes_source_root=hermes_source)

    assert (profile.status, profile.reason) == ("blocked_runner_capability", "isolated_home_unavailable")
    assert not list(outside.iterdir())
