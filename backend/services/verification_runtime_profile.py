from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from backend.agents.evidence_models import canonical_fingerprint


VERIFICATION_PROFILE_SCHEMA = "verification-runtime-profile-v1"
VERIFICATION_PROFILE_SCHEMA_V2 = "verification-runtime-profile-v2"
VERIFICATION_SOURCE_BINDING_SCHEMA = "verification-source-binding-v1"
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_TOP_LEVEL_KEYS = frozenset(
    {
        "schemaVersion",
        "profileId",
        "projectId",
        "gitHead",
        "worktreeFingerprint",
        "database",
        "baseline",
        "runtime",
        "services",
        "createdAt",
        "profileFingerprint",
    }
)
_TOP_LEVEL_KEYS_V2 = _TOP_LEVEL_KEYS | {"sourceBinding"}
_SOURCE_BINDING_KEYS = frozenset(
    {
        "schemaVersion",
        "sourceRole",
        "repositoryFingerprint",
        "sourceHead",
        "databaseFingerprint",
        "generationFingerprint",
        "baselineRegistryFingerprint",
    }
)


class VerificationRuntimeProfileError(ValueError):
    """Raised when a verification profile is not safe to consume."""


def _exact_keys(value: Mapping[str, object], expected: frozenset[str], label: str) -> None:
    if set(value) != expected:
        raise VerificationRuntimeProfileError(f"{label} keys must be exact")


