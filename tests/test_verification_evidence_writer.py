import json

import pytest

from backend.agents.verification_session import VerificationSession


def _command(**overrides):
    value = {
        "label": "pytest",
        "argv": ["python", "-m", "pytest", "-q"],
        "exitCode": 0,
        "stdoutTail": "3 passed",
        "stderrTail": "",
    }
    value.update(overrides)
    return value


def test_write_and_validate_verification_v1_keeps_exact_bounded_schema(tmp_path):
    from backend.agents.verification_evidence_writer import validate_verification_v1, write_verification_v1

    output = write_verification_v1([_command()], tmp_path / "verification.json")

    assert json.loads(output.read_text(encoding="utf-8")) == {"commands": [_command()]}
    assert validate_verification_v1(output)[0]["exitCode"] == 0


def test_verification_v1_rejects_extra_top_level_fields(tmp_path):
    from backend.agents.verification_evidence_writer import VerificationEvidenceError, validate_verification_v1

    output = tmp_path / "verification.json"
    output.write_text(json.dumps({"schemaVersion": "verification-v1", "commands": [_command()]}), encoding="utf-8")

    with pytest.raises(VerificationEvidenceError, match="only commands"):
        validate_verification_v1(output)


def test_verification_v1_rejects_unbounded_output_tail(tmp_path):
    from backend.agents.verification_evidence_writer import VerificationEvidenceError, write_verification_v1

    with pytest.raises(VerificationEvidenceError, match="bounded limit"):
        write_verification_v1([_command(stdoutTail="x" * 4001)], tmp_path / "verification.json")


def test_sha256_parser_defines_raw_and_standard_shasum_contract():
    from backend.agents.verification_evidence_writer import sha256_from_output

    digest = "A" * 64
    assert sha256_from_output(digest) == digest.lower()
    assert sha256_from_output(f"{digest}  docs/brief.md") == digest.lower()
    assert sha256_from_output("not-a-digest") == ""


def test_verification_writer_preserves_required_review_provenance_commands(tmp_path):
    from backend.agents.verification_evidence_writer import validate_verification_v1, write_verification_v1

    commands = [
        _command(label="review-head-fingerprint", argv=["git", "rev-parse", "HEAD"], stdoutTail="a" * 40),
        _command(label="review-brief-fingerprint", argv=["shasum", "-a", "256", "docs/brief.md"], stdoutTail="b" * 64),
        _command(label="review-worktree-fingerprint", argv=["sh", "-c", "git status --porcelain --untracked-files=all -- . ':(exclude)docs/superpowers' ':(exclude).superpowers' | shasum -a 256"], stdoutTail="c" * 64),
    ]

    output = write_verification_v1(commands, tmp_path / "verification.json")
    validated = validate_verification_v1(output)

    assert [item["label"] for item in validated] == [item["label"] for item in commands]
    assert [item["stdoutTail"] for item in validated] == [item["stdoutTail"] for item in commands]


# ---------------------------------------------------------------------------
# Task 2: write_gate_evidence / GateEvidence (bounded, session-bound metadata)
# ---------------------------------------------------------------------------


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


def _session_dir(tmp_path, session: VerificationSession):
    return (
        tmp_path / ".nbs_agent_runtime" / "verification_sessions"
        / session.session_id
    )


_GATE_METADATA_KEYS = {
    "schemaVersion", "gate", "sessionId", "sourceFingerprint", "status",
    "commandFingerprint", "evidenceFingerprint", "startedAt", "finishedAt",
    "producer", "stdoutDigest", "stderrDigest", "reuseReason",
}


def test_gate_evidence_keeps_verification_v1_shape(tmp_path):
    from backend.agents.verification_evidence_writer import write_gate_evidence

    session = _session()
    result = write_gate_evidence(session, "full_pytest", [_command()], _session_dir(tmp_path, session))

    assert json.loads(result.verification_path.read_text(encoding="utf-8")) == {"commands": [_command()]}
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["sessionId"] == result.session_id
    assert metadata["sourceFingerprint"] == result.source_fingerprint
    assert metadata["gate"] == "full_pytest"


