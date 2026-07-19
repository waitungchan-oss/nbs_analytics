import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.agents.workflow_store import WorkflowStore
from backend.agents.verified_backfill_service import SubprocessRunner, VerifiedBackfillService


COMMIT = "a" * 40
DIFF = hashlib.sha256(b"diff\n").hexdigest()


def passing_review(*, source_commit=COMMIT, source_branch="main", diff_fingerprint=DIFF):
    unsigned = {
        "schemaVersion": "review-report-v1",
        "verdict": "pass",
        "findings": [],
        "requirementCoverage": ["Task2 verified backfill requirements reviewed"],
        "testCoverage": ["focused Task2 tests passed"],
        "baselineRisk": "none",
        "residualRisk": ["Hermes remains the final acceptance gate"],
        "hermesRequiredChecks": ["run Hermes post-change check"],
        "sourceCommit": source_commit,
        "sourceBranch": source_branch,
        "diffFingerprint": diff_fingerprint,
    }
    return {**unsigned, "reviewFingerprint": hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()}


class FakeRunner:
    def __init__(self, *, branch="main", head=COMMIT, dirty=(), failures=()):
        self.branch = branch
        self.head = head
        self.dirty = dirty
        self.failures = set(failures)
        self.calls = []

    def run(self, argv, *, timeout):
        argv = tuple(argv)
        self.calls.append(argv)
        if argv[:3] == ("git", "status", "--porcelain=v1"):
            return {"returncode": 0, "stdout": "".join(self.dirty), "stderr": ""}
        if argv[:3] == ("git", "branch", "--show-current"):
            return {"returncode": 0, "stdout": self.branch + "\n", "stderr": ""}
        if argv[:2] == ("git", "rev-parse"):
            return {"returncode": 0, "stdout": self.head + "\n", "stderr": ""}
        if argv[:2] == ("git", "diff"):
            return {"returncode": 0, "stdout": "diff\n", "stderr": ""}
        name = "pytest"
        if "scripts/system_manager.py" in argv:
            name = "systemAcceptance"
        elif "scripts/hermes_post_change_check.py" in argv:
            name = "hermes"
        elif "tests/test_phase2_precheck_acceptance.py" in argv:
            name = "baseline"
        return {
            "returncode": 1 if name in self.failures else 0,
            "stdout": "pass\n" if name not in self.failures else "failed\n",
            "stderr": "" if name not in self.failures else "error\n",
        }


@pytest.fixture
def service(tmp_path: Path):
    runner = FakeRunner()
    brief = tmp_path / ".nbs_agent_runtime" / "reports" / "verified-backfill-task2-brief.md"
    brief.parent.mkdir(parents=True)
    brief.write_text("# Task 2\n", encoding="utf-8")
    return VerifiedBackfillService(
        tmp_path,
        store=WorkflowStore(tmp_path),
        runner=runner,
        review_provider=lambda identity, gates: passing_review(),
    )


def test_create_blocks_dirty_main_before_writing_run(service):
    service.runner.dirty = (" M backend/example.py\n",)
    result = service.create(source_commit=COMMIT, reason="documentation backfill")
    assert result["status"] == "blocked"
    assert result["reason"] == "dirty_worktree"
    assert list(service.store.runs_root.iterdir()) == []


@pytest.mark.parametrize(
    ("field", "expected"),
    [("branch", "non_main_branch"), ("head", "stale_commit")],
)
def test_create_blocks_invalid_git_identity(service, field, expected):
    setattr(service.runner, field, "codex/task" if field == "branch" else "b" * 40)
    result = service.create(source_commit=COMMIT, reason="documentation backfill")
    assert result == {"status": "blocked", "reason": expected}
    assert list(service.store.runs_root.iterdir()) == []


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("pytest", "pytest_failed"),
        ("systemAcceptance", "system_acceptance_failed"),
        ("hermes", "hermes_failed"),
        ("baseline", "baseline_failed"),
    ],
)
def test_create_blocks_failed_gate_without_writing_run(tmp_path, failure, expected):
    runner = FakeRunner(failures=(failure,))
    brief = tmp_path / ".nbs_agent_runtime" / "reports" / "verified-backfill-task2-brief.md"
    brief.parent.mkdir(parents=True)
    brief.write_text("# Task 2\n", encoding="utf-8")
    service = VerifiedBackfillService(
        tmp_path,
        store=WorkflowStore(tmp_path),
        runner=runner,
        review_provider=lambda identity, gates: passing_review(),
    )
    result = service.create(source_commit=COMMIT, reason="documentation backfill")
    assert result == {"status": "blocked", "reason": expected}
    assert list(service.store.runs_root.iterdir()) == []


