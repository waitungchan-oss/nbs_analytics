from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .memory_hub_catalog import MemoryCatalog
from .memory_hub_models import (
    MemoryACLDecision,
    MemoryHubSchemaError,
    MemoryQuery,
    MemoryQueryResult,
    MemoryRecord,
    RuntimeIdentity,
)
from .memory_hub_policy_service import MemoryHubPolicyService


@dataclass(frozen=True)
class SourceResolution:
    status: str
    source_id: str
    artifact_ref: str | None
    artifact_sha256: str | None
    scope: str | None


class MemoryHubService:
    """Read-only query facade over one immutable MemoryCatalog."""

    def __init__(self, catalog: MemoryCatalog, *, project_id: str, policy_service: MemoryHubPolicyService | None = None) -> None:
        self.catalog = catalog
        self.project_id = project_id
        if policy_service is not None and type(policy_service) is not MemoryHubPolicyService:
            raise TypeError("policy_service must be a deployment-owned MemoryHubPolicyService")
        self.policy_service = policy_service

    @staticmethod
    def _decision(query: MemoryQuery, identity: RuntimeIdentity, record: MemoryRecord) -> MemoryACLDecision:
        if query.scope != record.scope:
            return MemoryACLDecision.from_parts(
                consumer_id=identity.consumer_id, requested_scope=query.scope, record_scope=record.scope,
                decision="deny", reason="scope_mismatch",
            )
        if record.scope == "project":
            return MemoryACLDecision.from_parts(
                consumer_id=identity.consumer_id, requested_scope=query.scope, record_scope=record.scope,
                decision="allow", reason="same_project",
            )
        if record.scope == "agent" and record.owner == identity.consumer_id:
            return MemoryACLDecision.from_parts(
                consumer_id=identity.consumer_id, requested_scope=query.scope, record_scope=record.scope,
                decision="allow", reason="same_agent",
            )
        if record.scope == "team" and identity.team_id is not None and record.owner == identity.team_id:
            return MemoryACLDecision.from_parts(
                consumer_id=identity.consumer_id, requested_scope=query.scope, record_scope=record.scope,
                decision="allow", reason="same_team",
            )
        return MemoryACLDecision.from_parts(
            consumer_id=identity.consumer_id, requested_scope=query.scope, record_scope=record.scope,
            decision="blocked", reason="missing_identity" if record.scope == "team" and identity.team_id is None else "scope_mismatch",
        )

    def _validate_identity(self, identity: RuntimeIdentity) -> bool:
        if not isinstance(identity, RuntimeIdentity):
            raise ValueError("runtime identity is invalid or belongs to another project")
        return identity.project_id == self.project_id

    def query(self, query: MemoryQuery, identity: RuntimeIdentity) -> MemoryQueryResult:
        if not isinstance(query, MemoryQuery):
            raise ValueError("memory query is invalid")
        if not self._validate_identity(identity):
            return MemoryQueryResult.from_parts(query_fingerprint=query.query_fingerprint, status="blocked", records=(), acl_decisions=())
        if query.consumer_id != identity.consumer_id:
            return MemoryQueryResult.from_parts(query_fingerprint=query.query_fingerprint, status="blocked", records=(), acl_decisions=())
        if not isinstance(self.catalog, MemoryCatalog):
            return MemoryQueryResult.from_parts(query_fingerprint=query.query_fingerprint, status="blocked", records=(), acl_decisions=())
        if self.policy_service is not None and not self.policy_service.is_ready():
            return MemoryQueryResult.from_parts(query_fingerprint=query.query_fingerprint, status="blocked", records=(), acl_decisions=())
        if query.scope == "team" and identity.team_id is None:
            return MemoryQueryResult.from_parts(query_fingerprint=query.query_fingerprint, status="blocked", records=(), acl_decisions=())
        now = datetime.now(timezone.utc)
        candidates = [
            record for record in self.catalog.records
            if record.memory_kind in query.memory_kinds
            and record.freshness == "fresh"
            and record.status == "ready"
            and all(source.status == "verified" and datetime.fromisoformat(source.expires_at) > now for source in record.source_refs)
        ]
        candidates.sort(key=lambda record: (record.memory_kind, record.memory_id))
        if self.policy_service is not None:
            policy_decisions = tuple(self.policy_service.evaluate(identity, query, record) for record in candidates)
            if any(decision.decision == "blocked" for decision in policy_decisions):
                return MemoryQueryResult.from_parts(query_fingerprint=query.query_fingerprint, status="blocked", records=(), acl_decisions=())
            policy_allowed = tuple(record for record, decision in zip(candidates, policy_decisions) if decision.decision == "allow")
            if not policy_allowed:
                status = "blocked" if any(decision.decision == "blocked" for decision in policy_decisions) else "empty"
                return MemoryQueryResult.from_parts(query_fingerprint=query.query_fingerprint, status=status, records=(), acl_decisions=())
            candidates = list(policy_allowed)
        decisions = tuple(self._decision(query, identity, record) for record in candidates)
        allowed = tuple(record for record, decision in zip(candidates, decisions) if decision.decision == "allow")[: query.max_items]
        returned_ids = {record.memory_id for record in allowed}
        returned_decisions = [decision for record, decision in zip(candidates, decisions) if record.memory_id in returned_ids]
        audit_decisions = [decision for record, decision in zip(candidates, decisions) if record.memory_id not in returned_ids]
        selected_decisions = tuple((returned_decisions + audit_decisions)[:16])
        status = "ready" if allowed else "empty"
        result = MemoryQueryResult.from_parts(query_fingerprint=query.query_fingerprint, status=status, records=allowed, acl_decisions=selected_decisions)
        if len(str(result.to_dict()).encode("utf-8")) > query.max_bytes:
            return MemoryQueryResult.from_parts(query_fingerprint=query.query_fingerprint, status="empty", records=(), acl_decisions=())
        return result

    def resolve_source(self, source_id: str, identity: RuntimeIdentity) -> SourceResolution:
        if not self._validate_identity(identity):
            return SourceResolution("blocked", source_id, None, None, None)
        if not isinstance(self.catalog, MemoryCatalog):
            return SourceResolution("blocked", source_id, None, None, None)
        if self.policy_service is not None and not self.policy_service.is_ready():
            return SourceResolution("blocked", source_id, None, None, None)
        if self.policy_service is not None:
            referenced = [record for record in self.catalog.records if any(source.source_id == source_id for source in record.source_refs)]
            try:
                policy_identity = identity
                policy_allowed = False
                for record in referenced:
                    policy_query = MemoryQuery.from_parts(
                        query="source resolution", consumer_id=identity.consumer_id,
                        scope=record.scope, memory_kinds=(record.memory_kind,),
                        max_items=3, max_bytes=6000, timeout_ms=800,
                    )
                    if self.policy_service.evaluate(policy_identity, policy_query, record).decision == "allow":
                        policy_allowed = True
                        break
            except (MemoryHubSchemaError, ValueError):
                policy_allowed = False
            if not policy_allowed:
                return SourceResolution("blocked", source_id, None, None, None)
        source = next((item for item in self.catalog.sources if item.source_id == source_id), None)
        now = datetime.now(timezone.utc)
        if source is None or source.status != "verified" or datetime.fromisoformat(source.expires_at) <= now:
            return SourceResolution("empty", source_id, None, None, None)
        if source.scope == "project":
            allowed = True
        elif source.scope == "agent":
            allowed = source.owner == identity.consumer_id
        else:
            allowed = identity.team_id is not None and source.owner == identity.team_id
        if not allowed:
            return SourceResolution("blocked", source_id, None, None, source.scope)
        return SourceResolution("ready", source.source_id, source.artifact_ref, source.artifact_sha256, source.scope)
