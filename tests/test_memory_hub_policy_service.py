from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.memory_hub_agent_policy_catalog import AgentPolicyCatalog
from backend.agents.memory_hub_models import MemoryQuery, MemoryRecord, MemorySource, RuntimeIdentity
from backend.agents.memory_hub_policy_service import MemoryHubPolicyService
from backend.agents.memory_hub_team_catalog import TeamCatalog


PROJECT = "nbs_analytics"
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _team_catalog(scopes=("project", "team")):
    unsigned = {"schemaVersion": "memory-team-catalog-v1", "projectId": PROJECT, "teams": [{"schemaVersion": "memory-team-record-v1", "teamId": "team-finance-governance", "role": "governance_reader", "agentIds": ["agent-context-reader"], "allowedScopes": list(scopes), "recordFingerprint": ""}]}
    unsigned["teams"][0]["recordFingerprint"] = canonical_fingerprint({k: v for k, v in unsigned["teams"][0].items() if k != "recordFingerprint"})
    unsigned["catalogFingerprint"] = canonical_fingerprint(unsigned)
    return TeamCatalog.from_dict(unsigned, expected_project_id=PROJECT)


def _policy_catalog(rules=None):
    rule = {"schemaVersion": "memory-agent-policy-rule-v1", "memoryKinds": ["evidence", "governance"], "scopes": ["project", "team"], "decision": "allow", "ruleFingerprint": ""}
    rule["ruleFingerprint"] = canonical_fingerprint({k: v for k, v in rule.items() if k != "ruleFingerprint"})
    agent = {"schemaVersion": "memory-agent-policy-record-v1", "agentId": "agent-context-reader", "agentClass": "context", "teamIds": ["team-finance-governance"], "allowedMemoryKinds": ["evidence", "governance"], "allowedScopes": ["project", "team"], "rules": list(rules or [rule]), "recordFingerprint": ""}
    agent["recordFingerprint"] = canonical_fingerprint({k: v for k, v in agent.items() if k != "recordFingerprint"})
    payload = {"schemaVersion": "memory-agent-policy-catalog-v1", "projectId": PROJECT, "agents": [agent], "defaultDecision": "deny", "catalogFingerprint": ""}
    payload["catalogFingerprint"] = canonical_fingerprint({k: v for k, v in payload.items() if k != "catalogFingerprint"})
    return AgentPolicyCatalog.from_dict(payload, expected_project_id=PROJECT, team_catalog=_team_catalog())


def _record(*, scope="project", kind="governance", freshness="fresh", status="ready"):
    generated = NOW.isoformat()
    expires = (NOW + timedelta(days=2)).isoformat()
    source = MemorySource.from_parts(source_kind="governance_document", artifact_ref="docs/governance.md", artifact_sha256="a" * 64, run_id=None, git_head=None, scope=scope, owner="team-finance-governance" if scope == "team" else "nbs_analytics", status="verified", generated_at=generated, expires_at=expires, policy_version="policy_v1")
    return MemoryRecord.from_parts(memory_kind=kind, summary="verified summary", source_refs=[source], scope=scope, owner=source.owner, freshness=freshness, status=status)


def _query(scope="project", kinds=("governance",)):
    return MemoryQuery.from_parts(query="show verified governance", consumer_id="agent-context-reader", scope=scope, memory_kinds=kinds)


def _service():
    return MemoryHubPolicyService(_team_catalog(), _policy_catalog(), project_id=PROJECT)


def test_allow_contains_fingerprint_and_no_external_side_effect():
    decision = _service().evaluate(RuntimeIdentity.from_parts(project_id=PROJECT, consumer_id="agent-context-reader", team_id="team-finance-governance"), _query(), _record())
    assert decision.decision == "allow"
    assert decision.reason == "policy_allow"
    assert decision.record_returned is True
    assert len(decision.decision_fingerprint) == 64


def test_policy_kind_and_scope_denials_do_not_leak_record_metadata():
    identity = RuntimeIdentity.from_parts(project_id=PROJECT, consumer_id="agent-context-reader", team_id="team-finance-governance")
    denied_kind = _service().evaluate(identity, _query(kinds=("skill",)), _record(kind="skill"))
    denied_scope = _service().evaluate(identity, _query(scope="agent"), _record(scope="agent"))
    for decision in (denied_kind, denied_scope):
        assert decision.decision == "deny"
        assert decision.record_returned is False
        assert "summary" not in decision.to_dict()


def test_missing_team_identity_is_blocked():
    identity = RuntimeIdentity.from_parts(project_id=PROJECT, consumer_id="agent-context-reader")
    decision = _service().evaluate(identity, _query(scope="team"), _record(scope="team"))
    assert decision.decision == "blocked"
    assert decision.reason == "missing_identity"
    assert decision.record_returned is False


@pytest.mark.parametrize("record_kwargs", [{"freshness": "stale"}, {"status": "blocked"}])
def test_invalid_record_is_blocked(record_kwargs):
    identity = RuntimeIdentity.from_parts(project_id=PROJECT, consumer_id="agent-context-reader", team_id="team-finance-governance")
    decision = _service().evaluate(identity, _query(), _record(**record_kwargs))
    assert decision.decision == "blocked"
    assert decision.reason in {"record_stale", "source_blocked"}


