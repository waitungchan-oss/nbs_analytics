import json
from datetime import datetime, timezone, timedelta

from backend.agents.evidence_models import canonical_fingerprint
from scripts.release_gate import aggregate_from_paths, main


COMMIT = "a" * 40
SOURCE = "b" * 64
NOW = datetime(2026, 9, 1, 0, 2, tzinfo=timezone.utc)


def _evidence(gate, status="PASS"):
    value = {
        "schemaVersion": f"{gate.replace('_', '-')}-gate-v1", "gate": gate, "status": status,
        "commitSha": COMMIT, "sourceFingerprint": SOURCE,
        "startedAt": "2026-09-01T00:00:00Z", "finishedAt": "2026-09-01T00:01:00Z",
        "result": {"passed": 1, "failed": 0, "skipped": 0}, "metadata": {"commandId": gate},
    }
    return {**value, "evidenceFingerprint": canonical_fingerprint(value)}


def test_aggregator_reads_exactly_three_evidence_files_and_passes(tmp_path):
    paths = {}
    for gate in ("full_pytest", "hermes", "ui_acceptance"):
        path = tmp_path / f"{gate}.json"
        path.write_text(json.dumps(_evidence(gate)), encoding="utf-8")
        paths[gate] = path
    result = aggregate_from_paths(paths, COMMIT, SOURCE, NOW)
    assert result["status"] == "PASS"
    assert set(result["gates"]) == {"full_pytest", "hermes", "ui_acceptance"}


def test_aggregator_is_fail_closed_for_missing_or_blocked_or_mismatch(tmp_path):
    paths = {}
    for gate in ("full_pytest", "hermes", "ui_acceptance"):
        path = tmp_path / f"{gate}.json"
        path.write_text(json.dumps(_evidence(gate, "PASS")), encoding="utf-8")
        paths[gate] = path
    paths["hermes"].write_text(json.dumps(_evidence("hermes", "BLOCKED")), encoding="utf-8")
    assert aggregate_from_paths(paths, COMMIT, SOURCE, NOW)["status"] == "BLOCKED"

    paths["ui_acceptance"].unlink()
    try:
        aggregate_from_paths(paths, COMMIT, SOURCE, NOW)
    except ValueError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("missing evidence must fail closed")


def test_cli_returns_nonzero_for_invalid_evidence_and_does_not_run_commands(tmp_path, capsys):
    path = tmp_path / "full.json"
    value = _evidence("full_pytest")
    finished = datetime.now(timezone.utc).replace(microsecond=0)
    value["startedAt"] = (finished - timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
    value["finishedAt"] = finished.isoformat().replace("+00:00", "Z")
    value["evidenceFingerprint"] = canonical_fingerprint({k: v for k, v in value.items() if k != "evidenceFingerprint"})
    path.write_text(json.dumps(value), encoding="utf-8")
    rc = main(["validate", "--gate", "full_pytest", "--evidence", str(path), "--commit-sha", COMMIT, "--source-fingerprint", SOURCE])
    assert rc == 0
    assert "PASS" in capsys.readouterr().out

    path.write_text("{}", encoding="utf-8")
    rc = main(["validate", "--gate", "full_pytest", "--evidence", str(path), "--commit-sha", COMMIT, "--source-fingerprint", SOURCE])
    assert rc == 2
