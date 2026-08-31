import hashlib

import pytest

from backend.agents.runner_identity import RunnerIdentity, RunnerIdentityError


def test_runner_identity_round_trips_and_has_canonical_fingerprint():
    identity = RunnerIdentity.from_dict(
        {
            "schemaVersion": "runner-identity-v1",
            "runnerId": "local-cli-review",
            "transport": "local_cli",
            "provider": "codex",
            "model": "gpt-5.4",
            "profile": "strict-review",
            "executionEnvironment": "local-worktree",
        }
    )
    identity = RunnerIdentity.from_dict(identity.to_dict())

    canonical = dict(identity.to_dict())
    canonical.pop("identityFingerprint")
    expected = hashlib.sha256(
        ("{" + ",".join(f'"{key}":"{canonical[key]}"' for key in sorted(canonical)) + "}").encode()
    ).hexdigest()

    assert identity.identity_fingerprint == expected
    assert identity.to_dict()["identityFingerprint"] == expected


@pytest.mark.parametrize("transport", ["local_cli", "remote_api", "local_model"])
def test_supported_transports_are_accepted(transport):
    identity = RunnerIdentity.from_dict(
        {
            "schemaVersion": "runner-identity-v1",
            "runnerId": f"{transport}-runner",
            "transport": transport,
            "provider": "provider-x",
            "model": "model-y",
            "profile": "default",
            "executionEnvironment": "test",
        }
    )

    assert identity.transport == transport
    assert len(identity.identity_fingerprint) == 64


def test_legacy_local_cli_mapping_is_explicit():
    identity = RunnerIdentity.from_legacy_local_cli(
        runner_id="local-cli-review",
        provider="codex",
        model="gpt-5.4",
        profile="strict-review",
        execution_environment="local-worktree",
    )

    assert identity.to_dict()["transport"] == "local_cli"


def test_legacy_hermes_mapping_is_explicit():
    identity = RunnerIdentity.from_legacy_hermes(
        runner_id="hermes-local",
        provider="hermes",
        model="deepseek-v4-flash",
        profile="max",
        execution_environment="local",
    )

    assert identity.to_dict()["transport"] == "remote_api"


@pytest.mark.parametrize(
    "payload",
    [
        {"runnerId": "runner"},
        {"schemaVersion": "runner-identity-v1", "runnerId": "runner", "transport": "unknown"},
        {
            "schemaVersion": "runner-identity-v1",
            "runnerId": "runner",
            "transport": "local_cli",
            "provider": "provider",
            "model": "model",
            "profile": "profile",
            "executionEnvironment": "env",
            "identityFingerprint": "not-a-sha256",
            "unexpected": "field",
        },
    ],
)
def test_invalid_or_unknown_identity_is_rejected(payload):
    with pytest.raises(RunnerIdentityError):
        RunnerIdentity.from_dict(payload)


def test_model_only_payload_does_not_infer_identity():
    with pytest.raises(RunnerIdentityError, match="schemaVersion"):
        RunnerIdentity.from_dict({"model": "gpt-5.4"})
