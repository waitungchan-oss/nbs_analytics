"""Trusted semantic references for the formal GMV export cache."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping


TRUSTED_REFERENCE_SCHEMA_VERSION = "gmv-trusted-reference-v1"
TRUSTED_REFERENCE_ID_PREFIX = f"{TRUSTED_REFERENCE_SCHEMA_VERSION}:"
TRUSTED_ARTIFACT_CONTRACT_VERSION = "gmv-formal-export-artifacts-v1"
TRUSTED_REFERENCE_POINTER_SCHEMA_VERSION = "gmv-trusted-reference-pointer-v1"
TRUSTED_REFERENCE_INVALID_SCHEMA_VERSION = "gmv-trusted-reference-invalid-v1"
TRUSTED_REFERENCE_NAMESPACE = "references"

# Keep this contract independent from the downloadable files.  The cache
# publisher and the reference validator must agree on the same exact set.
TRUSTED_REFERENCE_ARTIFACT_KEYS = (
    "paid.detail",
    "paid.workbook.audit.xlsx",
    "paid.workbook.ex.xlsx",
    "paid.workbook.ex_no_writeoff.xlsx",
    "paid.workbook.ex_no_writeoff_refund_transfer.xlsx",
    "summaries",
    "total.detail",
    "total.workbook.audit.xlsx",
    "total.workbook.ex.xlsx",
    "total.workbook.ex_no_writeoff.xlsx",
    "total.workbook.ex_no_writeoff_refund_transfer.xlsx",
)

_ARTIFACT_KINDS = frozenset({"xlsx", "csv", "json"})
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SOURCE_FIELDS = (
    "revenueGenerationToken",
    "refundStateSha256",
    "ruleVersion",
    "exportSchemaVersion",
    "pipelineFingerprint",
    "serializerVersion",
)
_MANIFEST_FIELDS = {
    "schemaVersion",
    "referenceId",
    "contentFingerprint",
    "status",
    "createdAt",
    "seedMode",
    "source",
    "artifactContract",
    "artifacts",
    "seedProvenance",
}


class _ExpectedSourceMismatch(ValueError):
    """Caller metadata is not a reason to invalidate on-disk reference state."""


def _canonical_json(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _require_string(value: object, label: str, *, max_length: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length or "\x00" in value:
        raise ValueError(f"{label} must be a bounded non-empty string")
    return value


def _require_sha256(value: object, label: str) -> str:
    text = _require_string(value, label, max_length=64)
    if _SHA256_RE.fullmatch(text) is None:
        raise ValueError(f"{label} must be a sha256 fingerprint")
    return text


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(raw, path)
    finally:
        if os.path.exists(raw):
            os.unlink(raw)


def _cache_root(cache_dir: Path) -> Path:
    return Path(cache_dir).expanduser().resolve()


def _safe_relative_path(root: Path, relative_path: str, label: str) -> Path:
    value = _require_string(relative_path, label)
    candidate = Path(value)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError(f"unsafe {label}")
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"unsafe {label}") from exc
    return resolved


def _reference_dir(cache_dir: Path, content_fingerprint: str) -> Path:
    fingerprint = _require_sha256(content_fingerprint, "contentFingerprint")
    root = _cache_root(cache_dir)
    return _safe_relative_path(
        root,
        f"{TRUSTED_REFERENCE_NAMESPACE}/{fingerprint}",
        "reference directory",
    )


def _manifest_bytes(manifest: TrustedReferenceManifest) -> bytes:
    return (
        json.dumps(manifest.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path.name}")
    return payload


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@contextmanager
def _reference_lock(lock_path: Path, *, timeout_seconds: float = 10.0) -> Iterator[None]:
    """Use an O_EXCL lock so same-fingerprint writers are serialized across processes."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    acquired = False
    while not acquired:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                os.write(fd, str(os.getpid()).encode("ascii"))
            finally:
                os.close(fd)
            acquired = True
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError("trusted reference lock timeout")
            time.sleep(0.01)
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def _validate_pointer(payload: Mapping[str, object], *, content_fingerprint: str, root: Path) -> tuple[Path, Path]:
    expected = {
        "schemaVersion", "contentFingerprint", "referenceId",
        "generationPath", "manifestPath", "manifestSha256",
    }
    if set(payload) != expected:
        raise ValueError("trusted reference pointer contract is invalid")
    if payload["schemaVersion"] != TRUSTED_REFERENCE_POINTER_SCHEMA_VERSION:
        raise ValueError("trusted reference pointer schema version is invalid")
    if payload["contentFingerprint"] != content_fingerprint:
        raise ValueError("trusted reference pointer fingerprint mismatch")
    expected_reference_id = f"{TRUSTED_REFERENCE_ID_PREFIX}{content_fingerprint}"
    if payload["referenceId"] != expected_reference_id:
        raise ValueError("trusted reference pointer reference id mismatch")
    generation_path = _safe_relative_path(root, str(payload["generationPath"]), "generationPath")
    manifest_path = _safe_relative_path(root, str(payload["manifestPath"]), "manifestPath")
    if manifest_path != generation_path / "manifest.json":
        raise ValueError("trusted reference pointer manifest path mismatch")
    _require_sha256(payload["manifestSha256"], "manifestSha256")
    return generation_path, manifest_path


