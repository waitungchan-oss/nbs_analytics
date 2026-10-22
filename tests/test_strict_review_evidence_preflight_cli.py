from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

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


def test_preflight_source_fingerprint_hashes_nul_delimited_status_bytes(tmp_path: Path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@example.com"], cwd=tmp_path, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Acceptance Tests"], cwd=tmp_path, check=True,
    )
    subprocess.run(["git", "commit", "--allow-empty", "-qm", "fixture"], cwd=tmp_path, check=True)
    (tmp_path / "odd\nname.py").write_text("VALUE = 1\n", encoding="utf-8")
    status = subprocess.run(
        [
            "git", "status", "--porcelain=v1", "--untracked-files=all",
            "--no-renames", "-z", "--", ".",
            ":(exclude,icase,glob)**/Summay/**",
            ":(exclude,icase,glob)**/Summay",
            ":(exclude).nbs_agent_runtime",
        ],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    ).stdout

    assert _source_fingerprint(tmp_path) == hashlib.sha256(status).hexdigest()
    before_runtime_artifact = _source_fingerprint(tmp_path)
    runtime_artifact = tmp_path / ".nbs_agent_runtime" / "verification" / "profile.json"
    runtime_artifact.parent.mkdir(parents=True)
    runtime_artifact.write_text("generated evidence", encoding="utf-8")

    assert _source_fingerprint(tmp_path) == before_runtime_artifact


def test_preflight_source_fingerprint_fails_closed_over_output_budget(tmp_path: Path, monkeypatch):
    import scripts.strict_review_evidence_preflight as preflight

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@example.com"], cwd=tmp_path, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Acceptance Tests"], cwd=tmp_path, check=True,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-qm", "fixture"], cwd=tmp_path, check=True,
    )
    for index in range(100):
        (tmp_path / f"untracked-{index:03d}.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(preflight, "SOURCE_STATUS_MAX_BYTES", 1024)
    monkeypatch.setattr(preflight, "SOURCE_STATUS_MAX_STDERR_BYTES", 0)

    try:
        _source_fingerprint(tmp_path)
    except ValueError as exc:
        assert "bounded output budget" in str(exc)
    else:
        raise AssertionError("oversized Git status output must fail closed")


def test_preflight_source_fingerprint_uses_shared_live_transport_budget(tmp_path: Path, monkeypatch):
    import scripts.strict_review_evidence_preflight as preflight

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@example.com"], cwd=tmp_path, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Acceptance Tests"], cwd=tmp_path, check=True,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-qm", "fixture"], cwd=tmp_path, check=True,
    )
    for index in range(100):
        (tmp_path / f"untracked-{index:03d}.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(preflight, "SOURCE_STATUS_MAX_BYTES", 1024)
    monkeypatch.setattr(preflight, "SOURCE_STATUS_MAX_STDERR_BYTES", 0)

    with pytest.raises(ValueError, match="bounded output budget"):
        preflight._source_fingerprint(tmp_path)
