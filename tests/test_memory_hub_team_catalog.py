from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.memory_hub_team_catalog import TeamCatalog, MemoryHubTeamCatalogError


PROJECT = "nbs_analytics"


def _team(team_id: str = "team-finance-governance", *, agents=None, scopes=None):
    agents = ["agent-context-reader"] if agents is None else agents
    scopes = ["project", "team"] if scopes is None else scopes
    unsigned = {
        "schemaVersion": "memory-team-record-v1",
        "teamId": team_id,
        "role": "governance_reader",
        "agentIds": agents,
        "allowedScopes": scopes,
    }
    return {**unsigned, "recordFingerprint": canonical_fingerprint(unsigned)}


def _payload(*teams, project_id=PROJECT):
    teams = list(teams or (_team(),))
    unsigned = {
        "schemaVersion": "memory-team-catalog-v1",
        "projectId": project_id,
        "teams": teams,
    }
    return {**unsigned, "catalogFingerprint": canonical_fingerprint(unsigned)}


def test_valid_catalog_is_immutable_and_fingerprinted():
    catalog = TeamCatalog.from_dict(_payload(), expected_project_id=PROJECT)
    team = catalog.team("team-finance-governance")
    assert team is not None
    assert team.agent_ids == ("agent-context-reader",)
    assert team.allowed_scopes == ("project", "team")
    assert len(catalog.catalog_fingerprint) == 64
    assert catalog.to_dict()["catalogFingerprint"] == catalog.catalog_fingerprint


@pytest.mark.parametrize(
    "mutator",
    [
        lambda p: p.update(extra=True),
        lambda p: p["teams"][0].update(extra=True),
        lambda p: p["teams"][0].update(recordFingerprint="0" * 64),
        lambda p: p.update(catalogFingerprint="0" * 64),
        lambda p: p.update(projectId="other_project"),
        lambda p: p["teams"].append(_team("team-finance-governance")),
        lambda p: p["teams"][0].update(agentIds=["agent-z", "agent-a"]),
    ],
)
def test_malformed_or_tampered_catalog_is_rejected(mutator):
    payload = _payload()
    mutator(payload)
    with pytest.raises(MemoryHubTeamCatalogError):
        TeamCatalog.from_dict(payload, expected_project_id=PROJECT)


@pytest.mark.parametrize(
    "team_id,agents,scopes",
    [
        ("../team", ["agent-context-reader"], ["project"]),
        ("team-a", ["../agent"], ["project"]),
        ("team-a", ["agent-a"], ["secret"]),
        ("team-a", [], ["project"]),
    ],
)
def test_identifiers_and_scopes_are_bounded(team_id, agents, scopes):
    with pytest.raises(MemoryHubTeamCatalogError):
        TeamCatalog.from_dict(_payload(_team(team_id, agents=agents, scopes=scopes)), expected_project_id=PROJECT)


def test_load_is_read_only_and_rejects_path_escape_and_symlinks(tmp_path: Path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    catalog_path = runtime / "teams.json"
    catalog_path.write_text(json.dumps(_payload()), encoding="utf-8")
    loaded = TeamCatalog.load(catalog_path, runtime_root=runtime, expected_project_id=PROJECT)
    assert loaded.catalog_fingerprint == TeamCatalog.from_dict(_payload(), expected_project_id=PROJECT).catalog_fingerprint
    assert catalog_path.read_text(encoding="utf-8")

    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(_payload()), encoding="utf-8")
    with pytest.raises(MemoryHubTeamCatalogError):
        TeamCatalog.load(outside, runtime_root=runtime, expected_project_id=PROJECT)

    link = runtime / "link.json"
    link.symlink_to(catalog_path)
    with pytest.raises(MemoryHubTeamCatalogError):
        TeamCatalog.load(link, runtime_root=runtime, expected_project_id=PROJECT)


def test_team_lookup_missing_is_none():
    catalog = TeamCatalog.from_dict(_payload(), expected_project_id=PROJECT)
    assert catalog.team("team-unknown") is None
