from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .evidence_models import canonical_fingerprint


TEAM_CATALOG_SCHEMA = "memory-team-catalog-v1"
TEAM_RECORD_SCHEMA = "memory-team-record-v1"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SCOPES = frozenset({"project", "agent", "team"})


class MemoryHubTeamCatalogError(ValueError):
    """Raised when the immutable deployment-owned Team Catalog is unsafe."""


def _id(value: Any, key: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise MemoryHubTeamCatalogError(f"{key} is invalid")
    return value


def _sha(value: Any, key: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise MemoryHubTeamCatalogError(f"{key} must be a lowercase SHA-256")
    return value


def _exact(payload: Mapping[str, Any], expected: set[str], label: str) -> None:
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise MemoryHubTeamCatalogError(f"{label} keys are invalid")


def _sorted_unique(values: Any, key: str) -> tuple[str, ...]:
    if not isinstance(values, list) or not values or any(not isinstance(item, str) for item in values):
        raise MemoryHubTeamCatalogError(f"{key} must be a non-empty list")
    normalized = tuple(_id(item, key) for item in values)
    if len(set(normalized)) != len(normalized) or normalized != tuple(sorted(normalized)):
        raise MemoryHubTeamCatalogError(f"{key} must be unique and deterministically sorted")
    return normalized


@dataclass(frozen=True)
class TeamRecord:
    team_id: str
    role: str
    agent_ids: tuple[str, ...]
    allowed_scopes: tuple[str, ...]
    record_fingerprint: str

    def __post_init__(self) -> None:
        _id(self.team_id, "teamId")
        _id(self.role, "role")
        if not self.agent_ids or self.agent_ids != tuple(sorted(self.agent_ids)) or len(set(self.agent_ids)) != len(self.agent_ids):
            raise MemoryHubTeamCatalogError("agentIds must be unique and sorted")
        if not self.allowed_scopes or self.allowed_scopes != tuple(sorted(self.allowed_scopes)) or len(set(self.allowed_scopes)) != len(self.allowed_scopes):
            raise MemoryHubTeamCatalogError("allowedScopes must be unique and sorted")
        if any(scope not in _SCOPES for scope in self.allowed_scopes):
            raise MemoryHubTeamCatalogError("allowedScopes contains an unknown scope")
        _sha(self.record_fingerprint, "recordFingerprint")
        if self.record_fingerprint != canonical_fingerprint(self._unsigned()):
            raise MemoryHubTeamCatalogError("recordFingerprint mismatch")

    def _unsigned(self) -> dict[str, Any]:
        return {
            "schemaVersion": TEAM_RECORD_SCHEMA,
            "teamId": self.team_id,
            "role": self.role,
            "agentIds": list(self.agent_ids),
            "allowedScopes": list(self.allowed_scopes),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._unsigned(), "recordFingerprint": self.record_fingerprint}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TeamRecord":
        _exact(payload, {"schemaVersion", "teamId", "role", "agentIds", "allowedScopes", "recordFingerprint"}, "team record")
        if payload["schemaVersion"] != TEAM_RECORD_SCHEMA:
            raise MemoryHubTeamCatalogError("team record schema is invalid")
        agent_ids = _sorted_unique(payload["agentIds"], "agentIds")
        scopes = _sorted_unique(payload["allowedScopes"], "allowedScopes")
        return cls(
            _id(payload["teamId"], "teamId"),
            _id(payload["role"], "role"),
            agent_ids,
            scopes,
            _sha(payload["recordFingerprint"], "recordFingerprint"),
        )


@dataclass(frozen=True)
class TeamCatalog:
    project_id: str
    teams: tuple[TeamRecord, ...]
    catalog_fingerprint: str

    def __post_init__(self) -> None:
        _id(self.project_id, "projectId")
        if not self.teams or tuple(sorted(self.teams, key=lambda item: item.team_id)) != self.teams:
            raise MemoryHubTeamCatalogError("teams must be unique and deterministically sorted")
        team_ids = [team.team_id for team in self.teams]
        if len(set(team_ids)) != len(team_ids):
            raise MemoryHubTeamCatalogError("teamIds must be unique")
        all_agents = [agent_id for team in self.teams for agent_id in team.agent_ids]
        if len(set(all_agents)) != len(all_agents):
            raise MemoryHubTeamCatalogError("an agent cannot belong to multiple teams in one catalog")
        _sha(self.catalog_fingerprint, "catalogFingerprint")
        if self.catalog_fingerprint != canonical_fingerprint(self._unsigned()):
            raise MemoryHubTeamCatalogError("catalogFingerprint mismatch")

    def _unsigned(self) -> dict[str, Any]:
        return {
            "schemaVersion": TEAM_CATALOG_SCHEMA,
            "projectId": self.project_id,
            "teams": [team.to_dict() for team in self.teams],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._unsigned(), "catalogFingerprint": self.catalog_fingerprint}

    def team(self, team_id: str) -> TeamRecord | None:
        try:
            _id(team_id, "teamId")
        except MemoryHubTeamCatalogError:
            return None
        return next((team for team in self.teams if team.team_id == team_id), None)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], *, expected_project_id: str) -> "TeamCatalog":
        _exact(payload, {"schemaVersion", "projectId", "teams", "catalogFingerprint"}, "team catalog")
        if payload["schemaVersion"] != TEAM_CATALOG_SCHEMA:
            raise MemoryHubTeamCatalogError("team catalog schema is invalid")
        project_id = _id(payload["projectId"], "projectId")
        expected = _id(expected_project_id, "expectedProjectId")
        if project_id != expected or not isinstance(payload["teams"], list) or not payload["teams"]:
            raise MemoryHubTeamCatalogError("team catalog project or teams are invalid")
        teams = tuple(TeamRecord.from_dict(item) for item in payload["teams"])
        catalog = cls(project_id, teams, _sha(payload["catalogFingerprint"], "catalogFingerprint"))
        return catalog

    @classmethod
    def load(cls, path: Path, *, runtime_root: Path, expected_project_id: str) -> "TeamCatalog":
        if not isinstance(path, Path) or not isinstance(runtime_root, Path):
            raise MemoryHubTeamCatalogError("catalog path and runtimeRoot must be Path values")
        if runtime_root.is_symlink() or not runtime_root.exists() or not runtime_root.is_dir():
            raise MemoryHubTeamCatalogError("runtimeRoot must be a regular directory")
        root = runtime_root.resolve(strict=True)
        raw = path.absolute()
        try:
            relative = raw.relative_to(root)
        except ValueError as exc:
            raise MemoryHubTeamCatalogError("catalog path escapes runtimeRoot") from exc
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise MemoryHubTeamCatalogError("catalog path contains a symlink")
        if not raw.exists() or not raw.is_file():
            raise MemoryHubTeamCatalogError("catalog path must be a regular file")
        candidate = raw.resolve(strict=True)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise MemoryHubTeamCatalogError("catalog path escapes runtimeRoot") from exc
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise MemoryHubTeamCatalogError("catalog payload is unreadable") from exc
        return cls.from_dict(payload, expected_project_id=expected_project_id)
