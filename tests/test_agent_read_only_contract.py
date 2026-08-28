from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"
if not PYTHON.exists():
    PYTHON = Path(sys.executable)


def digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def tracked_status() -> str:
    return subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout


def test_collect_only_does_not_modify_tracked_db_or_runtime():
    db = ROOT / "nbs_marketing_data.db"
    generation = ROOT / ".nbs_runtime/data_generation.json"
    before = {"git": tracked_status(), "db": digest(db), "generation": digest(generation)}

    context = subprocess.run(
        [
            str(PYTHON),
            "scripts/context_agent.py",
            "--brief",
            "docs/agents/NBS_AGENT_ARCHITECTURE.md",
            "--collect-only",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    review = subprocess.run(
        [
            str(PYTHON),
            "scripts/review_agent.py",
            "--brief",
            "docs/agents/NBS_AGENT_ARCHITECTURE.md",
            "--base",
            "HEAD",
            "--head",
            "WORKTREE",
            "--collect-only",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    after = {"git": tracked_status(), "db": digest(db), "generation": digest(generation)}
    assert context.returncode == 0, context.stderr
    assert review.returncode == 0, review.stderr
    assert after == before


def test_hermes_targeted_pack_covers_implementation_agent_isolation_tests():
    hermes_source = (ROOT / "scripts/hermes_post_change_check.py").read_text(encoding="utf-8")

    for test_name in [
        "tests/test_implementation_models.py",
        "tests/test_implementation_guard.py",
        "tests/test_validation_runner.py",
        "tests/test_implementation_agent_service.py",
        "tests/test_implementation_agent_cli.py",
        "tests/test_implementation_agent_integration.py",
    ]:
        assert test_name in hermes_source


# ---------------------------------------------------------------------------
# Task 7: session verification chain contract boundaries
# ---------------------------------------------------------------------------


def test_docs_define_two_stage_review_chain():
    review = (ROOT / "docs/agents/REVIEW_AGENT_CONTRACT.md").read_text(encoding="utf-8")
    hermes = (ROOT / "NBS_HERMES_MONITORING.md").read_text(encoding="utf-8")

    assert "verification-session-v1" in review
    assert "Completion Attestation" in review
    assert "primary-runtime" in hermes
    assert "isolated-profile" in hermes


def test_review_doc_defines_session_aware_input_boundary():
    review = (ROOT / "docs/agents/REVIEW_AGENT_CONTRACT.md").read_text(encoding="utf-8")

    # Review consumes the source seal and pre-review targeted evidence only.
    assert "source seal" in review
    assert "pre-review targeted" in review
    assert "verification-session-v1" in review
    # Review must NOT require full pytest / Hermes for its own code-level PASS.
    assert "full pytest" in review
    assert "not require full pytest or Hermes" in review
    # Final completion still requires both later gates via Completion Attestation.
    assert "completion-attestation-v1" in review


def test_review_doc_defines_memory_hub_non_authoritative_boundary():
    review = (ROOT / "docs/agents/REVIEW_AGENT_CONTRACT.md").read_text(encoding="utf-8")

    assert "non-authoritative" in review
    assert "ignored" in review
    assert "cannot change" in review


def test_hermes_doc_defines_profile_separation_and_session_binding():
    hermes = (ROOT / "NBS_HERMES_MONITORING.md").read_text(encoding="utf-8")

    assert "primary-runtime" in hermes
    assert "isolated-profile" in hermes
    assert "cannot be mixed" in hermes
    # Hermes must bind its report to the session source fingerprint.
    assert "source fingerprint" in hermes
    assert "verification-session-v1" in hermes
    # Hermes stays read-only; the trusted controller alone records gate artifacts.
    assert "read-only" in hermes
    assert "completion-attestation-v1" in hermes
