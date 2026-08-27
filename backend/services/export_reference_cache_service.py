"""Identity-bound, bounded trusted-reference metadata for export validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Mapping


SCHEMA = "trusted-reference-v1"
_ROOT_NAME = "trusted_reference"
_POINTER_NAME = "active.json"
_ALLOWED_SOURCES = frozenset({"legacy_materialized", "validated_ready"})
_ARTIFACT_KEYS = frozenset({"ex", "ex_no_writeoff", "ex_no_writeoff_refund_transfer"})


@dataclass(frozen=True, slots=True)
class TrustedReferenceIdentity:
    source_fingerprint: str
    generation_token: str
    rules_fingerprint: str
    export_schema_version: str
    pipeline_fingerprint: str


@dataclass(frozen=True, slots=True)
class TrustedReferenceSnapshot:
    identity: TrustedReferenceIdentity
    artifact_digests: Mapping[str, Mapping[str, object]]
    artifact_fingerprints: Mapping[str, str]
    created_at: str
    source: str


def _identity_payload(identity: TrustedReferenceIdentity) -> dict[str, str]:
    return {
        "source_fingerprint": str(identity.source_fingerprint),
        "generation_token": str(identity.generation_token),
        "rules_fingerprint": str(identity.rules_fingerprint),
        "export_schema_version": str(identity.export_schema_version),
        "pipeline_fingerprint": str(identity.pipeline_fingerprint),
    }


def _identity_fingerprint(identity: TrustedReferenceIdentity) -> str:
    encoded = json.dumps(_identity_payload(identity), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _snapshot_payload(snapshot: TrustedReferenceSnapshot) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "identity": _identity_payload(snapshot.identity),
        "artifact_digests": {
            str(key): dict(value) for key, value in sorted(snapshot.artifact_digests.items())
        },
        "artifact_fingerprints": dict(sorted(snapshot.artifact_fingerprints.items())),
        "created_at": snapshot.created_at,
        "source": snapshot.source,
    }


def _encoded_payload(payload: Mapping[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def _safe_regular_file(root: Path, relative: str) -> Path | None:
    if not relative or Path(relative).is_absolute():
        return None
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    if not candidate.is_file() or candidate.is_symlink():
        return None
    return candidate


def _parse_snapshot(payload: object) -> TrustedReferenceSnapshot | None:
    if not isinstance(payload, dict):
        return None
    required = {"schema", "identity", "artifact_digests", "artifact_fingerprints", "created_at", "source"}
    if set(payload) != required or payload["schema"] != SCHEMA:
        return None
    identity_payload = payload["identity"]
    if not isinstance(identity_payload, dict) or set(identity_payload) != set(_identity_payload(TrustedReferenceIdentity("", "", "", "", ""))):
        return None
    digests = payload["artifact_digests"]
    fingerprints = payload["artifact_fingerprints"]
    if not isinstance(digests, dict) or not isinstance(fingerprints, dict):
        return None
    if set(fingerprints) != _ARTIFACT_KEYS or set(digests) != _ARTIFACT_KEYS:
        return None
    if payload["source"] not in _ALLOWED_SOURCES or not isinstance(payload["created_at"], str):
        return None
    if any(not isinstance(value, str) or len(value) != 64 for value in fingerprints.values()):
        return None
    if any(not isinstance(value, dict) for value in digests.values()):
        return None
    identity = TrustedReferenceIdentity(**{key: str(value) for key, value in identity_payload.items()})
    return TrustedReferenceSnapshot(
        identity=identity,
        artifact_digests={key: dict(value) for key, value in digests.items()},
        artifact_fingerprints=dict(fingerprints),
        created_at=payload["created_at"],
        source=str(payload["source"]),
    )


def materialize_trusted_reference(
    cache_root: Path,
    identity: TrustedReferenceIdentity,
    legacy_artifacts: Mapping[str, bytes],
    *,
    artifact_digests: Mapping[str, Mapping[str, object]] | None = None,
    source: str = "legacy_materialized",
) -> TrustedReferenceSnapshot:
    del cache_root  # Materialization is pure; publication owns filesystem writes.
    if set(legacy_artifacts) != _ARTIFACT_KEYS:
        raise ValueError("trusted reference requires the three export artifacts")
    if source not in _ALLOWED_SOURCES:
        raise ValueError("invalid trusted reference source")
    return TrustedReferenceSnapshot(
        identity=identity,
        artifact_digests={
            key: dict((artifact_digests or {}).get(key, {})) for key in sorted(_ARTIFACT_KEYS)
        },
        artifact_fingerprints={
            key: hashlib.sha256(value).hexdigest() for key, value in sorted(legacy_artifacts.items())
        },
        created_at=datetime.now(timezone.utc).isoformat(),
        source=source,
    )


def publish_trusted_reference(cache_root: Path, snapshot: TrustedReferenceSnapshot) -> Path:
    root = Path(cache_root).resolve() / _ROOT_NAME
    snapshot_id = _identity_fingerprint(snapshot.identity)
    relative_snapshot = Path(_ROOT_NAME) / "snapshots" / f"{snapshot_id}.json"
    snapshot_path = Path(cache_root).resolve() / relative_snapshot
    payload = _snapshot_payload(snapshot)
    payload["snapshot_fingerprint"] = hashlib.sha256(_encoded_payload(payload)).hexdigest()
    _atomic_write(snapshot_path, _encoded_payload(payload))
    pointer_payload = {
        "schema": SCHEMA,
        "snapshot": str(relative_snapshot.relative_to(_ROOT_NAME)),
        "identity_fingerprint": snapshot_id,
    }
    pointer_path = root / _POINTER_NAME
    _atomic_write(pointer_path, _encoded_payload(pointer_payload))
    return pointer_path


def load_trusted_reference(
    cache_root: Path,
    identity: TrustedReferenceIdentity,
) -> TrustedReferenceSnapshot | None:
    root = Path(cache_root).resolve() / _ROOT_NAME
    pointer = _safe_regular_file(root, _POINTER_NAME)
    if pointer is None:
        return None
    try:
        pointer_payload = json.loads(pointer.read_text(encoding="utf-8"))
        if set(pointer_payload) != {"schema", "snapshot", "identity_fingerprint"} or pointer_payload["schema"] != SCHEMA:
            return None
        if pointer_payload["identity_fingerprint"] != _identity_fingerprint(identity):
            return None
        snapshot = _safe_regular_file(root, str(pointer_payload["snapshot"]))
        if snapshot is None:
            return None
        payload = json.loads(snapshot.read_text(encoding="utf-8"))
        stored_fingerprint = payload.pop("snapshot_fingerprint", None)
        if stored_fingerprint != hashlib.sha256(_encoded_payload(payload)).hexdigest():
            return None
        result = _parse_snapshot(payload)
        if result is None or result.identity != identity:
            return None
        return result
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


__all__ = [
    "SCHEMA",
    "TrustedReferenceIdentity",
    "TrustedReferenceSnapshot",
    "load_trusted_reference",
    "materialize_trusted_reference",
    "publish_trusted_reference",
]
