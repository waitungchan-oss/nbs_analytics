from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytest

from backend.agents.short_term_offload_models import ShortTermOffloadArtifact, ShortTermOffloadReference


NOW = datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc)


def _payload(**overrides):
    content = "pytest completed"
    value = {
        "schemaVersion": "short-term-offload-v1",
        "refId": "offload_run-1_001",
        "runId": "run-1",
        "sessionId": "session-1",
        "sourceKind": "tool_output",
        "summary": "pytest completed",
        "content": content,
        "contentSha256": sha256(content.encode()).hexdigest(),
        "createdAt": NOW.isoformat(),
        "expiresAt": (NOW + timedelta(minutes=30)).isoformat(),
        "sourceFingerprint": "a" * 64,
        "redactionStatus": "clean",
        "status": "ready",
    }
    value.update(overrides)
    return value


def test_artifact_round_trips_strict_payload():
    artifact = ShortTermOffloadArtifact.from_dict(_payload())
    assert artifact.to_dict() == _payload()
    reference = ShortTermOffloadReference.from_artifact(artifact)
    assert reference.ref_id == artifact.ref_id
    assert reference.content_sha256 == artifact.content_sha256


@pytest.mark.parametrize("field,value", [
    ("refId", "../escape"), ("runId", "/absolute"), ("contentSha256", "A" * 64),
    ("sourceFingerprint", "not-sha"), ("redactionStatus", "unknown"), ("status", "unknown"),
])
def test_artifact_rejects_unsafe_values(field, value):
    with pytest.raises(ValueError):
        ShortTermOffloadArtifact.from_dict(_payload(**{field: value}))


def test_artifact_rejects_unknown_key_and_hash_mismatch():
    with pytest.raises(ValueError):
        ShortTermOffloadArtifact.from_dict({**_payload(), "extra": True})
    with pytest.raises(ValueError):
        ShortTermOffloadArtifact.from_dict(_payload(contentSha256="b" * 64))


def test_artifact_rejects_expired_or_overlong_content():
    with pytest.raises(ValueError):
        ShortTermOffloadArtifact.from_dict(_payload(expiresAt=(NOW - timedelta(seconds=1)).isoformat()))
    content = "x" * 32001
    with pytest.raises(ValueError):
        ShortTermOffloadArtifact.from_dict(_payload(content=content, contentSha256=sha256(content.encode()).hexdigest()))


def test_artifact_rejects_blocked_content_and_invalid_source_or_timezone():
    with pytest.raises(ValueError):
        ShortTermOffloadArtifact.from_dict(_payload(status="blocked", redactionStatus="clean"))
    with pytest.raises(ValueError):
        ShortTermOffloadArtifact.from_dict(_payload(sourceKind="prompt"))
    with pytest.raises(ValueError):
        ShortTermOffloadArtifact.from_dict(_payload(expiresAt=NOW.isoformat().replace("+00:00", "")))
    with pytest.raises(ValueError):
        ShortTermOffloadArtifact.from_dict(_payload(expiresAt=NOW.isoformat()))


def test_artifact_enforces_utf8_summary_cap_and_identifier_boundaries():
    summary = "字" * 1025
    with pytest.raises(ValueError):
        ShortTermOffloadArtifact.from_dict(_payload(summary=summary))
    valid_id = "a" + "b" * 127
    assert ShortTermOffloadArtifact.from_dict(_payload(refId=valid_id)).ref_id == valid_id
    with pytest.raises(ValueError):
        ShortTermOffloadArtifact.from_dict(_payload(refId="a" * 129))
