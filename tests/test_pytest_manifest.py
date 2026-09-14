import subprocess

from scripts.pytest_manifest import collect_pytest_manifest


COMMIT = "a" * 40
SOURCE = "b" * 64


def test_manifest_sorts_and_deduplicates_nodeids(monkeypatch, tmp_path):
    completed = subprocess.CompletedProcess(
        [],
        0,
        "tests/test_b.py::test_two\ntests/test_a.py::test_one\n",
        "",
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed)

    manifest = collect_pytest_manifest(tmp_path, commit_sha=COMMIT, source_fingerprint=SOURCE)

    assert manifest["status"] == "PASS"
    assert manifest["nodeids"] == [
        "tests/test_a.py::test_one",
        "tests/test_b.py::test_two",
    ]
    assert len(manifest["manifestFingerprint"]) == 64
    assert manifest["metadata"]["telemetry"]["durationSeconds"] >= 0


def test_manifest_duplicate_nodeids_fail_closed(monkeypatch, tmp_path):
    completed = subprocess.CompletedProcess([], 0, "tests/test_a.py::test_one\ntests/test_a.py::test_one\n", "")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed)

    manifest = collect_pytest_manifest(tmp_path, commit_sha=COMMIT, source_fingerprint=SOURCE)

    assert manifest["status"] == "FAIL"
    assert manifest["metadata"]["failureCode"] == "duplicate_nodeid"


def test_manifest_timeout_is_blocked(monkeypatch, tmp_path):
    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 180, output="partial")

    monkeypatch.setattr(subprocess, "run", timeout)

    manifest = collect_pytest_manifest(tmp_path, commit_sha=COMMIT, source_fingerprint=SOURCE)

    assert manifest["status"] == "BLOCKED"
    assert manifest["metadata"]["telemetry"]["blockedReason"] == "timeout"