def test_create_blocks_review_that_is_not_pass(tmp_path):
    review = passing_review()
    review["verdict"] = "changes_required"
    review["findings"] = []
    unsigned = {key: value for key, value in review.items() if key != "reviewFingerprint"}
    review["reviewFingerprint"] = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    service = VerifiedBackfillService(
        tmp_path,
        store=WorkflowStore(tmp_path),
        runner=FakeRunner(),
        review_provider=lambda identity, gates: review,
    )
    result = service.create(source_commit=COMMIT, reason="documentation backfill")
    assert result == {"status": "blocked", "reason": "review_not_pass"}
    assert list(service.store.runs_root.iterdir()) == []


@pytest.mark.parametrize("findings", [[{"severity": "medium"}], None, "invalid"])
def test_create_blocks_pass_review_without_strictly_empty_findings(tmp_path, findings):
    brief = tmp_path / ".nbs_agent_runtime" / "reports" / "verified-backfill-task2-brief.md"
    brief.parent.mkdir(parents=True)
    brief.write_text("# Task 2\n", encoding="utf-8")
    review = passing_review()
    if findings is None:
        del review["findings"]
    else:
        review["findings"] = findings
    unsigned = {key: value for key, value in review.items() if key != "reviewFingerprint"}
    review["reviewFingerprint"] = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    service = VerifiedBackfillService(
        tmp_path,
        store=WorkflowStore(tmp_path),
        runner=FakeRunner(),
        review_provider=lambda identity, gates: review,
    )

    result = service.create(source_commit=COMMIT, reason="documentation backfill")

    assert result["status"] == "blocked"
    assert list(service.store.runs_root.iterdir()) == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda review: review.pop("requirementCoverage"),
        lambda review: review.update({"testCoverage": "not-a-list"}),
        lambda review: review.update({"findings": [{"severity": "medium"}]}),
    ],
)
def test_create_blocks_review_missing_or_malformed_strict_schema(service, mutation):
    review = passing_review()
    mutation(review)
    unsigned = {key: value for key, value in review.items() if key != "reviewFingerprint"}
    review["reviewFingerprint"] = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    service.review_provider = lambda identity, gates: review

    result = service.create(source_commit=COMMIT, reason="documentation backfill")

    assert result == {"status": "blocked", "reason": "review_evidence_mismatch"}


@pytest.mark.parametrize("error", [OSError("identity unavailable"), subprocess.TimeoutExpired("git", 1)])
def test_create_identity_subprocess_failure_is_fail_closed_json(service, error):
    service.runner.run = lambda argv, *, timeout: (_ for _ in ()).throw(error)

    result = service.create(source_commit=COMMIT, reason="documentation backfill")

    assert result == {"status": "blocked", "reason": "identity_check_failed"}


def test_create_diff_fingerprint_io_failure_is_fail_closed_json(service):
    original = service.runner.run

    def fail_diff(argv, *, timeout):
        if tuple(argv)[:2] == ("git", "diff"):
            raise subprocess.TimeoutExpired(argv, timeout)
        return original(argv, timeout=timeout)

    service.runner.run = fail_diff

    result = service.create(source_commit=COMMIT, reason="documentation backfill")

    assert result == {"status": "blocked", "reason": "diff_collection_failed"}


@pytest.mark.parametrize("error", [OSError("review unavailable"), subprocess.TimeoutExpired("review", 1)])
def test_create_review_file_io_failure_is_fail_closed_json(service, error):
    service.review_provider = lambda identity, gates: (_ for _ in ()).throw(error)

    result = service.create(source_commit=COMMIT, reason="documentation backfill")

    assert result == {"status": "blocked", "reason": "review_file_read_failed"}


