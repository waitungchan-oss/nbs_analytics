from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from backend.agents.acceptance_rollout_models import validate_rollout_preflight
from backend.agents.evidence_models import canonical_fingerprint


COMMIT = "a" * 40
SOURCE = "b" * 64


def _manifest(path):
    payload = {
        "schemaVersion": "pytest-test-manifest-v1",
        "status": "PASS",
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "nodeids": ["tests/test_example.py::test_one"],
    }
    payload["manifestFingerprint"] = canonical_fingerprint({
        "schemaVersion": payload["schemaVersion"],
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "nodeids": payload["nodeids"],
    })
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_cli_returns_bounded_blocker_without_ui_fixture(tmp_path):
    manifest = tmp_path / "manifest.json"
    output = tmp_path / ".nbs_agent_runtime" / "preflight.json"
    _manifest(manifest)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/acceptance_shard_rollout_preflight.py",
            "--project-root", str(tmp_path),
            "--manifest", str(manifest),
            "--commit-sha", COMMIT,
            "--source-fingerprint", SOURCE,
            "--output", str(output),
        ],
        cwd=str(Path(__file__).resolve().parents[1]),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == "acceptance-shard-rollout-preflight-v1"
    assert payload["status"] == "BLOCKED"
    assert payload["rolloutCandidate"] is False
    assert all("/" not in item for item in payload["blockers"])
    validate_rollout_preflight(payload)


def test_cli_returns_zero_only_for_source_matched_preflight(tmp_path):
    manifest = tmp_path / "manifest.json"
    runner = tmp_path / "runner.json"
    isolation = tmp_path / "isolation.json"
    ui_cache = tmp_path / "ui-cache.json"
    output = tmp_path / ".nbs_agent_runtime" / "preflight.json"
    _manifest(manifest)
    binding = {"commitSha": COMMIT, "sourceFingerprint": SOURCE}
    runner.write_text(json.dumps({"sourceBinding": binding, "sandbox": "available", "interpreter": "qualified"}), encoding="utf-8")
    isolation.write_text(json.dumps({
        "sourceBinding": binding,
        "fixtureRoots": "unique",
        "sqlite": "isolated",
        "cache": "isolated",
        "processes": "profile-bound",
    }), encoding="utf-8")
    ui_cache.write_text(json.dumps({"sourceBinding": binding, "status": "PASS", "sourceMatched": True}), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/acceptance_shard_rollout_preflight.py",
            "--project-root", str(tmp_path),
            "--manifest", str(manifest),
            "--commit-sha", COMMIT,
            "--source-fingerprint", SOURCE,
            "--runner-capability", str(runner),
            "--isolation", str(isolation),
            "--ui-cache", str(ui_cache),
            "--output", str(output),
        ],
        cwd=str(Path(__file__).resolve().parents[1]),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "PASS"
    assert payload["formalReleaseEnabled"] is False
