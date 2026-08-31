from __future__ import annotations

import json
from pathlib import Path
import sys

from scripts.strict_review_evidence_preflight import main


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


def test_cli_rejects_output_outside_runtime(tmp_path: Path, capsys):
    assert main(["--project-root", str(tmp_path), "--session", "s1", "--source-fingerprint", "a" * 64, "--output", str(tmp_path / "escape")]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "invalid_evidence"
