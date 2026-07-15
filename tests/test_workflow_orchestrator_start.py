from __future__ import annotations

import hashlib
import io
import json
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from backend.agents.workflow_models import WorkflowEvent, WorkflowStatus
from backend.agents.workflow_notifications import NotificationResult
from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.context_agent_service import context_bundle_from_payload
from backend.agents.workflow_orchestrator import (
    OUTPUT_TAIL,
    StageResult,
    SubprocessStageExecutor,
    WorkflowOrchestrator,
)
from backend.agents.workflow_store import WorkflowStore


BRIEF = Path("docs/agents/CODEX_AGENT_DISPATCH.md")
CONTEXT_PAYLOAD = {
    "status": "ready",
    "taskUnderstanding": [],
    "systemBoundaries": [],
    "relevantFiles": [],
    "dependencies": [],
    "recommendedTests": [],
    "risks": [],
    "unknowns": [],
    "contextFingerprint": "a" * 64,
}
_COLLECT_ONLY_UNSIGNED = {
    "schemaVersion": "context-evidence-v1",
    "task": {"id": "task-5", "objective": "collect context", "scope": [], "forbidden": []},
    "repository": {"head": "a" * 40, "dirtyFiles": []},
    "guardrails": {"revenueScope": "read-only"},
    "documents": [],
    "symbols": [],
    "relatedTests": [],
    "recentChanges": [],
}
COLLECT_ONLY_PAYLOAD = {
    **_COLLECT_ONLY_UNSIGNED,
    "bundleFingerprint": canonical_fingerprint(_COLLECT_ONLY_UNSIGNED),
}


@dataclass
class FakeExecutor:
    result: StageResult
    calls: list[tuple[tuple[str, ...], int]]

    def run_json(self, argv: tuple[str, ...], *, timeout: int) -> StageResult:
        self.calls.append((argv, timeout))
        return self.result


@dataclass
class FakeNotifier:
    messages: list[tuple[str, str]]
    warning: str | None = None

    def send(self, title: str, message: str) -> NotificationResult:
        self.messages.append((title, message))
        return NotificationResult(False, self.warning)


def executor(payload: dict = CONTEXT_PAYLOAD, *, exit_code: int = 0) -> FakeExecutor:
    return FakeExecutor(
        StageResult(exit_code, payload, "stdout tail", "stderr tail", 3), []
    )


def make_orchestrator(tmp_path: Path, stage: FakeExecutor, notifier: FakeNotifier, **kwargs):
    return WorkflowOrchestrator(
        Path(__file__).resolve().parents[1],
        store=WorkflowStore(tmp_path),
        stage_executor=stage,
        notifier=notifier,
        **kwargs,
    )


def test_start_creates_manifest_context_artifact_and_stops_for_authorization(tmp_path):
    stage = executor()
    notifier = FakeNotifier([])
    result = make_orchestrator(tmp_path, stage, notifier).start(BRIEF)

    assert result.status == "awaiting_authorization"
    run_dir = tmp_path / ".nbs_agent_runtime" / "runs" / result.run_id
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["briefPath"] == BRIEF.as_posix()
    assert manifest["briefSha256"] == hashlib.sha256(BRIEF.read_bytes()).hexdigest()
    assert len(manifest["gitHead"]) == 40
    assert manifest["gitBranch"]
    assert isinstance(manifest["dirtyFiles"], list)
    assert manifest["contextFingerprint"] == "a" * 64
    assert json.loads((run_dir / "context.json").read_text()) == CONTEXT_PAYLOAD
    assert json.loads((run_dir / "status.json").read_text())["status"] == "awaiting_authorization"
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    assert [event["toStatus"] for event in events] == ["context_running", "awaiting_authorization"]
    assert any("authorization" in title.lower() for title, _ in notifier.messages)


def test_start_defaults_to_collect_only_and_does_not_persist_command(tmp_path):
    stage = executor()
    result = make_orchestrator(tmp_path, stage, FakeNotifier([])).start(BRIEF)

    argv = stage.calls[0][0]
    assert "--collect-only" in argv
    assert "--output" not in argv
    run_dir = tmp_path / ".nbs_agent_runtime" / "runs" / result.run_id
    assert "context_agent_command" not in (run_dir / "manifest.json").read_text()


def test_collect_only_context_evidence_payload_stops_for_authorization(tmp_path):
    stage = executor(COLLECT_ONLY_PAYLOAD)

    result = make_orchestrator(tmp_path, stage, FakeNotifier([])).start(BRIEF)

    assert result.status == "awaiting_authorization"
    run_dir = tmp_path / ".nbs_agent_runtime" / "runs" / result.run_id
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["contextFingerprint"] == COLLECT_ONLY_PAYLOAD["bundleFingerprint"]
    context_bundle_from_payload(COLLECT_ONLY_PAYLOAD)


