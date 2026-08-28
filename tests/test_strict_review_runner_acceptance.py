import json
import subprocess
import sys
from pathlib import Path

from backend.agents.evidence_models import EvidenceBundle, EvidenceItem

ROOT = Path(__file__).resolve().parents[1]


def test_invalid_cache_is_blocked_without_writing_runtime(tmp_path):
    from backend.agents.review_runner_profile import RunnerProfile, preflight_runner

    executable = tmp_path / "codex"
    executable.write_text("#!/bin/sh\nprintf 'codex-cli 0.142.5\\n'\n", encoding="utf-8")
    executable.chmod(0o755)
    cache = tmp_path / "models_cache.json"
    cache.write_text(json.dumps({"models": [{"slug": "gpt-5.4"}]}), encoding="utf-8")

    result = preflight_runner(RunnerProfile(str(executable), "gpt-5.4", cache))

    assert result.status == "blocked_runtime"
    assert not (tmp_path / ".nbs_agent_runtime").exists()


def test_timeout_is_blocked_and_does_not_change_project_files(tmp_path):
    script = tmp_path / "slow.py"
    script.write_text("import time; time.sleep(2)", encoding="utf-8")
    before = script.read_bytes()
    completed = subprocess.run(
        [sys.executable, "-c", "import subprocess,sys;\ntry: subprocess.run([sys.executable, sys.argv[1]], timeout=0.1, check=True)\nexcept subprocess.TimeoutExpired: print('blocked_runtime')", str(script)],
        capture_output=True, text=True, check=True,
    )

    assert completed.stdout.strip() == "blocked_runtime"
    assert script.read_bytes() == before


def test_actual_review_timeout_is_blocked_without_runtime_write(tmp_path):
    from backend.agents.review_agent_service import build_review_report

    class TimeoutRunner:
        def run(self, payload):
            raise subprocess.TimeoutExpired(cmd=["codex"], timeout=1)

    bundle = EvidenceBundle(
        schema_version="review-evidence-v1",
        task={"id": "strict", "objective": "runner recovery", "scope": ["scripts/review_agent.py"], "forbidden": []},
        repository={"baseSha": "a" * 40, "headRef": "WORKTREE", "dirtyFiles": ["scripts/review_agent.py"]},
        guardrails={"mayBaseline": "HKD 12,057,968"},
        evidence=(EvidenceItem(kind="diff", source="scripts/review_agent.py", content="+change"),),
    )
    context = {
        "schemaVersion": "context-summary-v1", "status": "ready",
        "taskUnderstanding": ["runner recovery"], "systemBoundaries": ["read-only"],
        "relevantFiles": [], "dependencies": [], "recommendedTests": ["pytest"],
        "risks": [], "unknowns": [], "contextFingerprint": "context",
    }
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    report = build_review_report(
        bundle, project_root=tmp_path, context_summary=context,
        verification=[{"label": "pytest", "argv": ["pytest"], "exitCode": 0, "stdoutTail": "passed", "stderrTail": ""}],
        runner=TimeoutRunner(), runtime_root=tmp_path / ".nbs_agent_runtime",
        instructions="review-contract-v1", strict=True,
    )

    after = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert report["verdict"] == "blocked"
    assert "timeout" in report["residualRisk"][0].lower()
    assert after == before
    assert not list((tmp_path / ".nbs_agent_runtime" / "reports").glob("*.json"))


# ---------------------------------------------------------------------------
# Task 6: session verification chain CLI acceptance
# ---------------------------------------------------------------------------


def _sessions_root(tmp_path):
    return tmp_path / ".nbs_agent_runtime" / "verification_sessions"


def _write_session(tmp_path, *, session_id, status, gates=None):
    """Create one session manifest below <tmp>/.nbs_agent_runtime/verification_sessions/."""
    from backend.agents.verification_session import VerificationSession, write_session

    session = VerificationSession.create(
        project_id="nbs_analytics",
        base_sha="a" * 40,
        head_sha="b" * 40,
        brief_path="docs/briefs/task.md",
        brief_fingerprint="c" * 64,
        worktree_fingerprint="d" * 64,
        diff_fingerprint="e" * 64,
        contract_fingerprint="f" * 64,
        policy_fingerprint="0" * 64,
        session_id=session_id,
        status=status,
        gates=gates,
    )
    path = _sessions_root(tmp_path) / session_id / "session.json"
    write_session(path, session)
    return session


def _run_cli(tmp_path, *args):
    """Invoke scripts/verification_chain.py main() and return (exit_code, parsed stdout)."""
    import contextlib
    import io

    from scripts import verification_chain as vc

    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        exit_code = vc.main([*args, "--runtime-root", str(_sessions_root(tmp_path))])
    text = stdout.getvalue()
    try:
        payload = json.loads(text) if text.strip() else None
    except json.JSONDecodeError:
        payload = text
    return exit_code, payload


def _marker_script(tmp_path, marker):
    script = tmp_path / "write_marker.py"
    script.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
        encoding="utf-8",
    )
    return script