def _validate_seed_provenance(root: Path, manifest: TrustedReferenceManifest) -> None:
    seed_generation = _safe_relative_path(
        root,
        manifest.seed_provenance["generationPath"],
        "seedProvenance.generationPath",
    )
    seed_manifest = seed_generation / "manifest.json"
    if not seed_manifest.is_file():
        raise ValueError("seed generation manifest is missing")
    if _sha256_file(seed_manifest) != manifest.seed_provenance["manifestSha256"]:
        raise ValueError("seed generation manifest checksum mismatch")


def _invalid_record_bytes(content_fingerprint: str, reason: str) -> bytes:
    _require_sha256(content_fingerprint, "contentFingerprint")
    _require_string(reason, "reason", max_length=256)
    return (
        json.dumps(
            {
                "schemaVersion": TRUSTED_REFERENCE_INVALID_SCHEMA_VERSION,
                "contentFingerprint": content_fingerprint,
                "reason": reason,
                "invalidatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class TrustedReferenceSource:
    revenue_generation_token: str
    refund_state_sha256: str
    rule_version: str
    export_schema_version: str
    pipeline_fingerprint: str
    serializer_version: str

    def to_dict(self) -> dict[str, str]:
        return {
            "revenueGenerationToken": self.revenue_generation_token,
            "refundStateSha256": self.refund_state_sha256,
            "ruleVersion": self.rule_version,
            "exportSchemaVersion": self.export_schema_version,
            "pipelineFingerprint": self.pipeline_fingerprint,
            "serializerVersion": self.serializer_version,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "TrustedReferenceSource":
        if set(payload) != set(_SOURCE_FIELDS):
            raise ValueError("source contract is invalid")
        return cls(
            revenue_generation_token=_require_string(payload["revenueGenerationToken"], "source.revenueGenerationToken"),
            refund_state_sha256=_require_sha256(payload["refundStateSha256"], "source.refundStateSha256"),
            rule_version=_require_string(payload["ruleVersion"], "source.ruleVersion"),
            export_schema_version=_require_string(payload["exportSchemaVersion"], "source.exportSchemaVersion"),
            pipeline_fingerprint=_require_string(payload["pipelineFingerprint"], "source.pipelineFingerprint"),
            serializer_version=_require_string(payload["serializerVersion"], "source.serializerVersion"),
        )


@dataclass(frozen=True, slots=True)
class TrustedReferenceArtifact:
    kind: str
    schema_fingerprint: str
    semantic_fingerprint: str
    row_count: int
    sheet_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "schemaFingerprint": self.schema_fingerprint,
            "semanticFingerprint": self.semantic_fingerprint,
            "rowCount": self.row_count,
            "sheetCount": self.sheet_count,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "TrustedReferenceArtifact":
        expected = {"kind", "schemaFingerprint", "semanticFingerprint", "rowCount", "sheetCount"}
        if set(payload) != expected:
            raise ValueError("artifact contract is invalid")
        row_count = payload["rowCount"]
        sheet_count = payload["sheetCount"]
        if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0:
            raise ValueError("artifact rowCount must be a non-negative integer")
        if isinstance(sheet_count, bool) or not isinstance(sheet_count, int) or sheet_count < 0:
            raise ValueError("artifact sheetCount must be a non-negative integer")
        return cls(
            kind=_require_string(payload["kind"], "artifact.kind", max_length=16),
            schema_fingerprint=_require_sha256(payload["schemaFingerprint"], "artifact.schemaFingerprint"),
            semantic_fingerprint=_require_sha256(payload["semanticFingerprint"], "artifact.semanticFingerprint"),
            row_count=row_count,
            sheet_count=sheet_count,
        )


@dataclass(frozen=True, slots=True)
class TrustedReferenceManifest:
    schema_version: str
    reference_id: str
    content_fingerprint: str
    status: str
    created_at: str
    seed_mode: str
    source: TrustedReferenceSource
    artifact_contract_version: str
    artifacts: dict[str, TrustedReferenceArtifact]
    seed_provenance: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "referenceId": self.reference_id,
            "contentFingerprint": self.content_fingerprint,
            "status": self.status,
            "createdAt": self.created_at,
            "seedMode": self.seed_mode,
            "source": self.source.to_dict(),
            "artifactContract": {
                "version": self.artifact_contract_version,
                "keys": sorted(self.artifacts),
            },
            "artifacts": {
                key: self.artifacts[key].to_dict()
                for key in sorted(self.artifacts)
            },
            "seedProvenance": dict(self.seed_provenance),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "TrustedReferenceManifest":
        if set(payload) != _MANIFEST_FIELDS:
            raise ValueError("trusted reference top-level contract is invalid")
        source = TrustedReferenceSource.from_dict(_require_mapping(payload["source"], "source"))
        artifact_contract = _require_mapping(payload["artifactContract"], "artifactContract")
        if set(artifact_contract) != {"version", "keys"}:
            raise ValueError("artifact contract is invalid")
        artifact_keys = artifact_contract["keys"]
        if not isinstance(artifact_keys, list) or not all(isinstance(key, str) for key in artifact_keys):
            raise ValueError("artifact contract keys must be a list of strings")
        raw_artifacts = _require_mapping(payload["artifacts"], "artifacts")
        if artifact_keys != sorted(artifact_keys) or artifact_keys != sorted(raw_artifacts):
            raise ValueError("artifact contract keys must be exact and sorted")
        artifacts = {
            key: TrustedReferenceArtifact.from_dict(_require_mapping(value, f"artifacts.{key}"))
            for key, value in raw_artifacts.items()
        }
        seed_provenance = _require_mapping(payload["seedProvenance"], "seedProvenance")
        if set(seed_provenance) != {"cacheKey", "generationPath", "manifestSha256"}:
            raise ValueError("seed provenance contract is invalid")
        return cls(
            schema_version=_require_string(payload["schemaVersion"], "schemaVersion", max_length=64),
            reference_id=_require_string(payload["referenceId"], "referenceId", max_length=160),
            content_fingerprint=_require_string(payload["contentFingerprint"], "contentFingerprint", max_length=64),
            status=_require_string(payload["status"], "status", max_length=32),
            created_at=_require_string(payload["createdAt"], "createdAt", max_length=128),
            seed_mode=_require_string(payload["seedMode"], "seedMode", max_length=32),
            source=source,
            artifact_contract_version=_require_string(artifact_contract["version"], "artifactContract.version", max_length=64),
            artifacts=artifacts,
            seed_provenance={
                key: _require_string(value, f"seedProvenance.{key}")
                for key, value in seed_provenance.items()
            },
        )


def build_gmv_content_fingerprint(
    *,
    revenue_generation_token: str,
    refund_state_sha256: str,
    rule_version: str,
    export_schema_version: str,
    pipeline_fingerprint: str,
    serializer_version: str,
) -> str:
    """Return the stable identity for source inputs and export contracts.

    This is intentionally an input identity, not an artifact-content digest:
    artifact semantics are stored as bounded per-artifact fingerprints in the
    trusted manifest and compared against the candidate before publication.
    Keeping the two identities separate lets a warm lookup happen before the
    candidate is serialized while still failing closed on any semantic drift.
    """
    _require_string(revenue_generation_token, "revenue_generation_token")
    _require_sha256(refund_state_sha256, "refund_state_sha256")
    _require_string(rule_version, "rule_version")
    _require_string(export_schema_version, "export_schema_version")
    _require_string(pipeline_fingerprint, "pipeline_fingerprint")
    _require_string(serializer_version, "serializer_version")
    source = TrustedReferenceSource(
        revenue_generation_token=revenue_generation_token,
        refund_state_sha256=refund_state_sha256,
        rule_version=rule_version,
        export_schema_version=export_schema_version,
        pipeline_fingerprint=pipeline_fingerprint,
        serializer_version=serializer_version,
    )
    payload = {
        **source.to_dict(),
        "artifactContractVersion": TRUSTED_ARTIFACT_CONTRACT_VERSION,
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def validate_trusted_reference_manifest(manifest: TrustedReferenceManifest) -> None:
    """Fail closed unless a manifest is a complete trusted reference contract."""
    _require_string(manifest.schema_version, "schemaVersion", max_length=64)
    if manifest.schema_version != TRUSTED_REFERENCE_SCHEMA_VERSION:
        raise ValueError("trusted reference schema version is invalid")
    _require_string(manifest.reference_id, "referenceId", max_length=160)
    _require_string(manifest.content_fingerprint, "contentFingerprint", max_length=64)
    _require_string(manifest.status, "status", max_length=32)
    _require_string(manifest.created_at, "createdAt", max_length=128)
    _require_string(manifest.seed_mode, "seedMode", max_length=32)
    _require_string(manifest.artifact_contract_version, "artifactContract.version", max_length=64)
    _require_string(manifest.source.revenue_generation_token, "source.revenueGenerationToken")
    _require_sha256(manifest.source.refund_state_sha256, "source.refundStateSha256")
    _require_string(manifest.source.rule_version, "source.ruleVersion")
    _require_string(manifest.source.export_schema_version, "source.exportSchemaVersion")
    _require_string(manifest.source.pipeline_fingerprint, "source.pipelineFingerprint")
    _require_string(manifest.source.serializer_version, "source.serializerVersion")
    if not _SHA256_RE.fullmatch(manifest.content_fingerprint):
        raise ValueError("content fingerprint must be a sha256 fingerprint")
    expected_fingerprint = build_gmv_content_fingerprint(
        revenue_generation_token=manifest.source.revenue_generation_token,
        refund_state_sha256=manifest.source.refund_state_sha256,
        rule_version=manifest.source.rule_version,
        export_schema_version=manifest.source.export_schema_version,
        pipeline_fingerprint=manifest.source.pipeline_fingerprint,
        serializer_version=manifest.source.serializer_version,
    )
    if manifest.content_fingerprint != expected_fingerprint:
        raise ValueError("content fingerprint does not match source identity")
    if manifest.reference_id != f"{TRUSTED_REFERENCE_ID_PREFIX}{manifest.content_fingerprint}":
        raise ValueError("reference id does not match content fingerprint")
    if manifest.status != "TRUSTED":
        raise ValueError("trusted reference status is invalid")
    if manifest.seed_mode != "LEGACY_SEED":
        raise ValueError("trusted reference seed mode is invalid")
    if manifest.artifact_contract_version != TRUSTED_ARTIFACT_CONTRACT_VERSION:
        raise ValueError("artifact contract version is invalid")
    if tuple(manifest.artifacts) != TRUSTED_REFERENCE_ARTIFACT_KEYS:
        raise ValueError("artifact contract keys must be exact and sorted")
    for key, artifact in manifest.artifacts.items():
        if artifact.kind not in _ARTIFACT_KINDS:
            raise ValueError(f"artifact kind is invalid: {key}")
        _require_sha256(artifact.schema_fingerprint, f"artifacts.{key}.schemaFingerprint")
        _require_sha256(artifact.semantic_fingerprint, f"artifacts.{key}.semanticFingerprint")
        if artifact.row_count < 0 or artifact.sheet_count < 0:
            raise ValueError(f"artifact counts are invalid: {key}")
    if set(manifest.seed_provenance) != {"cacheKey", "generationPath", "manifestSha256"}:
        raise ValueError("seed provenance contract is invalid")
    _require_string(manifest.seed_provenance["cacheKey"], "seedProvenance.cacheKey")
    _require_sha256(manifest.seed_provenance["manifestSha256"], "seedProvenance.manifestSha256")
    generation_path = _require_string(manifest.seed_provenance["generationPath"], "seedProvenance.generationPath")
    if generation_path.startswith(("/", "\\")) or ".." in generation_path.replace("\\", "/").split("/"):
        raise ValueError("seed provenance generationPath is unsafe")


def _invalidate_trusted_reference_locked(
    *, cache_dir: Path, content_fingerprint: str, reason: str,
) -> None:
    reference_dir = _reference_dir(cache_dir, content_fingerprint)
    _atomic_write(
        reference_dir / "invalid.json",
        _invalid_record_bytes(content_fingerprint, reason),
    )
    (reference_dir / "trusted.json").unlink(missing_ok=True)


def invalidate_trusted_reference(
    *, cache_dir: Path, content_fingerprint: str, reason: str,
) -> None:
    """Remove a trusted pointer while preserving a bounded invalidation record."""
    reference_dir = _reference_dir(cache_dir, content_fingerprint)
    with _reference_lock(reference_dir / "trusted.lock"):
        _invalidate_trusted_reference_locked(
            cache_dir=cache_dir,
            content_fingerprint=content_fingerprint,
            reason=reason,
        )


def _load_trusted_reference_unlocked(
    *, cache_dir: Path, content_fingerprint: str, expected_source: Mapping[str, object] | None,
) -> TrustedReferenceManifest | None:
    root = _cache_root(cache_dir)
    reference_dir = _reference_dir(root, content_fingerprint)
    pointer_path = reference_dir / "trusted.json"
    if not pointer_path.is_file():
        return None
    pointer = _read_json(pointer_path)
    _, manifest_path = _validate_pointer(
        pointer,
        content_fingerprint=content_fingerprint,
        root=root,
    )
    if not manifest_path.is_file():
        raise ValueError("trusted reference manifest is missing")
    if _sha256_file(manifest_path) != pointer["manifestSha256"]:
        raise ValueError("trusted reference manifest checksum mismatch")
    manifest = TrustedReferenceManifest.from_dict(_read_json(manifest_path))
    validate_trusted_reference_manifest(manifest)
    if manifest.content_fingerprint != content_fingerprint:
        raise ValueError("trusted reference manifest fingerprint mismatch")
    if expected_source is not None:
        source = _require_mapping(expected_source, "expected_source")
        if set(source) != set(_SOURCE_FIELDS):
            raise _ExpectedSourceMismatch("expected_source contract is invalid")
        try:
            expected = TrustedReferenceSource.from_dict(source).to_dict()
        except (TypeError, ValueError) as exc:
            raise _ExpectedSourceMismatch("expected_source contract is invalid") from exc
        if expected != manifest.source.to_dict():
            raise _ExpectedSourceMismatch("trusted reference source identity mismatch")
    _validate_seed_provenance(root, manifest)
    return manifest


def load_trusted_reference(
    *, cache_dir: Path, content_fingerprint: str, expected_source: dict[str, str],
) -> TrustedReferenceManifest | None:
    """Load only a checksum-valid, source-matching reference; invalid data fails closed."""
    _require_sha256(content_fingerprint, "contentFingerprint")
    reference_dir = _reference_dir(cache_dir, content_fingerprint)
    try:
        expected = TrustedReferenceSource.from_dict(expected_source).to_dict()
    except (TypeError, ValueError):
        # The caller supplied stale/malformed identity metadata.  Do not turn
        # a read-side mismatch into destructive invalidation of a valid pointer.
        return None
    with _reference_lock(reference_dir / "trusted.lock"):
        try:
            return _load_trusted_reference_unlocked(
                cache_dir=cache_dir,
                content_fingerprint=content_fingerprint,
                expected_source=expected,
            )
        except _ExpectedSourceMismatch:
            return None
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            _invalidate_trusted_reference_locked(
                cache_dir=cache_dir,
                content_fingerprint=content_fingerprint,
                reason=f"load_invalid: {type(exc).__name__}: {str(exc)[:180]}",
            )
            return None


def write_trusted_reference(
    *, cache_dir: Path, manifest: TrustedReferenceManifest,
) -> TrustedReferenceManifest:
    """Publish one immutable reference generation; first valid writer wins."""
    validate_trusted_reference_manifest(manifest)
    root = _cache_root(cache_dir)
    _validate_seed_provenance(root, manifest)
    reference_dir = _reference_dir(root, manifest.content_fingerprint)
    generation_name = uuid.uuid4().hex
    generation_relative = Path(
        TRUSTED_REFERENCE_NAMESPACE,
        manifest.content_fingerprint,
        "generations",
        generation_name,
    )
    generation_path = _safe_relative_path(root, str(generation_relative), "generationPath")
    generation_path.mkdir(parents=True, exist_ok=False)
    manifest_path = generation_path / "manifest.json"
    try:
        _atomic_write(manifest_path, _manifest_bytes(manifest))
        pointer = {
            "schemaVersion": TRUSTED_REFERENCE_POINTER_SCHEMA_VERSION,
            "contentFingerprint": manifest.content_fingerprint,
            "referenceId": manifest.reference_id,
            "generationPath": generation_relative.as_posix(),
            "manifestPath": (generation_relative / "manifest.json").as_posix(),
            "manifestSha256": _sha256_file(manifest_path),
        }
        with _reference_lock(reference_dir / "trusted.lock"):
            try:
                existing = _load_trusted_reference_unlocked(
                    cache_dir=root,
                    content_fingerprint=manifest.content_fingerprint,
                    expected_source=manifest.source.to_dict(),
                )
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                _invalidate_trusted_reference_locked(
                    cache_dir=root,
                    content_fingerprint=manifest.content_fingerprint,
                    reason=f"write_replaced_invalid: {type(exc).__name__}: {str(exc)[:180]}",
                )
                existing = None
            if existing is not None:
                shutil.rmtree(generation_path, ignore_errors=True)
                return existing
            _atomic_write(
                reference_dir / "trusted.json",
                (json.dumps(pointer, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
            )
            return manifest
    except Exception:
        # Keep a failed generation out of the trusted pointer.  The orphaned
        # directory is intentionally recoverable for later retention cleanup.
        raise