def test_gate_metadata_can_bind_a_canonical_runner_identity(tmp_path):
    from backend.agents.runner_identity import RunnerIdentity
    from backend.agents.verification_evidence_writer import write_gate_evidence

    identity = RunnerIdentity.from_dict({
        "schemaVersion": "runner-identity-v1", "runnerId": "review", "transport": "local_cli",
        "provider": "codex", "model": "gpt-5.4", "profile": "strict-review",
        "executionEnvironment": "local-macos",
    })
    session = _session()
    result = write_gate_evidence(
        session, "full_pytest", [_command()], _session_dir(tmp_path, session), runner_identity=identity
    )

    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert json.loads(result.verification_path.read_text(encoding="utf-8")).keys() == {"commands"}
    assert metadata["runnerIdentityFingerprint"] == identity.identity_fingerprint


def test_gate_evidence_verification_file_passes_existing_validator(tmp_path):
    from backend.agents.verification_evidence_writer import validate_verification_v1, write_gate_evidence

    session = _session()
    result = write_gate_evidence(session, "full_pytest", [_command()], _session_dir(tmp_path, session))

    validated = validate_verification_v1(result.verification_path)
    assert json.loads(result.verification_path.read_text(encoding="utf-8")) == {"commands": [_command()]}
    assert [item["label"] for item in validated] == ["pytest"]
    assert [item["exitCode"] for item in validated] == [0]


def test_gate_evidence_records_nonzero_command(tmp_path):
    from backend.agents.verification_evidence_writer import write_gate_evidence

    session = _session()
    result = write_gate_evidence(
        session, "full_pytest", [{**_command(), "exitCode": 1}], _session_dir(tmp_path, session)
    )
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["status"] == "failed"


def test_gate_evidence_metadata_records_bounded_provenance(tmp_path):
    from hashlib import sha256

    from backend.agents.verification_evidence_writer import PRODUCER_VERSION, write_gate_evidence

    session = _session()
    result = write_gate_evidence(
        session, "strict_review", [_command(stdoutTail="1 passed")], _session_dir(tmp_path, session)
    )
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))

    assert set(metadata) == _GATE_METADATA_KEYS
    assert metadata["schemaVersion"] == "verification-gate-evidence-v1"
    assert metadata["status"] == "pass"
    assert metadata["sessionId"] == session.session_id
    assert metadata["sourceFingerprint"] == session.source_fingerprint
    assert metadata["commandFingerprint"]
    assert metadata["evidenceFingerprint"]
    assert metadata["startedAt"] and metadata["finishedAt"]
    assert metadata["producer"] == PRODUCER_VERSION
    assert metadata["stdoutDigest"] == sha256(b"1 passed").hexdigest()
    assert metadata["stderrDigest"] == sha256(b"").hexdigest()
    assert metadata["reuseReason"] == "new"


def test_gate_evidence_command_fingerprint_is_deterministic(tmp_path):
    from backend.agents.verification_evidence_writer import write_gate_evidence

    session = _session()
    output_dir = _session_dir(tmp_path, session)
    first = write_gate_evidence(session, "full_pytest", [_command()], output_dir)
    second = write_gate_evidence(session, "full_pytest", [_command()], output_dir)
    assert first.command_fingerprint == second.command_fingerprint


def test_gate_evidence_fingerprint_changes_with_commands(tmp_path):
    from backend.agents.verification_evidence_writer import write_gate_evidence

    session = _session()
    output_dir = _session_dir(tmp_path, session)
    first = write_gate_evidence(session, "full_pytest", [_command(stdoutTail="1 passed")], output_dir)
    second = write_gate_evidence(session, "full_pytest", [_command(stdoutTail="2 passed")], output_dir)
    assert first.command_fingerprint != second.command_fingerprint
    assert first.evidence_fingerprint != second.evidence_fingerprint