def _string(value: object, label: str, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value:
        raise VerificationRuntimeProfileError(f"{label} must be a non-empty string")
    if pattern and not pattern.fullmatch(value):
        raise VerificationRuntimeProfileError(f"{label} has invalid format")
    return value


def _fingerprint(value: object, label: str) -> str:
    return _string(value, label, _HEX64)


def _relative_ref(value: object, label: str) -> str:
    ref = _string(value, label)
    path = Path(ref)
    if path.is_absolute() or ".." in path.parts or not ref.startswith("verification/"):
        raise VerificationRuntimeProfileError(f"{label} must be a safe relative verification ref")
    return ref


def _profile_ref(value: str, label: str, profile_id: str) -> str:
    prefix = f"verification/{profile_id}/"
    if not value.startswith(prefix):
        raise VerificationRuntimeProfileError(f"{label} must be scoped to profileId")
    return value


def _runtime_dir_ref(value: str, label: str, profile_id: str) -> str:
    # The runtime evidence root is the profile directory itself, or a
    # profile-scoped subdirectory beneath it. Anything else is another
    # profile's runtime or an escape attempt.
    if value != f"verification/{profile_id}" and not value.startswith(f"verification/{profile_id}/"):
        raise VerificationRuntimeProfileError(f"{label} must be scoped to profileId")
    return value


@dataclass(frozen=True)
class ProfileSourceBinding:
    source_role: str
    repository_fingerprint: str
    source_head: str
    database_fingerprint: str
    generation_fingerprint: str
    baseline_registry_fingerprint: str

    @classmethod
    def from_dict(cls, value: object) -> "ProfileSourceBinding":
        if not isinstance(value, Mapping):
            raise VerificationRuntimeProfileError("sourceBinding must be an object")
        _exact_keys(value, _SOURCE_BINDING_KEYS, "sourceBinding")
        if value["schemaVersion"] != VERIFICATION_SOURCE_BINDING_SCHEMA:
            raise VerificationRuntimeProfileError("sourceBinding schemaVersion is invalid")
        if value["sourceRole"] != "primary-checkout":
            raise VerificationRuntimeProfileError("sourceBinding sourceRole is invalid")
        return cls(
            source_role="primary-checkout",
            repository_fingerprint=_fingerprint(value["repositoryFingerprint"], "sourceBinding.repositoryFingerprint"),
            source_head=_string(value["sourceHead"], "sourceBinding.sourceHead", _HEX40),
            database_fingerprint=_fingerprint(value["databaseFingerprint"], "sourceBinding.databaseFingerprint"),
            generation_fingerprint=_fingerprint(value["generationFingerprint"], "sourceBinding.generationFingerprint"),
            baseline_registry_fingerprint=_fingerprint(
                value["baselineRegistryFingerprint"], "sourceBinding.baselineRegistryFingerprint",
            ),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "schemaVersion": VERIFICATION_SOURCE_BINDING_SCHEMA,
            "sourceRole": self.source_role,
            "repositoryFingerprint": self.repository_fingerprint,
            "sourceHead": self.source_head,
            "databaseFingerprint": self.database_fingerprint,
            "generationFingerprint": self.generation_fingerprint,
            "baselineRegistryFingerprint": self.baseline_registry_fingerprint,
        }


@dataclass(frozen=True)
class ProfileDatabase:
    snapshot_ref: str
    source_fingerprint: str
    snapshot_fingerprint: str
    read_only: bool

    @classmethod
    def from_dict(cls, value: object, *, profile_id: str | None = None) -> "ProfileDatabase":
        if not isinstance(value, Mapping):
            raise VerificationRuntimeProfileError("database must be an object")
        _exact_keys(value, frozenset({"snapshotRef", "sourceFingerprint", "snapshotFingerprint", "readOnly"}), "database")
        if value["readOnly"] is not True:
            raise VerificationRuntimeProfileError("database readOnly must be true")
        snapshot_ref = _relative_ref(value["snapshotRef"], "database.snapshotRef")
        if profile_id is not None:
            snapshot_ref = _profile_ref(snapshot_ref, "database.snapshotRef", profile_id)
        return cls(
            snapshot_ref=snapshot_ref,
            source_fingerprint=_fingerprint(value["sourceFingerprint"], "database.sourceFingerprint"),
            snapshot_fingerprint=_fingerprint(value["snapshotFingerprint"], "database.snapshotFingerprint"),
            read_only=True,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "snapshotRef": self.snapshot_ref,
            "sourceFingerprint": self.source_fingerprint,
            "snapshotFingerprint": self.snapshot_fingerprint,
            "readOnly": self.read_only,
        }


@dataclass(frozen=True)
class ProfileBaseline:
    registry_fingerprint: str
    required_may_2026_total: str

    @classmethod
    def from_dict(cls, value: object) -> "ProfileBaseline":
        if not isinstance(value, Mapping):
            raise VerificationRuntimeProfileError("baseline must be an object")
        _exact_keys(value, frozenset({"registryFingerprint", "requiredMay2026Total"}), "baseline")
        total = _string(value["requiredMay2026Total"], "baseline.requiredMay2026Total")
        if total != "HKD 12,057,968":
            raise VerificationRuntimeProfileError("baseline May 2026 total is not the frozen value")
        return cls(_fingerprint(value["registryFingerprint"], "baseline.registryFingerprint"), total)

    def to_dict(self) -> dict[str, str]:
        return {
            "registryFingerprint": self.registry_fingerprint,
            "requiredMay2026Total": self.required_may_2026_total,
        }


@dataclass(frozen=True)
class CacheInventory:
    file_count: int
    total_bytes: int
    fingerprint: str

    @classmethod
    def from_dict(cls, value: object) -> "CacheInventory":
        if not isinstance(value, Mapping):
            raise VerificationRuntimeProfileError("runtime.cacheInventory must be an object")
        _exact_keys(value, frozenset({"fileCount", "totalBytes", "fingerprint"}), "runtime.cacheInventory")
        file_count, total_bytes = value["fileCount"], value["totalBytes"]
        if not isinstance(file_count, int) or isinstance(file_count, bool) or file_count < 0:
            raise VerificationRuntimeProfileError("runtime.cacheInventory.fileCount must be non-negative")
        if not isinstance(total_bytes, int) or isinstance(total_bytes, bool) or total_bytes < 0:
            raise VerificationRuntimeProfileError("runtime.cacheInventory.totalBytes must be non-negative")
        return cls(file_count, total_bytes, _fingerprint(value["fingerprint"], "runtime.cacheInventory.fingerprint"))

    def to_dict(self) -> dict[str, object]:
        return {"fileCount": self.file_count, "totalBytes": self.total_bytes, "fingerprint": self.fingerprint}


@dataclass(frozen=True)
class ProfileRuntime:
    runtime_dir: str
    generation_ref: str
    generation_fingerprint: str
    cache_inventory: CacheInventory

    @classmethod
    def from_dict(cls, value: object, *, profile_id: str | None = None) -> "ProfileRuntime":
        if not isinstance(value, Mapping):
            raise VerificationRuntimeProfileError("runtime must be an object")
        _exact_keys(value, frozenset({"runtimeDir", "generationRef", "generationFingerprint", "cacheInventory"}), "runtime")
        runtime_dir = _relative_ref(value["runtimeDir"], "runtime.runtimeDir")
        if profile_id is not None:
            runtime_dir = _runtime_dir_ref(runtime_dir, "runtime.runtimeDir", profile_id)
        generation_ref = _relative_ref(value["generationRef"], "runtime.generationRef")
        if profile_id is not None:
            generation_ref = _profile_ref(generation_ref, "runtime.generationRef", profile_id)
        return cls(
            runtime_dir,
            generation_ref,
            _fingerprint(value["generationFingerprint"], "runtime.generationFingerprint"),
            CacheInventory.from_dict(value["cacheInventory"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "runtimeDir": self.runtime_dir,
            "generationRef": self.generation_ref,
            "generationFingerprint": self.generation_fingerprint,
            "cacheInventory": self.cache_inventory.to_dict(),
        }


@dataclass(frozen=True)
class ProfileServices:
    profile_namespace: str
    ports: Mapping[str, int]

    @classmethod
    def from_dict(cls, value: object) -> "ProfileServices":
        if not isinstance(value, Mapping):
            raise VerificationRuntimeProfileError("services must be an object")
        _exact_keys(value, frozenset({"profileNamespace", "ports"}), "services")
        namespace = _string(value["profileNamespace"], "services.profileNamespace", _ID)
        ports_value = value["ports"]
        if not isinstance(ports_value, Mapping) or set(ports_value) != {"api", "streamlit", "vue"}:
            raise VerificationRuntimeProfileError("services.ports keys must be exact")
        ports: dict[str, int] = {}
        for name, raw_port in ports_value.items():
            if not isinstance(raw_port, int) or isinstance(raw_port, bool) or not 1024 <= raw_port <= 65535:
                raise VerificationRuntimeProfileError(f"services.ports.{name} is invalid")
            ports[str(name)] = raw_port
        if len(set(ports.values())) != len(ports):
            raise VerificationRuntimeProfileError("services.ports must be unique")
        return cls(namespace, MappingProxyType(dict(ports)))

    def to_dict(self) -> dict[str, object]:
        return {"profileNamespace": self.profile_namespace, "ports": dict(self.ports)}


@dataclass(frozen=True)
class VerificationRuntimeProfile:
    profile_id: str
    project_id: str
    git_head: str
    worktree_fingerprint: str
    database: ProfileDatabase
    baseline: ProfileBaseline
    runtime: ProfileRuntime
    services: ProfileServices
    created_at: str
    profile_fingerprint: str
    source_binding: ProfileSourceBinding | None = None

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, object],
        *,
        expected_git_head: str | None = None,
        expected_worktree_fingerprint: str | None = None,
    ) -> "VerificationRuntimeProfile":
        if not isinstance(payload, Mapping):
            raise VerificationRuntimeProfileError("profile must be an object")
        schema = payload.get("schemaVersion")
        if schema == VERIFICATION_PROFILE_SCHEMA:
            _exact_keys(payload, _TOP_LEVEL_KEYS, "profile")
            source_binding = None
        elif schema == VERIFICATION_PROFILE_SCHEMA_V2:
            _exact_keys(payload, _TOP_LEVEL_KEYS_V2, "profile")
            source_binding = ProfileSourceBinding.from_dict(payload["sourceBinding"])
        else:
            raise VerificationRuntimeProfileError("schemaVersion is invalid")
        profile_id = _string(payload["profileId"], "profileId", _ID)
        project_id = _string(payload["projectId"], "projectId", _ID)
        git_head = _string(payload["gitHead"], "gitHead", _HEX40)
        if expected_git_head is not None and git_head != expected_git_head:
            raise VerificationRuntimeProfileError("gitHead does not match expected head")
        worktree_fingerprint = _fingerprint(payload["worktreeFingerprint"], "worktreeFingerprint")
        if expected_worktree_fingerprint is not None and worktree_fingerprint != expected_worktree_fingerprint:
            raise VerificationRuntimeProfileError("worktreeFingerprint does not match expected worktree")
        profile = cls(
            profile_id,
            project_id,
            git_head,
            worktree_fingerprint,
            ProfileDatabase.from_dict(payload["database"], profile_id=profile_id),
            ProfileBaseline.from_dict(payload["baseline"]),
            ProfileRuntime.from_dict(payload["runtime"], profile_id=profile_id),
            ProfileServices.from_dict(payload["services"]),
            _string(payload["createdAt"], "createdAt"),
            _fingerprint(payload["profileFingerprint"], "profileFingerprint"),
            source_binding,
        )
        if profile.source_binding is not None:
            binding = profile.source_binding
            if binding.database_fingerprint != profile.database.source_fingerprint:
                raise VerificationRuntimeProfileError("sourceBinding databaseFingerprint mismatch")
            if binding.generation_fingerprint != profile.runtime.generation_fingerprint:
                raise VerificationRuntimeProfileError("sourceBinding generationFingerprint mismatch")
            if binding.baseline_registry_fingerprint != profile.baseline.registry_fingerprint:
                raise VerificationRuntimeProfileError("sourceBinding baselineRegistryFingerprint mismatch")
        if profile.fingerprint() != profile.profile_fingerprint:
            raise VerificationRuntimeProfileError("profile fingerprint does not match profile content")
        return profile

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        expected_git_head: str | None = None,
        expected_worktree_fingerprint: str | None = None,
    ) -> "VerificationRuntimeProfile":
        target = Path(path)
        if target.is_symlink():
            raise VerificationRuntimeProfileError("profile file is symlinked")
        if not target.is_file():
            raise VerificationRuntimeProfileError("profile file is missing")
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VerificationRuntimeProfileError("profile file is unreadable") from exc
        return cls.from_dict(
            payload,
            expected_git_head=expected_git_head,
            expected_worktree_fingerprint=expected_worktree_fingerprint,
        )

    def _unsigned_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schemaVersion": (
                VERIFICATION_PROFILE_SCHEMA_V2 if self.source_binding is not None else VERIFICATION_PROFILE_SCHEMA
            ),
            "profileId": self.profile_id,
            "projectId": self.project_id,
            "gitHead": self.git_head,
            "worktreeFingerprint": self.worktree_fingerprint,
            "database": self.database.to_dict(),
            "baseline": self.baseline.to_dict(),
            "runtime": self.runtime.to_dict(),
            "services": self.services.to_dict(),
            "createdAt": self.created_at,
        }
        if self.source_binding is not None:
            payload["sourceBinding"] = self.source_binding.to_dict()
        return payload

    def fingerprint(self) -> str:
        return canonical_fingerprint(self._unsigned_dict())

    def to_dict(self) -> dict[str, object]:
        return {**self._unsigned_dict(), "profileFingerprint": self.profile_fingerprint}
