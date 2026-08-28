"""Task 1: immutable Verification Session contract tests.

Covers exact `verification-session-v1` schema round trips, lowercase
SHA-256/SHA validation, RFC3339 `createdAt`, the source-seal-only canonical
fingerprint, `assert_fresh` staleness, and the confined atomic
write_session/read_session persistence under
`.nbs_agent_runtime/verification_sessions/`.
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from hashlib import sha256
from pathlib import Path

import pytest

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.verification_session import (
    ALLOWED_SESSION_STATUSES,
    StaleVerificationSession,
    VerificationSession,
    read_session,
    write_session,
)


_EXPECTED_KEYS = {
    "schemaVersion", "sessionId", "status", "projectId", "baseSha", "headSha",
    "briefPath", "briefFingerprint", "worktreeFingerprint", "diffFingerprint",
    "contractFingerprint", "policyFingerprint", "createdAt", "gates",
}


def _session(**overrides) -> VerificationSession:
    values = dict(
        project_id="nbs_analytics",
        base_sha="a" * 40,
        head_sha="b" * 40,
        brief_path="docs/briefs/task.md",
        brief_fingerprint="c" * 64,
        worktree_fingerprint="d" * 64,
        diff_fingerprint="e" * 64,
        contract_fingerprint="f" * 64,
        policy_fingerprint="0" * 64,
    )
    values.update(overrides)
    return VerificationSession.create(**values)


def _session_path(tmp_path: Path, session: VerificationSession) -> Path:
    return (
        tmp_path / ".nbs_agent_runtime" / "verification_sessions"
        / session.session_id / "session.json"
    )


def test_session_round_trip_has_exact_schema():
    session = _session()
    restored = VerificationSession.from_dict(session.to_dict())
    assert restored.session_id == session.session_id
    assert restored.source_fingerprint == session.source_fingerprint
    assert restored == session
    assert set(session.to_dict()) == _EXPECTED_KEYS


def test_session_from_dict_rejects_non_object():
    with pytest.raises(ValueError, match="object"):
        VerificationSession.from_dict(["not", "a", "dict"])


def test_session_rejects_unknown_fields():
    payload = _session().to_dict()
    payload["extra"] = True
    with pytest.raises(ValueError, match="schema"):
        VerificationSession.from_dict(payload)


def test_session_rejects_missing_fields():
    payload = _session().to_dict()
    del payload["gates"]
    with pytest.raises(ValueError, match="schema"):
        VerificationSession.from_dict(payload)


def test_session_rejects_wrong_schema_version():
    payload = _session().to_dict()
    payload["schemaVersion"] = "verification-session-v2"
    with pytest.raises(ValueError, match="schemaVersion"):
        VerificationSession.from_dict(payload)


@pytest.mark.parametrize(
    "field",
    [
        "brief_fingerprint", "worktree_fingerprint", "diff_fingerprint",
        "contract_fingerprint", "policy_fingerprint",
    ],
)
@pytest.mark.parametrize("bad", ["A" * 64, "g" * 63, "g" * 65, "z" * 64])
def test_session_rejects_invalid_sha256_fingerprints(field, bad):
    with pytest.raises(ValueError, match="[Ff]ingerprint"):
        _session(**{field: bad})


@pytest.mark.parametrize("field", ["base_sha", "head_sha"])
@pytest.mark.parametrize("bad", ["A" * 40, "b" * 39, "z" * 40])
def test_session_rejects_invalid_shas(field, bad):
    with pytest.raises(ValueError, match="[Ss]ha"):
        _session(**{field: bad})


def test_session_rejects_invalid_status():
    payload = _session().to_dict()
    payload["status"] = "not-a-status"
    with pytest.raises(ValueError, match="status"):
        VerificationSession.from_dict(payload)


def test_allowed_statuses_cover_gate_state_machine():
    assert ALLOWED_SESSION_STATUSES == {
        "created", "sealed", "review_running", "review_passed",
        "full_verification_passed", "hermes_passed", "complete",
        "blocked_runner_capability", "blocked_runner_transport",
        "review_changes_required", "context_overflow", "verification_failed",
        "hermes_failed", "stale_source", "invalid_evidence",
    }


@pytest.mark.parametrize(
    "created_at",
    ["2026-08-28", "2026-08-28T09:34:45", "2026-08-28T09:34:45+0000",
     "not-a-time", ""],
)
def test_session_rejects_invalid_rfc3339(created_at):
    with pytest.raises(ValueError, match="createdAt"):
        _session(created_at=created_at)


@pytest.mark.parametrize(
    "created_at",
    ["2026-08-28T09:34:45Z", "2026-08-28T09:34:45+08:00",
     "2026-08-28T09:34:45.123456Z", "2026-08-18T00:00:00+00:00"],
)
def test_session_accepts_valid_rfc3339(created_at):
    session = _session(created_at=created_at)
    assert session.created_at == created_at


def test_session_is_frozen():
    session = _session()
    with pytest.raises(FrozenInstanceError):
        session.status = "complete"


def test_source_fingerprint_ignores_session_bookkeeping():
    first = _session()
    second = _session(
        session_id="different-session",
        status="review_passed",
        created_at="2026-08-28T00:00:00Z",
        gates={"strictReview": {"status": "pass"}},
    )
    assert first.session_id != second.session_id
    assert first.status != second.status
    assert first.source_fingerprint == second.source_fingerprint


def test_source_fingerprint_changes_with_source_seal():
    first = _session()
    second = _session(head_sha="c" * 40)
    assert first.source_fingerprint != second.source_fingerprint


def test_session_assert_fresh_passes_when_source_unchanged():
    session = _session()
    session.assert_fresh(
        head_sha=session.head_sha,
        brief_fingerprint=session.brief_fingerprint,
        worktree_fingerprint=session.worktree_fingerprint,
        diff_fingerprint=session.diff_fingerprint,
    )


def test_session_rejects_changed_worktree():
    session = _session()
    with pytest.raises(StaleVerificationSession, match="stale"):
        session.assert_fresh(
            head_sha=session.head_sha,
            brief_fingerprint=session.brief_fingerprint,
            worktree_fingerprint="f" * 64,
            diff_fingerprint=session.diff_fingerprint,
        )


def test_assert_fresh_rejects_each_drifted_field():
    session = _session()
    for drifted in (
        {"head_sha": "c" * 40},
        {"brief_fingerprint": "1" * 64},
        {"diff_fingerprint": "2" * 64},
    ):
        current = {
            key: getattr(session, key)
            for key in ("head_sha", "brief_fingerprint", "worktree_fingerprint", "diff_fingerprint")
        }
        current.update(drifted)
        with pytest.raises(StaleVerificationSession, match="stale"):
            session.assert_fresh(**current)


def test_assert_fresh_rejects_invalid_argument_format():
    session = _session()
    with pytest.raises(ValueError, match="headSha"):
        session.assert_fresh(
            head_sha="z" * 40,
            brief_fingerprint=session.brief_fingerprint,
            worktree_fingerprint=session.worktree_fingerprint,
            diff_fingerprint=session.diff_fingerprint,
        )


def test_write_session_round_trips(tmp_path):
    session = _session()
    written = write_session(_session_path(tmp_path, session), session)
    restored = read_session(written)
    assert restored == session
    assert json.loads(written.read_text(encoding="utf-8")) == session.to_dict()


def test_written_manifest_is_canonical_json(tmp_path):
    session = _session()
    written = write_session(_session_path(tmp_path, session), session)
    assert sha256(written.read_bytes()).hexdigest() == canonical_fingerprint(session.to_dict())


def test_write_session_leaves_no_temporary_files(tmp_path):
    session = _session()
    written = write_session(_session_path(tmp_path, session), session)
    assert sorted(path.name for path in written.parent.iterdir()) == ["session.json"]


def test_write_session_overwrites_existing_session_atomically(tmp_path):
    target = _session_path(tmp_path, _session())
    write_session(target, _session(status="created"))
    write_session(target, _session(status="complete"))
    assert read_session(target).status == "complete"


def test_write_session_rejects_path_outside_sessions_root(tmp_path):
    with pytest.raises(PermissionError, match="verification_sessions"):
        write_session(tmp_path / "outside" / "session.json", _session())


def test_write_session_rejects_lexical_parent_escape(tmp_path):
    target = tmp_path / ".nbs_agent_runtime" / "verification_sessions" / ".." / ".." / "leak.json"
    with pytest.raises(PermissionError, match="verification_sessions"):
        write_session(target, _session())


def test_write_session_rejects_symlinked_parent(tmp_path):
    sessions = tmp_path / ".nbs_agent_runtime" / "verification_sessions"
    sessions.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (sessions / "leak").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PermissionError, match="symlink"):
        write_session(sessions / "leak" / "session.json", _session())


def test_write_session_rejects_symlinked_runtime_root(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / ".nbs_agent_runtime").symlink_to(outside, target_is_directory=True)
    target = project / ".nbs_agent_runtime" / "verification_sessions" / "x" / "session.json"
    with pytest.raises(PermissionError):
        write_session(target, _session())


def test_read_session_rejects_invalid_json(tmp_path):
    session = _session()
    target = _session_path(tmp_path, session)
    target.parent.mkdir(parents=True)
    target.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON"):
        read_session(target)


def test_read_session_rejects_invalid_schema(tmp_path):
    session = _session()
    target = _session_path(tmp_path, session)
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"schemaVersion": "verification-session-v1"}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        read_session(target)


def test_read_session_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_session(_session_path(tmp_path, _session()))
