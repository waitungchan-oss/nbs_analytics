from __future__ import annotations

import json
import subprocess
import sys

import pytest


def _contract_payload():
    from backend.agents.acceptance_contract import build_acceptance_contract

    return build_acceptance_contract(
        contract_id="nbs-acceptance",
        contract_version="formal-scope-v1",
        scope_fingerprint="a" * 64,
        semantic_rules_fingerprint="b" * 64,
        dataset_snapshot_fingerprint="c" * 64,
        supersedes_contract_fingerprint=None,
        status="active",
    )


def test_bounded_artifact_round_trips_and_rejects_symlink(tmp_path):
    from scripts.acceptance_baseline import read_bounded_artifact, write_bounded_artifact

    target = tmp_path / "contract.json"
    payload = {"schemaVersion": "acceptance-contract-v1"}
    write_bounded_artifact(target, payload)
    assert read_bounded_artifact(target) == payload

    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        read_bounded_artifact(link)


def test_bounded_artifact_rejects_symlinked_parent(tmp_path):
    from scripts.acceptance_baseline import write_bounded_artifact

    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        write_bounded_artifact(linked_parent / "artifact.json", {"ok": True})


def test_bounded_artifact_write_stays_in_open_parent_on_replacement_race(tmp_path, monkeypatch):
    import scripts.acceptance_baseline as baseline

    parent = tmp_path / "parent"
    parent.mkdir()
    moved_parent = tmp_path / "moved-parent"
    malicious_parent = tmp_path / "malicious-parent"
    malicious_parent.mkdir()
    target = parent / "artifact.json"
    original_replace = baseline.os.replace

    def replace_with_parent_swap(source, destination, *args, **kwargs):
        parent.rename(moved_parent)
        parent.symlink_to(malicious_parent, target_is_directory=True)
        return original_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(baseline.os, "replace", replace_with_parent_swap)
    baseline.write_bounded_artifact(target, {"safe": True})

    assert (moved_parent / "artifact.json").is_file()
    assert not (malicious_parent / "artifact.json").exists()


def test_bounded_artifact_rejects_oversized_payload(tmp_path):
    from scripts.acceptance_baseline import write_bounded_artifact

    with pytest.raises(ValueError, match="size cap"):
        write_bounded_artifact(tmp_path / "large.json", {"value": "x" * 128}, max_bytes=32)


def test_performance_command_requires_explicit_contract(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "scripts/acceptance_baseline.py",
            "performance",
            "--output",
            str(tmp_path / "performance.json"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "contract" in result.stderr.lower()


def test_contract_cli_emits_a_valid_bounded_artifact(tmp_path):
    from backend.agents.acceptance_contract import validate_acceptance_contract
    from scripts.acceptance_baseline import main, read_bounded_artifact

    output = tmp_path / "contract.json"
    assert main(
        [
            "contract",
            "--contract-version",
            "formal-scope-v1",
            "--scope-fingerprint",
            "a" * 64,
            "--semantic-rules-fingerprint",
            "b" * 64,
            "--dataset-snapshot-fingerprint",
            "c" * 64,
            "--output",
            str(output),
        ]
    ) == 0
    payload = read_bounded_artifact(output)
    validate_acceptance_contract(payload)
    assert payload["status"] == "active"


def test_performance_cli_consumes_contract_and_emits_v2(tmp_path):
    from backend.agents.acceptance_performance_v2 import validate_performance_baseline_v2
    from scripts.acceptance_baseline import main, read_bounded_artifact

    contract_path = tmp_path / "contract.json"
    performance_path = tmp_path / "performance.json"
    contract_path.write_text(json.dumps(_contract_payload()), encoding="utf-8")
    assert main(
        [
            "performance",
            "--contract",
            str(contract_path),
            "--baseline-family-id",
            "acceptance-full-2026-09",
            "--baseline-role",
            "serial_control",
            "--lifecycle",
            "qualified",
            "--commit-sha",
            "d" * 40,
            "--source-fingerprint",
            "e" * 64,
            "--manifest-fingerprint",
            "f" * 64,
            "--test-population-fingerprint",
            "0" * 64,
            "--runner-fingerprint",
            "1" * 64,
            "--environment-fingerprint",
            "2" * 64,
            "--dataset-snapshot-fingerprint",
            "3" * 64,
            "--selection-mode",
            "full",
            "--stages",
            json.dumps(
                {
                    "collectionSeconds": 1.0,
                    "fixturePreparationSeconds": 2.0,
                    "pytestExecutionSeconds": 3.0,
                    "aggregateSeconds": 0.5,
                    "totalWallSeconds": 7.0,
                }
            ),
            "--test-count",
            json.dumps({"collected": 10, "passed": 10, "failed": 0, "skipped": 0}),
            "--output",
            str(performance_path),
        ]
    ) == 0
    payload = read_bounded_artifact(performance_path)
    validate_performance_baseline_v2(payload)
    assert payload["schemaVersion"] == "acceptance-performance-baseline-v2"


def test_performance_cli_does_not_write_when_contract_is_invalid(tmp_path):
    from scripts.acceptance_baseline import main

    contract_path = tmp_path / "invalid-contract.json"
    output = tmp_path / "performance.json"
    contract_path.write_text(json.dumps({"schemaVersion": "wrong"}), encoding="utf-8")
    assert main(
        [
            "performance",
            "--contract",
            str(contract_path),
            "--baseline-family-id",
            "family",
            "--baseline-role",
            "serial_control",
            "--lifecycle",
            "qualified",
            "--commit-sha",
            "a" * 40,
            "--source-fingerprint",
            "b" * 64,
            "--manifest-fingerprint",
            "c" * 64,
            "--test-population-fingerprint",
            "d" * 64,
            "--runner-fingerprint",
            "e" * 64,
            "--environment-fingerprint",
            "f" * 64,
            "--dataset-snapshot-fingerprint",
            "0" * 64,
            "--selection-mode",
            "full",
            "--stages",
            json.dumps({
                "collectionSeconds": 0,
                "fixturePreparationSeconds": 0,
                "pytestExecutionSeconds": 0,
                "aggregateSeconds": 0,
                "totalWallSeconds": 0,
            }),
            "--test-count",
            json.dumps({"collected": 0, "passed": 0, "failed": 0, "skipped": 0}),
            "--output",
            str(output),
        ]
    ) == 2
    assert not output.exists()
