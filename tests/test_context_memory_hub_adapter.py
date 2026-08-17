from pathlib import Path
import json

from backend.agents.context_memory_hub_adapter import query_context_memory
from backend.agents.memory_hub_models import MemoryQuery, RuntimeIdentity
from backend.agents.evidence_models import canonical_fingerprint
from tests.test_memory_hub_deployment_provider import _fixture
from tests.test_memory_hub_policy_service import _policy_catalog, _team_catalog
from tests.test_memory_hub_service import _service as memory_service


def _query(scope="project"):
    return MemoryQuery.from_parts(
        query="verified governance guidance", consumer_id="context-agent", scope=scope,
        memory_kinds=("governance", "evidence", "skill"), max_items=3, max_bytes=6000, timeout_ms=800,
    )


def _identity(team_id=None):
    return RuntimeIdentity.from_parts(project_id="nbs_analytics", consumer_id="context-agent", team_id=team_id)


def test_missing_deployment_composition_is_blocked_and_bounded(tmp_path: Path):
    result = query_context_memory(project_root=tmp_path, identity=_identity(), query=_query())
    assert result["status"] == "blocked"
    assert result["reason"] == "provider_unavailable"
    assert result["memoryHints"]["status"] == "degraded"
    assert result["memoryHints"]["limits"] == {"maxItems": 3, "maxBytes": 6000, "timeoutMs": 800}


def test_ready_projection_uses_existing_non_authoritative_hint_contract(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("backend.agents.context_memory_hub_adapter._deployment_service", lambda root: memory_service(tmp_path))
    result = query_context_memory(project_root=tmp_path, identity=_identity(), query=_query())
    assert result["status"] == "ready"
    assert result["reason"] == "enriched"
    assert result["memoryHints"]["status"] == "ready"
    assert len(result["memoryHints"]["hints"]) == 1
    assert result["memoryHints"]["limits"] == {"maxItems": 3, "maxBytes": 6000, "timeoutMs": 800}


def test_ready_projection_uses_real_deployment_catalog_and_policy_files(tmp_path: Path):
    _fixture(tmp_path)
    runtime = tmp_path / ".nbs_agent_runtime" / "memory-hub"
    team_payload = _team_catalog().to_dict()
    team_payload["teams"][0]["agentIds"] = ["context-agent"]
    team_payload["teams"][0]["recordFingerprint"] = canonical_fingerprint({key: value for key, value in team_payload["teams"][0].items() if key != "recordFingerprint"})
    team_payload["catalogFingerprint"] = canonical_fingerprint({key: value for key, value in team_payload.items() if key != "catalogFingerprint"})
    policy_payload = _policy_catalog().to_dict()
    policy_payload["agents"][0]["agentId"] = "context-agent"
    policy_payload["agents"][0]["allowedMemoryKinds"] = ["evidence", "governance", "skill"]
    for rule in policy_payload["agents"][0]["rules"]:
        rule["memoryKinds"] = ["evidence", "governance", "skill"]
        rule["ruleFingerprint"] = canonical_fingerprint({key: value for key, value in rule.items() if key != "ruleFingerprint"})
    policy_payload["agents"][0]["recordFingerprint"] = canonical_fingerprint({key: value for key, value in policy_payload["agents"][0].items() if key != "recordFingerprint"})
    policy_payload["catalogFingerprint"] = canonical_fingerprint({key: value for key, value in policy_payload.items() if key != "catalogFingerprint"})
    (runtime / "team-catalog.json").write_text(json.dumps(team_payload), encoding="utf-8")
    (runtime / "agent-policy-catalog.json").write_text(json.dumps(policy_payload), encoding="utf-8")
    result = query_context_memory(project_root=tmp_path, identity=_identity(), query=_query())
    assert result["status"] == "ready"
    assert result["reason"] == "enriched"
    assert result["memoryHints"]["hints"]


def test_invalid_identity_and_query_fail_closed(tmp_path: Path):
    wrong_identity = RuntimeIdentity.from_parts(project_id="other", consumer_id="context-agent")
    result = query_context_memory(project_root=tmp_path, identity=wrong_identity, query=_query())
    assert result["status"] == "blocked"
    assert result["reason"] == "invalid"


def test_query_kind_subset_fails_closed(tmp_path: Path):
    result = query_context_memory(
        project_root=tmp_path,
        identity=_identity(),
        query=MemoryQuery.from_parts(query="governance only", consumer_id="context-agent", scope="project", memory_kinds=("governance",)),
    )
    assert result["status"] == "blocked"
    assert result["reason"] == "invalid"


def test_blocked_policy_result_has_no_hints(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("backend.agents.context_memory_hub_adapter._deployment_service", lambda root: memory_service(tmp_path))
    result = query_context_memory(project_root=tmp_path, identity=_identity(), query=_query(scope="team"))
    assert result["status"] == "blocked"
    assert result["memoryHints"]["hints"] == []
