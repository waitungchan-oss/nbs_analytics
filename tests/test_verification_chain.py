"""Task 5: deterministic Verification Chain controller tests.

Covers the exact gate state machine (seal -> pre_review -> strict_review ->
full_pytest -> hermes -> completion), fail-closed terminal states, source
freshness at gate boundaries, atomic terminal artifacts, batch resume after a
runner transport failure, and the deterministic completion attestation that
never calls an LLM.
"""

from __future__ import annotations

import json
import subprocess
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.agents.verification_session import (
    SOURCE_PROBE_VERSION,
    StaleVerificationSession,
    VerificationSession,
    read_session,
)


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


def _session_dir(tmp_path: Path, session: VerificationSession) -> Path:
    return (
        tmp_path / ".nbs_agent_runtime" / "verification_sessions"
        / session.session_id
    )


def _fresh_probe(session: VerificationSession):
    def probe() -> dict:
        return {
            "head_sha": session.head_sha,
            "brief_fingerprint": session.brief_fingerprint,
            "worktree_fingerprint": session.worktree_fingerprint,
            "diff_fingerprint": session.diff_fingerprint,
            "contract_fingerprint": session.contract_fingerprint,
            "policy_fingerprint": session.policy_fingerprint,
            "source_probe_version": SOURCE_PROBE_VERSION,
        }
    return probe


def _mutable_probe(session: VerificationSession):
    """Probe whose worktree fingerprint can be drifted by the test."""
    state = {"worktree_fingerprint": session.worktree_fingerprint}

    def probe() -> dict:
        return {
            "head_sha": session.head_sha,
            "brief_fingerprint": session.brief_fingerprint,
            "worktree_fingerprint": state["worktree_fingerprint"],
            "diff_fingerprint": session.diff_fingerprint,
            "contract_fingerprint": session.contract_fingerprint,
            "policy_fingerprint": session.policy_fingerprint,
            "source_probe_version": SOURCE_PROBE_VERSION,
        }
    probe.state = state
    return probe


def _command(**overrides) -> dict:
    value = {
        "label": "pytest",
        "argv": ["python", "-m", "pytest", "-q"],
        "exitCode": 0,
        "stdoutTail": "3 passed",
        "stderrTail": "",
    }
    value.update(overrides)
    return value


def _passing_review_report(**overrides) -> dict:
    report = {
        "schemaVersion": "review-report-v1",
        "verdict": "pass",
        "findings": [],
        "requirementCoverage": ["objective"],
        "testCoverage": ["targeted: passed"],
        "baselineRisk": "none",
        "residualRisk": ["Hermes pending"],
        "hermesRequiredChecks": ["phase2-baseline"],
        "reviewFingerprint": "review-fp",
    }
    report.update(overrides)
    return report


class FailingRunner:
    """Runner whose live turn fails at transport level."""

    def __call__(self, session):
        raise subprocess.TimeoutExpired(cmd=["codex"], timeout=1)


def _must_not_run():
    raise AssertionError("runner must not be invoked when capability is blocked")


@pytest.fixture
def chain(tmp_path):
    from backend.agents.verification_chain import VerificationChain

    session = _session()
    return VerificationChain.seal(
        session,
        runtime_root=_session_dir(tmp_path, session),
        source_probe=_fresh_probe(session),
    )


# ---------------------------------------------------------------------------
# seal / session binding
# ---------------------------------------------------------------------------


def test_seal_persists_session_manifest(chain):
    restored = read_session(chain.session_dir / "session.json")
    assert restored == chain.session
    assert restored.status == "sealed"


def test_seal_rejects_stale_source(tmp_path):
    from backend.agents.verification_chain import VerificationChain

    session = _session()
    probe = _mutable_probe(session)
    probe.state["worktree_fingerprint"] = "1" * 64
    with pytest.raises(StaleVerificationSession, match="stale"):
        VerificationChain.seal(
            session, runtime_root=_session_dir(tmp_path, session), source_probe=probe
        )


def test_seal_rejects_source_probe_without_policy_identity(tmp_path):
    from backend.agents.verification_chain import VerificationChain

    session = _session()

    def incomplete_probe():
        return {
            "head_sha": session.head_sha,
            "brief_fingerprint": session.brief_fingerprint,
            "worktree_fingerprint": session.worktree_fingerprint,
            "diff_fingerprint": session.diff_fingerprint,
        }

    with pytest.raises(ValueError, match="source probe"):
        VerificationChain.seal(
            session, runtime_root=_session_dir(tmp_path, session), source_probe=incomplete_probe
        )


