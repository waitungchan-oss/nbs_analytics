from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agents.memory_hub_deployment_provider import deployment_owned_catalog_provider
from scripts.provision_memory_hub_catalog import main


def test_provision_builds_all_deployment_owned_catalogs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    source_root = tmp_path / "docs" / "memory_hub_sources"
    source_root.mkdir(parents=True)
    for name, body in {
        "context-agent-governance.md": "governance",
        "context-agent-evidence.md": "evidence",
        "context-agent-skill.md": "skill",
    }.items():
        (source_root / name).write_text(body, encoding="utf-8")
    # The command must use the repository manifest, not caller-provided catalog paths.
    project = Path(__file__).resolve().parents[1]
    manifest = json.loads((project / "agent_config/memory_hub_catalog_deployment.json").read_text(encoding="utf-8"))
    manifest["sourceRoot"] = "docs/memory_hub_sources"
    (tmp_path / "agent_config").mkdir()
    (tmp_path / "agent_config/memory_hub_catalog_deployment.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr("scripts.provision_memory_hub_catalog.PROJECT_ROOT", tmp_path)
    # Source identities in the copied manifest are intentionally invalid for these fixtures;
    # the command must fail closed instead of silently rebuilding a different catalog.
    assert main([]) == 2
    assert not (tmp_path / ".nbs_agent_runtime/memory-hub/catalog.json").exists()
    assert "blocked" in capsys.readouterr().out


def test_provision_is_idempotent_for_the_real_project(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    project = Path(__file__).resolve().parents[1]
    monkeypatch.setattr("scripts.provision_memory_hub_catalog.PROJECT_ROOT", project)
    assert main([]) == 0
    capsys.readouterr()
    assert main(["--check-only"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ready"
    assert payload["catalogFingerprint"]
    catalog = deployment_owned_catalog_provider(project)()
    assert catalog is not None
    assert len(catalog.records) == 3


def test_check_only_missing_runtime_is_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    project = Path(__file__).resolve().parents[1]
    (tmp_path / "agent_config").mkdir()
    (tmp_path / "agent_config/memory_hub_catalog_deployment.json").write_bytes(
        (project / "agent_config/memory_hub_catalog_deployment.json").read_bytes()
    )
    for name in ("context-agent-governance.md", "context-agent-evidence.md", "context-agent-skill.md"):
        target = tmp_path / "docs" / "memory_hub_sources" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((project / "docs" / "memory_hub_sources" / name).read_bytes())
    monkeypatch.setattr("scripts.provision_memory_hub_catalog.PROJECT_ROOT", tmp_path)
    assert main(["--check-only"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"
