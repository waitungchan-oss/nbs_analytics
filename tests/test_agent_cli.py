import json
import subprocess
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"
if not PYTHON.exists():
    PYTHON = ROOT.parent.parent / ".venv/bin/python"


def valid_context_summary():
    return {
        "schemaVersion": "context-summary-v1",
        "status": "ready",
        "taskUnderstanding": ["objective"],
        "systemBoundaries": ["read-only"],
        "relevantFiles": [],
        "dependencies": [],
        "recommendedTests": ["targeted"],
        "risks": [],
        "unknowns": [],
        "contextFingerprint": "context-fingerprint",
    }


def runtime_fixture(name, value):
    path = ROOT / ".nbs_agent_runtime" / "test-inputs" / f"{name}-{uuid.uuid4().hex}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_context_cli_collect_only_outputs_json():
    result = subprocess.run(
        [str(PYTHON), "scripts/context_agent.py", "--brief", "docs/agents/NBS_AGENT_ARCHITECTURE.md", "--collect-only"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["schemaVersion"] == "context-evidence-v1"


def test_context_cli_missing_brief_exits_two():
    result = subprocess.run(
        [str(PYTHON), "scripts/context_agent.py", "--brief", "docs/missing.md", "--collect-only"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 2


def test_context_cli_rejects_output_outside_agent_runtime():
    forbidden = ROOT / "docs/context-output.json"
    result = subprocess.run(
        [str(PYTHON), "scripts/context_agent.py", "--brief", "docs/agents/NBS_AGENT_ARCHITECTURE.md", "--collect-only", "--output", "docs/context-output.json"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 3
    assert not forbidden.exists()


def test_context_cli_malformed_fingerprint_valid_bundle_exits_five(tmp_path):
    source = subprocess.run(
        [str(PYTHON), "scripts/context_agent.py", "--brief", "docs/agents/NBS_AGENT_ARCHITECTURE.md", "--collect-only"],
        cwd=ROOT, text=True, capture_output=True, check=True,
    )
    payload = json.loads(source.stdout)
    payload["documents"] = ["wrong"]
    unsigned = {key: value for key, value in payload.items() if key != "bundleFingerprint"}
    import hashlib
    payload["bundleFingerprint"] = hashlib.sha256(json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    bundle = tmp_path / "malformed.json"
    bundle.write_text(json.dumps(payload), encoding="utf-8")
    result = subprocess.run(
        [str(PYTHON), "scripts/context_agent.py", "--bundle", str(bundle), "--collect-only"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 5
    assert result.stdout == ""
    assert "Traceback" not in result.stderr


def test_review_cli_collect_only_outputs_review_bundle():
    result = subprocess.run(
        [str(PYTHON), "scripts/review_agent.py", "--brief", "docs/agents/NBS_AGENT_ARCHITECTURE.md", "--base", "HEAD", "--head", "WORKTREE", "--collect-only"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["schemaVersion"] == "review-evidence-v1"
    assert set(payload) == {"schemaVersion", "taskContract", "contextSummary", "gitDiff", "verification", "bundleFingerprint"}


def test_review_cli_accepts_working_tree_alias_for_dirty_review():
    result = subprocess.run(
        [str(PYTHON), "scripts/review_agent.py", "--brief", "docs/agents/NBS_AGENT_ARCHITECTURE.md", "--base", "HEAD", "--head", "working-tree", "--collect-only"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["gitDiff"]["head"] == "WORKTREE"


def test_review_cli_strict_without_verification_exits_two(tmp_path):
    context = runtime_fixture("context", valid_context_summary())
    result = subprocess.run(
        [str(PYTHON), "scripts/review_agent.py", "--brief", "docs/agents/NBS_AGENT_ARCHITECTURE.md", "--base", "HEAD", "--head", "WORKTREE", "--context", str(context), "--strict"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 2
    context.unlink()


@pytest.mark.parametrize("filename", ["outside.json", "secret.env", "data.db", "report.xlsx", "events.log"])
def test_review_cli_rejects_unsafe_input_paths(tmp_path, filename):
    candidate = tmp_path / filename
    candidate.write_text(json.dumps(valid_context_summary()), encoding="utf-8")
    result = subprocess.run(
        [str(PYTHON), "scripts/review_agent.py", "--brief", "docs/agents/NBS_AGENT_ARCHITECTURE.md",
         "--context", str(candidate), "--strict"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 3


def test_review_cli_accepts_runtime_input_and_rejects_symlink_escape(tmp_path):
    context = runtime_fixture("context", valid_context_summary())
    try:
        result = subprocess.run(
            [str(PYTHON), "scripts/review_agent.py", "--brief", "docs/agents/NBS_AGENT_ARCHITECTURE.md",
             "--context", str(context), "--strict"],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        assert result.returncode == 2

        outside = tmp_path / "outside.json"
        outside.write_text(json.dumps(valid_context_summary()), encoding="utf-8")
        link = context.with_name(f"link-{uuid.uuid4().hex}.json")
        link.symlink_to(outside)
        try:
            result = subprocess.run(
                [str(PYTHON), "scripts/review_agent.py", "--brief", "docs/agents/NBS_AGENT_ARCHITECTURE.md",
                 "--context", str(link), "--strict"],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            assert result.returncode == 3
        finally:
            link.unlink()
    finally:
        context.unlink()


def test_review_cli_rejects_malformed_verification_file(tmp_path):
    context = runtime_fixture("context", valid_context_summary())
    verification = runtime_fixture("verification", {"commands": [{"exitCode": 0}]})
    result = subprocess.run(
        [
            str(PYTHON), "scripts/review_agent.py",
            "--brief", "docs/agents/NBS_AGENT_ARCHITECTURE.md",
            "--base", "HEAD", "--head", "WORKTREE",
            "--context", str(context), "--verification", str(verification), "--strict",
        ],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 5
    assert result.stdout == ""


    context.unlink()
    verification.unlink()
