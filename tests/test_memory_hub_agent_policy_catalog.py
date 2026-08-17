from __future__ import annotations

from pathlib import Path

import pytest

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.memory_hub_agent_policy_catalog import (
    AgentPolicyCatalog,
    MemoryHubAgentPolicyCatalogError,
)
from backend.agents.memory_hub_team_catalog import TeamCatalog


PROJECT = "nbs_analytics"


def _team(team_id="team-finance-governance", agents=None):
    agents = ["agent-context-reader"] if agents is None else agents
    unsigned = {"schemaVersion": "memory-team-record-v1", "teamId": team_id, "role": "governance_reader", "agentIds": agents, "allowedScopes": ["project", "team"]}
    return {**unsigned, "recordFingerprint": canonical_fingerprint(unsigned)}


def _team_catalog():
    unsigned = {"schemaVersion": "memory-team-catalog-v1", "projectId": PROJECT, "teams": [_team()]}
    return TeamCatalog.from_dict({**unsigned, "catalogFingerprint": canonical_fingerprint(unsigned)}, expected_project_id=PROJECT)


def _rule(memory_kinds=None, scopes=None, decision="allow"):
    memory_kinds = ["evidence", "governance"] if memory_kinds is None else memory_kinds
    scopes = ["project", "team"] if scopes is None else scopes
    unsigned = {"schemaVersion": "memory-agent-policy-rule-v1", "memoryKinds": memory_kinds, "scopes": scopes, "decision": decision}
    return {**unsigned, "ruleFingerprint": canonical_fingerprint(unsigned)}


def _agent(agent_id="agent-context-reader", teams=None, kinds=None, scopes=None, rules=None):
    teams = ["team-finance-governance"] if teams is None else teams
    kinds = ["evidence", "governance"] if kinds is None else kinds
    scopes = ["project", "team"] if scopes is None else scopes
    rules = [_rule()] if rules is None else rules
    unsigned = {"schemaVersion": "memory-agent-policy-record-v1", "agentId": agent_id, "agentClass": "context", "teamIds": teams, "allowedMemoryKinds": kinds, "allowedScopes": scopes, "rules": rules}
    return {**unsigned, "recordFingerprint": canonical_fingerprint(unsigned)}


def _payload(*agents, project_id=PROJECT, default_decision="deny"):
    unsigned = {"schemaVersion": "memory-agent-policy-catalog-v1", "projectId": project_id, "agents": list(agents or (_agent(),)), "defaultDecision": default_decision}
    return {**unsigned, "catalogFingerprint": canonical_fingerprint(unsigned)}


def test_valid_policy_catalog_and_allowlist():
    catalog = AgentPolicyCatalog.from_dict(_payload(_agent()), expected_project_id=PROJECT, team_catalog=_team_catalog())
    agent = catalog.agent("agent-context-reader")
    assert agent is not None
    assert agent.allows("governance", "project") is True
    assert agent.allows("skill", "project") is False
    assert catalog.default_decision == "deny"
    assert len(catalog.catalog_fingerprint) == 64


@pytest.mark.parametrize(
    "mutator",
    [
        lambda p: p.update(extra=True),
        lambda p: p.update(defaultDecision="allow"),
        lambda p: p["agents"][0].update(extra=True),
        lambda p: p["agents"][0].update(recordFingerprint="0" * 64),
        lambda p: p["agents"][0]["rules"][0].update(ruleFingerprint="0" * 64),
        lambda p: p.update(catalogFingerprint="0" * 64),
        lambda p: p["agents"][0].update(teamIds=["team-unknown"]),
        lambda p: p["agents"][0].update(allowedMemoryKinds=["skill"]),
    ],
)
def test_invalid_policy_contract_is_rejected(mutator):
    payload = _payload(_agent())
    mutator(payload)
    with pytest.raises(MemoryHubAgentPolicyCatalogError):
        AgentPolicyCatalog.from_dict(payload, expected_project_id=PROJECT, team_catalog=_team_catalog())


def test_explicit_deny_and_no_matching_rule_are_not_allow():
    deny = _rule(memory_kinds=["evidence"], scopes=["project"], decision="deny")
    agent = _agent(kinds=["evidence"], scopes=["project"], rules=[deny])
    catalog = AgentPolicyCatalog.from_dict(_payload(agent), expected_project_id=PROJECT, team_catalog=_team_catalog())
    loaded = catalog.agent("agent-context-reader")
    assert loaded is not None
    assert loaded.allows("evidence", "project") is False
    assert loaded.allows("governance", "project") is False


def test_unsorted_rules_are_rejected():
    first = _rule(memory_kinds=["evidence"], scopes=["project"])
    second = _rule(memory_kinds=["governance"], scopes=["team"])
    rules = list(reversed(sorted([first, second], key=lambda item: item["ruleFingerprint"])))
    agent = _agent(kinds=["evidence", "governance"], scopes=["project", "team"], rules=rules)
    with pytest.raises(MemoryHubAgentPolicyCatalogError):
        AgentPolicyCatalog.from_dict(_payload(agent), expected_project_id=PROJECT, team_catalog=_team_catalog())


def test_team_reference_requires_membership():
    team_catalog = _team_catalog()
    payload = _payload(_agent(teams=["team-finance-governance"], agent_id="agent-not-member"))
    with pytest.raises(MemoryHubAgentPolicyCatalogError):
        AgentPolicyCatalog.from_dict(payload, expected_project_id=PROJECT, team_catalog=team_catalog)


def test_load_rejects_path_escape(tmp_path: Path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    with pytest.raises(MemoryHubAgentPolicyCatalogError):
        AgentPolicyCatalog.load(tmp_path / "policy.json", runtime_root=runtime, expected_project_id=PROJECT, team_catalog=_team_catalog())
    outside = tmp_path / "policy.json"
    outside.write_text("{}", encoding="utf-8")
    with pytest.raises(MemoryHubAgentPolicyCatalogError):
        AgentPolicyCatalog.load(runtime / ".." / "policy.json", runtime_root=runtime, expected_project_id=PROJECT, team_catalog=_team_catalog())
