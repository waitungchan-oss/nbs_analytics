"""Fail-closed Memory Sidecar request factory with deployment-owned readers."""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Mapping, Protocol, runtime_checkable

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError:  # Deployment without the verifier must remain recall-off.
    InvalidSignature = ValueError
    Ed25519PublicKey = None


_SHA = re.compile(r"^[0-9a-f]{64}$")
_HEAD = re.compile(r"^[0-9a-f]{40}$")
_AMENDMENT = Path("docs/agents/approved/memory-sidecar-default-on-amendment-v1.json")
_EVIDENCE = Path(".nbs_agent_runtime/live-ab/memory-sidecar-live-ab-evidence-v1.json")
_WORKFLOW = Path("docs/agents/approved/memory-sidecar-workflow-descriptor-v1.json")
_PROTECTED = (
    "approval", "dispatch", "acceptance", "baseline", "sqlite", "data", "revenue",
    "security", "credential", "runtime-write", "rollback", "merge", "push", "snapshot",
)
_DEPLOYMENT_COMPOSITION_TOKEN = object()
_DEPLOYMENT_COMPOSITION = None


def canonical_signed_payload(payload: Mapping, *, excluded=None) -> bytes:
    if not isinstance(payload, Mapping):
        raise ValueError("payload")
    return json.dumps(
        {key: value for key, value in payload.items() if key not in set(excluded or {"signature"})},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _fingerprint(payload, field):
    return sha256(canonical_signed_payload(payload, excluded={"signature", field})).hexdigest()


def _sha(value):
    return isinstance(value, str) and bool(_SHA.fullmatch(value))


def _time(value):
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else None
        return parsed if parsed and parsed.tzinfo and parsed.utcoffset() is not None else None
    except ValueError:
        return None


def _json_object(path: Path):
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    try:
        if path.is_symlink() or not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicates)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


@dataclass(frozen=True)
class RuntimeIdentity:
    git_head: object
    policy_fingerprint: object
    provider: object
    model: object
    config_fingerprint: object
    now: object

    @property
    def valid(self):
        return (
            isinstance(self.git_head, str) and bool(_HEAD.fullmatch(self.git_head))
            and _sha(self.policy_fingerprint) and _sha(self.config_fingerprint)
            and isinstance(self.provider, str) and bool(self.provider)
            and isinstance(self.model, str) and bool(self.model)
            and isinstance(self.now, datetime) and self.now.tzinfo is not None
            and self.now.utcoffset() is not None
        )


@dataclass(frozen=True)
class TrustedWorkflowDescriptorBinding:
    descriptor_fingerprint: str
    binding_id: str


@dataclass(frozen=True)
class TrustedAmendmentBinding:
    amendment_id: str
    revision: str
    approved_by: str
    artifact_fingerprint: str


@dataclass(frozen=True)
class TrustedWorkflowDescriptor:
    workflow_stage: str
    action: str
    approved_scope: tuple[str, ...]
    brief_fingerprint: str
    descriptor_fingerprint: str


@runtime_checkable
class TrustedGovernanceKeyProvider(Protocol):
    def resolve_public_key(self, key_id: str) -> object: ...


@dataclass(frozen=True)
class MemorySidecarRuntimeRequest:
    schema_version: str
    task_class: str | None
    protected_markers: tuple[str, ...]
    task_fingerprint: str
    amendment_fingerprint: str | None
    amendment_revision: str | None
    live_evidence_manifest_fingerprint: str | None
    live_acceptance_status: str | None
    provider_invocation_allowed: bool
    mode: str
    status: str
    reason: str


class TrustedWorkflowDescriptorReader:
    def __init__(self, project_root, binding):
        self.root = Path(project_root).resolve()
        self.binding = binding

    def read(self):
        payload = _json_object(self.root / _WORKFLOW)
        keys = {"schemaVersion", "workflowStage", "action", "approvedScope", "briefFingerprint", "descriptorFingerprint"}
        if (
            not isinstance(self.binding, TrustedWorkflowDescriptorBinding)
            or not _sha(self.binding.descriptor_fingerprint) or not self.binding.binding_id
            or not isinstance(payload, dict) or set(payload) != keys
            or payload.get("schemaVersion") != "memory-sidecar-workflow-descriptor-v1"
            or payload.get("descriptorFingerprint") != self.binding.descriptor_fingerprint
            or payload.get("descriptorFingerprint") != _fingerprint(payload, "descriptorFingerprint")
            or not isinstance(payload["workflowStage"], str) or not payload["workflowStage"]
            or not isinstance(payload["action"], str) or not payload["action"]
            or not isinstance(payload["approvedScope"], list)
            or not all(isinstance(item, str) and item for item in payload["approvedScope"])
            or not _sha(payload["briefFingerprint"])
        ):
            return None
        return TrustedWorkflowDescriptor(
            payload["workflowStage"], payload["action"], tuple(payload["approvedScope"]),
            payload["briefFingerprint"], payload["descriptorFingerprint"],
        )


