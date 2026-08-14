from datetime import datetime, timedelta, timezone

import pytest

from backend.agents.short_term_offload_policy import ShortTermOffloadPolicy


def test_policy_uses_bounded_defaults_and_stable_fingerprint():
    policy = ShortTermOffloadPolicy()
    assert policy.default_ttl_minutes == 30
    assert policy.max_ttl_hours == 24
    assert policy.max_content_bytes == 32000
    assert policy.max_summary_bytes == 2048
    assert policy.max_artifacts_per_run == 20
    assert policy.max_total_bytes_per_run == 200000
    assert policy.fingerprint() == ShortTermOffloadPolicy().fingerprint()


def test_policy_rejects_unsafe_ref_and_ttl():
    policy = ShortTermOffloadPolicy()
    for value in ("", "../x", "/tmp/x", "bad space"):
        with pytest.raises(ValueError):
            policy.validate_ref_id(value)
    created = datetime(2026, 8, 14, tzinfo=timezone.utc)
    policy.validate_ttl(created, created + timedelta(minutes=30))
    with pytest.raises(ValueError):
        policy.validate_ttl(created, created + timedelta(hours=24, seconds=1))


def test_policy_rejects_invalid_caps():
    with pytest.raises(ValueError):
        ShortTermOffloadPolicy(max_content_bytes=0)
    with pytest.raises(ValueError):
        ShortTermOffloadPolicy(max_summary_bytes=2049)