def test_seal_rejects_runtime_outside_sessions_root(tmp_path):
    from backend.agents.verification_chain import VerificationChain

    with pytest.raises(PermissionError, match="verification_sessions"):
        VerificationChain.seal(_session(), runtime_root=tmp_path / "outside")


def test_load_reconstructs_chain_from_session_dir(tmp_path):
    from backend.agents.verification_chain import VerificationChain

    session = _session()
    chain = VerificationChain.seal(
        session, runtime_root=_session_dir(tmp_path, session), source_probe=None
    )
    loaded = VerificationChain.load(
        session.session_id,
        runtime_root=tmp_path / ".nbs_agent_runtime" / "verification_sessions",
    )
    assert loaded.session == session
    assert loaded.session_id == session.session_id
    assert loaded.session_dir == chain.session_dir


def test_gate_without_source_probe_blocks_closed(tmp_path):
    from backend.agents.verification_chain import VerificationChain

    session = _session()
    chain = VerificationChain.seal(
        session, runtime_root=_session_dir(tmp_path, session), source_probe=None
    )

    result = chain.run_pre_review([_command()])

    assert result.status == "blocked_source_probe"
    assert chain.session.status == "blocked_source_probe"
    assert not (chain.session_dir / "gates" / "pre_review" / "verification.json").exists()


def test_cli_reload_reconstructs_source_probe(tmp_path, monkeypatch):
    from backend.agents.verification_chain import VerificationChain
    from scripts import verification_chain as verification_chain_cli

    project_root = tmp_path / "project"
    (project_root / "docs" / "briefs").mkdir(parents=True)
    (project_root / "docs" / "briefs" / "task.md").write_text("brief\n", encoding="utf-8")
    session = _session(brief_path="docs/briefs/task.md")
    runtime_root = project_root / ".nbs_agent_runtime" / "verification_sessions"
    VerificationChain.seal(
        session, runtime_root=runtime_root / session.session_id, source_probe=_fresh_probe(session)
    )
    state = {"worktree_fingerprint": session.worktree_fingerprint}

    def probe(*_args, **_kwargs):
        return {
            "head_sha": session.head_sha,
            "brief_fingerprint": session.brief_fingerprint,
            "worktree_fingerprint": state["worktree_fingerprint"],
            "diff_fingerprint": session.diff_fingerprint,
            "contract_fingerprint": session.contract_fingerprint,
            "policy_fingerprint": session.policy_fingerprint,
            "source_probe_version": SOURCE_PROBE_VERSION,
        }

    monkeypatch.setattr(verification_chain_cli, "git_source_probe", probe)
    loaded = verification_chain_cli._load_chain(
        session.session_id, str(runtime_root), project_root
    )
    state["worktree_fingerprint"] = "f" * 64

    result = loaded.run_pre_review([_command()])

    assert result.status == "stale_source"


def test_cli_reload_missing_brief_blocks_source_probe(tmp_path):
    from backend.agents.verification_chain import VerificationChain
    from scripts import verification_chain as verification_chain_cli

    project_root = tmp_path / "project"
    project_root.mkdir()
    session = _session(brief_path="docs/briefs/missing.md")
    runtime_root = project_root / ".nbs_agent_runtime" / "verification_sessions"
    VerificationChain.seal(
        session, runtime_root=runtime_root / session.session_id, source_probe=_fresh_probe(session)
    )

    loaded = verification_chain_cli._load_chain(
        session.session_id, str(runtime_root), project_root
    )
    result = loaded.run_pre_review([_command()])

    assert result.status == "blocked_source_probe"
    assert "unavailable" in result.diagnostics[0]


def test_cli_reload_rejects_session_path_traversal(tmp_path):
    from scripts import verification_chain as verification_chain_cli

    project_root = tmp_path / "project"
    project_root.mkdir()
    runtime_root = project_root / ".nbs_agent_runtime" / "verification_sessions"

    with pytest.raises(ValueError, match="session .* unavailable"):
        verification_chain_cli._load_chain("../outside", str(runtime_root), project_root)


def test_cli_preflight_uses_the_session_source_fingerprint(monkeypatch, tmp_path):
    from scripts import verification_chain as verification_chain_cli

    source = "a" * 64
    fake_chain = SimpleNamespace(
        session=SimpleNamespace(source_fingerprint=source),
        check_source_freshness=lambda **_: None,
    )
    captured = {}

    monkeypatch.setattr(verification_chain_cli, "_load_chain", lambda *args: fake_chain)

    def fake_preflight(argv):
        captured["argv"] = argv
        return 0

    monkeypatch.setattr("scripts.strict_review_evidence_preflight.main", fake_preflight)
    args = SimpleNamespace(
        project_root=str(tmp_path), session="session-1", runtime_root=str(tmp_path / "runtime"),
        source_fingerprint=None, output=None, strict=False,
    )

    assert verification_chain_cli.cmd_run_preflight(args) == 0
    assert "--source-fingerprint" in captured["argv"]
    assert captured["argv"][captured["argv"].index("--source-fingerprint") + 1] == source