class DeploymentOwnedEd25519ArtifactReader:
    """Reads only fixed artifacts and only verifies with a deployment key bundle."""
    def __init__(self, project_root: Path, key_provider: TrustedGovernanceKeyProvider | None):
        self._root = Path(project_root).resolve()
        self._key_provider = key_provider if isinstance(key_provider, TrustedGovernanceKeyProvider) else None

    def _read_signed(self, relative_path: Path, keys: set[str], fingerprint_field: str):
        payload = _json_object(self._root / relative_path)
        if Ed25519PublicKey is None or not isinstance(payload, dict) or set(payload) != keys:
            return None
        key_id, signature = payload.get("keyId"), payload.get("signature")
        try:
            public_key = self._key_provider.resolve_public_key(key_id) if self._key_provider and isinstance(key_id, str) else None
        except Exception:
            return None
        if not isinstance(public_key, Ed25519PublicKey) or not isinstance(signature, str) or not _sha(payload.get(fingerprint_field)):
            return None
        try:
            public_key.verify(base64.b64decode(signature, validate=True), canonical_signed_payload(payload))
        except (InvalidSignature, TypeError, ValueError):
            return None
        return payload if payload[fingerprint_field] == _fingerprint(payload, fingerprint_field) else None

    def read_verified_amendment(self):
        keys = {
            "schemaVersion", "contractAmendmentStatus", "liveAcceptanceStatus", "writerEnabled",
            "providerInvocationPermission", "amendmentId", "revision", "approvedBy", "approvedAt",
            "artifactFingerprint", "allowedTelemetrySchemas", "keyId", "signature",
        }
        payload = self._read_signed(_AMENDMENT, keys, "artifactFingerprint")
        expected_telemetry = ["memory-hints-v1", "memory-sidecar-telemetry-v1", "memory-sidecar-policy-decision-v1"]
        if not isinstance(payload, dict) or any((
            payload.get("schemaVersion") != "memory-sidecar-default-on-amendment-v1",
            payload.get("contractAmendmentStatus") != "approved",
            payload.get("liveAcceptanceStatus") != "ready",
            payload.get("writerEnabled") is not False,
            payload.get("providerInvocationPermission") != "explicit",
            not _sha(payload.get("amendmentId")),
            not isinstance(payload.get("revision"), str) or not payload["revision"],
            not isinstance(payload.get("approvedBy"), str) or not payload["approvedBy"],
            _time(payload.get("approvedAt")) is None,
            payload.get("allowedTelemetrySchemas") != expected_telemetry,
        )):
            return None
        return payload

    def read_verified_evidence(self, identity: RuntimeIdentity, amendment):
        keys = {
            "schemaVersion", "liveAcceptanceStatus", "amendmentId", "amendmentRevision", "amendmentFingerprint",
            "gitHead", "policyFingerprint", "provider", "model", "configFingerprint", "createdAt", "expiresAt",
            "evidenceFingerprint", "attempts", "keyId", "signature",
        }
        payload = self._read_signed(_EVIDENCE, keys, "evidenceFingerprint")
        if not isinstance(payload, dict) or not isinstance(amendment, Mapping):
            return None
        if any(payload.get(source) != amendment.get(target) for source, target in (
            ("amendmentId", "amendmentId"), ("amendmentRevision", "revision"), ("amendmentFingerprint", "artifactFingerprint"),
        )):
            return None
        return payload if _evidence_ok(payload, identity) else None


@dataclass(frozen=True)
class DeploymentMemorySidecarComposition:
    project_root: Path
    key_provider: TrustedGovernanceKeyProvider | None
    workflow_binding: TrustedWorkflowDescriptorBinding
    amendment_binding: TrustedAmendmentBinding

    @property
    def workflow_descriptor_reader(self):
        return TrustedWorkflowDescriptorReader(self.project_root, self.workflow_binding)

    @property
    def artifact_reader(self):
        return DeploymentOwnedEd25519ArtifactReader(self.project_root, self.key_provider)


def install_deployment_composition(composition, deployment_token) -> None:
    """Deployment bootstrap-only injection; callers need the module-private token."""
    if deployment_token is not _DEPLOYMENT_COMPOSITION_TOKEN:
        raise PermissionError("deployment token required")
    if composition is not None and not isinstance(composition, DeploymentMemorySidecarComposition):
        raise TypeError("deployment composition required")
    global _DEPLOYMENT_COMPOSITION
    _DEPLOYMENT_COMPOSITION = composition


