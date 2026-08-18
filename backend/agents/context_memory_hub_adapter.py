from __future__ import annotations

from pathlib import Path
from typing import Any

from .evidence_models import canonical_fingerprint
from .memory_hub_deployment_provider import deployment_owned_catalog_provider
from .memory_hub_models import MemoryHubSchemaError, MemoryQuery, RuntimeIdentity
from .memory_hub_projection import project_memory_result
from .memory_hub_service import MemoryHubService
from .memory_hub_agent_policy_catalog import AgentPolicyCatalog, MemoryHubAgentPolicyCatalogError
from .memory_hub_policy_service import MemoryHubPolicyService
from .memory_hub_team_catalog import TeamCatalog, MemoryHubTeamCatalogError
from .memory_sidecar_hint_models import MemoryHints, MemorySidecarSchemaError


PROJECT_ID = "nbs_analytics"
CONSUMER_ID = "context-agent"
_ALLOWED_KINDS = frozenset({"governance", "evidence", "skill"})


def _deployment_service(project_root: Path) -> MemoryHubService | None:
    """Load only the fixed deployment-owned catalog composition.

    C2 policy catalogs are intentionally required by the service.  Until the
    deployment supplies that composition this returns ``None`` and callers get
    a canonical-only, fail-closed result.
    """
    try:
        catalog = deployment_owned_catalog_provider(project_root)()
        if catalog is None:
            return None
        runtime_root = project_root.resolve(strict=False) / ".nbs_agent_runtime" / "memory-hub"
        team_path = runtime_root / "team-catalog.json"
        policy_path = runtime_root / "agent-policy-catalog.json"
        team = TeamCatalog.load(team_path, runtime_root=runtime_root, expected_project_id=PROJECT_ID)
        policy = AgentPolicyCatalog.load(policy_path, runtime_root=runtime_root, expected_project_id=PROJECT_ID, team_catalog=team)
        policy_service = MemoryHubPolicyService(team, policy, project_id=PROJECT_ID)
        if not policy_service.is_ready():
            return None
        return MemoryHubService(catalog, project_id=PROJECT_ID, policy_service=policy_service)
    except (MemoryHubAgentPolicyCatalogError, MemoryHubTeamCatalogError, MemoryHubSchemaError, OSError, TypeError, ValueError):
        return None


def _result(query: MemoryQuery, *, status: str, reason: str, hints: MemoryHints | None = None) -> dict[str, object]:
    query_fingerprint = query.query_fingerprint if isinstance(query, MemoryQuery) else canonical_fingerprint({"schemaVersion": "context-memory-invalid-query-v1"})
    payload = hints.to_dict() if hints is not None else MemoryHints.empty(query_fingerprint=query_fingerprint, status=status if status in {"empty", "timeout", "degraded"} else "degraded").to_dict()
    return {"status": status, "reason": reason, "memoryHints": payload}


def query_context_memory(*, project_root: Path, identity: RuntimeIdentity, query: MemoryQuery) -> dict[str, object]:
    """Return a bounded, non-authoritative MemoryHints projection.

    This adapter is read-only.  It never reads artifact bytes, changes a
    catalog, or lets callers replace the deployment identity/policy gate.
    """
    try:
        if not isinstance(project_root, Path) or project_root.is_symlink():
            raise ValueError("project root is invalid")
        if not isinstance(identity, RuntimeIdentity) or identity.project_id != PROJECT_ID or identity.consumer_id != CONSUMER_ID:
            raise ValueError("context identity is invalid")
        if not isinstance(query, MemoryQuery) or query.consumer_id != CONSUMER_ID or query.scope not in {"project", "team"}:
            raise ValueError("context query identity is invalid")
        if set(query.memory_kinds) != _ALLOWED_KINDS or query.max_items != 3 or query.max_bytes != 6000 or query.timeout_ms != 800:
            raise ValueError("context query bounds are invalid")
        service = _deployment_service(project_root)
        if service is None:
            return _result(query, status="blocked", reason="provider_unavailable")
        result = service.query(query, identity)
        if result.status != "ready":
            return _result(query, status=result.status, reason=result.status)
        projected = project_memory_result(result)
        if projected is None or projected.status != "ready":
            return _result(query, status="blocked", reason="invalid_or_stale")
        if any(item.freshness != "fresh" for item in projected.hints):
            return _result(query, status="blocked", reason="invalid_or_stale")
        # Re-parse the serialized model to enforce the public bounded schema.
        parsed = MemoryHints.from_dict(projected.to_dict())
        return _result(query, status="ready", reason="enriched", hints=parsed)
    except (MemoryHubSchemaError, MemorySidecarSchemaError, OSError, TypeError, ValueError):
        return _result(query, status="blocked", reason="invalid")