def test_chain_source_freshness_check_blocks_drift(tmp_path):
    from backend.agents.verification_chain import VerificationChain

    session = _session()
    probe = _mutable_probe(session)
    chain = VerificationChain.seal(
        session, runtime_root=_session_dir(tmp_path, session), source_probe=probe
    )
    assert chain.check_source_freshness(gate="pre_review") is None
    probe.state["worktree_fingerprint"] = "f" * 64
    result = chain.check_source_freshness(gate="pre_review")
    assert result is not None
    assert result.status == "stale_source"


def test_source_probe_failure_blocks_closed(tmp_path):
    from backend.agents.verification_chain import VerificationChain

    session = _session()
    chain = VerificationChain.seal(
        session, runtime_root=_session_dir(tmp_path, session), source_probe=_fresh_probe(session)
    )

    def unavailable_probe():
        raise OSError("git unavailable")

    chain = VerificationChain(
        chain.session,
        runtime_root=chain.session_dir,
        source_probe=unavailable_probe,
    )
    result = chain.run_pre_review([_command()])

    assert result.status == "blocked_source_probe"
    assert "unavailable" in result.diagnostics[0]


def test_attempt_telemetry_failure_does_not_change_gate_result(tmp_path, monkeypatch):
    from backend.agents.verification_chain import VerificationChain

    session = _session()
    chain = VerificationChain.seal(
        session, runtime_root=_session_dir(tmp_path, session), source_probe=_fresh_probe(session)
    )

    def unavailable_attempt_writer(*_args, **_kwargs):
        raise OSError("attempts artifact unavailable")

    monkeypatch.setattr(
        "backend.agents.verification_chain.record_attempt", unavailable_attempt_writer
    )

    result = chain.run_pre_review([_command()])

    assert result.status == "sealed"
    assert chain.session.gates["preReview"]["gateStatus"] == "pass"


def test_git_source_probe_tracks_dirty_file_content(tmp_path):
    from backend.agents.verification_chain import git_source_probe

    def run_git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True
        )

    run_git("init", "-q")
    run_git("config", "user.email", "tests@example.com")
    run_git("config", "user.name", "Acceptance Tests")
    brief = tmp_path / "brief.md"
    source = tmp_path / "source.txt"
    brief.write_text("brief\n", encoding="utf-8")
    source.write_text("first\n", encoding="utf-8")
    run_git("add", "brief.md", "source.txt")
    run_git("commit", "-qm", "initial")
    base_sha = run_git("rev-parse", "HEAD").stdout.strip()

    source.write_text("second\n", encoding="utf-8")
    first = git_source_probe(tmp_path, brief_path="brief.md", base_sha=base_sha)
    source.write_text("third\n", encoding="utf-8")
    second = git_source_probe(tmp_path, brief_path="brief.md", base_sha=base_sha)

    assert first["worktree_fingerprint"] != second["worktree_fingerprint"]
    assert first["source_probe_version"] == SOURCE_PROBE_VERSION


def test_git_source_probe_rejects_brief_outside_project_root(tmp_path):
    from backend.agents.verification_chain import git_source_probe

    def run_git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True
        )

    run_git("init", "-q")
    run_git("config", "user.email", "tests@example.com")
    run_git("config", "user.name", "Acceptance Tests")
    (tmp_path / "brief.md").write_text("brief\n", encoding="utf-8")
    run_git("add", "brief.md")
    run_git("commit", "-qm", "initial")
    base_sha = run_git("rev-parse", "HEAD").stdout.strip()
    outside = tmp_path.parent / "outside-brief.md"
    outside.write_text("outside\n", encoding="utf-8")

    with pytest.raises(ValueError, match="brief path"):
        git_source_probe(tmp_path, brief_path="../outside-brief.md", base_sha=base_sha)


def test_git_source_probe_handles_renamed_dirty_file(tmp_path):
    from backend.agents.verification_chain import git_source_probe

    def run_git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True
        )

    run_git("init", "-q")
    run_git("config", "user.email", "tests@example.com")
    run_git("config", "user.name", "Acceptance Tests")
    brief = tmp_path / "brief.md"
    original = tmp_path / "a"
    renamed = tmp_path / "b"
    brief.write_text("brief\n", encoding="utf-8")
    original.write_text("source\n", encoding="utf-8")
    run_git("add", "brief.md", "a")
    run_git("commit", "-qm", "initial")
    base_sha = run_git("rev-parse", "HEAD").stdout.strip()

    original.rename(renamed)
    run_git("add", "-A")

    result = git_source_probe(tmp_path, brief_path="brief.md", base_sha=base_sha)

    assert len(result["worktree_fingerprint"]) == 64


