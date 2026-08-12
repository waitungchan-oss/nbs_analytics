from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import backend.agents.memory_sidecar_task_descriptor as descriptor


SHA, HEAD = "a" * 64, "b" * 40
IDENTITY = descriptor.RuntimeIdentity(
    HEAD, SHA, "hermes", "model", "c" * 64, datetime(2026, 8, 11, tzinfo=timezone.utc)
)


def _canonical(payload: dict, excluded: set[str]) -> bytes:
    return json.dumps(
        {key: value for key, value in payload.items() if key not in excluded},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _fingerprint(payload: dict, field: str) -> str:
    return sha256(_canonical(payload, {"signature", field})).hexdigest()


def _task_fingerprint(workflow: dict) -> str:
    return sha256(_canonical({
        "workflowStage": workflow["workflowStage"],
        "action": workflow["action"],
        "approvedScope": workflow["approvedScope"],
        "briefFingerprint": workflow["briefFingerprint"],
        "descriptorFingerprint": workflow["descriptorFingerprint"],
    }, {"descriptorFingerprint"})).hexdigest()


def _sign(payload: dict, private_key: Ed25519PrivateKey) -> None:
    payload["signature"] = base64.b64encode(private_key.sign(_canonical(payload, {"signature"}))).decode("ascii")


def _write_json(path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _valid_artifacts(root, private_key: Ed25519PrivateKey, *, scope=None, expires_at=None, workflow_stage="implementation", action="code"):
    amendment = {
        "schemaVersion": "memory-sidecar-default-on-amendment-v1",
        "contractAmendmentStatus": "approved",
        "liveAcceptanceStatus": "ready",
        "writerEnabled": False,
        "providerInvocationPermission": "explicit",
        "amendmentId": "d" * 64,
        "revision": "r1",
        "approvedBy": "governance-role",
        "approvedAt": "2026-08-10T00:00:00+00:00",
        "artifactFingerprint": "",
        "allowedTelemetrySchemas": [
            "memory-hints-v1",
            "memory-sidecar-telemetry-v1",
            "memory-sidecar-policy-decision-v1",
        ],
        "keyId": "governance-1",
        "signature": "",
    }
    amendment["artifactFingerprint"] = _fingerprint(amendment, "artifactFingerprint")
    _sign(amendment, private_key)
    evidence = {
        "schemaVersion": "memory-sidecar-live-ab-evidence-v1",
        "liveAcceptanceStatus": "ready",
        "amendmentId": amendment["amendmentId"],
        "amendmentRevision": amendment["revision"],
        "amendmentFingerprint": amendment["artifactFingerprint"],
        "gitHead": HEAD,
        "policyFingerprint": SHA,
        "provider": "hermes",
        "model": "model",
        "configFingerprint": "c" * 64,
        "createdAt": "2026-08-10T00:00:00+00:00",
        "expiresAt": expires_at or "2026-08-12T00:00:00+00:00",
        "evidenceFingerprint": "",
        "attempts": [
            {
                "attemptId": f"attempt-{number}",
                "controlReceiptFingerprint": f"{number}" * 64,
                "treatmentReceiptFingerprint": f"{number + 3}" * 64,
                "evidenceFingerprint": f"{number + 6}" * 64,
                "nonLatencyGatesPassed": True,
                "originalVerdict": "ready",
                "originalReasons": [],
                "gitHead": HEAD,
                "policyFingerprint": SHA,
                "provider": "hermes",
                "model": "model",
                "configFingerprint": "c" * 64,
            }
            for number in (1, 2, 3)
        ],
        "keyId": "governance-1",
        "signature": "",
    }
    evidence["evidenceFingerprint"] = _fingerprint(evidence, "evidenceFingerprint")
    _sign(evidence, private_key)
    workflow = {
        "schemaVersion": "memory-sidecar-workflow-descriptor-v1",
        "workflowStage": workflow_stage,
        "action": action,
        "approvedScope": scope or ["backend agents"],
        "briefFingerprint": "e" * 64,
        "descriptorFingerprint": "",
    }
    workflow["descriptorFingerprint"] = _fingerprint(workflow, "descriptorFingerprint")
    _write_json(root / "docs/agents/approved/memory-sidecar-default-on-amendment-v1.json", amendment)
    _write_json(root / ".nbs_agent_runtime/live-ab/memory-sidecar-live-ab-evidence-v1.json", evidence)
    _write_json(root / "docs/agents/approved/memory-sidecar-workflow-descriptor-v1.json", workflow)
    return amendment, evidence, workflow


@pytest.fixture
def installed_composition():
    token = descriptor._DEPLOYMENT_COMPOSITION_TOKEN
    yield lambda composition: descriptor.install_deployment_composition(composition, token)
    descriptor.install_deployment_composition(None, token)


class ControlledKeyProvider:
    def __init__(self, keys):
        self.keys = keys
        self.calls = []

    def resolve_public_key(self, key_id):
        self.calls.append(key_id)
        return self.keys.get(key_id)


def _provider(keys):
    return ControlledKeyProvider(keys)


def _install_deployment_composition(installed_composition, root, key_provider, workflow_fingerprint, amendment):
    installed_composition(descriptor.DeploymentMemorySidecarComposition(
        project_root=root,
        key_provider=key_provider,
        workflow_binding=descriptor.TrustedWorkflowDescriptorBinding(workflow_fingerprint, "deployment-binding"),
        amendment_binding=descriptor.TrustedAmendmentBinding(
            amendment["amendmentId"], amendment["revision"], amendment["approvedBy"], amendment["artifactFingerprint"],
        ),
    ))


def test_real_ed25519_reader_allows_only_verified_bound_artifacts(tmp_path, installed_composition):
    private_key = Ed25519PrivateKey.generate()
    amendment, evidence, workflow = _valid_artifacts(tmp_path, private_key)
    _install_deployment_composition(installed_composition, tmp_path, _provider({"governance-1": private_key.public_key()}), workflow["descriptorFingerprint"], amendment)

    request = descriptor.build_memory_sidecar_runtime_request(project_root=tmp_path, identity=IDENTITY)

    assert request.provider_invocation_allowed is True
    assert request.task_class == "development"
    assert request.protected_markers == ()
    assert request.task_fingerprint == _task_fingerprint(workflow)
    assert request.amendment_fingerprint == amendment["artifactFingerprint"]
    assert request.live_evidence_manifest_fingerprint == evidence["evidenceFingerprint"]


@pytest.mark.parametrize("artifact_name,mutation", [
    ("amendment", "unsigned"), ("evidence", "unsigned"),
    ("amendment", "tampered"), ("evidence", "tampered"),
])
def test_real_ed25519_reader_rejects_unsigned_or_tampered_artifacts(tmp_path, installed_composition, artifact_name, mutation):
    private_key = Ed25519PrivateKey.generate()
    amendment, evidence, workflow = _valid_artifacts(tmp_path, private_key)
    artifact = amendment if artifact_name == "amendment" else evidence
    if mutation == "unsigned":
        del artifact["signature"]
    else:
        artifact["approvedBy" if artifact_name == "amendment" else "provider"] = "tampered"
    path = tmp_path / ("docs/agents/approved/memory-sidecar-default-on-amendment-v1.json" if artifact_name == "amendment" else ".nbs_agent_runtime/live-ab/memory-sidecar-live-ab-evidence-v1.json")
    _write_json(path, artifact)
    _install_deployment_composition(installed_composition, tmp_path, _provider({"governance-1": private_key.public_key()}), workflow["descriptorFingerprint"], amendment)

    request = descriptor.build_memory_sidecar_runtime_request(project_root=tmp_path, identity=IDENTITY)

    assert (request.mode, request.provider_invocation_allowed) == ("recall_off", False)


@pytest.mark.parametrize("unknown_key,key_provider", [(True, "unknown"), (False, None)])
def test_reader_rejects_unknown_key_and_missing_deployment_provider(tmp_path, installed_composition, unknown_key, key_provider):
    private_key = Ed25519PrivateKey.generate()
    amendment, _, workflow = _valid_artifacts(tmp_path, private_key)
    if unknown_key:
        amendment["keyId"] = "unknown-key"
        _write_json(tmp_path / "docs/agents/approved/memory-sidecar-default-on-amendment-v1.json", amendment)
        key_provider = _provider({"governance-1": private_key.public_key()})
    _install_deployment_composition(installed_composition, tmp_path, key_provider, workflow["descriptorFingerprint"], amendment)

    request = descriptor.build_memory_sidecar_runtime_request(project_root=tmp_path, identity=IDENTITY)

    assert (request.status, request.reason, request.provider_invocation_allowed) == ("blocked_policy", "key_bundle_missing", False)
    if unknown_key:
        assert key_provider.calls == ["unknown-key"]


@pytest.mark.parametrize("attempt_count,expires_at,identity", [
    (2, "2026-08-12T00:00:00+00:00", IDENTITY),
    (3, "2026-08-10T00:00:00+00:00", IDENTITY),
    (3, "2026-08-12T00:00:00+00:00", descriptor.RuntimeIdentity("f" * 40, SHA, "hermes", "model", "c" * 64, datetime(2026, 8, 11, tzinfo=timezone.utc))),
])
def test_reader_requires_exactly_three_fresh_current_identity_attempts(tmp_path, installed_composition, attempt_count, expires_at, identity):
    private_key = Ed25519PrivateKey.generate()
    amendment, evidence, workflow = _valid_artifacts(tmp_path, private_key, expires_at=expires_at)
    evidence["attempts"] = evidence["attempts"][:attempt_count]
    evidence["evidenceFingerprint"] = _fingerprint(evidence, "evidenceFingerprint")
    _sign(evidence, private_key)
    _write_json(tmp_path / ".nbs_agent_runtime/live-ab/memory-sidecar-live-ab-evidence-v1.json", evidence)
    _install_deployment_composition(installed_composition, tmp_path, _provider({"governance-1": private_key.public_key()}), workflow["descriptorFingerprint"], amendment)

    request = descriptor.build_memory_sidecar_runtime_request(project_root=tmp_path, identity=identity)

    assert (request.mode, request.provider_invocation_allowed) == ("recall_off", False)


def test_protected_descriptor_is_recomputed_and_retained_when_off(tmp_path, installed_composition):
    private_key = Ed25519PrivateKey.generate()
    amendment, _, workflow = _valid_artifacts(tmp_path, private_key, scope=["runtime write", "backend agents"])
    _install_deployment_composition(installed_composition, tmp_path, _provider({"governance-1": private_key.public_key()}), workflow["descriptorFingerprint"], amendment)

    request = descriptor.build_memory_sidecar_runtime_request(project_root=tmp_path, identity=IDENTITY)

    assert request.task_class == "runtime_mutation"
    assert request.protected_markers == ("runtime-write",)
    assert request.task_fingerprint == _task_fingerprint(workflow)
    assert request.task_fingerprint != "0" * 64
    assert request.provider_invocation_allowed is False


def test_public_factory_rejects_caller_reader_and_artifact_path_overrides(tmp_path):
    with pytest.raises(TypeError):
        descriptor.build_memory_sidecar_runtime_request(project_root=tmp_path, identity=IDENTITY, artifact_reader=object())


def test_installation_rejects_invalid_deployment_token(tmp_path):
    with pytest.raises(PermissionError):
        descriptor.install_deployment_composition(object(), object())


@pytest.mark.parametrize("binding_field", ["amendmentId", "revision", "approvedBy", "artifactFingerprint"])
def test_verified_amendment_must_match_deployment_binding(tmp_path, installed_composition, binding_field):
    private_key = Ed25519PrivateKey.generate()
    amendment, _, workflow = _valid_artifacts(tmp_path, private_key)
    expected = amendment.copy()
    expected[binding_field] = "f" * 64 if binding_field in {"amendmentId", "artifactFingerprint"} else "different"
    _install_deployment_composition(installed_composition, tmp_path, _provider({"governance-1": private_key.public_key()}), workflow["descriptorFingerprint"], expected)

    request = descriptor.build_memory_sidecar_runtime_request(project_root=tmp_path, identity=IDENTITY)

    assert (request.mode, request.provider_invocation_allowed) == ("recall_off", False)


@pytest.mark.parametrize("scope,task_class,markers", [
    (["approval"], "governance", ("approval",)),
    (["dispatch"], "governance", ("dispatch",)),
    (["acceptance"], "acceptance", ("acceptance",)),
    (["baseline"], "baseline", ("baseline",)),
    (["security", "credential"], "security", ("security", "credential")),
    (["data"], "data", ("data",)),
    (["sqlite", "revenue"], "data", ("sqlite", "revenue")),
    (["runtime-write"], "runtime_mutation", ("runtime-write",)),
    (["runtime_write"], "runtime_mutation", ("runtime-write",)),
    (["runtime write"], "runtime_mutation", ("runtime-write",)),
    (["rollback", "merge", "push", "snapshot"], "runtime_mutation", ("rollback", "merge", "push", "snapshot")),
])
def test_protected_marker_normalization_and_classification(tmp_path, installed_composition, scope, task_class, markers):
    private_key = Ed25519PrivateKey.generate()
    amendment, _, workflow = _valid_artifacts(tmp_path, private_key, scope=scope)
    _install_deployment_composition(installed_composition, tmp_path, _provider({"governance-1": private_key.public_key()}), workflow["descriptorFingerprint"], amendment)

    request = descriptor.build_memory_sidecar_runtime_request(project_root=tmp_path, identity=IDENTITY)

    assert (request.task_class, request.protected_markers, request.provider_invocation_allowed) == (task_class, markers, False)


@pytest.mark.parametrize("workflow_stage,action", [("unknown", "code"), ("implementation", "unknown"), ("implementation", "")])
def test_unknown_or_missing_workflow_class_is_not_development(tmp_path, installed_composition, workflow_stage, action):
    private_key = Ed25519PrivateKey.generate()
    amendment, _, workflow = _valid_artifacts(tmp_path, private_key, workflow_stage=workflow_stage, action=action)
    _install_deployment_composition(installed_composition, tmp_path, _provider({"governance-1": private_key.public_key()}), workflow["descriptorFingerprint"], amendment)

    request = descriptor.build_memory_sidecar_runtime_request(project_root=tmp_path, identity=IDENTITY)

    assert (request.task_class, request.mode, request.provider_invocation_allowed) == (None, "recall_off", False)