def test_malformed_collect_only_evidence_does_not_reach_authorization(tmp_path):
    malformed = {**COLLECT_ONLY_PAYLOAD, "documents": [{"invalid": True}]}
    stage = executor(malformed)

    result = make_orchestrator(tmp_path, stage, FakeNotifier([])).start(BRIEF)

    assert result.status == "failed"
    assert result.status != "awaiting_authorization"


def test_start_forwards_supplied_context_command_without_persisting_it(tmp_path):
    stage = executor()
    result = make_orchestrator(tmp_path, stage, FakeNotifier([])).start(
        BRIEF, context_agent_command="codex --json"
    )

    assert "--collect-only" not in stage.calls[0][0]
    assert stage.calls[0][0][-2:] == ("--agent-command", "codex --json")
    run_dir = tmp_path / ".nbs_agent_runtime" / "runs" / result.run_id
    assert "codex --json" not in (run_dir / "manifest.json").read_text()


def test_git_identity_uses_nul_porcelain_and_records_delete_rename_and_status_paths(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "old name.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "deleted.py").write_text("DELETE = True\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    subprocess.run(["git", "mv", "old name.py", "new name.py"], cwd=tmp_path, check=True)
    (tmp_path / "deleted.py").unlink()
    (tmp_path / "untracked name.py").write_text("UNTRACKED = True\n", encoding="utf-8")

    flow = object.__new__(WorkflowOrchestrator)
    flow.project_root = tmp_path
    identity = flow._git_identity()
    entries = {item["path"]: item for item in identity["dirtyFiles"]}

    assert entries["deleted.py"]["status"] == " D"
    assert entries["new name.py"] == {
        "status": "R ",
        "path": "new name.py",
        "originalPath": "old name.py",
        "sha256": hashlib.sha256((tmp_path / "new name.py").read_bytes()).hexdigest(),
    }
    assert entries["untracked name.py"]["status"] == "??"
    assert all(len(item["sha256"]) == 64 for item in entries.values())

    before = identity["dirtyFiles"]
    (tmp_path / "deleted.py").write_text("DELETE = False\n", encoding="utf-8")
    assert flow._git_identity()["dirtyFiles"] != before


def test_missing_or_denied_brief_blocks_before_context(tmp_path):
    stage = executor()
    orchestrator = make_orchestrator(tmp_path, stage, FakeNotifier([]))

    result = orchestrator.start(Path("docs/agents/missing-brief.md"))

    assert result.status == "blocked"
    assert result.error_code in {"blocked_missing_brief", "blocked_brief"}
    assert stage.calls == []


@pytest.mark.parametrize("exit_code", [2, 5])
def test_context_failure_is_recorded_as_blocked_or_failed(tmp_path, exit_code):
    stage = executor({"status": "blocked_missing_evidence", **CONTEXT_PAYLOAD}, exit_code=exit_code)
    result = make_orchestrator(tmp_path, stage, FakeNotifier([])).start(BRIEF)

    assert result.status in {"blocked", "failed"}
    assert result.error_code
    run_dir = tmp_path / ".nbs_agent_runtime" / "runs" / result.run_id
    assert json.loads((run_dir / "status.json").read_text())["status"] == result.status


@pytest.mark.parametrize(
    ("error", "expected_code", "expected_message"),
    [
        (
            subprocess.TimeoutExpired(["codex", "exec", "--token", "runner-secret"], 1),
            "failed_context_timeout",
            "Context stage timed out",
        ),
        (
            RuntimeError("runner failed: codex exec --token runner-secret"),
            "failed_context_executor",
            "Context stage execution failed",
        ),
    ],
)
def test_stage_timeout_and_error_never_persist_or_notify_full_runner_argv(
    tmp_path, error, expected_code, expected_message,
):
    class RaisingExecutor:
        def run_json(self, argv, *, timeout, require_json=True):
            raise error

    notifier = FakeNotifier([])
    flow = make_orchestrator(tmp_path, RaisingExecutor(), notifier)

    status = flow.start(
        BRIEF,
        context_agent_command="codex exec --token runner-secret",
    )

    assert status.status == "failed"
    assert status.error_code == expected_code
    assert status.message == expected_message
    run_dir = tmp_path / ".nbs_agent_runtime" / "runs" / status.run_id
    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in run_dir.iterdir()
        if path.is_file() and path.name != ".lock"
    )
    notified = "\n".join(f"{title}\n{message}" for title, message in notifier.messages)
    assert "runner-secret" not in persisted
    assert "runner-secret" not in notified
    assert "codex exec" not in persisted
    assert "codex exec" not in notified


