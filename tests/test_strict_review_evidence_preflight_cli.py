from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from scripts.strict_review_evidence_preflight import _source_fingerprint, main
from backend.agents.strict_review_preflight_models import validate_preflight_result


def test_cli_help_is_available(capsys):
    assert main(["--help"]) == 0
    assert "Strict Review evidence preflight" in capsys.readouterr().out


def test_cli_emits_one_bounded_json_and_writes_artifacts(tmp_path: Path, capsys):
    runtime = tmp_path / ".nbs_agent_runtime"
    output = runtime / "verification_sessions" / "s1"
    (tmp_path / "agent_config").mkdir()
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    interpreter = tmp_path / ".venv" / "bin" / "python"
    interpreter.write_text(f"#!/bin/sh\nexec {sys.executable} \"$@\"\n", encoding="utf-8")
    interpreter.chmod(0o755)
    (tmp_path / "backend" / "agents").mkdir(parents=True)
    (tmp_path / "backend" / "agents" / "strict_review_evidence_service.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "backend" / "agents" / "strict_review_evidence_cache.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "agent_config" / "implementation_commands.json").write_text(json.dumps({"commands": {"py_compile": {"prefix": [".venv/bin/python", "-m", "py_compile"], "approvedRepositoryInterpreter": ".venv/bin/python", "timeoutSeconds": 30}}}), encoding="utf-8")
    assert main(["--project-root", str(tmp_path), "--session", "s1", "--source-fingerprint", "a" * 64, "--output", str(output)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] in {"ready", "degraded"}
    assert payload["sessionId"] == "s1"
    assert (output / "preflight.json").exists()
    assert (output / "verification-v1.json").exists()
    assert validate_preflight_result(json.loads((output / "preflight.json").read_text(encoding="utf-8")))["status"] == "ready"


def test_cli_rejects_output_outside_runtime(tmp_path: Path, capsys):
    assert main(["--project-root", str(tmp_path), "--session", "s1", "--source-fingerprint", "a" * 64, "--output", str(tmp_path / "escape")]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "invalid_evidence"


def test_preflight_source_fingerprint_excludes_summary_paths(tmp_path: Path):
    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=tmp_path, capture_output=True, text=True, check=True,
        )
        return result.stdout

    git("init", "-q")
    git("config", "user.email", "tests@example.com")
    git("config", "user.name", "Acceptance Tests")
    summary = tmp_path / "Summay" / "fixture.md"
    summary.parent.mkdir()
    summary.write_text("summary fixture one\n", encoding="utf-8")
    git("add", "--all")
    git("commit", "-qm", "fixture")

    first = _source_fingerprint(tmp_path)
    summary.write_text("summary fixture two\n", encoding="utf-8")
    (tmp_path / "Summay/untracked.md").write_text("summary fixture untracked\n", encoding="utf-8")
    second = _source_fingerprint(tmp_path)

    assert first == second
    (tmp_path / "ordinary.py").write_text("VALUE = 1\n", encoding="utf-8")
    assert _source_fingerprint(tmp_path) != second