def test_git_source_probe_binds_both_sides_of_renamed_dirty_file(tmp_path):
    from backend.agents.verification_chain import git_source_probe

    def build_case(case_name, original_name):
        project = tmp_path / case_name
        project.mkdir()

        def run_git(*args):
            return subprocess.run(
                ["git", *args], cwd=project, check=True, capture_output=True, text=True
            )

        run_git("init", "-q")
        run_git("config", "user.email", "tests@example.com")
        run_git("config", "user.name", "Acceptance Tests")
        (project / "brief.md").write_text("brief\n", encoding="utf-8")
        (project / original_name).write_text("source\n", encoding="utf-8")
        run_git("add", "brief.md", original_name)
        run_git("commit", "-qm", "initial")
        base_sha = run_git("rev-parse", "HEAD").stdout.strip()
        (project / original_name).rename(project / "renamed.txt")
        run_git("add", "-A")
        return git_source_probe(project, brief_path="brief.md", base_sha=base_sha)

    first = build_case("case-a", "original-a.txt")
    second = build_case("case-b", "original-b.txt")

    assert first["worktree_fingerprint"] != second["worktree_fingerprint"]


def test_full_gate_checks_source_before_runner(tmp_path):
    from backend.agents.verification_chain import VerificationChain

    session = _session()
    probe = _mutable_probe(session)
    chain = VerificationChain.seal(
        session, runtime_root=_session_dir(tmp_path, session), source_probe=probe
    )
    chain.run_pre_review([_command()])
    chain.run_strict_review(runner=lambda current: _passing_review_report())
    probe.state["worktree_fingerprint"] = "f" * 64
    called = []

    def runner():
        called.append(True)
        return [_command()]

    result = chain.run_full_verification(runner=runner)

    assert result.status == "stale_source"
    assert called == []


def test_full_verification_transport_failure_is_resumable(tmp_path):
    from backend.agents.verification_chain import VerificationChain
    from backend.agents.runner_identity import RunnerIdentity

    session = _session()
    identity = RunnerIdentity.from_legacy_local_cli(
        runner_id="full-runner", provider="local", model="pytest",
        profile="full", execution_environment="local",
    )
    chain = VerificationChain.seal(
        session, runtime_root=_session_dir(tmp_path, session), source_probe=_fresh_probe(session),
        runner_identity=identity,
    )
    chain.run_pre_review([_command()])
    chain.run_strict_review(runner=lambda current: _passing_review_report())

    def runner():
        raise subprocess.TimeoutExpired(cmd=["pytest"], timeout=1)

    result = chain.run_full_verification(runner=runner)

    assert result.status == "blocked_runner_transport"
    assert chain.session.status == "blocked_runner_transport"
    chain.bind_runner_identity(identity)
    resumed = chain.run_full_verification([_command()])
    assert resumed.status == "full_verification_passed"


def test_hermes_transport_failure_is_resumable(tmp_path):
    from backend.agents.verification_chain import VerificationChain
    from backend.agents.runner_identity import RunnerIdentity

    session = _session()
    identity = RunnerIdentity.from_legacy_local_cli(
        runner_id="hermes-runner", provider="local", model="hermes",
        profile="isolated", execution_environment="local",
    )
    chain = VerificationChain.seal(
        session, runtime_root=_session_dir(tmp_path, session), source_probe=_fresh_probe(session),
        runner_identity=identity,
    )
    chain.run_pre_review([_command()])
    chain.run_strict_review(runner=lambda current: _passing_review_report())
    chain.run_full_verification([_command()])

    def runner(current):
        raise OSError("hermes unavailable")

    result = chain.run_hermes(runner=runner, profile="isolated-profile")

    assert result.status == "blocked_runner_transport"
    assert chain.session.status == "blocked_runner_transport"
    chain.bind_runner_identity(identity)
    resumed = chain.run_hermes(result={"overallStatus": "pass"}, profile="isolated-profile")
    assert resumed.status == "hermes_passed"


# ---------------------------------------------------------------------------
# gate ordering (monotonic, fail-closed)
# ---------------------------------------------------------------------------


def test_full_verification_cannot_run_before_review_pass(chain):
    from backend.agents.verification_chain import InvalidGateTransition

    with pytest.raises(InvalidGateTransition):
        chain.run_full_verification()


