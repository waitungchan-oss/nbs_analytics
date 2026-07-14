import json
import subprocess
from pathlib import Path


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


def test_review_cli_strict_without_verification_exits_two(tmp_path):
    context = tmp_path / "context.json"
    context.write_text(json.dumps(valid_context_summary()), encoding="utf-8")
    result = subprocess.run(
        [str(PYTHON), "scripts/review_agent.py", "--brief", "docs/agents/NBS_AGENT_ARCHITECTURE.md", "--base", "HEAD", "--head", "WORKTREE", "--context", str(context), "--strict"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 2


def test_review_cli_rejects_malformed_verification_file(tmp_path):
    context = tmp_path / "context.json"
    context.write_text(json.dumps(valid_context_summary()), encoding="utf-8")
    verification = tmp_path / "verification.json"
    verification.write_text('{"commands":[{"exitCode":0}]}', encoding="utf-8")
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
