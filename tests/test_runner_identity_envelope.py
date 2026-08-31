import json

import pytest

from backend.agents.runner_identity import RunnerIdentity
from backend.agents.runner_identity_envelope import (
    IdentityEnvelopeError,
    read_identity_envelope,
    write_identity_envelope,
)


@pytest.fixture
def identity():
    return RunnerIdentity.from_dict(
        {
            "schemaVersion": "runner-identity-v1",
            "runnerId": "local-cli-review",
            "transport": "local_cli",
            "provider": "codex",
            "model": "gpt-5.4",
            "profile": "strict-review",
            "executionEnvironment": "test",
        }
    )


def test_envelope_round_trips_and_binds_to_source(identity, tmp_path):
    path = write_identity_envelope(
        tmp_path / "identity.json",
        identity,
        source_fingerprint="a" * 64,
        artifact_kind="runner-capability-v1",
    )

    loaded = read_identity_envelope(path, expected_source_fingerprint="a" * 64)

    assert loaded.identity == identity
    assert loaded.source_fingerprint == "a" * 64
    assert loaded.artifact_kind == "runner-capability-v1"


def test_envelope_rejects_source_mismatch(identity, tmp_path):
    path = write_identity_envelope(
        tmp_path / "identity.json",
        identity,
        source_fingerprint="a" * 64,
        artifact_kind="runner-capability-v1",
    )

    with pytest.raises(IdentityEnvelopeError, match="source fingerprint"):
        read_identity_envelope(path, expected_source_fingerprint="b" * 64)


def test_envelope_rejects_symlink_path(identity, tmp_path):
    target = write_identity_envelope(
        tmp_path / "identity.json",
        identity,
        source_fingerprint="a" * 64,
        artifact_kind="runner-capability-v1",
    )
    link = tmp_path / "identity-link.json"
    link.symlink_to(target)

    with pytest.raises(IdentityEnvelopeError, match="regular file"):
        read_identity_envelope(link)


def test_envelope_rejects_tampered_identity(identity, tmp_path):
    path = write_identity_envelope(
        tmp_path / "identity.json",
        identity,
        source_fingerprint="a" * 64,
        artifact_kind="runner-capability-v1",
    )
    payload = json.loads(path.read_text())
    payload["identity"]["model"] = "tampered-model"
    path.write_text(json.dumps(payload))

    with pytest.raises(IdentityEnvelopeError):
        read_identity_envelope(path)