def test_hermes_cannot_run_before_full_verification_pass(chain):
    from backend.agents.verification_chain import InvalidGateTransition

    chain.run_pre_review([_command()])
    chain.run_strict_review(runner=lambda session: _passing_review_report())
    with pytest.raises(InvalidGateTransition):
        chain.run_hermes(result={"overallStatus": "pass"})


def test_strict_review_requires_passing_pre_review_gate(chain):
    from backend.agents.verification_chain import InvalidGateTransition

    with pytest.raises(InvalidGateTransition, match="pre_review"):
        chain.run_strict_review(runner=lambda session: _passing_review_report())


def test_completion_requires_review_full_and_hermes_pass(chain):
    result = chain.attest()
    assert result.status == "blocked"
    assert "strictReview" in result.diagnostics[0]


def test_attest_blocked_keeps_session_terminal_status(tmp_path):
    from backend.agents.verification_chain import VerificationChain

    session = _session()
    chain = VerificationChain.seal(
        session, runtime_root=_session_dir(tmp_path, session), source_probe=_fresh_probe(session)
    )
    chain.run_pre_review([_command()])
    chain.run_strict_review(runner=lambda s: _passing_review_report())
    chain.run_full_verification([_command(exitCode=1)])

    attestation = chain.attest()
    assert attestation.status == "blocked"
    assert any("fullPytest" in item for item in attestation.diagnostics)
    assert chain.session.status == "verification_failed"


# ---------------------------------------------------------------------------
# terminal states
# ---------------------------------------------------------------------------


def test_runner_failure_creates_new_transport_terminal_state(chain):
    chain.run_pre_review([_command()])
    result = chain.run_strict_review(runner=FailingRunner())
    assert result.status == "blocked_runner_transport"
    assert result.session_id == chain.session_id
    assert not chain.current_result_is_previous_session()

    terminal = json.loads((chain.session_dir / "terminal.json").read_text(encoding="utf-8"))
    assert terminal["sessionId"] == chain.session_id
    assert terminal["status"] == "blocked_runner_transport"
    assert terminal["gate"] == "strict_review"


def test_capability_blocked_fails_closed_without_running(chain):
    chain.run_pre_review([_command()])
    result = chain.run_strict_review(
        runner=lambda session: _must_not_run(),
        capability="blocked_runner_capability",
    )
    assert result.status == "blocked_runner_capability"
    assert chain.session.status == "blocked_runner_capability"
    assert result.gate_status == "blocked"


def test_capability_transport_fails_closed(chain):
    chain.run_pre_review([_command()])
    result = chain.run_strict_review(
        runner=lambda session: _must_not_run(),
        capability="blocked_runner_transport",
    )
    assert result.status == "blocked_runner_transport"


def test_static_ready_capability_is_not_turn_ready(chain):
    chain.run_pre_review([_command()])
    result = chain.run_strict_review(
        runner=lambda session: _must_not_run(),
        capability="static_ready",
    )
    assert result.status == "blocked_runner_capability"


def test_review_changes_required_is_terminal(chain):
    chain.run_pre_review([_command()])
    report = _passing_review_report(verdict="changes_required")
    result = chain.run_strict_review(runner=lambda session: report)
    assert result.status == "review_changes_required"
    assert chain.session.status == "review_changes_required"
    # review_passed must not have been published
    assert chain.session.gates["strictReview"]["gateStatus"] == "failed"


def test_review_context_overflow_is_terminal(chain):
    chain.run_pre_review([_command()])
    report = _passing_review_report(verdict="context_overflow")
    result = chain.run_strict_review(runner=lambda session: report)
    assert result.status == "context_overflow"


def test_invalid_review_report_is_invalid_evidence(chain):
    chain.run_pre_review([_command()])
    result = chain.run_strict_review(runner=lambda session: {"not": "a report"})
    assert result.status == "invalid_evidence"
    assert chain.session.status == "invalid_evidence"


def test_pre_review_failure_is_terminal(chain):
    result = chain.run_pre_review([_command(exitCode=1)])
    assert result.status == "verification_failed"
    assert chain.session.status == "verification_failed"
    assert result.gate_status == "failed"


def test_full_verification_failure_is_terminal(tmp_path):
    from backend.agents.verification_chain import VerificationChain

    session = _session()
    chain = VerificationChain.seal(
        session, runtime_root=_session_dir(tmp_path, session), source_probe=_fresh_probe(session)
    )
    chain.run_pre_review([_command()])
    chain.run_strict_review(runner=lambda s: _passing_review_report())

    result = chain.run_full_verification([_command(exitCode=1)])
    assert result.status == "verification_failed"
    assert chain.session.status == "verification_failed"
    # earlier strict review PASS is preserved but never rewritten into complete
    assert chain.session.gates["strictReview"]["gateStatus"] == "pass"


