from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


_FIELDS = {"sourceCommit", "sourceBranch", "dirtyFiles", "gateHashes", "reviewHash"}
_GATE_KEYS = {"pytest", "systemAcceptance", "hermes"}


def _keys(payload: Mapping[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise ValueError("verified backfill manifest must be an object")
    missing = _FIELDS - set(payload)
    unknown = set(payload) - _FIELDS
    if missing or unknown:
        details = []
        if missing:
            details.append("missing fields: " + ", ".join(sorted(missing)))
        if unknown:
            details.append("unknown fields: " + ", ".join(sorted(unknown)))
        raise ValueError("verified backfill manifest keys are invalid (" + "; ".join(details) + ")")


def _sha256(value: Any, key: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{key} must be a lowercase SHA-256 hex digest")
    return value


@dataclass(frozen=True)
class VerifiedBackfillManifest:
    source_commit: str
    source_branch: str
    dirty_files: tuple[dict[str, str], ...]
    gate_hashes: Mapping[str, str]
    review_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_commit, str) or len(self.source_commit) != 40 or any(
            char not in "0123456789abcdef" for char in self.source_commit
        ):
            raise ValueError("sourceCommit must be a 40-character lowercase commit SHA")
        if self.source_branch != "main":
            raise ValueError("sourceBranch must be main")
        if self.dirty_files:
            raise ValueError("dirtyFiles must be empty")
        if set(self.gate_hashes) != _GATE_KEYS:
            raise ValueError("gateHashes must contain pytest, systemAcceptance, and hermes")
        for key, value in self.gate_hashes.items():
            _sha256(value, key)
        _sha256(self.review_hash, "reviewHash")
        object.__setattr__(self, "gate_hashes", MappingProxyType(dict(self.gate_hashes)))

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "VerifiedBackfillManifest":
        _keys(payload)
        dirty_files = payload["dirtyFiles"]
        if not isinstance(dirty_files, list):
            raise ValueError("dirtyFiles must be a list")
        gate_hashes = payload["gateHashes"]
        if not isinstance(gate_hashes, dict):
            raise ValueError("gateHashes must be an object")
        return cls(
            source_commit=payload["sourceCommit"],
            source_branch=payload["sourceBranch"],
            dirty_files=tuple(dict(item) for item in dirty_files),
            gate_hashes=dict(gate_hashes),
            review_hash=payload["reviewHash"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sourceCommit": self.source_commit,
            "sourceBranch": self.source_branch,
            "dirtyFiles": [dict(item) for item in self.dirty_files],
            "gateHashes": dict(self.gate_hashes),
            "reviewHash": self.review_hash,
        }
