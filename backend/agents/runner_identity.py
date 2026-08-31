"""Canonical identity contract for all runner transports."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping


class RunnerIdentityError(ValueError):
    """Raised when a runner identity is incomplete, malformed, or inconsistent."""


_SCHEMA_VERSION = "runner-identity-v1"
_TRANSPORTS = frozenset({"local_cli", "remote_api", "local_model"})
_IDENTITY_FIELDS = (
    "runnerId",
    "transport",
    "provider",
    "model",
    "profile",
    "executionEnvironment",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")


def _canonical_payload(values: Mapping[str, str]) -> str:
    return json.dumps(dict(values), ensure_ascii=True, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class RunnerIdentity:
    runner_id: str
    transport: str
    provider: str
    model: str
    profile: str
    execution_environment: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RunnerIdentity":
        if not isinstance(payload, Mapping):
            raise RunnerIdentityError("identity must be an object")
        allowed = set(_IDENTITY_FIELDS) | {"schemaVersion", "identityFingerprint"}
        unknown = set(payload) - allowed
        if unknown:
            raise RunnerIdentityError(f"unknown identity fields: {sorted(unknown)}")
        if payload.get("schemaVersion") != _SCHEMA_VERSION:
            raise RunnerIdentityError("unsupported identity schemaVersion")
        missing = [field for field in _IDENTITY_FIELDS if not isinstance(payload.get(field), str)]
        if missing:
            raise RunnerIdentityError(f"missing identity fields: {', '.join(missing)}")
        if payload["transport"] not in _TRANSPORTS:
            raise RunnerIdentityError("unsupported identity transport")
        if any(not payload[field].strip() for field in _IDENTITY_FIELDS):
            raise RunnerIdentityError("identity fields must be non-empty")
        if any(not _SLUG_RE.fullmatch(payload[field]) for field in _IDENTITY_FIELDS):
            raise RunnerIdentityError("identity fields must be bounded slugs")

        identity = cls(
            runner_id=payload["runnerId"],
            transport=payload["transport"],
            provider=payload["provider"],
            model=payload["model"],
            profile=payload["profile"],
            execution_environment=payload["executionEnvironment"],
        )
        supplied = payload.get("identityFingerprint")
        if supplied is not None and (not isinstance(supplied, str) or supplied != identity.identity_fingerprint):
            raise RunnerIdentityError("identityFingerprint does not match canonical identity")
        return identity

    @classmethod
    def from_legacy_local_cli(
        cls, *, runner_id: str, provider: str, model: str, profile: str, execution_environment: str
    ) -> "RunnerIdentity":
        return cls(runner_id, "local_cli", provider, model, profile, execution_environment)

    @classmethod
    def from_legacy_hermes(
        cls, *, runner_id: str, provider: str, model: str, profile: str, execution_environment: str
    ) -> "RunnerIdentity":
        return cls(runner_id, "remote_api", provider, model, profile, execution_environment)

    def to_dict(self) -> dict[str, str]:
        values = {
            "schemaVersion": _SCHEMA_VERSION,
            "runnerId": self.runner_id,
            "transport": self.transport,
            "provider": self.provider,
            "model": self.model,
            "profile": self.profile,
            "executionEnvironment": self.execution_environment,
        }
        values["identityFingerprint"] = self.identity_fingerprint
        return values

    @property
    def identity_fingerprint(self) -> str:
        values = {
            "schemaVersion": _SCHEMA_VERSION,
            "executionEnvironment": self.execution_environment,
            "model": self.model,
            "profile": self.profile,
            "provider": self.provider,
            "runnerId": self.runner_id,
            "transport": self.transport,
        }
        return hashlib.sha256(_canonical_payload(values).encode("utf-8")).hexdigest()