def test_notifier_and_housekeeping_failures_only_emit_warnings(tmp_path):
    stage = executor()
    notifier = FakeNotifier([], warning="notification unavailable")
    warnings: list[str] = []

    def housekeeping() -> None:
        raise RuntimeError("retention unavailable")

    result = make_orchestrator(
        tmp_path, stage, notifier, housekeeping=housekeeping, warning_sink=warnings.append
    ).start(BRIEF)

    assert result.status == "awaiting_authorization"
    assert any("notification" in warning for warning in warnings)
    assert any("housekeeping" in warning for warning in warnings)


def test_production_stage_result_contract_is_importable():
    status = WorkflowStatus.from_dict(
        {
            "schemaVersion": "agent-workflow-status-v1",
            "runId": "run-1",
            "stage": "context",
            "status": "awaiting_authorization",
            "startedAt": "2026-07-15T00:00:00+00:00",
            "updatedAt": "2026-07-15T00:00:00+00:00",
            "completedAt": None,
            "message": "ready",
            "errorCode": None,
            "artifactBytes": 0,
        }
    )
    assert status.status == "awaiting_authorization"


def test_subprocess_executor_drains_oversized_stdout_and_stderr_with_bounded_tails(monkeypatch):
    def unexpected_run(*args, **kwargs):
        raise AssertionError("bounded executor must not use subprocess.run")

    monkeypatch.setattr(subprocess, "run", unexpected_run)
    executor = SubprocessStageExecutor(Path(__file__).resolve().parents[3])
    script = (
        "import sys; "
        f"sys.stdout.write('o' * {OUTPUT_TAIL * 3}); sys.stdout.flush(); "
        f"sys.stderr.write('e' * {OUTPUT_TAIL * 3}); sys.stderr.flush(); "
        "sys.exit(7)"
    )

    result = executor.run_json((str(executor.python), "-c", script), timeout=10)

    assert result.exit_code == 7
    assert result.payload == {}
    assert len(result.stdout_tail.encode("utf-8")) == OUTPUT_TAIL
    assert len(result.stderr_tail.encode("utf-8")) == OUTPUT_TAIL
    assert set(result.stdout_tail) == {"o"}
    assert set(result.stderr_tail) == {"e"}


def test_subprocess_executor_parses_successful_json_larger_than_tail(monkeypatch):
    def unexpected_run(*args, **kwargs):
        raise AssertionError("bounded executor must not use subprocess.run")

    monkeypatch.setattr(subprocess, "run", unexpected_run)
    executor = SubprocessStageExecutor(Path(__file__).resolve().parents[3])
    expected_padding = "x" * (OUTPUT_TAIL * 2)
    payload = {"status": "ready", "contextFingerprint": "a" * 64, "padding": expected_padding}
    encoded = json.dumps(payload, ensure_ascii=False)
    script = f"import sys; sys.stdout.write({encoded!r}); sys.stdout.flush()"

    result = executor.run_json((str(executor.python), "-c", script), timeout=10)

    assert result.exit_code == 0
    assert result.payload == payload
    assert len(result.stdout_tail.encode("utf-8")) == OUTPUT_TAIL


def test_subprocess_executor_allows_successful_text_output_when_json_is_not_required(monkeypatch):
    def unexpected_run(*args, **kwargs):
        raise AssertionError("bounded executor must not use subprocess.run")

    monkeypatch.setattr(subprocess, "run", unexpected_run)
    executor = SubprocessStageExecutor(Path(__file__).resolve().parents[3])
    script = "import sys; sys.stdout.write('35 passed in 1.58s\\n')"

    result = executor.run_json((str(executor.python), "-c", script), timeout=10, require_json=False)

    assert result.exit_code == 0
    assert result.payload == {}
    assert result.stdout_tail == "35 passed in 1.58s\n"


def test_subprocess_executor_kills_process_group_on_timeout(monkeypatch):
    calls: list[tuple] = []

    class FakeProcess:
        pid = 4321
        returncode = -signal.SIGKILL

        def __init__(self):
            self.stdout = io.BytesIO()
            self.stderr = io.BytesIO()
            self.wait_calls = 0

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired("fake", timeout)
            return self.returncode

    process = FakeProcess()

    def fake_popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(os, "getpgid", lambda pid: pid + 1)
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: calls.append(("killpg", pgid, sig)))
    executor = SubprocessStageExecutor(Path(__file__).resolve().parents[3])

    with pytest.raises(subprocess.TimeoutExpired):
        executor.run_json(("fake-python", "-c", "pass"), timeout=1)

    assert calls[0][1]["start_new_session"] is True
    assert ("killpg", 4322, signal.SIGKILL) in calls