def test_hermes_failure_is_terminal_and_resumes_with_explicit_profile(tmp_path):
    from backend.agents.verification_chain import InvalidGateTransition, VerificationChain

    session = _session()
    chain = VerificationChain.seal(
        session, runtime_root=_session_dir(tmp_path, session), source_probe=_fresh_probe(session)
    )
    chain.run_pre_review([_command()])
    chain.run_strict_review(runner=lambda s: _passing_review_report())
    chain.run_full_verification([_command()])

    result = chain.run_hermes(result={"overallStatus": "fail"}, profile="isolated-profile")
    assert result.status == "hermes_failed"
    assert chain.session.status == "hermes_failed"
    # resume without an explicit profile is rejected
    with pytest.raises(InvalidGateTransition, match="profile"):
        chain.run_hermes(result={"overallStatus": "pass"})
    resumed = chain.run_hermes(result={"overallStatus": "pass"}, profile="isolated-profile")
    assert resumed.status == "hermes_passed"


def test_hermes_rejects_evidence_from_another_session(tmp_path):
    from backend.agents.verification_chain import VerificationChain

    session = _session()
    chain = VerificationChain.seal(
        session, runtime_root=_session_dir(tmp_path, session), source_probe=_fresh_probe(session)
    )
    chain.run_pre_review([_command()])
    chain.run_strict_review(runner=lambda s: _passing_review_report())
    chain.run_full_verification([_command()])

    result = chain.run_hermes(
        result={"overallStatus": "pass", "sessionId": "other-session"},
        profile="primary-runtime",
    )
    assert result.status == "hermes_failed"


def test_stale_source_between_gates_writes_stale_terminal(tmp_path):
    from backend.agents.verification_chain import VerificationChain

    session = _session()
    probe = _mutable_probe(session)
    chain = VerificationChain.seal(
        session, runtime_root=_session_dir(tmp_path, session), source_probe=probe
    )
    chain.run_pre_review([_command()])

    probe.state["worktree_fingerprint"] = "f" * 64
    result = chain.run_strict_review(runner=lambda s: _passing_review_report())
    assert result.status == "stale_source"
    assert result.gate_status == "stale"
    assert chain.session.status == "stale_source"
    assert "stale" in result.diagnostics[0]


# ---------------------------------------------------------------------------
# resume and isolation
# ---------------------------------------------------------------------------


def test_strict_review_resumes_after_transport_failure(tmp_path):
    from backend.agents.verification_chain import VerificationChain
    from backend.agents.runner_identity import RunnerIdentity

    session = _session()
    identity = RunnerIdentity.from_legacy_local_cli(
        runner_id="review-runner", provider="codex", model="gpt-5.6-luna",
        profile="standard", execution_environment="local",
    )
    chain = VerificationChain.seal(
        session, runtime_root=_session_dir(tmp_path, session), source_probe=_fresh_probe(session),
        runner_identity=identity,
    )
    chain.run_pre_review([_command()])

    first = chain.run_strict_review(runner=FailingRunner())
    assert first.status == "blocked_runner_transport"

    chain.bind_runner_identity(identity)
    second = chain.run_strict_review(runner=lambda s: _passing_review_report())
    assert second.status == "review_passed"
    assert chain.session.status == "review_passed"


def test_transport_resume_budget_is_bounded_per_gate(tmp_path):
    from backend.agents.verification_chain import VerificationChain
    from backend.agents.runner_identity import RunnerIdentity

    session = _session()
    identity = RunnerIdentity.from_legacy_local_cli(
        runner_id="review-runner", provider="codex", model="gpt-5.6-luna",
        profile="standard", execution_environment="local",
    )
    chain = VerificationChain.seal(
        session, runtime_root=_session_dir(tmp_path, session), source_probe=_fresh_probe(session),
        runner_identity=identity,
    )
    chain.run_pre_review([_command()])
    assert chain.run_strict_review(runner=FailingRunner()).status == "blocked_runner_transport"
    assert chain.run_strict_review(runner=FailingRunner()).status == "blocked_runner_transport"
    called = []
    result = chain.run_strict_review(runner=lambda current: called.append(True))

    assert result.status == "blocked_runner_transport"
    assert "retry" in result.diagnostics[0].lower()
    assert called == []


def test_transport_resume_rejects_missing_runner_identity(tmp_path):
    from backend.agents.runner_identity import RunnerIdentity
    from backend.agents.verification_chain import InvalidGateTransition, VerificationChain

    session = _session()
    chain = VerificationChain.seal(
        session, runtime_root=_session_dir(tmp_path, session), source_probe=_fresh_probe(session)
    )
    chain.run_pre_review([_command()])
    assert chain.run_strict_review(runner=FailingRunner()).status == "blocked_runner_transport"
    identity = RunnerIdentity.from_legacy_local_cli(
        runner_id="review-runner", provider="codex", model="gpt-5.6-luna",
        profile="standard", execution_environment="local",
    )

    with pytest.raises(InvalidGateTransition, match="missing valid runner identity"):
        chain.bind_runner_identity(identity)


