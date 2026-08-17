from pathlib import Path

from backend.agents.context_memory_hub_adapter import query_context_memory
from backend.agents.memory_hub_models import MemoryQuery, RuntimeIdentity
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


def test_invalid_identity_and_query_fail_closed(tmp_path: Path):
    wrong_identity = RuntimeIdentity.from_parts(project_id="other", consumer_id="context-agent")
    result = query_context_memory(project_root=tmp_path, identity=wrong_identity, query=_query())
    assert result["status"] == "blocked"
    assert result["reason"] == "invalid"


def test_blocked_policy_result_has_no_hints(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("backend.agents.context_memory_hub_adapter._deployment_service", lambda root: memory_service(tmp_path))
    result = query_context_memory(project_root=tmp_path, identity=_identity(), query=_query(scope="team"))
    assert result["status"] in {"empty", "blocked"}
    assert result["memoryHints"]["hints"] == []

