from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .evidence_models import canonical_fingerprint
from .memory_hub_models import MEMORY_KINDS, SCOPES
from .memory_hub_team_catalog import TeamCatalog


AGENT_POLICY_CATALOG_SCHEMA = "memory-agent-policy-catalog-v1"
AGENT_POLICY_RECORD_SCHEMA = "memory-agent-policy-record-v1"
AGENT_POLICY_RULE_SCHEMA = "memory-agent-policy-rule-v1"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class MemoryHubAgentPolicyCatalogError(ValueError):
    """Raised when an immutable Agent Policy Catalog is malformed or unsafe."""


def _id(value: Any, key: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise MemoryHubAgentPolicyCatalogError(f"{key} is invalid")
    return value


def _sha(value: Any, key: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise MemoryHubAgentPolicyCatalogError(f"{key} must be a lowercase SHA-256")
    return value


def _exact(payload: Mapping[str, Any], expected: set[str], label: str) -> None:
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise MemoryHubAgentPolicyCatalogError(f"{label} keys are invalid")


def _sorted_ids(values: Any, key: str) -> tuple[str, ...]:
    if not isinstance(values, list) or not values or any(not isinstance(value, str) for value in values):
        raise MemoryHubAgentPolicyCatalogError(f"{key} must be a non-empty list")
    normalized = tuple(_id(value, key) for value in values)
    if len(set(normalized)) != len(normalized) or normalized != tuple(sorted(normalized)):
        raise MemoryHubAgentPolicyCatalogError(f"{key} must be unique and sorted")
    return normalized


def _sorted_allowed(values: Any, key: str, allowed: frozenset[str]) -> tuple[str, ...]:
    normalized = _sorted_ids(values, key)
    if any(value not in allowed for value in normalized):
        raise MemoryHubAgentPolicyCatalogError(f"{key} contains an unsupported value")
    return normalized


@dataclass(frozen=True)
class AgentPolicyRule:
    memory_kinds: tuple[str, ...]
    scopes: tuple[str, ...]
    decision: str
    rule_fingerprint: str

    def __post_init__(self) -> None:
        if not self.memory_kinds or tuple(sorted(self.memory_kinds)) != self.memory_kinds or len(set(self.memory_kinds)) != len(self.memory_kinds) or any(kind not in MEMORY_KINDS for kind in self.memory_kinds):
            raise MemoryHubAgentPolicyCatalogError("rule memoryKinds are invalid")
        if not self.scopes or tuple(sorted(self.scopes)) != self.scopes or len(set(self.scopes)) != len(self.scopes) or any(scope not in SCOPES for scope in self.scopes):
            raise MemoryHubAgentPolicyCatalogError("rule scopes are invalid")
        if self.decision not in {"allow", "deny"}:
            raise MemoryHubAgentPolicyCatalogError("rule decision is invalid")
        _sha(self.rule_fingerprint, "ruleFingerprint")
        if self.rule_fingerprint != canonical_fingerprint(self._unsigned()):
            raise MemoryHubAgentPolicyCatalogError("ruleFingerprint mismatch")

    def _unsigned(self) -> dict[str, Any]:
        return {
            "schemaVersion": AGENT_POLICY_RULE_SCHEMA,
            "memoryKinds": list(self.memory_kinds),
            "scopes": list(self.scopes),
            "decision": self.decision,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._unsigned(), "ruleFingerprint": self.rule_fingerprint}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AgentPolicyRule":
        _exact(payload, {"schemaVersion", "memoryKinds", "scopes", "decision", "ruleFingerprint"}, "policy rule")
        if payload["schemaVersion"] != AGENT_POLICY_RULE_SCHEMA:
            raise MemoryHubAgentPolicyCatalogError("policy rule schema is invalid")
        return cls(
            _sorted_allowed(payload["memoryKinds"], "memoryKinds", MEMORY_KINDS),
            _sorted_allowed(payload["scopes"], "scopes", SCOPES),
            payload["decision"],
            _sha(payload["ruleFingerprint"], "ruleFingerprint"),
        )


@dataclass(frozen=True)
class AgentPolicyRecord:
    agent_id: str
    agent_class: str
    team_ids: tuple[str, ...]
    allowed_memory_kinds: tuple[str, ...]
    allowed_scopes: tuple[str, ...]
    rules: tuple[AgentPolicyRule, ...]
    record_fingerprint: str

    def __post_init__(self) -> None:
        _id(self.agent_id, "agentId")
        _id(self.agent_class, "agentClass")
        if not self.team_ids or tuple(sorted(self.team_ids)) != self.team_ids or len(set(self.team_ids)) != len(self.team_ids):
            raise MemoryHubAgentPolicyCatalogError("teamIds must be unique and sorted")
        if not self.allowed_memory_kinds or tuple(sorted(self.allowed_memory_kinds)) != self.allowed_memory_kinds or len(set(self.allowed_memory_kinds)) != len(self.allowed_memory_kinds) or any(kind not in MEMORY_KINDS for kind in self.allowed_memory_kinds):
            raise MemoryHubAgentPolicyCatalogError("allowedMemoryKinds are invalid")
        if not self.allowed_scopes or tuple(sorted(self.allowed_scopes)) != self.allowed_scopes or len(set(self.allowed_scopes)) != len(self.allowed_scopes) or any(scope not in SCOPES for scope in self.allowed_scopes):
            raise MemoryHubAgentPolicyCatalogError("allowedScopes are invalid")
        if not self.rules or tuple(sorted(self.rules, key=lambda rule: rule.rule_fingerprint)) != self.rules or len({rule.rule_fingerprint for rule in self.rules}) != len(self.rules):
            raise MemoryHubAgentPolicyCatalogError("rules must be unique and sorted")
        for rule in self.rules:
            if not set(rule.memory_kinds).issubset(self.allowed_memory_kinds) or not set(rule.scopes).issubset(self.allowed_scopes):
                raise MemoryHubAgentPolicyCatalogError("rule exceeds agent allowlists")
        _sha(self.record_fingerprint, "recordFingerprint")
        if self.record_fingerprint != canonical_fingerprint(self._unsigned()):
            raise MemoryHubAgentPolicyCatalogError("recordFingerprint mismatch")

    def _unsigned(self) -> dict[str, Any]:
        return {
            "schemaVersion": AGENT_POLICY_RECORD_SCHEMA,
            "agentId": self.agent_id,
            "agentClass": self.agent_class,
            "teamIds": list(self.team_ids),
            "allowedMemoryKinds": list(self.allowed_memory_kinds),
            "allowedScopes": list(self.allowed_scopes),
            "rules": [rule.to_dict() for rule in self.rules],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._unsigned(), "recordFingerprint": self.record_fingerprint}

    def allows(self, memory_kind: str, scope: str) -> bool:
        if memory_kind not in self.allowed_memory_kinds or scope not in self.allowed_scopes:
            return False
        for rule in self.rules:
            if memory_kind in rule.memory_kinds and scope in rule.scopes:
                return rule.decision == "allow"
        return False

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AgentPolicyRecord":
        _exact(payload, {"schemaVersion", "agentId", "agentClass", "teamIds", "allowedMemoryKinds", "allowedScopes", "rules", "recordFingerprint"}, "policy record")
        if payload["schemaVersion"] != AGENT_POLICY_RECORD_SCHEMA or not isinstance(payload["rules"], list) or not payload["rules"]:
            raise MemoryHubAgentPolicyCatalogError("policy record schema is invalid")
        rules = tuple(AgentPolicyRule.from_dict(rule) for rule in payload["rules"])
        return cls(
            _id(payload["agentId"], "agentId"),
            _id(payload["agentClass"], "agentClass"),
            _sorted_ids(payload["teamIds"], "teamIds"),
            _sorted_allowed(payload["allowedMemoryKinds"], "allowedMemoryKinds", MEMORY_KINDS),
            _sorted_allowed(payload["allowedScopes"], "allowedScopes", SCOPES),
            rules,
            _sha(payload["recordFingerprint"], "recordFingerprint"),
        )


@dataclass(frozen=True)
class AgentPolicyCatalog:
    project_id: str
    agents: tuple[AgentPolicyRecord, ...]
    default_decision: str
    catalog_fingerprint: str

    def __post_init__(self) -> None:
        _id(self.project_id, "projectId")
        if self.default_decision != "deny":
            raise MemoryHubAgentPolicyCatalogError("defaultDecision must be deny")
        if not self.agents or tuple(sorted(self.agents, key=lambda agent: agent.agent_id)) != self.agents:
            raise MemoryHubAgentPolicyCatalogError("agents must be unique and sorted")
        if len({agent.agent_id for agent in self.agents}) != len(self.agents):
            raise MemoryHubAgentPolicyCatalogError("agentIds must be unique")
        _sha(self.catalog_fingerprint, "catalogFingerprint")
        if self.catalog_fingerprint != canonical_fingerprint(self._unsigned()):
            raise MemoryHubAgentPolicyCatalogError("catalogFingerprint mismatch")

    def _unsigned(self) -> dict[str, Any]:
        return {
            "schemaVersion": AGENT_POLICY_CATALOG_SCHEMA,
            "projectId": self.project_id,
            "agents": [agent.to_dict() for agent in self.agents],
            "defaultDecision": self.default_decision,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._unsigned(), "catalogFingerprint": self.catalog_fingerprint}

    def agent(self, agent_id: str) -> AgentPolicyRecord | None:
        return next((agent for agent in self.agents if agent.agent_id == agent_id), None)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], *, expected_project_id: str, team_catalog: TeamCatalog) -> "AgentPolicyCatalog":
        _exact(payload, {"schemaVersion", "projectId", "agents", "defaultDecision", "catalogFingerprint"}, "agent policy catalog")
        if payload["schemaVersion"] != AGENT_POLICY_CATALOG_SCHEMA or payload["defaultDecision"] != "deny":
            raise MemoryHubAgentPolicyCatalogError("agent policy catalog schema/default is invalid")
        project_id = _id(payload["projectId"], "projectId")
        expected = _id(expected_project_id, "expectedProjectId")
        if project_id != expected or team_catalog.project_id != project_id or not isinstance(payload["agents"], list) or not payload["agents"]:
            raise MemoryHubAgentPolicyCatalogError("agent policy project or agents are invalid")
        agents = tuple(AgentPolicyRecord.from_dict(item) for item in payload["agents"])
        for agent in agents:
            for team_id in agent.team_ids:
                team = team_catalog.team(team_id)
                if team is None or agent.agent_id not in team.agent_ids:
                    raise MemoryHubAgentPolicyCatalogError("agent policy references an unknown or non-member team")
        return cls(project_id, agents, "deny", _sha(payload["catalogFingerprint"], "catalogFingerprint"))

    @classmethod
    def load(cls, path: Path, *, runtime_root: Path, expected_project_id: str, team_catalog: TeamCatalog) -> "AgentPolicyCatalog":
        if not isinstance(path, Path) or not isinstance(runtime_root, Path) or runtime_root.is_symlink() or not runtime_root.exists() or not runtime_root.is_dir():
            raise MemoryHubAgentPolicyCatalogError("runtimeRoot must be a regular directory")
        root = runtime_root.resolve(strict=True)
        raw = path.absolute()
        try:
            relative = raw.relative_to(root)
        except ValueError as exc:
            raise MemoryHubAgentPolicyCatalogError("catalog path escapes runtimeRoot") from exc
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise MemoryHubAgentPolicyCatalogError("catalog path contains a symlink")
        try:
            candidate = raw.resolve(strict=True)
            candidate.relative_to(root)
        except (FileNotFoundError, ValueError) as exc:
            raise MemoryHubAgentPolicyCatalogError("catalog path escapes runtimeRoot or is missing") from exc
        if candidate.is_symlink() or not candidate.is_file():
            raise MemoryHubAgentPolicyCatalogError("catalog path must be a regular file")
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise MemoryHubAgentPolicyCatalogError("catalog payload is unreadable") from exc
        return cls.from_dict(payload, expected_project_id=expected_project_id, team_catalog=team_catalog)
