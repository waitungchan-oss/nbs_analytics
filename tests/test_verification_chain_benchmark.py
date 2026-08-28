"""Task 8 bounded verification-chain benchmark tests."""

from __future__ import annotations

from pathlib import Path

from backend.agents.verification_session import VerificationSession
from scripts.verification_chain_benchmark import benchmark_session


def _session() -> VerificationSession:
    return VerificationSession.create(
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


def _command(exit_code: int = 0) -> dict:
    return {
        "label": "pytest",
        "argv": ["python", "-m", "pytest", "-q"],
        "exitCode": exit_code,
        "stdoutTail": "pass" if exit_code == 0 else "fail",
        "stderrTail": "",
    }


def _report() -> dict:
    return {
        "schemaVersion": "review-report-v1",
        "verdict": "pass",
        "findings": [],
        "requirementCoverage": ["objective"],
        "testCoverage": ["targeted: passed"],
        "baselineRisk": "none",
        "residualRisk": [],
        "hermesRequiredChecks": [],
        "reviewFingerprint": "review-fingerprint",
    }


def _completed_chain(tmp_path: Path):
    from backend.agents.verification_chain import VerificationChain

    session = _session()
    directory = tmp_path / ".nbs_agent_runtime" / "verification_sessions" / session.session_id
    chain = VerificationChain.seal(session, runtime_root=directory)
    chain.run_pre_review([_command()])
    chain.run_strict_review(runner=lambda _: _report())
    chain.run_full_verification([_command()])
    chain.run_hermes(result={"overallStatus": "pass"}, profile="isolated-profile")
    chain.attest()
    return chain


def test_benchmark_is_bounded_and_contains_gate_metadata(tmp_path):
    chain = _completed_chain(tmp_path)
    payload = benchmark_session(
        chain.session_dir,
        reused_batch_count=2,
        runner_probe_count=3,
    )

    assert payload["schemaVersion"] == "verification-chain-benchmark-v1"
    assert payload["status"] == "complete"
    assert payload["sourceFingerprint"] == chain.session.source_fingerprint
    assert [row["gate"] for row in payload["gates"]] == [
        "pre_review", "strict_review", "full_pytest", "hermes",
    ]
    assert payload["gates"][1]["reusedBatchCount"] == 2
    assert payload["gates"][1]["runnerProbeCount"] == 3
    assert all(row["durationMs"] >= 0 for row in payload["gates"])
    assert len(payload["artifactFingerprints"]) <= 32
    serialized = str(payload)
    for forbidden in ("stdoutTail", "stderrTail", "prompt", "secret", "rows"):
        assert forbidden not in serialized


def test_benchmark_preserves_blocked_status_without_mutating_session(tmp_path):
    from backend.agents.verification_chain import VerificationChain

    session = _session()
    directory = tmp_path / ".nbs_agent_runtime" / "verification_sessions" / session.session_id
    chain = VerificationChain.seal(session, runtime_root=directory)
    chain.run_pre_review([_command()])
    chain.run_strict_review(capability="blocked_runner_capability", runner=lambda _: _report())

    before = (chain.session_dir / "session.json").read_bytes()
    payload = benchmark_session(chain.session_dir)
    after = (chain.session_dir / "session.json").read_bytes()

    assert payload["status"] == "blocked_runner_capability"
    assert payload["gates"][1]["gateStatus"] == "blocked"
    assert before == after
