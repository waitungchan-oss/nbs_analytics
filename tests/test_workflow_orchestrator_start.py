from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from backend.agents.workflow_models import WorkflowEvent, WorkflowStatus
from backend.agents.workflow_notifications import NotificationResult
from backend.agents.workflow_orchestrator import (
    StageResult,
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
    assert any(item["path"].endswith("test_workflow_orchestrator_start.py") for item in manifest["dirtyFiles"])
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


def test_start_forwards_supplied_context_command_without_persisting_it(tmp_path):
    stage = executor()
    result = make_orchestrator(tmp_path, stage, FakeNotifier([])).start(
        BRIEF, context_agent_command="codex --json"
    )

    assert "--collect-only" not in stage.calls[0][0]
    assert stage.calls[0][0][-2:] == ("--agent-command", "codex --json")
    run_dir = tmp_path / ".nbs_agent_runtime" / "runs" / result.run_id
    assert "codex --json" not in (run_dir / "manifest.json").read_text()


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