def test_strict_review_transport_resume_requires_the_strict_review_block(tmp_path):
    from dataclasses import replace
    from backend.agents.verification_chain import InvalidGateTransition, VerificationChain

    session = _session()
    chain = VerificationChain.seal(
        session, runtime_root=_session_dir(tmp_path, session), source_probe=_fresh_probe(session)
    )
    chain.run_pre_review([_command()])
    chain.run_strict_review(runner=FailingRunner())
    chain._session = replace(
        chain.session,
        gates={**chain.session.gates, "strictReview": {"gateStatus": "pass"}},
    )

    with pytest.raises(InvalidGateTransition, match="blocked strictReview"):
        chain.run_strict_review(runner=lambda current: _passing_review_report())


def test_loaded_transport_resume_requires_the_same_runner_identity(tmp_path):
    from backend.agents.runner_identity import RunnerIdentity
    from backend.agents.verification_chain import InvalidGateTransition, VerificationChain

    identity = RunnerIdentity.from_legacy_local_cli(
        runner_id="review-runner",
        provider="codex",
        model="gpt-5.6-luna",
        profile="standard",
        execution_environment="local",
    )
    session = _session()
    runtime_root = tmp_path / ".nbs_agent_runtime" / "verification_sessions"
    chain = VerificationChain.seal(
        session,
        runtime_root=_session_dir(tmp_path, session),
        source_probe=_fresh_probe(session),
        runner_identity=identity,
    )
    chain.run_pre_review([_command()])
    assert chain.run_strict_review(runner=FailingRunner()).status == "blocked_runner_transport"
    assert (
        chain.session.gates["strictReview"]["runnerFingerprint"]
        == identity.identity_fingerprint
    )

    loaded = VerificationChain.load(
        session.session_id, runtime_root=runtime_root, source_probe=_fresh_probe(session)
    )
    mismatch = RunnerIdentity.from_legacy_local_cli(
        runner_id="review-runner",
        provider="codex",
        model="gpt-5.6-sol",
        profile="standard",
        execution_environment="local",
    )
    with pytest.raises(InvalidGateTransition, match="runner identity"):
        loaded.bind_runner_identity(mismatch)

    loaded.bind_runner_identity(identity)
    result = loaded.run_strict_review(runner=lambda current: _passing_review_report())
    assert result.status == "review_passed"


def test_strict_review_rechecks_source_after_runner_returns(tmp_path):
    from backend.agents.verification_chain import VerificationChain

    session = _session()
    probe = _mutable_probe(session)
    chain = VerificationChain.seal(
        session, runtime_root=_session_dir(tmp_path, session), source_probe=probe
    )
    chain.run_pre_review([_command()])

    def runner(_session):
        probe.state["worktree_fingerprint"] = "f" * 64
        return _passing_review_report()

    result = chain.run_strict_review(runner=runner)

    assert result.status == "stale_source"
    assert not (chain.session_dir / "review-report.json").exists()


def test_current_result_detects_previous_session_terminal(chain):
    assert not chain.current_result_is_previous_session()
    terminal = chain.session_dir / "terminal.json"
    terminal.write_text(
        json.dumps({"sessionId": "other-session", "status": "context_overflow"}),
        encoding="utf-8",
    )
    assert chain.current_result_is_previous_session()


# ---------------------------------------------------------------------------
# full chain happy path and artifacts
# ---------------------------------------------------------------------------


def test_full_chain_reaches_complete(tmp_path):
    from backend.agents.verification_chain import VerificationChain

    session = _session()
    chain = VerificationChain.seal(
        session, runtime_root=_session_dir(tmp_path, session), source_probe=_fresh_probe(session)
    )

    pre = chain.run_pre_review([_command()])
    assert pre.status == "sealed"
    assert chain.session.gates["preReview"]["gateStatus"] == "pass"

    review = chain.run_strict_review(runner=lambda s: _passing_review_report())
    assert review.status == "review_passed"
    assert review.gate_status == "pass"

    full = chain.run_full_verification([_command()])
    assert full.status == "full_verification_passed"

    hermes = chain.run_hermes(result={"overallStatus": "pass"}, profile="primary-runtime")
    assert hermes.status == "hermes_passed"
    assert chain.session.gates["hermes"]["profile"] == "primary-runtime"

    attestation = chain.attest()
    assert attestation.status == "complete"
    assert chain.session.status == "complete"
    assert not chain.current_result_is_previous_session()


