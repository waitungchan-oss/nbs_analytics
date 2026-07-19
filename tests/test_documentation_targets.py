from __future__ import annotations

import json

import pytest

from backend.agents.documentation_targets import ObsidianTargetResolver


def test_resolver_uses_cli_then_environment_then_local_config(tmp_path, monkeypatch):
    cli = tmp_path / "cli"
    env = tmp_path / "env"
    local = tmp_path / "local"
    for root in (cli, env, local):
        (root / "70_Codex_Briefs").mkdir(parents=True)
    runtime = tmp_path / ".nbs_agent_runtime"
    runtime.mkdir()
    (runtime / "documentation.local.json").write_text(
        json.dumps({"obsidianVault": str(local)}), encoding="utf-8"
    )

    monkeypatch.setenv("NBS_OBSIDIAN_VAULT", str(env))
    resolver = ObsidianTargetResolver.from_sources(tmp_path, cli_root=cli, environ=dict(__import__("os").environ))
    assert resolver is not None
    assert resolver.resolve("brief_backfill", "brief.md") == cli / "70_Codex_Briefs" / "brief.md"

    resolver = ObsidianTargetResolver.from_sources(tmp_path, cli_root=None, environ=dict(__import__("os").environ))
    assert resolver is not None
    assert resolver.resolve("brief_backfill", "brief.md") == env / "70_Codex_Briefs" / "brief.md"

    resolver = ObsidianTargetResolver.from_sources(tmp_path, cli_root=None, environ={})
    assert resolver is not None
    assert resolver.resolve("brief_backfill", "brief.md") == local / "70_Codex_Briefs" / "brief.md"


def test_resolver_rejects_traversal_and_symlinks(tmp_path):
    vault = tmp_path / "vault"
    (vault / "70_Codex_Briefs").mkdir(parents=True)
    resolver = ObsidianTargetResolver(vault)
    with pytest.raises(PermissionError):
        resolver.resolve("brief_backfill", "../outside.md")

    outside = tmp_path / "outside.md"
    outside.write_text("x", encoding="utf-8")
    (vault / "70_Codex_Briefs" / "linked.md").symlink_to(outside)
    with pytest.raises(PermissionError):
        resolver.resolve("brief_backfill", "linked.md")


def test_resolver_rejects_unknown_subdirectory(tmp_path):
    vault = tmp_path / "vault"
    (vault / "70_Codex_Briefs").mkdir(parents=True)
    resolver = ObsidianTargetResolver(vault)
    with pytest.raises(PermissionError):
        resolver.resolve("brief_backfill", "20_Decisions/brief.md")