@pytest.mark.parametrize("gate", ["pre_review", "strict_review", "full_pytest", "hermes", "completion"])
def test_gate_evidence_accepts_all_allowlisted_gates(tmp_path, gate):
    from backend.agents.verification_evidence_writer import write_gate_evidence

    session = _session()
    result = write_gate_evidence(session, gate, [_command()], _session_dir(tmp_path, session))
    assert json.loads(result.metadata_path.read_text(encoding="utf-8"))["gate"] == gate


@pytest.mark.parametrize("gate", ["", "review", "pre-review", "FULL_PYTEST", "unknown_gate"])
def test_gate_evidence_rejects_disallowed_gate(tmp_path, gate):
    from backend.agents.verification_evidence_writer import VerificationEvidenceError, write_gate_evidence

    session = _session()
    with pytest.raises(VerificationEvidenceError, match="gate"):
        write_gate_evidence(session, gate, [_command()], _session_dir(tmp_path, session))


def test_gate_evidence_rejects_unbounded_command_tail(tmp_path):
    from backend.agents.verification_evidence_writer import VerificationEvidenceError, write_gate_evidence

    session = _session()
    with pytest.raises(VerificationEvidenceError, match="bounded limit"):
        write_gate_evidence(session, "full_pytest", [_command(stdoutTail="x" * 4001)], _session_dir(tmp_path, session))


def test_gate_evidence_rejects_stale_source_fingerprint(tmp_path):
    from backend.agents.verification_evidence_writer import VerificationEvidenceError, write_gate_evidence

    session = _session()
    output_dir = _session_dir(tmp_path, session)
    write_gate_evidence(session, "full_pytest", [_command()], output_dir)

    stale = _session(session_id=session.session_id, head_sha="c" * 40)
    with pytest.raises(VerificationEvidenceError, match="source"):
        write_gate_evidence(stale, "full_pytest", [_command()], output_dir)


def test_gate_evidence_rejects_output_outside_session_directory(tmp_path):
    from backend.agents.verification_evidence_writer import write_gate_evidence

    session = _session()
    other = tmp_path / ".nbs_agent_runtime" / "verification_sessions" / "other-session"
    with pytest.raises(PermissionError, match="session"):
        write_gate_evidence(session, "full_pytest", [_command()], other)


def test_gate_evidence_rejects_output_outside_runtime_root(tmp_path):
    from backend.agents.verification_evidence_writer import write_gate_evidence

    session = _session()
    with pytest.raises(PermissionError, match="verification_sessions"):
        write_gate_evidence(session, "full_pytest", [_command()], tmp_path / "elsewhere")


def test_gate_evidence_rejects_symlinked_session_directory(tmp_path):
    from backend.agents.verification_evidence_writer import write_gate_evidence

    session = _session()
    sessions = tmp_path / ".nbs_agent_runtime" / "verification_sessions"
    sessions.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (sessions / session.session_id).symlink_to(outside, target_is_directory=True)
    with pytest.raises(PermissionError, match="symlink"):
        write_gate_evidence(session, "full_pytest", [_command()], sessions / session.session_id)


def test_gate_evidence_exposes_bound_attributes(tmp_path):
    from backend.agents.verification_evidence_writer import GateEvidence, write_gate_evidence

    session = _session()
    result = write_gate_evidence(session, "completion", [_command()], _session_dir(tmp_path, session))

    assert isinstance(result, GateEvidence)
    assert result.gate == "completion"
    assert result.session_id == session.session_id
    assert result.source_fingerprint == session.source_fingerprint
    assert result.status == "pass"
    assert result.verification_path.parent == result.metadata_path.parent
    assert result.verification_path.name == "verification.json"
    assert result.metadata_path.name == "gate.json"
    assert result.verification_path.is_file() and result.metadata_path.is_file()
