from __future__ import annotations

import json
from pathlib import Path

from backend.agents.memory_sidecar_hint_models import MemoryHint, MemoryHints
from scripts import hermes_post_change_check as post_check


def _hint_payload(*, status: str = "ready", source_ref: str = "context.json") -> dict:
    if status != "ready":
        return MemoryHints.empty(query_fingerprint="a" * 64, status=status).to_dict()
    return MemoryHints(
        query_fingerprint="a" * 64,
        status="ready",
        hints=(MemoryHint(
            memory_id="b" * 64,
            summary="Use bounded evidence only.",
            source_refs=(source_ref,),
            freshness="fresh",
            confidence="high",
            source_fingerprints=("c" * 64,),
        ),),
    ).to_dict()


def test_memory_sidecar_contract_states_non_authoritative_read_only_gateway_boundary():
    contract = (Path(post_check.PROJECT_ROOT) / "docs/agents/MEMORY_SIDECAR_CONTRACT.md").read_text(encoding="utf-8")

    assert "non-authoritative" in contract
    assert "NBS Hermes" in contract
    assert "not Tencent Hermes" in contract
    assert "never starts Gateway" in contract
    assert "invocations=0" in contract
    assert "writes=0" in contract


def test_memory_sidecar_report_is_read_only_and_blocks_unsafe_evidence(tmp_path):
    run = tmp_path / ".nbs_agent_runtime" / "runs" / "run-fixture"
    run.mkdir(parents=True)
    (run / "memory-hints.json").write_text(json.dumps(_hint_payload()), encoding="utf-8")
    telemetry = tmp_path / ".nbs_agent_runtime" / "telemetry"
    telemetry.mkdir()
    (telemetry / "memory_sidecar.jsonl").write_text(
        json.dumps({
            "schemaVersion": "memory-sidecar-telemetry-v1",
            "runId": "run-fixture",
            "mode": "shadow",
            "queryFingerprint": "a" * 64,
            "status": "stale",
            "latencyMs": 0,
            "hintCount": 0,
            "inputBytes": 0,
            "fallback": True,
            "redactionCount": 0,
        }) + "\n",
        encoding="utf-8",
    )

    report = post_check.memory_sidecar_artifact_report(tmp_path)

    assert report["schemaVersion"] == "memory-sidecar-hermes-report-v1"
    assert report["policy"] == "read-only"
    assert report["invocations"] == 0
    assert report["writes"] == 0
    assert report["status"] == "blocked"
    assert report["artifactCounts"] == {"memory-hints.json": 1, "memory_sidecar.jsonl": 1}
    assert report["fallbackChecks"]["stale"] == "blocked"
    assert report["diagnostics"] == [{"code": "stale_memory_hints", "runId": "run-fixture"}]


def test_memory_sidecar_report_marks_malformed_over_cap_and_absolute_paths_invalid(tmp_path):
    runs = tmp_path / ".nbs_agent_runtime" / "runs"
    absolute_path = _hint_payload()
    absolute_path["hints"][0]["sourceRefs"] = ["/private/secret"]
    for name, payload in {
        "run-malformed": {"schemaVersion": "wrong"},
        "run-over-cap": {**_hint_payload(), "hints": [{}] * 4},
        "run-absolute-path": absolute_path,
    }.items():
        run = runs / name
        run.mkdir(parents=True)
        (run / "memory-hints.json").write_text(json.dumps(payload), encoding="utf-8")

    report = post_check.memory_sidecar_artifact_report(tmp_path)

    assert report["status"] == "invalid"
    assert report["artifactCounts"]["memory-hints.json"] == 3
    assert {item["runId"] for item in report["diagnostics"]} == {
        "run-malformed", "run-over-cap", "run-absolute-path",
    }
    assert {item["code"] for item in report["diagnostics"]} == {"invalid_memory_hints"}


def test_memory_sidecar_report_blocks_symlinked_permission_evidence(tmp_path):
    run = tmp_path / ".nbs_agent_runtime" / "runs" / "run-symlink"
    run.mkdir(parents=True)
    target = tmp_path / "outside.json"
    target.write_text(json.dumps(_hint_payload()), encoding="utf-8")
    (run / "memory-hints.json").symlink_to(target)

    report = post_check.memory_sidecar_artifact_report(tmp_path)

    assert report["status"] == "blocked"
    assert report["artifactCounts"]["memory-hints.json"] == 0
    assert report["diagnostics"] == [{"code": "permission_denied", "runId": "run-symlink"}]


def test_memory_sidecar_report_allows_explicit_timeout_fallback_without_authority_change(tmp_path):
    run = tmp_path / ".nbs_agent_runtime" / "runs" / "run-timeout"
    run.mkdir(parents=True)
    (run / "memory-hints.json").write_text(json.dumps(_hint_payload(status="timeout")), encoding="utf-8")

    report = post_check.memory_sidecar_artifact_report(tmp_path)

    assert report["status"] == "pass"
    assert report["fallbackChecks"]["timeout"] == "fallback"
    assert report["diagnostics"] == [{"code": "fallback_timeout", "runId": "run-timeout"}]


def test_memory_sidecar_report_rejects_schema_valid_hints_over_6000_bytes(tmp_path):
    run = tmp_path / ".nbs_agent_runtime" / "runs" / "run-over-bytes"
    run.mkdir(parents=True)
    (run / "memory-hints.json").write_text(json.dumps(_hint_payload()) + (" " * 6000), encoding="utf-8")

    report = post_check.memory_sidecar_artifact_report(tmp_path)

    assert report["status"] == "invalid"
    assert report["diagnostics"] == [{"code": "invalid_memory_hints", "runId": "run-over-bytes"}]


def test_memory_sidecar_report_blocks_unreadable_runs_directory(tmp_path, monkeypatch):
    runs = tmp_path / ".nbs_agent_runtime" / "runs"
    runs.mkdir(parents=True)
    original_iterdir = Path.iterdir

    def deny_runs(path):
        if path == runs:
            raise PermissionError("denied")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", deny_runs)
    report = post_check.memory_sidecar_artifact_report(tmp_path)

    assert report["status"] == "blocked"
    assert report["diagnostics"] == [{"code": "permission_denied", "runId": "runs"}]


def test_memory_sidecar_report_blocks_symlinked_runtime_telemetry_and_run_dirs(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    runtime = tmp_path / ".nbs_agent_runtime"
    runtime.symlink_to(external, target_is_directory=True)
    report = post_check.memory_sidecar_artifact_report(tmp_path)
    assert report["status"] == "blocked"
    assert report["diagnostics"] == [{"code": "permission_denied", "runId": "runtime"}]

    runtime.unlink()
    runtime.mkdir()
    (runtime / "runs").mkdir()
    (runtime / "telemetry").symlink_to(external, target_is_directory=True)
    report = post_check.memory_sidecar_artifact_report(tmp_path)
    assert report["status"] == "blocked"
    assert report["diagnostics"] == [{"code": "permission_denied", "runId": "telemetry"}]

    (runtime / "telemetry").unlink()
    (runtime / "telemetry").mkdir()
    (runtime / "runs" / "run-link").symlink_to(external, target_is_directory=True)
    report = post_check.memory_sidecar_artifact_report(tmp_path)
    assert report["status"] == "blocked"
    assert report["diagnostics"] == [{"code": "permission_denied", "runId": "run-link"}]
