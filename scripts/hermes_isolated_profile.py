"""Create a one-shot, credential-free isolated Hermes profile for live A/B runs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
import importlib
import json
from pathlib import Path
import re
import shutil
import sys
from typing import Mapping

from backend.agents.evidence_models import canonical_fingerprint


_ACCEPTANCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_REQUIRED_PLUGIN_FILES = ("__init__.py", "plugin.py")
_CREDENTIAL_NAMES = ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL")
_DEEPSEEK_ENDPOINT = "https://api.deepseek.com/v1"
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class IsolatedHermesProfile:
    status: str
    reason: str
    home_dir: Path | None
    config_path: Path | None
    plugin_dir: Path | None
    credential_env_names: tuple[str, ...]
    profile_fingerprint: str
    plugin_checksum: str


def _blocked(reason: str) -> IsolatedHermesProfile:
    return IsolatedHermesProfile("blocked_runner_capability", reason, None, None, None, (), "", "")


def _is_regular_source(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _plugin_checksum(plugin_dir: Path) -> str:
    digest = hashlib.sha256()
    for name in _REQUIRED_PLUGIN_FILES:
        path = plugin_dir / name
        if not _is_regular_source(path):
            raise OSError("allowlisted plugin source is unavailable")
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _valid_manifest(manifest: Mapping[str, object]) -> bool:
    required = {
        "schemaVersion", "manifestId", "recallMode", "sequence", "gitHead", "projectId", "workspaceKind",
        "workspaceFingerprint", "taskFingerprint", "briefFingerprint", "allowedFilesFingerprint",
        "commandsFingerprint", "provider", "model", "reasoningProfile", "cleanWorktreeFingerprint", "writerDisabled",
    }
    if set(manifest) != required or manifest.get("schemaVersion") != "hermes-runner-capability-manifest-v1":
        return False
    if manifest.get("recallMode") not in {"off", "on"} or manifest.get("sequence") not in {1, 2} or manifest.get("workspaceKind") not in {"repo", "isolated_worktree"}:
        return False
    if manifest.get("provider") != "hermes" or manifest.get("model") != "deepseek-v4-flash" or manifest.get("reasoningProfile") != "max" or manifest.get("writerDisabled") is not True:
        return False
    if not isinstance(manifest.get("gitHead"), str) or not _SHA40.fullmatch(manifest["gitHead"]):
        return False
    if not isinstance(manifest.get("projectId"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}", manifest["projectId"]):
        return False
    for key in ("manifestId", "workspaceFingerprint", "taskFingerprint", "briefFingerprint", "allowedFilesFingerprint", "commandsFingerprint", "cleanWorktreeFingerprint"):
        if not isinstance(manifest.get(key), str) or not _SHA256.fullmatch(manifest[key]):
            return False
    unsigned = {key: value for key, value in manifest.items() if key != "manifestId"}
    return manifest["manifestId"] == canonical_fingerprint(unsigned) and manifest["cleanWorktreeFingerprint"] == canonical_fingerprint({"gitHead": manifest["gitHead"], "gitStatusPorcelain": ""})


def _discover_plugin(plugin_path: Path, hermes_source_root: Path) -> bool:
    """Load the copied plugin against the supplied Hermes MemoryProvider ABC."""
    memory_provider = hermes_source_root / "agent" / "memory_provider.py"
    if not _is_regular_source(memory_provider):
        return False
    source_text = str(hermes_source_root)
    if source_text not in sys.path:
        sys.path.insert(0, source_text)
    importlib.invalidate_caches()
    try:
        loaded = sys.modules.get("agent.memory_provider")
        if loaded is not None and Path(getattr(loaded, "__file__", "")).resolve(strict=True) != memory_provider.resolve(strict=True):
            sys.modules.pop("agent.memory_provider", None)
            sys.modules.pop("agent", None)
        module = importlib.import_module("agent.memory_provider")
        if Path(getattr(module, "__file__", "")).resolve(strict=True) != memory_provider.resolve(strict=True):
            return False
        memory_provider_base = getattr(module, "MemoryProvider")
    except (ImportError, OSError, AttributeError, ValueError):
        return False
    spec = importlib.util.spec_from_file_location("_nbs_isolated_sidecar_plugin", plugin_path)
    if spec is None or spec.loader is None:
        return False
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (ImportError, OSError, ValueError):
        return False
    provider = getattr(module, "NbsHermesSidecarProvider", None)
    return isinstance(provider, type) and isinstance(memory_provider_base, type) and issubclass(provider, memory_provider_base)


def create_isolated_profile(
    project_root: str | Path,
    acceptance_id: str,
    manifest: Mapping[str, object],
    env: Mapping[str, str],
    *,
    hermes_source_root: str | Path | None = None,
) -> IsolatedHermesProfile:
    """Create an isolated profile or return bounded blocked evidence.

    Credential values are deliberately inspected only for availability and exact
    endpoint identity. They never enter paths, config, return values or errors.
    """
    if not _ACCEPTANCE_ID.fullmatch(acceptance_id) or not _valid_manifest(manifest):
        return _blocked("isolated_home_unavailable")
    if not isinstance(env.get("DEEPSEEK_API_KEY"), str) or not env["DEEPSEEK_API_KEY"] or env.get("DEEPSEEK_BASE_URL") != _DEEPSEEK_ENDPOINT:
        return _blocked("live_identity_missing")
    if hermes_source_root is None:
        return _blocked("isolated_home_unavailable")

    root = Path(project_root).resolve(strict=False)
    source = root / "integrations" / "hermes_nbs_sidecar"
    hermes_root = Path(hermes_source_root).resolve(strict=False)
    runtime = root / ".nbs_agent_runtime"
    live_root = runtime / "live-ab"
    acceptance_root = live_root / acceptance_id
    if runtime.is_symlink() or live_root.is_symlink() or acceptance_root.exists() or acceptance_root.is_symlink():
        return _blocked("isolated_home_unavailable")
    created = False
    try:
        source_checksum = _plugin_checksum(source)
        acceptance_root.mkdir(parents=True, exist_ok=False)
        created = True
        home_dir = acceptance_root / "hermes-home"
        plugin_dir = home_dir / "plugins" / "nbs_sidecar"
        plugin_dir.mkdir(parents=True, exist_ok=False)
        for name in _REQUIRED_PLUGIN_FILES:
            shutil.copyfile(source / name, plugin_dir / name, follow_symlinks=False)
        copied_checksum = _plugin_checksum(plugin_dir)
        if copied_checksum != source_checksum or not _discover_plugin(plugin_dir / "plugin.py", hermes_root):
            raise OSError("isolated plugin loader discovery failed")
        config = {
            "memory": {"provider": "nbs_sidecar", "loaderPath": "plugins/nbs_sidecar/plugin.py"},
            "model": "deepseek-v4-flash",
            "reasoningProfile": "max",
            "plugins": ["plugins/nbs_sidecar"],
        }
        config_path = home_dir / "config.yaml"
        config_path.write_text(json.dumps(config, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        profile_fingerprint = canonical_fingerprint({
            "manifestId": manifest["manifestId"], "config": config,
            "pluginChecksum": copied_checksum, "credentialEnvNames": _CREDENTIAL_NAMES,
        })
        return IsolatedHermesProfile("ready", "", home_dir, config_path, plugin_dir, _CREDENTIAL_NAMES, profile_fingerprint, copied_checksum)
    except (OSError, ValueError):
        if created and acceptance_root.is_dir() and not acceptance_root.is_symlink():
            shutil.rmtree(acceptance_root)
        return _blocked("isolated_home_unavailable")