def test_completion_attestation_schema_and_fingerprints(tmp_path):
    from backend.agents.verification_chain import VerificationChain

    session = _session()
    chain = VerificationChain.seal(
        session, runtime_root=_session_dir(tmp_path, session), source_probe=_fresh_probe(session)
    )
    chain.run_pre_review([_command()])
    chain.run_strict_review(runner=lambda s: _passing_review_report())
    chain.run_full_verification([_command()])
    chain.run_hermes(result={"overallStatus": "pass"}, profile="isolated-profile")

    attestation = chain.attest()
    payload = attestation.to_dict()
    assert set(payload) == {
        "schemaVersion", "sessionId", "status", "requiredGates", "sourceFingerprint",
        "artifactFingerprints", "diagnostics", "generatedAt",
    }
    assert payload["schemaVersion"] == "completion-attestation-v1"
    assert payload["sessionId"] == session.session_id
    assert payload["requiredGates"] == {
        "strictReview": "pass", "fullPytest": "pass", "hermes": "pass",
    }
    assert payload["sourceFingerprint"] == session.source_fingerprint
    assert payload["diagnostics"] == []

    completion = json.loads((chain.session_dir / "completion.json").read_text(encoding="utf-8"))
    assert completion["schemaVersion"] == "completion-attestation-v1"
    assert completion["artifactFingerprints"]["session.json"] == sha256(
        (chain.session_dir / "session.json").read_bytes()
    ).hexdigest()
    for gate in ("strictReview", "fullPytest", "hermes"):
        assert completion["artifactFingerprints"][gate] == chain.session.gates[gate]["evidenceFingerprint"]


def test_gate_evidence_and_reports_stay_below_session_dir(tmp_path):
    from backend.agents.verification_chain import VerificationChain

    session = _session()
    chain = VerificationChain.seal(
        session,
        runtime_root=_session_dir(tmp_path, session),
        source_probe=_fresh_probe(session),
    )
    chain.run_pre_review([_command()])
    chain.run_strict_review(runner=lambda s: _passing_review_report())
    chain.run_full_verification([_command()])
    chain.run_hermes(result={"overallStatus": "pass"}, profile="primary-runtime")
    chain.attest()

    gates_dir = chain.session_dir / "gates"
    for gate in ("pre_review", "strict_review", "full_pytest", "hermes"):
        assert (gates_dir / gate / "gate.json").is_file()
        assert (gates_dir / gate / "verification.json").is_file()
    assert (chain.session_dir / "review-report.json").is_file()
    assert (chain.session_dir / "hermes-result.json").is_file()
    assert (chain.session_dir / "completion.json").is_file()
    # verification-v1 stays exactly {"commands": [...]}
    full_verification = json.loads(
        (gates_dir / "full_pytest" / "verification.json").read_text(encoding="utf-8")
    )
    assert full_verification == {"commands": [_command()]}
    # session.json lives beside the gates
    assert json.loads((chain.session_dir / "session.json").read_text(encoding="utf-8"))["status"] == "complete"


def test_gate_result_schema_is_exact(chain):
    result = chain.run_pre_review([_command()])
    payload = result.to_dict()
    assert set(payload) == {
        "schemaVersion", "sessionId", "gate", "status", "gateStatus",
        "sourceFingerprint", "evidenceFingerprint", "startedAt", "finishedAt",
        "diagnostics", "recovery",
    }
    assert payload["schemaVersion"] == "verification-gate-result-v1"
    assert payload["gate"] == "pre_review"
    assert payload["gateStatus"] == "pass"
    assert payload["sourceFingerprint"] == chain.session.source_fingerprint


def test_pre_review_is_repeatable_without_status_rewind(chain):
    first = chain.run_pre_review([_command()])
    second = chain.run_pre_review([_command()])
    assert first.status == "sealed"
    assert second.status == "sealed"
    assert chain.session.status == "sealed"


def test_pre_review_transport_failure_is_resumable(tmp_path):
    from backend.agents.verification_chain import VerificationChain

    session = _session()
    chain = VerificationChain.seal(
        session, runtime_root=_session_dir(tmp_path, session), source_probe=_fresh_probe(session)
    )
    def failing_runner():
        raise subprocess.TimeoutExpired(cmd=["pytest"], timeout=1)

    first = chain.run_pre_review(runner=failing_runner)
    assert first.status == "blocked_runner_transport"
    second = chain.run_pre_review([_command()])
    assert second.status == "sealed"
    assert chain.session.gates["preReview"]["gateStatus"] == "pass"