def _evidence_ok(evidence, identity):
    if not isinstance(identity, RuntimeIdentity) or not identity.valid or not isinstance(evidence, Mapping):
        return False
    expected = {
        "gitHead": identity.git_head, "policyFingerprint": identity.policy_fingerprint,
        "provider": identity.provider, "model": identity.model, "configFingerprint": identity.config_fingerprint,
    }
    created_at, expires_at, attempts = _time(evidence.get("createdAt")), _time(evidence.get("expiresAt")), evidence.get("attempts")
    if (
        evidence.get("schemaVersion") != "memory-sidecar-live-ab-evidence-v1"
        or evidence.get("liveAcceptanceStatus") != "ready" or not _sha(evidence.get("evidenceFingerprint"))
        or any(evidence.get(key) != value for key, value in expected.items())
        or not created_at or not expires_at or not created_at <= expires_at <= created_at + timedelta(days=30)
        or expires_at <= identity.now or not isinstance(attempts, list) or len(attempts) != 3
    ):
        return False
    ids = set()
    for attempt in attempts:
        if not isinstance(attempt, Mapping) or not isinstance(attempt.get("attemptId"), str) or not attempt["attemptId"]:
            return False
        ids.add(attempt["attemptId"])
        if (
            not all(_sha(attempt.get(key)) for key in ("controlReceiptFingerprint", "treatmentReceiptFingerprint", "evidenceFingerprint"))
            or attempt.get("nonLatencyGatesPassed") is not True
            or (attempt.get("originalVerdict"), attempt.get("originalReasons")) not in (("ready", []), ("rejected", ["latency"]))
            or any(attempt.get(key) != value for key, value in expected.items())
        ):
            return False
    return len(ids) == 3


def _descriptor_details(descriptor):
    task_fingerprint = _fingerprint({
        "workflowStage": descriptor.workflow_stage, "action": descriptor.action,
        "approvedScope": list(descriptor.approved_scope), "briefFingerprint": descriptor.brief_fingerprint,
        "descriptorFingerprint": descriptor.descriptor_fingerprint,
    }, "descriptorFingerprint")
    words = re.sub(r"\s+", " ", re.sub(r"[-_]", " ", " ".join(
        (descriptor.workflow_stage, descriptor.action, *descriptor.approved_scope)
    ).lower())).strip()
    markers = tuple(marker for marker in _PROTECTED if f" {marker.replace('-', ' ')} " in f" {words} ")
    if any(marker in markers for marker in ("approval", "dispatch")):
        task_class = "governance"
    elif "acceptance" in markers:
        task_class = "acceptance"
    elif "baseline" in markers:
        task_class = "baseline"
    elif any(marker in markers for marker in ("security", "credential")):
        task_class = "security"
    elif any(marker in markers for marker in ("sqlite", "data", "revenue")):
        task_class = "data"
    elif markers:
        task_class = "runtime_mutation"
    elif descriptor.workflow_stage != "implementation" or descriptor.action not in {"code", "test", "refactor", "configuration", "documentation"}:
        task_class = None
    else:
        task_class = "development"
    return task_class, markers, task_fingerprint


def _off(task_class=None, markers=(), fingerprint=None, reason="blocked_policy"):
    return MemorySidecarRuntimeRequest(
        "memory-sidecar-runtime-request-v1", task_class, markers,
        fingerprint or "0" * 64, None, None, None, None, False, "recall_off", "blocked_policy", reason,
    )


def _build_with_composition(composition, identity):
    if not isinstance(composition, DeploymentMemorySidecarComposition) or not isinstance(identity, RuntimeIdentity) or not identity.valid:
        return _off(reason="trusted_input_missing")
    descriptor = composition.workflow_descriptor_reader.read()
    if descriptor is None:
        return _off(reason="descriptor_invalid")
    task_class, markers, task_fingerprint = _descriptor_details(descriptor)
    if task_class != "development":
        return _off(task_class, markers, task_fingerprint, "protected_task" if markers else "descriptor_invalid")
    reader = composition.artifact_reader
    amendment = reader.read_verified_amendment()
    if amendment is None:
        return _off(task_class, markers, task_fingerprint, "key_bundle_missing")
    binding = composition.amendment_binding
    if (
        not isinstance(binding, TrustedAmendmentBinding)
        or not _sha(binding.amendment_id) or not _sha(binding.artifact_fingerprint)
        or not isinstance(binding.revision, str) or not binding.revision
        or not isinstance(binding.approved_by, str) or not binding.approved_by
        or any(amendment.get(key) != value for key, value in (
            ("amendmentId", binding.amendment_id), ("revision", binding.revision),
            ("approvedBy", binding.approved_by), ("artifactFingerprint", binding.artifact_fingerprint),
        ))
    ):
        return _off(task_class, markers, task_fingerprint, "amendment_binding_mismatch")
    evidence = reader.read_verified_evidence(identity, amendment)
    if evidence is None:
        return _off(task_class, markers, task_fingerprint, "evidence_not_ready")
    return MemorySidecarRuntimeRequest(
        "memory-sidecar-runtime-request-v1", task_class, markers, task_fingerprint,
        amendment["artifactFingerprint"], amendment["revision"], evidence["evidenceFingerprint"],
        "ready", True, "recall_on", "allowed", "eligible_development",
    )


def build_memory_sidecar_runtime_request(*, project_root: Path | str, identity: object) -> MemorySidecarRuntimeRequest:
    """Public factory: callers can supply only root and current runtime identity."""
    root = Path(project_root).resolve()
    try:
        composition = _DEPLOYMENT_COMPOSITION
        if composition is not None:
            return _build_with_composition(composition, identity)
    except Exception:
        pass
    return _off(fingerprint=sha256(str(root).encode("utf-8")).hexdigest(), reason="composition_unavailable")
