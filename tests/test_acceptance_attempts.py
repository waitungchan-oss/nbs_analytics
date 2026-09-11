"""Tests for bounded, append-only acceptance attempt metadata."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError

import pytest

from backend.agents.acceptance_attempts import (
    AcceptanceAttempt, MAX_ATTEMPTS, count_transport_attempts, record_attempt,
)


def test_record_attempt_appends_bounded_session_artifact(tmp_path):
    first = record_attempt(
        tmp_path,
        gate="strict_review",
        source_fingerprint="a" * 64,
        runner_fingerprint=None,
        outcome="blocked_runner_transport",
        blocked_reason="timeout",
        cache_hit=False,
        started_at="2026-09-11T00:00:00Z",
        finished_at="2026-09-11T00:00:01Z",
    )
    second = record_attempt(
        tmp_path,
        gate="strict_review",
        source_fingerprint="a" * 64,
        runner_fingerprint=None,
        outcome="pass",
        blocked_reason=None,
        cache_hit=False,
        started_at="2026-09-11T00:00:02Z",
        finished_at="2026-09-11T00:00:03Z",
    )

    assert first["attemptId"] != second["attemptId"]
    payload = json.loads((tmp_path / "attempts.json").read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == "verification-attempts-v1"
    assert len(payload["attempts"]) == 2
    assert payload["attempts"][0]["blockedReason"] == "timeout"
    assert "tokenUsage" not in payload["attempts"][0]
    assert "prompt" not in json.dumps(payload, ensure_ascii=False)


def test_record_attempt_rejects_invalid_outcome_and_fingerprint(tmp_path):
    with pytest.raises(ValueError, match="outcome"):
        record_attempt(
            tmp_path,
            gate="strict_review",
            source_fingerprint="a" * 64,
            runner_fingerprint=None,
            outcome="retry_forever",
            blocked_reason=None,
            cache_hit=False,
            started_at="2026-09-11T00:00:00Z",
            finished_at="2026-09-11T00:00:01Z",
        )

    with pytest.raises(ValueError, match="source fingerprint"):
        record_attempt(
            tmp_path,
            gate="strict_review",
            source_fingerprint="not-a-fingerprint",
            runner_fingerprint=None,
            outcome="pass",
            blocked_reason=None,
            cache_hit=False,
            started_at="2026-09-11T00:00:00Z",
            finished_at="2026-09-11T00:00:01Z",
        )


def test_record_attempt_bounds_reason_and_token_usage(tmp_path):
    value = record_attempt(
        tmp_path,
        gate="full_pytest",
        source_fingerprint="b" * 64,
        runner_fingerprint="c" * 64,
        outcome="pass",
        blocked_reason="x" * 512,
        cache_hit=True,
        started_at="2026-09-11T00:00:00Z",
        finished_at="2026-09-11T00:00:01Z",
        token_usage={"input": 12, "output": 7},
    )

    assert len(value["blockedReason"]) == 512
    payload = json.loads((tmp_path / "attempts.json").read_text(encoding="utf-8"))
    assert payload["attempts"][0]["runnerFingerprint"] == "c" * 64
    assert payload["attempts"][0]["tokenUsage"] == {"input": 12, "output": 7}


def test_acceptance_attempt_model_is_frozen():
    attempt = AcceptanceAttempt(
        attempt_id="attempt-1",
        gate="strict_review",
        source_fingerprint="a" * 64,
        runner_fingerprint=None,
        outcome="pass",
        blocked_reason=None,
        cache_hit=False,
        started_at="2026-09-11T00:00:00Z",
        finished_at="2026-09-11T00:00:01Z",
    )

    with pytest.raises(FrozenInstanceError):
        attempt.outcome = "failed"


def test_record_attempts_keep_only_the_newest_bounded_history(tmp_path):
    for index in range(MAX_ATTEMPTS + 3):
        record_attempt(
            tmp_path,
            gate="strict_review",
            source_fingerprint="a" * 64,
            runner_fingerprint=None,
            outcome="pass",
            blocked_reason=None,
            cache_hit=False,
            started_at="2026-09-11T00:00:00Z",
            finished_at="2026-09-11T00:00:01Z",
        )

    payload = json.loads((tmp_path / "attempts.json").read_text(encoding="utf-8"))
    assert len(payload["attempts"]) == MAX_ATTEMPTS


def test_record_attempt_rejects_malformed_existing_attempt_entry(tmp_path):
    (tmp_path / "attempts.json").write_text(
        json.dumps({
            "schemaVersion": "verification-attempts-v1",
            "attempts": [{"attemptId": "missing-required-fields"}],
        }),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="attempts artifact entry"):
        record_attempt(
            tmp_path,
            gate="strict_review",
            source_fingerprint="a" * 64,
            runner_fingerprint=None,
            outcome="pass",
            blocked_reason=None,
            cache_hit=False,
            started_at="2026-09-11T00:00:00Z",
            finished_at="2026-09-11T00:00:01Z",
        )


def test_record_attempt_serializes_concurrent_appends(tmp_path):
    def write(index):
        return record_attempt(
            tmp_path,
            gate="review_batch",
            source_fingerprint="a" * 64,
            runner_fingerprint=None,
            outcome="pass",
            blocked_reason=None,
            cache_hit=False,
            started_at="2026-09-11T00:00:00Z",
            finished_at="2026-09-11T00:00:01Z",
            retry_group="b" * 64,
            attempt_number=index + 1,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write, range(8)))

    payload = json.loads((tmp_path / "attempts.json").read_text(encoding="utf-8"))
    assert len(payload["attempts"]) == 8


def test_count_transport_attempts_is_gate_scoped(tmp_path):
    record_attempt(
        tmp_path, gate="strict_review", source_fingerprint="a" * 64,
        runner_fingerprint=None, outcome="blocked_runner_transport",
        blocked_reason="timeout", cache_hit=False,
        started_at="2026-09-11T00:00:00Z", finished_at="2026-09-11T00:00:01Z",
    )
    record_attempt(
        tmp_path, gate="full_pytest", source_fingerprint="a" * 64,
        runner_fingerprint=None, outcome="blocked_runner_transport",
        blocked_reason="timeout", cache_hit=False,
        started_at="2026-09-11T00:00:00Z", finished_at="2026-09-11T00:00:01Z",
    )

    assert count_transport_attempts(tmp_path, gate="strict_review") == 1
    assert count_transport_attempts(tmp_path, gate="hermes") == 0
