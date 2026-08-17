from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice
import re
from typing import Any, Iterable

from .evidence_models import canonical_fingerprint
from .memory_hub_agent_policy_catalog import AgentPolicyCatalog, MemoryHubAgentPolicyCatalogError
from .memory_hub_models import MemoryQuery, MemoryRecord, RuntimeIdentity
from .memory_hub_team_catalog import TeamCatalog, MemoryHubTeamCatalogError


POLICY_DECISION_SCHEMA = "memory-policy-decision-v1"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_REASONS = {
    "same_project", "same_agent", "same_team", "policy_allow", "policy_deny",
    "scope_mismatch", "memory_kind_not_allowed", "agent_not_in_team",
    "missing_identity", "invalid_identity", "team_catalog_missing",
    "agent_policy_catalog_missing", "catalog_fingerprint_mismatch", "cross_project_catalog",
    "unknown_agent", "unknown_team", "record_invalid", "record_stale", "source_blocked",
}


class MemoryHubPolicyError(ValueError):
    """Raised only for malformed service construction, never for a policy deny."""


def _sha(value: Any, key: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise MemoryHubPolicyError(f"{key} must be a lowercase SHA-256")
    return value


@dataclass(frozen=True)
class MemoryPolicyDecision:
    project_id: str
    agent_id: str
    team_id: str | None
    memory_id: str | None
    requested_scope: str
    record_scope: str | None
    decision: str
    reason: str
    team_catalog_fingerprint: str | None
    agent_policy_catalog_fingerprint: str | None
    record_returned: bool
    summary: str | None
    decision_fingerprint: str

    def __post_init__(self) -> None:
        if self.decision not in {"allow", "deny", "blocked"} or self.reason not in _REASONS:
            raise MemoryHubPolicyError("policy decision status or reason is invalid")
        if self.record_returned != (self.decision == "allow"):
            raise MemoryHubPolicyError("recordReturned must match allow decision")
        if not self.record_returned and self.summary is not None:
            raise MemoryHubPolicyError("non-allow decision must not carry summary")
        if self.record_returned and (self.memory_id is None or self.summary is None):
            raise MemoryHubPolicyError("allow decision requires record identity and summary")
        if self.team_catalog_fingerprint is not None:
            _sha(self.team_catalog_fingerprint, "teamCatalogFingerprint")
        if self.agent_policy_catalog_fingerprint is not None:
            _sha(self.agent_policy_catalog_fingerprint, "agentPolicyCatalogFingerprint")
        _sha(self.decision_fingerprint, "decisionFingerprint")
        if self.decision_fingerprint != canonical_fingerprint(self._unsigned()):
            raise MemoryHubPolicyError("decisionFingerprint mismatch")

    def _unsigned(self) -> dict[str, Any]:
        return {
            "schemaVersion": POLICY_DECISION_SCHEMA,
            "projectId": self.project_id,
            "agentId": self.agent_id,
            "teamId": self.team_id,
            "memoryId": self.memory_id,
            "requestedScope": self.requested_scope,
            "recordScope": self.record_scope,
            "decision": self.decision,
            "reason": self.reason,
            "teamCatalogFingerprint": self.team_catalog_fingerprint,
            "agentPolicyCatalogFingerprint": self.agent_policy_catalog_fingerprint,
            "recordReturned": self.record_returned,
            **({"summary": self.summary} if self.record_returned else {}),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._unsigned(), "decisionFingerprint": self.decision_fingerprint}


@dataclass(frozen=True)
class MemoryPolicyQueryDecision:
    status: str
    decisions: tuple[MemoryPolicyDecision, ...]
    records: tuple[MemoryRecord, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"schemaVersion": "memory-policy-query-decision-v1", "status": self.status, "decisions": [item.to_dict() for item in self.decisions], "records": [record.to_dict() for record in self.records]}


class MemoryHubPolicyService:
    """Deterministic, local, read-only policy decision service."""

    def __init__(self, team_catalog: TeamCatalog | None, agent_policy_catalog: AgentPolicyCatalog | None, *, project_id: str) -> None:
        self.team_catalog = team_catalog
        self.agent_policy_catalog = agent_policy_catalog
        self.project_id = project_id

    def _catalogs(self) -> tuple[TeamCatalog, AgentPolicyCatalog] | None:
        if not isinstance(self.team_catalog, TeamCatalog):
            return None
        if not isinstance(self.agent_policy_catalog, AgentPolicyCatalog):
            return None
        try:
            team = TeamCatalog.from_dict(self.team_catalog.to_dict(), expected_project_id=self.project_id)
            policy = AgentPolicyCatalog.from_dict(self.agent_policy_catalog.to_dict(), expected_project_id=self.project_id, team_catalog=team)
        except (MemoryHubTeamCatalogError, MemoryHubAgentPolicyCatalogError, MemoryHubPolicyError, TypeError, ValueError):
            return None
        if team.project_id != policy.project_id or team.project_id != self.project_id:
            return None
        return team, policy

    def is_ready(self) -> bool:
        """Return whether both deployment-owned catalogs pass read-only validation."""
        return self._catalogs() is not None

    @staticmethod
    def _record_integrity(record: MemoryRecord) -> tuple[bool, bool]:
        try:
            unsigned = record._unsigned()
            expected_memory_id = canonical_fingerprint(unsigned)
            expected_record = canonical_fingerprint({**unsigned, "memoryId": expected_memory_id})
            if record.memory_id != expected_memory_id or record.record_fingerprint != expected_record:
                return False, False
            for source in record.source_refs:
                source_unsigned = source._unsigned()
                expected_source_id = canonical_fingerprint(source_unsigned)
                expected_source_fp = canonical_fingerprint({**source_unsigned, "sourceId": expected_source_id})
                if source.source_id != expected_source_id or source.source_fingerprint != expected_source_fp:
                    return True, False
        except (AttributeError, TypeError, ValueError):
            return False, False
        return True, True

    @staticmethod
    def _policy_allows(agent: Any, memory_kind: str, scope: str) -> bool:
        if memory_kind not in agent.allowed_memory_kinds or scope not in agent.allowed_scopes:
            return False
        matching = [rule for rule in agent.rules if memory_kind in rule.memory_kinds and scope in rule.scopes]
        if any(rule.decision == "deny" for rule in matching):
            return False
        return any(rule.decision == "allow" for rule in matching)

    def _decision(self, *, identity: RuntimeIdentity, query: MemoryQuery, record: MemoryRecord | None, decision: str, reason: str, catalogs: tuple[TeamCatalog, AgentPolicyCatalog] | None) -> MemoryPolicyDecision:
        team_fp = catalogs[0].catalog_fingerprint if catalogs else None
        policy_fp = catalogs[1].catalog_fingerprint if catalogs else None
        memory_id = record.memory_id if isinstance(record, MemoryRecord) else None
        record_scope = record.scope if isinstance(record, MemoryRecord) else None
        returned = decision == "allow"
        summary = record.summary if returned and isinstance(record, MemoryRecord) else None
        unsigned = {
            "schemaVersion": POLICY_DECISION_SCHEMA,
            "projectId": self.project_id,
            "agentId": identity.consumer_id,
            "teamId": identity.team_id,
            "memoryId": memory_id,
            "requestedScope": query.scope,
            "recordScope": record_scope,
            "decision": decision,
            "reason": reason,
            "teamCatalogFingerprint": team_fp,
            "agentPolicyCatalogFingerprint": policy_fp,
            "recordReturned": returned,
            **({"summary": summary} if returned else {}),
        }
        return MemoryPolicyDecision(self.project_id, identity.consumer_id, identity.team_id, memory_id, query.scope, record_scope, decision, reason, team_fp, policy_fp, returned, summary, canonical_fingerprint(unsigned))

    def evaluate(self, identity: RuntimeIdentity, query: MemoryQuery, record: MemoryRecord) -> MemoryPolicyDecision:
        if not isinstance(identity, RuntimeIdentity) or not isinstance(query, MemoryQuery) or identity.project_id != self.project_id or query.consumer_id != identity.consumer_id:
            identity = identity if isinstance(identity, RuntimeIdentity) else RuntimeIdentity.from_parts(project_id=self.project_id, consumer_id="invalid-agent")
            return self._decision(identity=identity, query=query if isinstance(query, MemoryQuery) else MemoryQuery.from_parts(query="invalid", consumer_id=identity.consumer_id, scope="project", memory_kinds=("governance",)), record=None, decision="blocked", reason="invalid_identity", catalogs=None)
        if self.team_catalog is None:
            return self._decision(identity=identity, query=query, record=None, decision="blocked", reason="team_catalog_missing", catalogs=None)
        if self.agent_policy_catalog is None:
            return self._decision(identity=identity, query=query, record=None, decision="blocked", reason="agent_policy_catalog_missing", catalogs=None)
        if isinstance(self.team_catalog, TeamCatalog) and self.team_catalog.project_id != self.project_id:
            return self._decision(identity=identity, query=query, record=None, decision="blocked", reason="cross_project_catalog", catalogs=None)
        if isinstance(self.agent_policy_catalog, AgentPolicyCatalog) and self.agent_policy_catalog.project_id != self.project_id:
            return self._decision(identity=identity, query=query, record=None, decision="blocked", reason="cross_project_catalog", catalogs=None)
        catalogs = self._catalogs()
        if catalogs is None:
            return self._decision(identity=identity, query=query, record=None, decision="blocked", reason="team_catalog_missing" if self.team_catalog is None else "catalog_fingerprint_mismatch", catalogs=None)
        team_catalog, policy_catalog = catalogs
        agent = policy_catalog.agent(identity.consumer_id)
        if agent is None:
            return self._decision(identity=identity, query=query, record=None, decision="blocked", reason="unknown_agent", catalogs=catalogs)
        if query.scope == "team" and identity.team_id is None:
            return self._decision(identity=identity, query=query, record=record, decision="blocked", reason="missing_identity", catalogs=catalogs)
        team = team_catalog.team(identity.team_id) if identity.team_id is not None else None
        if identity.team_id is not None and team is None:
            return self._decision(identity=identity, query=query, record=record, decision="blocked", reason="unknown_team", catalogs=catalogs)
        if identity.team_id is not None and identity.team_id not in agent.team_ids:
            return self._decision(identity=identity, query=query, record=record, decision="deny", reason="agent_not_in_team", catalogs=catalogs)
        if team is not None and query.scope not in team.allowed_scopes:
            return self._decision(identity=identity, query=query, record=record, decision="deny", reason="scope_mismatch", catalogs=catalogs)
        if query.scope not in agent.allowed_scopes:
            return self._decision(identity=identity, query=query, record=record, decision="deny", reason="scope_mismatch", catalogs=catalogs)
        if any(kind not in agent.allowed_memory_kinds for kind in query.memory_kinds):
            return self._decision(identity=identity, query=query, record=record, decision="deny", reason="memory_kind_not_allowed", catalogs=catalogs)
        if not isinstance(record, MemoryRecord):
            return self._decision(identity=identity, query=query, record=None, decision="blocked", reason="record_invalid", catalogs=catalogs)
        if record.memory_kind not in query.memory_kinds or record.scope != query.scope:
            return self._decision(identity=identity, query=query, record=record, decision="deny", reason="scope_mismatch" if record.scope != query.scope else "memory_kind_not_allowed", catalogs=catalogs)
        if record.scope == "agent" and record.owner != identity.consumer_id:
            return self._decision(identity=identity, query=query, record=record, decision="deny", reason="scope_mismatch", catalogs=catalogs)
        if record.scope == "team" and (identity.team_id is None or record.owner != identity.team_id):
            return self._decision(identity=identity, query=query, record=record, decision="deny", reason="scope_mismatch", catalogs=catalogs)
        if not self._policy_allows(agent, record.memory_kind, record.scope):
            return self._decision(identity=identity, query=query, record=record, decision="deny", reason="policy_deny", catalogs=catalogs)
        now = datetime.now(timezone.utc)
        if record.freshness != "fresh":
            return self._decision(identity=identity, query=query, record=record, decision="blocked", reason="record_stale", catalogs=catalogs)
        try:
            source_blocked = record.status != "ready" or any(source.status != "verified" or datetime.fromisoformat(source.expires_at) <= now for source in record.source_refs)
        except (TypeError, ValueError):
            source_blocked = True
        if source_blocked:
            return self._decision(identity=identity, query=query, record=record, decision="blocked", reason="source_blocked", catalogs=catalogs)
        record_ok, sources_ok = self._record_integrity(record)
        if not record_ok:
            return self._decision(identity=identity, query=query, record=record, decision="blocked", reason="record_invalid", catalogs=catalogs)
        if not sources_ok:
            return self._decision(identity=identity, query=query, record=record, decision="blocked", reason="source_blocked", catalogs=catalogs)
        return self._decision(identity=identity, query=query, record=record, decision="allow", reason="policy_allow", catalogs=catalogs)

    def evaluate_query(self, identity: RuntimeIdentity, query: MemoryQuery, records: Iterable[MemoryRecord]) -> MemoryPolicyQueryDecision:
        decisions: list[MemoryPolicyDecision] = []
        allowed: list[MemoryRecord] = []
        for record in islice(records, 16):
            decision = self.evaluate(identity, query, record)
            decisions.append(decision)
            if decision.decision == "allow" and len(allowed) < query.max_items:
                allowed.append(record)
        status = "ready" if allowed else ("blocked" if any(item.decision == "blocked" for item in decisions) else "empty")
        return MemoryPolicyQueryDecision(status, tuple(decisions), tuple(allowed))