def _fake_hermes_script(tmp_path, *, profile_override=None):
    """Realistic read-only fake: binds to the session manifest like the real script."""
    fake = tmp_path / "fake_hermes.py"
    profile_line = (
        f"profile = {profile_override!r}"
        if profile_override is not None
        else "profile = val('--profile')"
    )
    fake.write_text(
        "import json, sys\n"
        "sys.path.insert(0, %r)\n"
        "from scripts.hermes_post_change_check import session_evidence_binding\n"
        "args = sys.argv[1:]\n"
        "def val(flag):\n"
        "    return args[args.index(flag) + 1] if flag in args else None\n"
        "binding = session_evidence_binding(val('--session-root'), val('--session'))\n"
        f"{profile_line}\n"
        "binding['profile'] = profile\n"
        "print(json.dumps({'overallStatus': 'pass', **binding}))\n"
        % (str(ROOT),),
        encoding="utf-8",
    )
    return fake


def test_cli_status_does_not_select_old_report(tmp_path):
    old = _write_session(tmp_path, session_id="old", status="context_overflow")
    new = _write_session(tmp_path, session_id="new", status="blocked_runner_transport")

    code, result = _run_cli(tmp_path, "status", "--session", new.session_id)

    assert code == 0
    assert result["sessionId"] == "new"
    assert result["status"] == "blocked_runner_transport"
    assert result["gates"] == {}


def test_cli_status_is_scoped_to_the_requested_session(tmp_path):
    old = _write_session(tmp_path, session_id="old", status="context_overflow")
    new = _write_session(tmp_path, session_id="new", status="sealed")

    code, result = _run_cli(tmp_path, "status", "--session", old.session_id)

    assert code == 0
    assert result["sessionId"] == "old"
    assert result["status"] == "context_overflow"


def test_cli_status_requires_explicit_session(tmp_path):
    code, result = _run_cli(tmp_path, "status")

    assert code == 5
    assert "session" in str(result).lower()


def test_cli_status_missing_session_is_invalid_runtime(tmp_path):
    code, result = _run_cli(tmp_path, "status", "--session", "missing-session")

    assert code == 5
    assert result["status"] == "not_found"


def test_cli_seal_uses_session_subdirectory_under_runtime_root(tmp_path):
    code, result = _run_cli(
        tmp_path,
        "seal",
        "--brief",
        "docs/briefs/2026-08-28-strict-review-runner-runtime-recovery-brief.md",
        "--base",
        "HEAD",
    )

    assert code == 0
    assert result["status"] == "sealed"
    assert (
        _sessions_root(tmp_path) / result["sessionId"] / "session.json"
    ).is_file()


def test_cli_run_full_refuses_before_review_pass(tmp_path):
    session = _write_session(tmp_path, session_id="s1", status="sealed")
    marker = tmp_path / "marker"

    code, result = _run_cli(
        tmp_path, "run-full", "--session", session.session_id,
        "--full-command", f"{sys.executable} {_marker_script(tmp_path, marker)}",
    )

    assert not marker.exists()
    assert code == 5
    assert "review_passed" in str(result)


def test_cli_run_full_runs_after_review_pass(tmp_path):
    session = _write_session(tmp_path, session_id="s1", status="review_passed")

    code, result = _run_cli(
        tmp_path, "run-full", "--session", session.session_id,
        "--full-command", f"{sys.executable} -c 'print(\"ok\")'",
    )

    assert code == 0
    assert result["status"] == "full_verification_passed"
    gate = json.loads(
        (_sessions_root(tmp_path) / session.session_id / "gates" / "full_pytest" / "gate.json")
        .read_text(encoding="utf-8")
    )
    assert gate["sessionId"] == session.session_id
    assert gate["status"] == "pass"
    assert gate["sourceFingerprint"] == session.source_fingerprint


def test_cli_run_full_failure_is_verification_failed(tmp_path):
    session = _write_session(tmp_path, session_id="s1", status="review_passed")

    code, result = _run_cli(
        tmp_path, "run-full", "--session", session.session_id,
        "--full-command", f"{sys.executable} -c 'import sys; sys.exit(1)'",
    )

    assert code == 1
    assert result["status"] == "verification_failed"


def test_cli_run_hermes_refuses_before_full_verification_pass(tmp_path):
    session = _write_session(tmp_path, session_id="s1", status="review_passed")
    marker = tmp_path / "hermes-marker"

    code, result = _run_cli(
        tmp_path, "run-hermes", "--session", session.session_id,
        "--profile", "primary-runtime",
        "--hermes-command", f"{sys.executable} {_marker_script(tmp_path, marker)}",
    )

    assert not marker.exists()
    assert code == 5
    assert "full_verification_passed" in str(result)


def test_cli_run_hermes_requires_explicit_profile(tmp_path):
    session = _write_session(tmp_path, session_id="s1", status="full_verification_passed")

    code, result = _run_cli(tmp_path, "run-hermes", "--session", session.session_id)

    assert code == 5
    assert "profile" in str(result).lower()


