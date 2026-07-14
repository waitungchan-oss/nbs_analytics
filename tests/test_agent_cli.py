import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"
if not PYTHON.exists():
    PYTHON = ROOT.parent.parent / ".venv/bin/python"


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