def test_create_brief_read_failure_is_fail_closed_json(service):
    brief = service.project_root / ".nbs_agent_runtime" / "reports" / "verified-backfill-task2-brief.md"
    brief.unlink()
    brief.mkdir()

    result = service.create(source_commit=COMMIT, reason="documentation backfill")

    assert result == {"status": "blocked", "reason": "brief_read_failed"}


@pytest.mark.parametrize("error", [OSError("artifact write failed"), FileExistsError("run exists"), subprocess.TimeoutExpired("promote", 1)])
def test_create_artifact_write_or_promotion_failure_is_fail_closed_json(service, error):
    service._atomic_create_run = lambda *args, **kwargs: (_ for _ in ()).throw(error)

    result = service.create(source_commit=COMMIT, reason="documentation backfill")

    assert result == {"status": "blocked", "reason": "artifact_write_failed"}


def test_create_does_not_hide_programming_errors(service):
    service._atomic_create_run = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("programming error"))

    with pytest.raises(RuntimeError, match="programming error"):
        service.create(source_commit=COMMIT, reason="documentation backfill")


def test_fixed_gates_use_current_interpreter_and_fixed_project_relative_commands():
    from backend.agents import verified_backfill_service

    assert all(argv[0] == sys.executable for _, argv in verified_backfill_service._GATE_COMMANDS)
    assert all(not Path(argv[0]).is_absolute() or argv[0] == sys.executable for _, argv in verified_backfill_service._GATE_COMMANDS)


def test_create_writes_bounded_standard_artifacts_and_hashes(service):
    result = service.create(source_commit=COMMIT, reason="documentation backfill")
    assert result["status"] == "completed"
    run_dir = service.store.runs_root / result["runId"]
    assert {path.name for path in run_dir.iterdir()} >= {
        "approval.json", "implementation.json", "targeted-verification.json",
        "review.json", "full-verification.json", "hermes.json", "verified-backfill.json",
    }
    manifest = service.store._read_json(run_dir / "verified-backfill.json")
    assert manifest["sourceCommit"] == COMMIT
    assert all(len(value) == 64 for value in manifest["gateHashes"].values())
    serialized = "".join(path.read_text(encoding="utf-8") for path in run_dir.glob("*.json"))
    assert str(service.project_root) not in serialized
    assert "system_manager.py acceptance" not in serialized
    assert not any(isinstance(value, list) and len(value) > 20 for value in manifest.values())

    assert service.runner.calls
    assert all(isinstance(argv, tuple) for argv in service.runner.calls)
    assert all(";" not in " ".join(argv) and "|" not in " ".join(argv) for argv in service.runner.calls)


def test_subprocess_runner_uses_service_project_root(monkeypatch, tmp_path):
    captured = {}

    def fake_run(argv, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = SubprocessRunner(tmp_path)
    runner.run(("git", "status"), timeout=1)
    assert captured["cwd"] == tmp_path.resolve()


@pytest.mark.parametrize(
    "field",
    ["sourceCommit", "sourceBranch", "diffFingerprint", "reviewFingerprint"],
)
def test_create_blocks_review_evidence_not_bound_to_expected_source(field, service):
    review = passing_review()
    if field == "sourceCommit":
        review[field] = "c" * 40
    elif field == "sourceBranch":
        review[field] = "codex/task"
    elif field == "diffFingerprint":
        review[field] = "d" * 64
    else:
        review[field] = "e" * 64
    service.review_provider = lambda identity, gates: review
    result = service.create(source_commit=COMMIT, reason="documentation backfill")
    assert result == {"status": "blocked", "reason": "review_evidence_mismatch"}
    assert list(service.store.runs_root.iterdir()) == []


def test_create_cleans_staged_run_when_artifact_write_fails(service):
    original = service.store._atomic_json
    calls = 0

    def fail_after_first_artifact(path, payload):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("simulated artifact write failure")
        return original(path, payload)

    service.store._atomic_json = fail_after_first_artifact
    result = service.create(source_commit=COMMIT, reason="documentation backfill")

    assert result == {"status": "blocked", "reason": "artifact_write_failed"}
    assert not [path for path in service.store.runs_root.iterdir() if not path.name.startswith(".")]
    assert not list(service.store.runs_root.glob(".run-*"))