def test_cli_run_hermes_binds_session_evidence_and_profile(tmp_path):
    session = _write_session(tmp_path, session_id="s1", status="full_verification_passed")
    fake = _fake_hermes_script(tmp_path)

    code, result = _run_cli(
        tmp_path, "run-hermes", "--session", session.session_id,
        "--profile", "primary-runtime",
        "--hermes-command", f"{sys.executable} {fake}",
    )

    assert code == 0
    assert result["status"] == "hermes_passed"
    hermes_result = json.loads(
        (_sessions_root(tmp_path) / session.session_id / "hermes-result.json")
        .read_text(encoding="utf-8")
    )
    assert hermes_result["sessionId"] == session.session_id
    assert hermes_result["sourceFingerprint"] == session.source_fingerprint
    assert hermes_result["profile"] == "primary-runtime"
    status = json.loads(
        (_sessions_root(tmp_path) / session.session_id / "session.json")
        .read_text(encoding="utf-8")
    )
    assert status["gates"]["hermes"]["profile"] == "primary-runtime"
    assert status["gates"]["hermes"]["gateStatus"] == "pass"


def test_cli_run_hermes_rejects_mixed_profile_evidence(tmp_path):
    session = _write_session(tmp_path, session_id="s1", status="full_verification_passed")
    fake = _fake_hermes_script(tmp_path, profile_override="isolated-profile")

    code, result = _run_cli(
        tmp_path, "run-hermes", "--session", session.session_id,
        "--profile", "primary-runtime",
        "--hermes-command", f"{sys.executable} {fake}",
    )

    assert code == 1
    assert result["status"] == "hermes_failed"
    assert "profile" in str(result).lower()


def test_cli_attest_complete_after_all_gates(tmp_path):
    seeded_gates = {
        "strictReview": {
            "gateStatus": "pass", "evidenceFingerprint": "a" * 64,
            "sourceFingerprint": "b" * 64,
        }
    }
    session = _write_session(
        tmp_path, session_id="s1", status="review_passed", gates=seeded_gates
    )

    code, full = _run_cli(
        tmp_path, "run-full", "--session", session.session_id,
        "--full-command", f"{sys.executable} -c 'print(\"ok\")'",
    )
    assert code == 0
    assert full["status"] == "full_verification_passed"

    fake = _fake_hermes_script(tmp_path)
    code, hermes = _run_cli(
        tmp_path, "run-hermes", "--session", session.session_id,
        "--profile", "primary-runtime",
        "--hermes-command", f"{sys.executable} {fake}",
    )
    assert code == 0
    assert hermes["status"] == "hermes_passed"

    code, attestation = _run_cli(tmp_path, "attest", "--session", session.session_id)

    assert code == 0
    assert attestation["schemaVersion"] == "completion-attestation-v1"
    assert attestation["status"] == "complete"
    assert attestation["requiredGates"] == {
        "strictReview": "pass", "fullPytest": "pass", "hermes": "pass",
    }


def test_cli_attest_blocked_when_hermes_gate_missing(tmp_path):
    seeded_gates = {
        "strictReview": {"gateStatus": "pass", "evidenceFingerprint": "a" * 64},
        "fullPytest": {"gateStatus": "pass", "evidenceFingerprint": "b" * 64},
    }
    session = _write_session(
        tmp_path, session_id="s1", status="full_verification_passed", gates=seeded_gates
    )

    code, attestation = _run_cli(tmp_path, "attest", "--session", session.session_id)

    assert code == 1
    assert attestation["status"] == "blocked"
    assert any("hermes" in item for item in attestation["diagnostics"])


def test_hermes_session_binding_is_read_only_and_fail_closed(tmp_path):
    from scripts import hermes_post_change_check as hermes

    session = _write_session(tmp_path, session_id="s1", status="sealed")

    binding = hermes.session_evidence_binding(
        _sessions_root(tmp_path), session.session_id, "primary-runtime"
    )
    assert binding == {
        "status": "bound",
        "sessionId": session.session_id,
        "sourceFingerprint": session.source_fingerprint,
        "profile": "primary-runtime",
    }

    report = {"overallStatus": "pass", "projectRoot": str(tmp_path), "results": []}
    hermes.attach_session_binding(
        report, _sessions_root(tmp_path), session.session_id, "isolated-profile"
    )
    assert report["overallStatus"] == "pass"
    assert report["sessionId"] == session.session_id
    assert report["sourceFingerprint"] == session.source_fingerprint
    assert report["profile"] == "isolated-profile"
    assert report["sessionBinding"]["status"] == "bound"

    blocked = {"overallStatus": "pass", "projectRoot": str(tmp_path), "results": []}
    hermes.attach_session_binding(
        blocked, _sessions_root(tmp_path), "missing-session", "primary-runtime"
    )
    assert blocked["overallStatus"] == "fail"
    assert blocked["sessionBinding"]["status"] == "blocked_missing_session"


def test_hermes_session_binding_does_not_add_check_steps(tmp_path):
    from scripts import hermes_post_change_check as hermes

    plan = hermes.build_check_plan(include_monitor=True, include_tests=True)
    labels = [step.label for step in plan]

    assert "session-binding" not in labels
    assert labels[:3] == ["git-status", "git-diff-stat", "git-diff-name-only"]