def test_tampered_record_or_source_fingerprint_is_blocked():
    identity = RuntimeIdentity.from_parts(project_id=PROJECT, consumer_id="agent-context-reader", team_id="team-finance-governance")
    record = _record()
    object.__setattr__(record, "record_fingerprint", "0" * 64)
    assert _service().evaluate(identity, _query(), record).reason == "record_invalid"
    source_record = _record()
    object.__setattr__(source_record.source_refs[0], "source_fingerprint", "0" * 64)
    assert _service().evaluate(identity, _query(), source_record).reason == "source_blocked"


def test_malformed_source_timestamp_is_blocked_not_raised():
    identity = RuntimeIdentity.from_parts(project_id=PROJECT, consumer_id="agent-context-reader", team_id="team-finance-governance")
    record = _record()
    object.__setattr__(record.source_refs[0], "expires_at", "not-a-timestamp")
    decision = _service().evaluate(identity, _query(), record)
    assert decision.decision == "blocked"
    assert decision.reason == "source_blocked"


def test_missing_policy_catalog_has_specific_block_reason():
    service = MemoryHubPolicyService(_team_catalog(), None, project_id=PROJECT)
    identity = RuntimeIdentity.from_parts(project_id=PROJECT, consumer_id="agent-context-reader", team_id="team-finance-governance")
    assert service.evaluate(identity, _query(), _record()).reason == "agent_policy_catalog_missing"


def test_unknown_agent_and_cross_project_identity_are_blocked():
    identity = RuntimeIdentity.from_parts(project_id=PROJECT, consumer_id="agent-unknown", team_id="team-finance-governance")
    unknown_query = MemoryQuery.from_parts(query="show verified governance", consumer_id="agent-unknown", scope="project", memory_kinds=("governance",))
    assert _service().evaluate(identity, unknown_query, _record()).reason == "unknown_agent"
    cross = RuntimeIdentity.from_parts(project_id="other_project", consumer_id="agent-context-reader", team_id="team-finance-governance")
    assert _service().evaluate(cross, _query(), _record()).reason == "invalid_identity"


def test_query_decision_is_bounded_and_denied_records_are_not_returned():
    identity = RuntimeIdentity.from_parts(project_id=PROJECT, consumer_id="agent-context-reader", team_id="team-finance-governance")
    result = _service().evaluate_query(identity, _query(kinds=("evidence", "governance")), [_record(), _record(kind="evidence")])
    assert result.status == "ready"
    assert len(result.decisions) == 2
    assert all(item.decision == "allow" for item in result.decisions)


def test_team_catalog_scope_is_an_independent_upper_bound():
    team_catalog = _team_catalog(scopes=("project",))
    policy = _policy_catalog()
    service = MemoryHubPolicyService(team_catalog, policy, project_id=PROJECT)
    identity = RuntimeIdentity.from_parts(project_id=PROJECT, consumer_id="agent-context-reader", team_id="team-finance-governance")
    decision = service.evaluate(identity, _query(scope="team"), _record(scope="team"))
    assert decision.decision == "deny"
    assert decision.reason == "scope_mismatch"


def test_agent_and_team_record_owners_are_isolated():
    identity = RuntimeIdentity.from_parts(project_id=PROJECT, consumer_id="agent-context-reader", team_id="team-finance-governance")
    agent_record = _record(scope="agent")
    object.__setattr__(agent_record, "owner", "agent-other")
    assert _service().evaluate(identity, _query(scope="agent"), agent_record).reason == "scope_mismatch"
    team_record = _record(scope="team")
    object.__setattr__(team_record, "owner", "team-other")
    assert _service().evaluate(identity, _query(scope="team"), team_record).reason == "scope_mismatch"


def test_explicit_deny_wins_over_overlapping_allow_rule():
    allow = {"schemaVersion": "memory-agent-policy-rule-v1", "memoryKinds": ["governance"], "scopes": ["project"], "decision": "allow", "ruleFingerprint": ""}
    allow["ruleFingerprint"] = canonical_fingerprint({k: v for k, v in allow.items() if k != "ruleFingerprint"})
    deny = {**allow, "decision": "deny", "ruleFingerprint": ""}
    deny["ruleFingerprint"] = canonical_fingerprint({k: v for k, v in deny.items() if k != "ruleFingerprint"})
    catalog = _policy_catalog(rules=sorted([allow, deny], key=lambda item: item["ruleFingerprint"]))
    identity = RuntimeIdentity.from_parts(project_id=PROJECT, consumer_id="agent-context-reader", team_id="team-finance-governance")
    decision = MemoryHubPolicyService(_team_catalog(), catalog, project_id=PROJECT).evaluate(identity, _query(), _record())
    assert decision.decision == "deny"
    assert decision.reason == "policy_deny"


def test_mixed_allowed_and_disallowed_kinds_are_denied():
    identity = RuntimeIdentity.from_parts(project_id=PROJECT, consumer_id="agent-context-reader", team_id="team-finance-governance")
    decision = _service().evaluate(identity, _query(kinds=("governance", "skill")), _record())
    assert decision.decision == "deny"
    assert decision.reason == "memory_kind_not_allowed"
    assert decision.record_returned is False


def test_query_evaluation_consumes_at_most_sixteen_records():
    identity = RuntimeIdentity.from_parts(project_id=PROJECT, consumer_id="agent-context-reader", team_id="team-finance-governance")
    consumed = 0

    def records():
        nonlocal consumed
        for _ in range(100):
            consumed += 1
            yield _record()

    result = _service().evaluate_query(identity, _query(), records())
    assert result.status == "ready"
    assert consumed == 16
